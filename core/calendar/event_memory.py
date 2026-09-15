"""
Event Performance Memory & Longitudinal Intelligence for Pita Media.
Stores and learns from previous years' event content performance.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy import select, desc
from database.connection import async_session_factory
from database.models import EventPerformanceRecord

logger = logging.getLogger("pita_media.calendar.memory")


class EventMemoryManager:
    """Manages historical event learning records and performance multiplier boosts."""

    async def get_event_historical_boost(self, event_id: str, pilar: Optional[str] = None) -> float:
        """Retrieves historical boost factor (e.g., 1.15x) based on past years' engagement."""
        try:
            async with async_session_factory() as db:
                stmt = select(EventPerformanceRecord).where(
                    EventPerformanceRecord.event_id == event_id
                )
                if pilar:
                    stmt = stmt.where(EventPerformanceRecord.pillar == pilar.upper())
                stmt = stmt.order_by(desc(EventPerformanceRecord.year)).limit(5)
                res = await db.execute(stmt)
                records = res.scalars().all()

                if not records:
                    return 1.0

                avg_boost = sum(r.relative_boost for r in records) / len(records)
                return max(0.8, min(avg_boost, 1.25))
        except Exception as e:
            logger.warning(f"Could not fetch event memory boost for {event_id}: {e}")
            return 1.0

    async def record_event_performance(
        self,
        event_id: str,
        year: int,
        pillar: str,
        concept_title: str,
        concept_hash: str,
        platform: str = "facebook",
        format_type: str = "video",
        reach: int = 0,
        views: int = 0,
        shares: int = 0,
        saves: int = 0,
        engagement_rate: float = 0.0,
        relative_boost: float = 1.0,
        lesson: str = "",
    ) -> None:
        """Records content performance for future years' memory."""
        try:
            async with async_session_factory() as db:
                rec = EventPerformanceRecord(
                    event_id=event_id,
                    year=year,
                    pillar=pillar.upper(),
                    concept_title=concept_title,
                    concept_hash=concept_hash,
                    platform=platform,
                    format=format_type,
                    reach=reach,
                    views=views,
                    shares=shares,
                    saves=saves,
                    engagement_rate=engagement_rate,
                    relative_boost=relative_boost,
                    lesson=lesson,
                )
                db.add(rec)
                await db.commit()
                logger.info(f"Recorded event memory for '{event_id}' (Year {year}, Pillar {pillar})")
        except Exception as e:
            logger.warning(f"Failed to record event performance: {e}")

    async def list_recent_event_memories(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Lists recent historical event learnings for the dashboard."""
        try:
            async with async_session_factory() as db:
                res = await db.execute(
                    select(EventPerformanceRecord)
                    .order_by(desc(EventPerformanceRecord.created_at))
                    .limit(limit)
                )
                records = res.scalars().all()
                return [
                    {
                        "event_id": r.event_id,
                        "year": r.year,
                        "pillar": r.pillar,
                        "title": r.concept_title,
                        "platform": r.platform,
                        "shares": r.shares,
                        "engagement_rate": f"{r.engagement_rate:.2f}%",
                        "boost": f"{r.relative_boost:.2f}x",
                        "lesson": r.lesson or "Resonansi audiens tinggi."
                    }
                    for r in records
                ]
        except Exception as e:
            logger.warning(f"Error listing event memories: {e}")
            return []


event_memory = EventMemoryManager()
