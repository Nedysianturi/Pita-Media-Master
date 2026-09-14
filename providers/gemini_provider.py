"""
Pita Media Enterprise AI Engine - Gemini Provider Adapter
Implements BaseAIProvider for Google Gemini ecosystem (Text, Reasoning, Imagen, Veo).
"""

import json
import logging
from typing import Dict, Any, List, Optional, Type, TypeVar
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from providers.base_provider import BaseAIProvider, AICapability
from providers.gemini_client import gemini_client
from config.settings import settings

logger = logging.getLogger("pita.providers.gemini")

T = TypeVar("T", bound=BaseModel)

class GeminiProvider(BaseAIProvider):
    def __init__(self, api_key: Optional[str] = None):
        super().__init__(
            provider_id="gemini",
            display_name="Google / Gemini",
            api_key=api_key or settings.GEMINI_API_KEY,
            default_model=settings.GEMINI_TEXT_MODEL or "gemini-3.6-flash",
            secondary_model=settings.GEMINI_PRO_MODEL or "gemini-3.6-flash",
            capabilities=[
                AICapability.TEXT,
                AICapability.REASONING,
                AICapability.IMAGE_GENERATION,
                AICapability.VIDEO_GENERATION,
                AICapability.MODERATION
            ],
            cost_per_1m_tokens_usd=0.10,
            rate_limit_rpm=15,
            enabled=True,
            priority=1
        )
        self.client = gemini_client

    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None
    ) -> str:
        target_model = model or self.default_model
        return await self.client.generate_text(
            prompt=prompt,
            system_instruction=system_instruction,
            model=target_model,
            db_session=db_session,
            job_id=job_id
        )

    async def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None
    ) -> T:
        target_model = model or self.secondary_model or self.default_model
        return await self.client.generate_structured(
            prompt=prompt,
            schema=schema,
            system_instruction=system_instruction,
            model=target_model,
            db_session=db_session,
            job_id=job_id
        )

    def test_connection(self) -> Dict[str, Any]:
        from core.security.credential_manager import credential_manager
        return credential_manager.test_connection("gemini", custom_token=self.api_key)

gemini_provider = GeminiProvider()
