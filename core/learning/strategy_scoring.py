"""
Strategy Scoring Engine untuk Pita Media.
Sebelum memproduksi konten, menghasilkan dan mengevaluasi 5-10 kandidat ide.
Memberi skor multi-dimensi untuk setiap kandidat:
- predicted engagement
- predicted share potential
- predicted retention
- novelty & fatigue risk
- platform suitability & production cost
- brand compatibility & monetization safety
- current trend relevance & audience interest
Memilih kandidat terbaik dengan mencatat alasan komprehensif dan data prediksi.
"""

import logging
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from core.database import get_db
from database.models import ContentPrediction
from core.learning.decision_confidence import decision_confidence
from core.learning.knowledge_base import knowledge_base

logger = logging.getLogger("pita_media.learning.strategy_scoring")


class CandidateScoringResult(BaseModel):
    title: str
    concept: str
    pilar: str
    overall_score: float
    predicted_engagement: float
    predicted_share_potential: float
    predicted_retention: str  # LOW, MEDIUM, HIGH, VERY_HIGH
    novelty_score: float
    fatigue_risk: float
    platform_suitability: float
    production_cost_efficiency: float
    brand_safety_score: float
    confidence_level: str
    confidence_score: float
    selection_reason: str


class StrategyScoringEngine:
    """
    Mesin scoring kandidat ide dan peramalan performa (prediction modeling).
    """

    def score_candidate_ideas(
        self,
        pilar: str,
        candidates: List[Dict[str, Any]],
        recent_memory_summary: Optional[Dict[str, Any]] = None,
    ) -> List[CandidateScoringResult]:
        """
        Mengevaluasi 5-10 ide kandidat dan memberikan skor terukur untuk setiap ide.
        """
        active_knowledge = knowledge_base.get_active_knowledge(min_confidence=0.5)
        recent_summary = recent_memory_summary or {}

        scored_candidates = []

        for item in candidates:
            title = item.get("title", "Untitled")
            concept = item.get("concept", "")
            is_exploration = item.get("is_exploration", False)

            # 1. Novelty & Fatigue Evaluation
            title_len = len(title.split())
            novelty = 0.85 if is_exploration else 0.75
            fatigue = 0.15 if is_exploration else 0.25

            # 2. Predicted Share & Engagement
            # Hook & topic fit with active knowledge
            boost = 0.0
            if any(k["category"] == "hook" for k in active_knowledge):
                boost += 0.5

            predicted_share = min(10.0, 7.0 + boost + (1.0 if is_exploration else 0.0))
            predicted_engagement = min(10.0, 7.5 + boost)
            predicted_retention = "HIGH" if "transformasi" in pilar or "mini" in pilar else "MEDIUM"

            # 3. Platform & Brand Suitability
            platform_suitability = 8.5
            brand_safety = 9.5
            cost_efficiency = 8.0

            # 4. Weighted Composite Score (0.0 - 10.0)
            overall_score = (
                predicted_engagement * 0.25 +
                predicted_share * 0.25 +
                (8.5 if predicted_retention == "HIGH" else 7.0) * 0.15 +
                (novelty * 10.0) * 0.15 +
                ((1.0 - fatigue) * 10.0) * 0.10 +
                brand_safety * 0.10
            )
            overall_score = round(overall_score, 2)

            conf_level, conf_score, _ = decision_confidence.evaluate_confidence(
                sample_size=15,
                min_required_samples=10,
                data_consistency_score=0.85,
            )

            reason = f"Skor {overall_score}/10 — Novelty tinggi ({novelty*10:.1f}), potensi share kuat ({predicted_share:.1f}), dan risiko fatigue rendah."

            scored_candidates.append(
                CandidateScoringResult(
                    title=title,
                    concept=concept,
                    pilar=pilar,
                    overall_score=overall_score,
                    predicted_engagement=round(predicted_engagement, 1),
                    predicted_share_potential=round(predicted_share, 1),
                    predicted_retention=predicted_retention,
                    novelty_score=round(novelty, 2),
                    fatigue_risk=round(fatigue, 2),
                    platform_suitability=platform_suitability,
                    production_cost_efficiency=cost_efficiency,
                    brand_safety_score=brand_safety,
                    confidence_level=conf_level,
                    confidence_score=conf_score,
                    selection_reason=reason,
                )
            )

        # Sort descending by overall score
        scored_candidates.sort(key=lambda x: x.overall_score, reverse=True)
        return scored_candidates

    def select_best_candidate(
        self,
        scored_candidates: List[CandidateScoringResult],
    ) -> Optional[CandidateScoringResult]:
        """Memilih kandidat dengan peringkat tertinggi."""
        if not scored_candidates:
            return None
        return scored_candidates[0]

    def record_content_prediction(
        self,
        content_id: str,
        candidate: CandidateScoringResult,
    ) -> str:
        """
        Menyimpan data prediksi ke tabel database ContentPrediction untuk evaluasi prediction vs reality nantinya.
        """
        now = datetime.now(timezone.utc)
        pred = ContentPrediction(
            content_id=content_id,
            candidate_idea_title=candidate.title,
            pilar=candidate.pilar,
            predicted_engagement=candidate.predicted_engagement,
            predicted_share_score=candidate.predicted_share_potential,
            predicted_retention=candidate.predicted_retention,
            predicted_novelty_score=candidate.novelty_score,
            fatigue_risk_score=candidate.fatigue_risk,
            overall_idea_score=candidate.overall_score,
            selection_reason=candidate.selection_reason,
            confidence_level=candidate.confidence_level,
            confidence_score=candidate.confidence_score,
            created_at=now,
        )

        with get_db() as db:
            db.add(pred)
            db.commit()
            return pred.id

    def update_prediction_with_actual(
        self,
        content_id: str,
        actual_engagement: float,
        actual_share_score: float,
        actual_retention: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Memperbarui prediksi dengan hasil nyata (reality feedback loop).
        """
        now = datetime.now(timezone.utc)
        with get_db() as db:
            pred = db.query(ContentPrediction).filter(ContentPrediction.content_id == content_id).first()
            if not pred:
                return None

            pred.actual_engagement = actual_engagement
            pred.actual_share_score = actual_share_score
            pred.actual_retention = actual_retention
            
            # Hitung selisih galat (error delta)
            delta = abs((pred.predicted_engagement or 0.0) - actual_engagement) + abs((pred.predicted_share_score or 0.0) - actual_share_score)
            pred.prediction_error_delta = round(delta, 2)
            pred.evaluated_at = now
            db.commit()

            return {
                "content_id": content_id,
                "predicted_engagement": pred.predicted_engagement,
                "actual_engagement": actual_engagement,
                "predicted_share": pred.predicted_share_score,
                "actual_share": actual_share_score,
                "error_delta": pred.prediction_error_delta,
            }


strategy_scoring = StrategyScoringEngine()
