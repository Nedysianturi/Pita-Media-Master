"""
Pita Media Enterprise AI Engine - OpenRouter Multi-Model Gateway Adapter
Implements BaseAIProvider for OpenRouter OpenAI-compatible API (https://openrouter.ai/api/v1).
Supports dynamic model selection, multi-model fallback pool, circuit breaker isolation,
capability-aware dispatch, and Cost Governor integration.
"""

import os
import time
import json
import re
import random
import asyncio
import logging
from typing import Dict, Any, List, Optional, Type, TypeVar
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from providers.base_provider import BaseAIProvider, AICapability
from database.models import CostRecord
from core.security.secret_store import secret_store
from core.governors.cost_governor import cost_governor

logger = logging.getLogger("pita.providers.openrouter")

T = TypeVar("T", bound=BaseModel)


class CircuitState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class OpenRouterCircuitBreaker:
    """Independent circuit breaker specifically for OpenRouter provider."""
    def __init__(self, failure_threshold: int = 5, recovery_cooldown_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_cooldown = recovery_cooldown_seconds
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = 0.0
        self.last_state_change = time.time()

    def is_available(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_cooldown:
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = time.time()
                logger.info("[OPENROUTER CIRCUIT] Cooldown elapsed. Entering HALF_OPEN test state.")
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return True
        return True

    def record_success(self):
        self.consecutive_failures = 0
        if self.state != CircuitState.CLOSED:
            logger.info("[OPENROUTER CIRCUIT] Request succeeded. Circuit state returned to CLOSED.")
            self.state = CircuitState.CLOSED
            self.last_state_change = time.time()

    def record_failure(self, error: Optional[Exception] = None):
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        logger.warning(f"[OPENROUTER CIRCUIT] Recorded failure #{self.consecutive_failures}: {error}")
        if self.consecutive_failures >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                self.state = CircuitState.OPEN
                self.last_state_change = time.time()
                logger.error(f"[OPENROUTER CIRCUIT] Threshold reached ({self.failure_threshold}). Circuit OPENED. Cooldown: {self.recovery_cooldown}s.")


class OpenRouterProvider(BaseAIProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1",
        default_model: Optional[str] = None,
        secondary_model: Optional[str] = None,
        model_pool: Optional[List[str]] = None,
        enabled: bool = True
    ):
        secret_ref = "OPENROUTER_API_KEY"
        configured_model = default_model or os.getenv("OPENROUTER_DEFAULT_MODEL", "openai/gpt-4o-mini")
        configured_secondary = secondary_model or os.getenv("OPENROUTER_FALLBACK_MODEL", "anthropic/claude-3.5-haiku")

        super().__init__(
            provider_id="openrouter",
            display_name="OpenRouter",
            api_key=api_key or "",
            base_url=base_url or "https://openrouter.ai/api/v1",
            default_model=configured_model,
            secondary_model=configured_secondary,
            capabilities=[
                AICapability.TEXT,
                AICapability.REASONING,
                AICapability.MODERATION,
                AICapability.OTHER
            ],
            cost_per_1m_tokens_usd=0.15,
            rate_limit_rpm=30,
            enabled=enabled,
            priority=30
        )
        self.secret_ref = secret_ref
        self.provider_type = "MULTI_MODEL_GATEWAY"
        self.circuit_breaker = OpenRouterCircuitBreaker()
        self.model_pool: List[str] = model_pool or [self.default_model]
        if self.secondary_model and self.secondary_model not in self.model_pool:
            self.model_pool.append(self.secondary_model)

    @property
    def api_key(self) -> str:
        """Always resolves authoritative key from DPAPI SecretStore."""
        return secret_store.get_secret(self.secret_ref) or self._api_key or os.getenv("OPENROUTER_API_KEY", "")

    @api_key.setter
    def api_key(self, value: str):
        self._api_key = value

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def get_models_for_capability(self, capability: AICapability) -> List[str]:
        """Returns candidate model slugs supporting the capability."""
        # OpenRouter models support text/reasoning natively
        return self.model_pool

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "https://pitamedia.localhost"),
            "X-Title": os.getenv("OPENROUTER_APP_TITLE", "Pita Media Autonomous Suite")
        }
        return headers

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None
    ) -> str:
        if not self.is_configured():
            raise RuntimeError("OPENROUTER_API_KEY belum dikonfigurasi di Vault.")

        if not self.circuit_breaker.is_available():
            raise RuntimeError(f"OpenRouter Circuit Breaker is OPEN (failures: {self.circuit_breaker.consecutive_failures}).")

        # Check Cost Governor
        if db_session:
            can_proceed, reason = await cost_governor.can_proceed_with_paid_generation(db_session, estimated_addition_usd=0.01)
            if not can_proceed:
                raise RuntimeError(f"COST_LIMIT_REACHED: {reason}")

        target_model = model or self.default_model
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = self._get_headers()
        payload = {
            "model": target_model,
            "messages": messages,
            "temperature": 0.7
        }

        max_retries = 3
        last_err = None

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)

                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    self.circuit_breaker.record_success()

                    # Record cost in CostGovernor
                    usage = data.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", len(prompt) // 4)
                    completion_tokens = usage.get("completion_tokens", len(content) // 4)
                    total_tokens = prompt_tokens + completion_tokens
                    estimated_cost = round((total_tokens / 1_000_000) * self.cost_per_1m_tokens_usd, 6)

                    if db_session:
                        try:
                            cost_rec = CostRecord(
                                job_id=job_id,
                                service="openrouter",
                                token_count=total_tokens,
                                duration_seconds=0.0,
                                estimated_cost_usd=estimated_cost
                            )
                            db_session.add(cost_rec)
                            await db_session.commit()
                        except Exception as log_err:
                            logger.warning(f"Failed to record OpenRouter cost: {log_err}")

                    return content

                elif resp.status_code == 429:
                    retry_after = 2.0 * (2 ** attempt) + random.uniform(0.1, 0.5)
                    logger.warning(f"[OPENROUTER] Rate limited (429). Retrying in {retry_after:.2f}s...")
                    await asyncio.sleep(retry_after)
                    continue

                elif resp.status_code == 402:
                    self.circuit_breaker.record_failure()
                    raise RuntimeError("QUOTA_EXHAUSTED: OpenRouter credit balance insufficient (HTTP 402).")

                elif resp.status_code in [401, 403]:
                    self.circuit_breaker.record_failure()
                    raise RuntimeError(f"AUTH_ERROR: OpenRouter API key invalid or unauthorized (HTTP {resp.status_code}).")

                else:
                    self.circuit_breaker.record_failure()
                    raise RuntimeError(f"OpenRouter Error (HTTP {resp.status_code}): {resp.text[:200]}")

            except httpx.RequestError as net_err:
                last_err = net_err
                wait_t = 1.5 * (attempt + 1)
                logger.warning(f"[OPENROUTER] Network error attempt {attempt+1}/{max_retries}: {net_err}")
                await asyncio.sleep(wait_t)
            except Exception as e:
                self.circuit_breaker.record_failure(e)
                raise

        self.circuit_breaker.record_failure(last_err)
        raise RuntimeError(f"OpenRouter generation failed after {max_retries} attempts: {last_err}")

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
            f"KEMBALIKAN OUTPUT HANYA SEBAGAI JSON VALID SESUAI SKEMA BERIKUT:\n"
            f"{schema_json}"
        )
        raw_text = await self.generate_text(
            prompt=enforced_prompt,
            system_instruction=system_instruction,
            model=model,
            db_session=db_session,
            job_id=job_id
        )
        cleaned = raw_text.strip().replace("```json", "").replace("```", "").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        json_str = match.group(0) if match else cleaned
        parsed = json.loads(json_str)
        return schema.model_validate(parsed)

    def test_connection(self, custom_token: Optional[str] = None) -> Dict[str, Any]:
        """Runs lightweight authentication and connectivity test against OpenRouter."""
        token = custom_token or self.api_key
        if not token:
            return {"status": "NOT_CONFIGURED", "message": "OPENROUTER_API_KEY belum dikonfigurasi di Vault."}

        start_t = time.time()
        url = self.base_url.rstrip("/") + "/models"
        headers = {
            "Authorization": f"Bearer {token}",
            "HTTP-Referer": "https://pitamedia.localhost",
            "X-Title": "Pita Media Diagnostic Check"
        }

        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url, headers=headers)
            latency = round((time.time() - start_t) * 1000)

            if resp.status_code == 200:
                models_data = resp.json().get("data", [])
                models_count = len(models_data)
                return {
                    "status": "VALID",
                    "latency_ms": latency,
                    "message": f"Koneksi OpenRouter Aktif ({models_count} model tersedia)."
                }
            elif resp.status_code == 429:
                return {
                    "status": "RATE_LIMITED",
                    "latency_ms": latency,
                    "message": "OpenRouter terhubung namun terkena batas rate limit sementara (429)."
                }
            elif resp.status_code == 402:
                return {
                    "status": "QUOTA_EXHAUSTED",
                    "latency_ms": latency,
                    "message": "Saldo kredit OpenRouter habis atau perlu top-up (402)."
                }
            elif resp.status_code in [401, 403]:
                return {
                    "status": "INVALID",
                    "latency_ms": latency,
                    "message": "OPENROUTER_API_KEY tidak valid atau ditolak oleh OpenRouter."
                }
            else:
                return {
                    "status": "NEEDS_ATTENTION",
                    "latency_ms": latency,
                    "message": f"OpenRouter HTTP {resp.status_code}"
                }
        except httpx.TimeoutException:
            return {
                "status": "NETWORK_ERROR",
                "message": "Timeout saat menghubungi https://openrouter.ai/api/v1 (koneksi lambat)."
            }
        except Exception as e:
            return {
                "status": "NETWORK_ERROR",
                "message": f"Eror jaringan koneksi OpenRouter: {str(e)}"
            }

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["secret_ref"] = self.secret_ref
        d["provider_type"] = self.provider_type
        d["tier"] = "SECONDARY (Tier 2 Multi-Model)"
        d["circuit_state"] = self.circuit_breaker.state
        d["model_pool"] = self.model_pool
        return d


openrouter_provider = OpenRouterProvider()
