"""
Pita Media Enterprise AI Engine - xAI / Grok Provider Adapter
Implements BaseAIProvider for xAI Grok API (Text Generation & Deep Reasoning).
"""

import os
import json
import re
import httpx
import logging
from typing import Dict, Any, List, Optional, Type, TypeVar
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from providers.base_provider import BaseAIProvider, AICapability
from database.models import CostRecord

logger = logging.getLogger("pita.providers.xai")

T = TypeVar("T", bound=BaseModel)

class XAIProvider(BaseAIProvider):
    def __init__(self, api_key: Optional[str] = None):
        super().__init__(
            provider_id="xai",
            display_name="xAI / Grok",
            api_key=api_key or os.getenv("XAI_API_KEY", ""),
            base_url="https://api.x.ai/v1",
            default_model="grok-beta",
            secondary_model="grok-2",
            capabilities=[
                AICapability.TEXT,
                AICapability.REASONING,
                AICapability.MODERATION
            ],
            cost_per_1m_tokens_usd=0.25,
            rate_limit_rpm=30,
            enabled=bool(api_key or os.getenv("XAI_API_KEY", "")),
            priority=2
        )

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None
    ) -> str:
        if not self.api_key:
            raise RuntimeError("xAI API Key belum dikonfigurasi.")

        target_model = model or self.default_model
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.7
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"xAI API Error (HTTP {resp.status_code}): {resp.text}")

            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            # Record cost
            if db_session and "usage" in data:
                tokens = data["usage"].get("total_tokens", 0)
                cost = (tokens / 1_000_000) * self.cost_per_1m_tokens_usd
                rec = CostRecord(
                    job_id=job_id,
                    service="xai_grok",
                    token_count=tokens,
                    estimated_cost_usd=cost
                )
                db_session.add(rec)
                await db_session.commit()

            return content

    async def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None
    ) -> T:
        schema_json = json.dumps(schema.model_json_schema())
        enforced_prompt = (
            f"{prompt}\n\n"
            f"KEMBALIKAN OUTPUT HANYA SEBAGAI JSON VALID SESUAI SKEMA BERIKUT (TANPA MARKDOWN DAN TANPA PENJELASAN LAIN):\n"
            f"{schema_json}"
        )

        raw = await self.generate_text(
            prompt=enforced_prompt,
            system_instruction=system_instruction,
            model=model,
            db_session=db_session,
            job_id=job_id
        )

        cleaned = raw.strip()
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

    def test_connection(self) -> Dict[str, Any]:
        from core.security.credential_manager import credential_manager
        return credential_manager.test_connection("xai", custom_token=self.api_key)

xai_provider = XAIProvider()
