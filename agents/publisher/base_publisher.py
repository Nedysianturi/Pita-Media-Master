"""
Pita Media Enterprise Publisher - Base Platform Publisher
Abstract Base Class defining standard interface for isolated social media publishers
(Facebook, Instagram, Threads, and Mock Simulator).
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

class BasePlatformPublisher(ABC):
    def __init__(self, platform_id: str, display_name: str):
        self.platform_id = platform_id
        self.display_name = display_name

    @abstractmethod
    async def publish(
        self,
        content_id: str,
        job_id: str,
        content_payload: Dict[str, Any],
        is_dry_run: bool = True,
        db_session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """
        Executes publication to the target platform.
        Returns standard result dict:
        {
            "platform": str,
            "status": "PUBLISHED" | "VERIFIED" | "FAILED" | "WAITING",
            "external_post_id": str,
            "permalink": str,
            "response_metadata": dict,
            "error_message": str (if failed)
        }
        """
        pass

    @abstractmethod
    async def verify_post(
        self,
        external_post_id: str,
        permalink: Optional[str] = None
    ) -> Dict[str, Any]:
        """Verifies existence and accessibility of post via API."""
        pass
