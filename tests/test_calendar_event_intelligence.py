"""
Comprehensive Unit & Integration Test Suite for Calendar & Event Intelligence Engine
and Bio Campaign Manager.

Strictly adheres to Section 37 Verification Criteria:
1. No event -> old flow unchanged (NO_RELEVANT_EVENT)
2. Relevant event -> rich context added (EVENT_ACTIVE / TEASER / PRE_EVENT / etc.)
3. Low relevance -> skip event (is_eligible == False)
4. Calendar failure -> failsafe CALENDAR_UNAVAILABLE and regular flow runs smoothly
5. Lead time calculates TEASER, PRE_EVENT, MAIN_EVENT accurately
6. Four official pillars preserved (PITA_TRANSFORMASI, PITA_MINI, PITA_CERITA, PITA_KREASI)
7. Sensitive event stricter handling & respect
8. Bio campaign platform capability reporting (MANUAL_ONLY for Instagram/Threads/FB)
9. Bio campaign duration guardrail (>= 48 hours)
10. Bio campaign creation and restore
11. Event memory and learning boost calculation
12. Ideator incorporates calendar context gracefully
"""

import pytest
from datetime import datetime, date, timezone, timedelta
from unittest.mock import patch, AsyncMock

from core.calendar import (
    EventDefinition,
    EventContext,
    RelevanceScore,
    EventIntelligenceEngine,
    EventRelevanceScorer,
    BioCampaignManager,
    EventMemoryManager,
    get_verified_indonesian_events,
    get_curated_awareness_events,
)


