"""
Pita Media Enterprise AI Engine - Base Provider Interface
Abstract Base Class for all AI providers (Gemini, xAI/Grok, OpenAI, Custom Providers).
Enforces unified capability typing and standardized execution contracts.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Type, TypeVar
from enum import Enum
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T", bound=BaseModel)

class AICapability(str, Enum):
    TEXT = "TEXT"
    REASONING = "REASONING"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    VIDEO_GENERATION = "VIDEO_GENERATION"
    AUDIO = "AUDIO"
    EMBEDDING = "EMBEDDING"
    MODERATION = "MODERATION"
    OTHER = "OTHER"

class BaseAIProvider(ABC):
    def __init__(
        self,
        provider_id: str,
        display_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: str = "",
        secondary_model: Optional[str] = None,
        capabilities: Optional[List[AICapability]] = None,
        cost_per_1m_tokens_usd: float = 0.10,
        rate_limit_rpm: int = 15,
        enabled: bool = True,
        priority: int = 1
    ):
        self.provider_id = provider_id
        self.display_name = display_name
        self.api_key = api_key or ""
        self.base_url = base_url or ""
        self.default_model = default_model
        self.secondary_model = secondary_model
        self.capabilities = capabilities or [AICapability.TEXT]
        self.cost_per_1m_tokens_usd = cost_per_1m_tokens_usd
        self.rate_limit_rpm = rate_limit_rpm
        self.enabled = enabled
        self.priority = priority

    def supports(self, capability: AICapability) -> bool:
        """Checks if provider supports a given capability."""
        return capability in self.capabilities and self.enabled

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None
    ) -> str:
        """Generate text narrative or response."""
        pass

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        system_instruction: Optional[str] = None,
        model: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None
    ) -> T:
        """Generate validated Pydantic structured output."""
        pass

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """Runs authentication and readiness check."""
        pass

    def to_dict(self) -> Dict[str, Any]:
        """Serializes provider configuration safely (zero plain secrets)."""
        from core.security.secret_store import secret_store
        tier_label = "PRIMARY (Tier 1)" if self.priority == 1 else ("SECONDARY (Tier 2)" if self.priority == 2 else "CUSTOM (Tier 3)")
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "name": self.display_name,
            "tier": tier_label,
            "masked_key": secret_store.mask_secret(self.api_key),
            "base_url": self.base_url,
            "default_model": self.default_model,
            "secondary_model": self.secondary_model,
            "capabilities": [c.value for c in self.capabilities],
            "cost_per_1m_tokens": self.cost_per_1m_tokens_usd,
            "rate_limit_rpm": self.rate_limit_rpm,
            "enabled": self.enabled,
            "priority": self.priority
        }
