"""
Calendar & Event Intelligence Engine for Pita Media.
Provides date-aware context, national/cultural holiday detection, lead-time planning,
and graceful failure isolation (returning CALENDAR_UNAVAILABLE or NO_RELEVANT_EVENT).
"""

import os
import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
import zoneinfo

from core.calendar.event_models import EventDefinition, EventContext, RelevanceScore
from core.calendar.calendar_sources import calendar_sources, CalendarSourceRegistry
from core.calendar.relevance_scorer import relevance_scorer, EventRelevanceScorer
from core.calendar.event_memory import event_memory
from database.connection import async_session_factory
from database.models import EventItem, EventRunCandidate, AuditLog

logger = logging.getLogger("pita_media.calendar.engine")

DEFAULT_TIMEZONE = "Asia/Jakarta"


class EventIntelligenceEngine:
    """
    Main engine for Calendar & Event Intelligence in Pita Media.
    Enriches strategy and content creation with respectful, date-aware cultural context.
    """

    def __init__(self, timezone_name: str = DEFAULT_TIMEZONE):
        self.timezone_name = timezone_name
        self.sources = calendar_sources
        self.scorer = relevance_scorer
        self.memory = event_memory
        self._cache: Dict[str, EventContext] = {}

    def get_current_date(self) -> date:
        """Retrieves current application date in Asia/Jakarta timezone."""
        try:
            tz = zoneinfo.ZoneInfo(self.timezone_name)
            return datetime.now(tz).date()
        except Exception:
            return datetime.now().date()

    async def get_today_event_context(
        self,
        target_date: Optional[date] = None,
        target_pillar: Optional[str] = None
    ) -> EventContext:
        """
        Evaluates events for the given date (defaults to today in Asia/Jakarta).
        Returns rich EventContext if an event is active and meets relevance threshold,
        or NO_RELEVANT_EVENT if none found, or CALENDAR_UNAVAILABLE on failure.
        """
        try:
            curr_date = target_date or self.get_current_date()
            cache_key = f"{curr_date.isoformat()}_{target_pillar or 'ALL'}"

            if cache_key in self._cache:
                return self._cache[cache_key]

            # 1. Load registered events (built-in + DB custom events)
            all_events = await self._get_combined_events()

            if not all_events:
                ctx = EventContext(
                    status="NO_RELEVANT_EVENT",
                    event_detected=False,
                    target_date=curr_date.isoformat()
                )
                self._cache[cache_key] = ctx
                return ctx

            # 2. Score candidates against current date
            candidates = []
            for ev in all_events:
                if not ev.enabled:
                    continue

                hist_boost = await self.memory.get_event_historical_boost(ev.event_id, target_pillar)
                rel_score = self.scorer.calculate_score(
                    event=ev,
                    current_date=curr_date,
                    target_pillar=target_pillar,
                    historical_boost=hist_boost
                )

                if rel_score.is_eligible:
                    candidates.append((ev, rel_score))

            if not candidates:
                ctx = EventContext(
                    status="NO_RELEVANT_EVENT",
                    event_detected=False,
                    target_date=curr_date.isoformat()
                )
                self._cache[cache_key] = ctx
                return ctx

            # 3. Pick top scoring candidate
            candidates.sort(key=lambda item: item[1].score, reverse=True)
            top_event, top_score = candidates[0]

            ctx = EventContext(
                status="EVENT_ACTIVE",
                event_detected=True,
                event_id=top_event.event_id,
                event_name=top_event.event_name,
                event_type=top_event.event_type,
                target_date=curr_date.isoformat(),
                phase=top_score.timing_phase,
                relevance_score=top_score.score,
                confidence=top_score.confidence,
                sensitivity_level=top_event.sensitivity_level,
                suggested_pillars=top_event.suggested_pillars,
                tone=top_event.tone,
                visual_context=top_event.visual_context,
                avoid_guidelines=top_event.avoid_guidelines,
                notes=top_event.notes
            )

            self._cache[cache_key] = ctx
            logger.info(f"Event detected & selected: '{ctx.event_name}' (Score: {ctx.relevance_score}, Phase: {ctx.phase})")
            return ctx

        except Exception as e:
            logger.error(f"Error in EventIntelligenceEngine (Graceful Fallback): {e}", exc_info=True)
            # Safe Fallback: Never crash the main system!
            return EventContext(
                status="CALENDAR_UNAVAILABLE",
                event_detected=False,
                target_date=(target_date or date.today()).isoformat(),
                notes=str(e)
            )

    async def list_upcoming_events(self, days_ahead: int = 14) -> List[Dict[str, Any]]:
        """Lists events occurring within the next N days for dashboard visibility."""
        try:
            curr_date = self.get_current_date()
            all_events = await self._get_combined_events()
            upcoming = []

            for ev in all_events:
                if not ev.enabled:
                    continue
                try:
                    parts = ev.start_date.split("-")
                    e_month = int(parts[-2])
                    e_day = int(parts[-1])
                    ev_date = date(curr_date.year, e_month, e_day)
                    if ev_date < curr_date:
                        ev_date = date(curr_date.year + 1, e_month, e_day)

                    days_left = (ev_date - curr_date).days
                    if 0 <= days_left <= days_ahead:
                        upcoming.append({
                            "event_id": ev.event_id,
                            "event_name": ev.event_name,
                            "event_type": ev.event_type,
                            "date": ev_date.strftime("%d %B %Y"),
                            "days_left": days_left,
                            "sensitivity": ev.sensitivity_level,
                            "default_relevance": ev.default_relevance,
                            "suggested_pillars": ev.suggested_pillars,
                            "tone": ev.tone
                        })
                except Exception:
                    continue

            upcoming.sort(key=lambda x: x["days_left"])
            return upcoming
        except Exception as e:
            logger.warning(f"Error listing upcoming events: {e}")
            return []

    async def _get_combined_events(self) -> List[EventDefinition]:
        """Combines built-in verified events with custom events from SQLite database."""
        events_dict = {e.event_id: e for e in self.sources.list_all_events()}

        try:
            async with async_session_factory() as db:
                from sqlalchemy import select
                res = await db.execute(select(EventItem).where(EventItem.enabled == True))
                db_events = res.scalars().all()
                for dbe in db_events:
                    events_dict[dbe.event_id] = EventDefinition(
                        event_id=dbe.event_id,
                        event_name=dbe.event_name,
                        event_type=dbe.event_type,
                        start_date=dbe.start_date,
                        end_date=dbe.end_date,
                        timezone=dbe.timezone,
                        country=dbe.country,
                        region=dbe.region,
                        audience_scope=dbe.audience_scope,
                        sensitivity_level=dbe.sensitivity_level,
                        default_relevance=dbe.default_relevance,
                        source=dbe.source,
                        verification_status=dbe.verification_status,
                        suggested_pillars=dbe.suggested_pillars or ["PITA_CERITA", "PITA_MINI"],
                        tone=dbe.tone or "Inspiratif",
                        visual_context=dbe.visual_context or "",
                        avoid_guidelines=dbe.avoid_guidelines or [],
                        lead_days=dbe.lead_days or 2,
                        notes=dbe.notes or "",
                        enabled=dbe.enabled
                    )
        except Exception as e:
            logger.debug(f"DB custom events lookup skipped: {e}")

        return list(events_dict.values())


event_intelligence_engine = EventIntelligenceEngine()
