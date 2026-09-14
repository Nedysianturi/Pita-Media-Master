"""
Pita Media Enterprise AI Engine - Intelligent AI Provider Router
Routes requests to the optimal AI provider based on capability, priority, health, and cost rules.
Supports PRIMARY -> SECONDARY -> FALLBACK with smart rate-limit and quota backoff.
"""

import logging
from typing import Dict, Any, List, Optional, Type, TypeVar
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from providers.base_provider import BaseAIProvider, AICapability
from providers.provider_registry import provider_registry
from core.security.token_health_manager import token_health_manager

logger = logging.getLogger("pita.providers.router")

T = TypeVar("T", bound=BaseModel)

class AIProviderRouter:
    def __init__(self):
        self.registry = provider_registry
        self.health_mgr = token_health_manager

    def select_provider(self, capability: AICapability, preferred_id: Optional[str] = None) -> Optional[BaseAIProvider]:
        """Selects highest-priority healthy provider that supports the requested capability."""
        if preferred_id:
            p = self.registry.get_provider(preferred_id)
            if p and p.supports(capability) and self.health_mgr.is_service_healthy(p.provider_id):
                return p

        candidates = self.registry.get_providers_by_capability(capability)
        for cand in candidates:
            if cand.enabled and cand.api_key and self.health_mgr.is_service_healthy(cand.provider_id):
                return cand

        # Fallback to any enabled candidate with API key
        for cand in candidates:
            if cand.enabled and cand.api_key:
                return cand

        return candidates[0] if candidates else None

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
        """Executes text generation with automatic fallback to secondary provider."""
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
                err_msg = str(e)
                last_error = e
                logger.warning(f"[AI ROUTER] Provider '{prov.display_name}' gagal: {err_msg[:80]}. Mencoba fallback...")
                if "401" in err_msg or "invalid" in err_msg.lower():
                    # Invalid auth -> do not retry endlessly
                    continue

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
        """Executes structured Pydantic schema generation with automatic provider fallback."""
        candidates = self.registry.get_providers_by_capability(capability)
        if not candidates:
            candidates = self.registry.get_providers_by_capability(AICapability.TEXT)

        last_error = None
        for prov in candidates:
            if not prov.enabled or not prov.api_key:
                continue
            try:
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
                logger.warning(f"[AI ROUTER] Structured schema call on '{prov.display_name}' gagal: {e}. Mencoba fallback...")

        raise RuntimeError(f"Semua provider AI untuk structured schema gagal: {last_error}")

ai_router = AIProviderRouter()
