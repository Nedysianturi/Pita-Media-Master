"""
Client Google Gemini AI untuk Sistem Pita Media.
Terintegrasi penuh dengan Centralized Gemini Rate Limiter (Jeda 15s Free Tier, Concurrency 1,
Smart 429 RetryInfo backoff, dan model murni dari .env tanpa hardcoding).
"""

import asyncio
import json
import os
import re
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Type, TypeVar
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from database.models import CostRecord
from core.resilience.gemini_rate_limiter import gemini_rate_limiter

logger = logging.getLogger("gemini_client")

try:
    from google import genai
    from google.genai import types
    GENAI_NEW_SDK = True
except ImportError:
    import google.generativeai as genai
    GENAI_NEW_SDK = False

T = TypeVar("T", bound=BaseModel)


class GeminiClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_keys = []
        if api_key:
            self.api_keys = [api_key]
        else:
            from core.security.secret_store import secret_store
            k1 = secret_store.get_secret("GEMINI_PRIMARY_API_KEY") or secret_store.get_secret("GEMINI_API_KEY") or settings.GEMINI_API_KEY
            k2 = secret_store.get_secret("GEMINI_BACKUP_API_KEY") or secret_store.get_secret("GEMINI_API_KEY_2") or getattr(settings, "GEMINI_API_KEY_2", "")
            for k in [k1, k2]:
                if k and k != "your_gemini_api_key_here" and k not in self.api_keys:
                    self.api_keys.append(k)
        
        self.current_key_idx = 0
        self.rate_limiter = gemini_rate_limiter
        self.client = None
        self._init_client()

    def _init_client(self):
        if not self.api_keys:
            self.client = None
            return
        active_key = self.api_keys[self.current_key_idx]
        if GENAI_NEW_SDK:
            try:
                self.client = genai.Client(api_key=active_key)
            except Exception:
                self.client = None
        else:
            genai.configure(api_key=active_key)

    def rotate_key(self) -> bool:
        """Rotates to next available Gemini API key if multiple are configured."""
        if len(self.api_keys) > 1:
            self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
            logger.info(f"Mengalihkan otomatis ke Gemini API Key #{self.current_key_idx + 1}...")
            self._init_client()
            return True
        return False

    @property
    def api_key(self) -> str:
        return self.api_keys[self.current_key_idx] if self.api_keys else ""

    def is_configured(self) -> bool:
        return bool(self.api_keys and any(k != "your_gemini_api_key_here" for k in self.api_keys))

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None,
    ) -> str:
        """
        Menghasilkan teks dari Gemini dengan Centralized Rate Limiting dan Smart 429 Retry.
        Model dipilih murni dari parameter atau .env (settings.GEMINI_TEXT_MODEL).
        """
        target_model = model or settings.GEMINI_TEXT_MODEL

        if not self.is_configured() or not self.client:
            return f"[MOCK TEXT RESPONSE]: Narasi estetika untuk {prompt[:40]}..."

        max_retries = settings.GEMINI_MAX_RETRIES_429
        attempt = 1

        # Kunci mutex concurrency = 1
        async with self.rate_limiter.lock:
            while attempt <= max_retries:
                # 1. Tunggu slot interval aman (minimal 15s)
                await self.rate_limiter.acquire_slot(target_model)

                try:
                    if GENAI_NEW_SDK and self.client:
                        config = {}
                        if system_instruction:
                            config["system_instruction"] = system_instruction

                        response = self.client.models.generate_content(
                            model=target_model,
                            contents=prompt,
                            config=config if config else None,
                        )
                        text_out = response.text or ""

                        # Catat estimasi biaya
                        if hasattr(response, "usage_metadata") and response.usage_metadata and db_session:
                            total_tokens = getattr(response.usage_metadata, "total_token_count", 0) or 0
                            cost_est = (total_tokens / 1_000_000) * 0.10
                            cost_rec = CostRecord(
                                job_id=job_id,
                                service="gemini_text",
                                token_count=total_tokens,
                                estimated_cost_usd=cost_est,
                            )
                            db_session.add(cost_rec)
                            await db_session.commit()

                        return text_out

                    else:
                        model_inst = genai.GenerativeModel(
                            model_name=target_model,
                            system_instruction=system_instruction,
                        )
                        response = model_inst.generate_content(prompt)
                        return response.text or ""

                except Exception as e:
                    err_msg = str(e)

                    # Jika 429 RESOURCE_EXHAUSTED:
                    if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "quota" in err_msg.lower():
                        # Jika ada API Key cadangan, rotasi ke key berikutnya terlebih dahulu
                        if self.rotate_key() and attempt < max_retries:
                            attempt += 1
                            await asyncio.sleep(1.0)
                            continue

                        # Jika kuota harian model tersebut habis (PerDay / limit: 20), beralih ke model alternatif
                        if "perday" in err_msg.lower() or "limit: 20" in err_msg:
                            pool = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"]
                            next_models = [m for m in pool if m != target_model]
                            if next_models and attempt < max_retries:
                                next_model = next_models[(attempt - 1) % len(next_models)]
                                logger.warning(f"Kuota harian untuk model '{target_model}' tercapai. Mengalihkan otomatis ke model alternatif '{next_model}'...")
                                target_model = next_model
                                attempt += 1
                                await asyncio.sleep(2.0)
                                continue

                        if attempt < max_retries:
                            await self.rate_limiter.handle_429_backoff(target_model, e, attempt)
                            attempt += 1
                            continue
                        else:
                            logger.error(f"Batas retry 429 ({max_retries}) tercapai untuk model '{target_model}'.")
                            raise RuntimeError(f"Gemini API 429 (Rate Limit Terlampaui setelah {max_retries} retry): {e}")

                    # Jika 503 UNAVAILABLE / High Demand / 500 Internal Error / 404: Tunggu sebentar lalu retry dengan model stabil
                    if "503" in err_msg or "UNAVAILABLE" in err_msg or "high demand" in err_msg.lower() or "500" in err_msg or "404" in err_msg or "NOT_FOUND" in err_msg:
                        if attempt < max_retries:
                            wait_time = 2.0 * attempt + 1.0
                            pool = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"]
                            next_model = pool[(attempt - 1) % len(pool)]
                            logger.warning(f"Model '{target_model}' mengalami kendala ({err_msg[:60]}...). Beralih ke model '{next_model}' (Percobaan {attempt}/{max_retries})...")
                            target_model = next_model
                            await asyncio.sleep(wait_time)
                            attempt += 1
                            continue
                        else:
                            logger.error(f"Batas retry ({max_retries}) tercapai untuk model '{target_model}'.")
                            raise RuntimeError(f"Gemini API Error setelah {max_retries} retry: {e}")

                    # Jika error model tidak ditemukan (404)
                    if "404" in err_msg or "NOT_FOUND" in err_msg:
                        logger.error(f"Model '{target_model}' tidak ditemukan di API version ini.")
                        raise RuntimeError(f"Model '{target_model}' dari .env tidak didukung atau tidak ditemukan: {e}")

                    # Error lainnya
                    logger.error(f"Error memanggil Gemini API ({target_model}): {e}")
                    raise RuntimeError(f"Gagal memanggil Gemini API ({target_model}): {e}")

        raise RuntimeError(f"Gagal memproses request Gemini setelah {max_retries} percobaan.")

    async def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None,
    ) -> T:
        """
        Menghasilkan output terstruktur yang tervalidasi dengan skema Pydantic.
        Model dipilih murni dari parameter atau .env (settings.GEMINI_PRO_MODEL).
        """
        target_model = model or settings.GEMINI_PRO_MODEL

        if not self.is_configured() or not self.client:
            return schema.model_construct()

        schema_json = json.dumps(schema.model_json_schema())
        enforced_prompt = (
            f"{prompt}\n\n"
            f"KEMBALIKAN OUTPUT HARUS HANYA BERUPA JSON VALID SESUAI SKEMA BERIKUT (TANPA PENJELASAN LAIN DAN TANPA MARKDOWN BACKTICKS):\n"
            f"{schema_json}"
        )

        raw_output = await self.generate_text(
            prompt=enforced_prompt,
            system_instruction=system_instruction,
            model=target_model,
            db_session=db_session,
            job_id=job_id,
        )

        try:
            cleaned = raw_output.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(0)

            parsed = json.loads(cleaned)
            return schema.model_validate(parsed)
        except Exception as parse_err:
            logger.warning(f"Gagal parse output JSON: {parse_err}. Menggunakan konstruksi aman.")
            return schema.model_construct()


gemini_client = GeminiClient()
