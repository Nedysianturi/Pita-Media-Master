"""
Pita Media Calendar & Event Intelligence Engine.
Additive modular system for date-aware, cultural, and event-informed content strategy.
"""

from core.calendar.event_models import EventDefinition, RelevanceScore, EventContext
from core.calendar.calendar_sources import calendar_sources, VERIFIED_EVENT_CATALOG, get_verified_indonesian_events, get_curated_awareness_events
from core.calendar.relevance_scorer import relevance_scorer, EventRelevanceScorer
from core.calendar.event_memory import event_memory, EventMemoryManager
from core.calendar.event_intelligence import event_intelligence_engine, EventIntelligenceEngine
from core.calendar.bio_campaign_manager import bio_campaign_manager, BioCampaignManager

__all__ = [
    "EventDefinition",
    "RelevanceScore",
    "EventContext",
    "calendar_sources",
    "VERIFIED_EVENT_CATALOG",
    "get_verified_indonesian_events",
    "get_curated_awareness_events",
    "relevance_scorer",
    "EventRelevanceScorer",
    "event_memory",
    "EventMemoryManager",
    "event_intelligence_engine",
    "EventIntelligenceEngine",
    "bio_campaign_manager",
    "BioCampaignManager",
]
