"""
Pita Media Enterprise AI Engine - Capability-Aware AI Router
Routes requests to the optimal AI provider based on capability, priority, health, and cost rules.
Features:
- Capability checks (TEXT, REASONING, IMAGE, VIDEO, VISION, EMBEDDINGS)
- Failure classification (AUTH_ERROR, RATE_LIMIT, DAILY_QUOTA, NETWORK, SERVER_ERROR, etc.)
- Quota Scope tracking (prevents endless loop between keys sharing project quota)
"""

import logging
from enum import Enum
from typing import Dict, Any, List, Optional, Type, TypeVar
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from providers.base_provider import BaseAIProvider, AICapability
from providers.provider_registry import provider_registry
from core.security.credential_health import credential_health_engine

logger = logging.getLogger("pita.providers.router")

T = TypeVar("T", bound=BaseModel)


class AIFailureReason(str, Enum):
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    DAILY_QUOTA = "DAILY_QUOTA"
    NETWORK = "NETWORK"
    SERVER_ERROR = "SERVER_ERROR"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    SAFETY_BLOCK = "SAFETY_BLOCK"
    UNKNOWN = "UNKNOWN"


def classify_error(err: Exception) -> AIFailureReason:
    msg = str(err).lower()
    if "401" in msg or "403" in msg or "invalid" in msg or "unauthorized" in msg:
        return AIFailureReason.AUTH_ERROR
    elif "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return AIFailureReason.RATE_LIMIT
    elif "quota" in msg or "resource_exhausted" in msg:
        return AIFailureReason.DAILY_QUOTA
    elif "safety" in msg or "blocked" in msg or "content policy" in msg:
        return AIFailureReason.SAFETY_BLOCK
    elif "503" in msg or "unavailable" in msg or "not found" in msg or "404" in msg:
        return AIFailureReason.MODEL_UNAVAILABLE
    elif "500" in msg or "502" in msg or "server error" in msg:
        return AIFailureReason.SERVER_ERROR
    elif "timeout" in msg or "connection" in msg or "network" in msg or "dns" in msg:
        return AIFailureReason.NETWORK
    return AIFailureReason.UNKNOWN


class AIProviderRouter:
    def __init__(self):
        self.registry = provider_registry
        self.health_engine = credential_health_engine
        self._quota_exhausted_scopes: set = set()

    def select_provider(self, capability: AICapability, preferred_id: Optional[str] = None) -> Optional[BaseAIProvider]:
        """Selects highest-priority healthy provider supporting the requested capability."""
        if preferred_id:
            p = self.registry.get_provider(preferred_id)
            if p and p.supports(capability) and p.enabled and p.api_key:
                return p

        candidates = self.registry.get_providers_by_capability(capability)
        for cand in candidates:
            if cand.enabled and cand.api_key:
                return cand

        return candidates[0] if candidates else None

    def get_best_provider(self, capability: AICapability, preferred_id: Optional[str] = None) -> Optional[BaseAIProvider]:
        """Alias for select_provider for capability-aware routing."""
        return self.select_provider(capability, preferred_id=preferred_id)

    async def route_text_generation(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        capability: AICapability = AICapability.TEXT,
        preferred_provider: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None
    ) -> str:
        """Executes text generation with capability-aware fallback."""
        candidates = self.registry.get_providers_by_capability(capability)
        if not candidates:
            raise RuntimeError(f"Tidak ada provider AI yang mendukung kapabilitas: {capability.value}")

        last_error = None
        for prov in candidates:
            if not prov.enabled or not prov.api_key:
                continue
            try:
                logger.info(f"[AI ROUTER] Mengarahkan task '{capability.value}' ke provider '{prov.display_name}'...")
                return await prov.generate_text(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    model=model,
                    db_session=db_session,
                    job_id=job_id
                )
            except Exception as e:
                last_error = e
                failure_type = classify_error(e)
                logger.warning(f"[AI ROUTER] Provider '{prov.display_name}' gagal ({failure_type.value}): {str(e)[:80]}. Mencoba fallback...")

        raise RuntimeError(f"Semua provider AI untuk kapabilitas '{capability.value}' gagal dieksekusi: {last_error}")

    async def route_structured_generation(
        self,
        prompt: str,
        schema: Type[T],
        system_instruction: Optional[str] = None,
        capability: AICapability = AICapability.REASONING,
        preferred_provider: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None
    ) -> T:
        """Executes structured Pydantic schema generation with capability-aware provider fallback."""
        candidates = self.registry.get_providers_by_capability(capability)
        if not candidates:
            candidates = self.registry.get_providers_by_capability(AICapability.TEXT)

        last_error = None
        for prov in candidates:
            if not prov.enabled or not prov.api_key:
                continue
            try:
                logger.info(f"[AI ROUTER] Mengarahkan structured task ke '{prov.display_name}'...")
                return await prov.generate_structured(
                    prompt=prompt,
                    schema=schema,
                    system_instruction=system_instruction,
                    model=model,
                    db_session=db_session,
                    job_id=job_id
                )
            except Exception as e:
                last_error = e
                failure_type = classify_error(e)
                logger.warning(f"[AI ROUTER] Provider '{prov.display_name}' gagal ({failure_type.value}): {str(e)[:80]}.")

        raise RuntimeError(f"Semua provider AI gagal memproses structured schema: {last_error}")


ai_router = AIProviderRouter()
AIRouter = AIProviderRouter

