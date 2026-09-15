"""
Relevance Scoring & Eligibility Engine for Calendar Events.
Evaluates brand relevance, pillar alignment, timing lead time, audience fit, and historical boost.
"""

from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
from core.calendar.event_models import EventDefinition, RelevanceScore


DEFAULT_MIN_RELEVANCE_SCORE = 70.0


class EventRelevanceScorer:
    """Evaluates event relevance and timing phase against current date."""

    def __init__(self, min_threshold: float = DEFAULT_MIN_RELEVANCE_SCORE):
        self.min_threshold = min_threshold

    def calculate_score(
        self,
        event: EventDefinition,
        current_date: date,
        target_pillar: Optional[str] = None,
        historical_boost: float = 1.0,
    ) -> RelevanceScore:
        """
        Calculates relevance score and identifies event timing phase:
        - MAIN_EVENT: today matches start_date
        - PRE_EVENT: 1 day before start_date
        - TEASER: 2-3 days before start_date (within lead_days)
        - FOLLOW_UP: 1 day after end_date or start_date
        """
        reasons = []
        
        # 1. Parse event month & day
        try:
            parts = event.start_date.split("-")
            e_month = int(parts[-2])
            e_day = int(parts[-1])
            event_this_year = date(current_date.year, e_month, e_day)
        except Exception:
            return RelevanceScore(
                score=0.0,
                confidence="LOW",
                timing_phase="NONE",
                is_eligible=False,
                reasons=["Format tanggal event tidak valid"],
                suggested_pillars=event.suggested_pillars
            )

        days_diff = (event_this_year - current_date).days
        timing_phase = "NONE"
        timing_multiplier = 0.0

        if days_diff == 0:
            timing_phase = "MAIN_EVENT"
            timing_multiplier = 1.0
            reasons.append("Tepat pada hari puncak event")
        elif days_diff == 1:
            timing_phase = "PRE_EVENT"
            timing_multiplier = 0.95
            reasons.append("H-1 menjelang event (Pre-event)")
        elif 2 <= days_diff <= max(event.lead_days, 2):
            timing_phase = "TEASER"
            timing_multiplier = 0.85
            reasons.append(f"H-{days_diff} masa persiapan / teaser")
        elif days_diff == -1:
            timing_phase = "FOLLOW_UP"
            timing_multiplier = 0.75
            reasons.append("H+1 refleksi & pesan penutup event")
        else:
            return RelevanceScore(
                score=0.0,
                confidence="LOW",
                timing_phase="OUT_OF_WINDOW",
                is_eligible=False,
                reasons=[f"Di luar jendela lead time ({days_diff} hari dari event)"],
                suggested_pillars=event.suggested_pillars
            )

        # 2. Base score
        base = event.default_relevance

        # 3. Pillar Fit multiplier
        pillar_multiplier = 1.0
        if target_pillar:
            norm_pillar = str(target_pillar).upper()
            if norm_pillar in [p.upper() for p in event.suggested_pillars]:
                pillar_multiplier = 1.05
                reasons.append(f"Pilar {norm_pillar} sangat selaras dengan tema event")
            else:
                pillar_multiplier = 0.90
                reasons.append(f"Pilar {norm_pillar} dapat disesuaikan dengan pendekatan kreatif")

        # 4. Sensitivity factor
        sensitivity_mod = 1.0
        if event.sensitivity_level == "HIGH":
            sensitivity_mod = 0.95
            reasons.append("Event sensitif: memerlukan nada bicara penuh hormat & netral")

        # 5. Historical performance boost (bounded 0.8 - 1.25)
        hist_factor = max(0.8, min(historical_boost, 1.25))
        if hist_factor > 1.0:
            reasons.append(f"Boost performa historis ({hist_factor:.2f}x)")

        final_score = base * timing_multiplier * pillar_multiplier * sensitivity_mod * hist_factor
        final_score = max(0.0, min(final_score, 100.0))

        confidence = "HIGH" if final_score >= 85.0 else ("MEDIUM" if final_score >= 70.0 else "LOW")
        is_eligible = (final_score >= self.min_threshold) and event.enabled

        return RelevanceScore(
            score=round(final_score, 1),
            confidence=confidence,
            timing_phase=timing_phase,
            is_eligible=is_eligible,
            reasons=reasons,
            suggested_pillars=event.suggested_pillars
        )


relevance_scorer = EventRelevanceScorer()
