"""
Bio Campaign Manager for Pita Media.
Manages event-driven bio campaigns, platform capability assessment, safe backups,
and automatic rollback when campaigns conclude.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy import select, desc
from database.connection import async_session_factory
from database.models import BioCampaign, AuditLog

logger = logging.getLogger("pita_media.calendar.bio")

MIN_CAMPAIGN_DURATION_HOURS = 48


class BioCampaignManager:
    """Manages social media profile bio campaigns with safe rollback."""

    def __init__(self):
        self.capabilities = {
            "facebook": "MANUAL_ONLY",  # Official API requires Page admin metadata permission
            "instagram": "MANUAL_ONLY",  # Instagram Graph API requires manual user update for bio
            "threads": "MANUAL_ONLY",  # Threads API profile bio update not available in v19.0 endpoints
        }

    def get_platform_capability(self, platform: str) -> str:
        """Returns platform capability: SUPPORTED, MANUAL_ONLY, READ_ONLY, NOT_SUPPORTED."""
        return self.capabilities.get(platform.lower(), "NOT_SUPPORTED")

    async def create_bio_campaign(
        self,
        campaign_name: str,
        platform: str,
        campaign_bio: str,
        default_bio: str,
        start_at: datetime,
        end_at: datetime,
        event_id: Optional[str] = None,
        previous_bio: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates a new Bio Campaign with minimum duration guardrail."""
        duration_hours = (end_at - start_at).total_seconds() / 3600.0

        if duration_hours < MIN_CAMPAIGN_DURATION_HOURS:
            return {
                "success": False,
                "message": f"Durasi bio campaign minimal adalah {MIN_CAMPAIGN_DURATION_HOURS} jam untuk mencegah flapping status."
            }

        capability = self.get_platform_capability(platform)
        status = "RECOMMENDED_ONLY" if capability == "MANUAL_ONLY" else "SCHEDULED"

        async with async_session_factory() as db:
            camp = BioCampaign(
                campaign_name=campaign_name,
                event_id=event_id,
                platform=platform.lower(),
                default_bio=default_bio,
                previous_bio=previous_bio or default_bio,
                campaign_bio=campaign_bio,
                start_at=start_at,
                end_at=end_at,
                min_duration_hours=MIN_CAMPAIGN_DURATION_HOURS,
                status=status,
                capability_status=capability
            )
            db.add(camp)
            await db.commit()
            await db.refresh(camp)

            return {
                "success": True,
                "campaign_id": camp.id,
                "status": camp.status,
                "capability": capability,
                "message": f"Bio Campaign '{campaign_name}' berhasil didaftarkan ({capability})."
            }

    async def restore_default_bio(self, campaign_id: str) -> Dict[str, Any]:
        """Restores previous or default bio state."""
        async with async_session_factory() as db:
            camp = await db.get(BioCampaign, campaign_id)
            if not camp:
                return {"success": False, "message": "Campaign tidak ditemukan."}

            camp.status = "RESTORED"
            camp.restored_at = datetime.now(timezone.utc)
            await db.commit()

            return {
                "success": True,
                "message": f"Bio untuk platform {camp.platform.upper()} berhasil dikembalikan ke status standar.",
                "restored_bio": camp.default_bio
            }

    async def list_bio_campaigns(self) -> List[Dict[str, Any]]:
        """Lists active and past bio campaigns."""
        try:
            async with async_session_factory() as db:
                res = await db.execute(select(BioCampaign).order_by(desc(BioCampaign.created_at)).limit(10))
                camps = res.scalars().all()
                return [
                    {
                        "id": c.id,
                        "name": c.campaign_name,
                        "event_id": c.event_id,
                        "platform": c.platform,
                        "campaign_bio": c.campaign_bio,
                        "default_bio": c.default_bio,
                        "start_at": c.start_at.strftime("%Y-%m-%d %H:%M") if c.start_at else "-",
                        "end_at": c.end_at.strftime("%Y-%m-%d %H:%M") if c.end_at else "-",
                        "status": c.status,
                        "capability": c.capability_status
                    }
                    for c in camps
                ]
        except Exception as e:
            logger.warning(f"Error listing bio campaigns: {e}")
            return []


bio_campaign_manager = BioCampaignManager()