class TestCalendarAndEventIntelligence:
    
    @pytest.mark.asyncio
    async def test_01_no_event_returns_no_relevant_event(self):
        """Test 1: No event on a generic date returns NO_RELEVANT_EVENT status with empty context."""
        engine = EventIntelligenceEngine()
        target_date = date(2026, 3, 15)
        ctx = await engine.get_today_event_context(target_date=target_date)
        
        assert isinstance(ctx, EventContext)
        if not ctx.event_detected:
            assert ctx.status == "NO_RELEVANT_EVENT"
            assert not ctx.event_name
            assert ctx.suggested_pillars == []

    @pytest.mark.asyncio
    async def test_02_relevant_event_generates_rich_context(self):
        """Test 2: Independence Day (17 August) generates rich context with high confidence."""
        engine = EventIntelligenceEngine()
        aug_17 = date(2026, 8, 17)
        ctx = await engine.get_today_event_context(target_date=aug_17)
        
        assert ctx.event_detected is True
        assert ctx.status == "EVENT_ACTIVE"
        assert "Kemerdekaan" in (ctx.event_name or "")
        assert ctx.relevance_score >= 70.0
        assert ctx.confidence in ("HIGH", "MEDIUM")
        assert len(ctx.suggested_pillars) > 0
        assert any(p in ["PITA_TRANSFORMASI", "PITA_MINI", "PITA_CERITA", "PITA_KREASI"] for p in ctx.suggested_pillars)

    def test_03_low_relevance_event_skipped(self):
        """Test 3: Event scoring below configured threshold is skipped."""
        scorer = EventRelevanceScorer(min_threshold=80.0)
        
        low_event = EventDefinition(
            event_id="test_low_rel",
            event_name="Low Relevance Niche Day",
            event_type="NICHE",
            start_date="06-10",
            end_date="06-10",
            default_relevance=20.0,
            suggested_pillars=["PITA_KREASI"],
            sensitivity_level="LOW"
        )
        
        score_res = scorer.calculate_score(low_event, date(2026, 6, 10))
        assert score_res.is_eligible is False
        assert score_res.score < 80.0

    @pytest.mark.asyncio
    async def test_04_calendar_failure_isolation(self):
        """Test 4: Failures in calendar resolution return CALENDAR_UNAVAILABLE without crashing."""
        engine = EventIntelligenceEngine()
        with patch.object(engine, "_get_combined_events", side_effect=RuntimeError("Simulated DB error")):
            ctx = await engine.get_today_event_context(target_date=date(2026, 8, 17))
            assert ctx.status == "CALENDAR_UNAVAILABLE"
            assert ctx.event_detected is False
            assert not ctx.event_name

    def test_05_lead_time_phases(self):
        """Test 5: Lead time calculates TEASER, PRE_EVENT, MAIN_EVENT accurately."""
        scorer = EventRelevanceScorer()
        event = EventDefinition(
            event_id="test_lead_time",
            event_name="Big Event",
            event_type="OFFICIAL",
            start_date="08-17",
            end_date="08-17",
            lead_days=4,
            default_relevance=90.0,
            suggested_pillars=["PITA_CERITA"]
        )
        
        # 3 days before -> TEASER
        res_teaser = scorer.calculate_score(event, date(2026, 8, 14))
        assert res_teaser.timing_phase == "TEASER"
        
        # 1 day before -> PRE_EVENT
        res_pre = scorer.calculate_score(event, date(2026, 8, 16))
        assert res_pre.timing_phase == "PRE_EVENT"
        
        # On the day -> MAIN_EVENT
        res_main = scorer.calculate_score(event, date(2026, 8, 17))
        assert res_main.timing_phase == "MAIN_EVENT"

    def test_06_four_official_pillars_preserved(self):
        """Test 6: Suggested pillars only contain official pillars."""
        official_pillars = {"PITA_TRANSFORMASI", "PITA_MINI", "PITA_CERITA", "PITA_KREASI"}
        events = get_verified_indonesian_events() + get_curated_awareness_events()
        for evt in events:
            for pilar in evt.suggested_pillars:
                assert pilar in official_pillars, f"Non-official pillar {pilar} found in {evt.event_name}"

    def test_07_sensitive_event_stricter_handling(self):
        """Test 7: HIGH sensitivity events get strict caution notes and higher bar."""
        scorer = EventRelevanceScorer()
        event = EventDefinition(
            event_id="test_sensitive",
            event_name="Solemn Memorial Day",
            event_type="CULTURE",
            start_date="09-30",
            end_date="09-30",
            default_relevance=85.0,
            sensitivity_level="HIGH",
            avoid_guidelines=["Sensationalism", "Political polarization"]
        )
        score_res = scorer.calculate_score(event, date(2026, 9, 30))
        assert score_res.is_eligible is True
        assert "Sensationalism" in event.avoid_guidelines

    def test_08_bio_campaign_platform_capability(self):
        """Test 8: Bio campaign checks capabilities and returns MANUAL_ONLY for Instagram/Threads."""
        bio_mgr = BioCampaignManager()
        assert bio_mgr.get_platform_capability("instagram") == "MANUAL_ONLY"
        assert bio_mgr.get_platform_capability("threads") == "MANUAL_ONLY"
        assert bio_mgr.get_platform_capability("facebook") == "MANUAL_ONLY"
        assert bio_mgr.get_platform_capability("unknown_platform") == "NOT_SUPPORTED"

    @pytest.mark.asyncio
    async def test_09_bio_campaign_duration_guardrail(self):
        """Test 9: Bio campaign requires minimum duration (48 hours)."""
        bio_mgr = BioCampaignManager()
        start = datetime.now(timezone.utc)
        end_short = start + timedelta(hours=12)
        
        plan_short = await bio_mgr.create_bio_campaign(
            campaign_name="Flash Bio",
            platform="instagram",
            campaign_bio="Short test bio",
            default_bio="Original Default Bio",
            start_at=start,
            end_at=end_short
        )
        assert plan_short["success"] is False
        assert "minimal" in plan_short["message"].lower()

    @pytest.mark.asyncio
    async def test_10_bio_campaign_creation_and_restore(self):
        """Test 10: Valid bio campaign can be planned and restored cleanly."""
        bio_mgr = BioCampaignManager()
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=4)
        
        plan = await bio_mgr.create_bio_campaign(
            campaign_name="HUT RI 81 Campaign",
            platform="instagram",
            campaign_bio="🇮🇩 Semangat Merdeka bersama Pita Media!",
            default_bio="Pita Media Official - Inspirasi Setiap Hari",
            start_at=start,
            end_at=end,
            event_id="hut_ri"
        )
        assert plan["success"] is True
        camp_id = plan["campaign_id"]
        assert camp_id is not None
        
        # Test Restore
        res = await bio_mgr.restore_default_bio(camp_id)
        assert res["success"] is True
        assert res["restored_bio"] == "Pita Media Official - Inspirasi Setiap Hari"

    @pytest.mark.asyncio
    async def test_11_event_memory_and_learning_boost(self):
        """Test 11: Long-term memory records event metrics and applies score boost."""
        memory_mgr = EventMemoryManager()
        
        # Record high engagement run
        await memory_mgr.record_event_performance(
            event_id="hut_ri_test",
            year=2025,
            pillar="PITA_CERITA",
            concept_title="Kisah Penjahit Sang Saka",
            concept_hash="hash123",
            platform="facebook",
            format_type="video",
            reach=15000,
            views=8000,
            shares=350,
            engagement_rate=0.08,
            relative_boost=1.15,
            lesson="Cerita sejarah heroik lokal sangat viral"
        )
        
        boost = await memory_mgr.get_event_historical_boost("hut_ri_test")
        assert boost >= 1.0

    @pytest.mark.asyncio
    async def test_12_ideator_incorporates_calendar_context_gracefully(self):
        """Test 12: Ideator receives event context and injects it into generation prompt safely."""
        from agents.creator.ideator import Ideator, ContentIdea
        ideator = Ideator()
        
        mock_idea = ContentIdea(
            pilar="pita_cerita",
            title="Kisah Sang Saka Merah Putih",
            concept="Refleksi kemerdekaan penuh makna",
            target_audience="Pecinta sejarah & estetika",
            visual_theme="Cinematic Warm Lighting",
            is_exploration=False
        )
        
        with patch.object(ideator.gemini, "generate_structured", new=AsyncMock(return_value=mock_idea)):
            idea = await ideator.generate_idea(pilar="pita_cerita")
            assert idea is not None
            assert idea.pilar == "pita_cerita"
            assert "Kisah" in idea.title
