"""
Learning Intelligence Engine Sentral untuk Pita Media.
Menghubungkan seluruh siklus hidup pembelajaran:
KEPUTUSAN (Strategy & Ideation)
  → KONTEN YANG DIBUAT (Creator & Reviewer)
  → CARA KONTEN DIPUBLIKASIKAN (Publisher)
  → HASIL PERFORMA NYATA (Social Telemetry)
  → LESSON LEARNED (Postmortem & Knowledge Base)
  → STRATEGI BERIKUTNYA (Strategy Versioning & Autonomy Control)
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from core.learning.data_quality_gate import data_quality_gate
from core.learning.failure_classifier import failure_classifier, FailureType
from core.learning.long_term_memory import long_term_memory
from core.learning.knowledge_base import knowledge_base
from core.learning.postmortem_engine import postmortem_engine
from core.learning.strategy_scoring import strategy_scoring, CandidateScoringResult
from core.learning.decision_confidence import decision_confidence
from core.learning.audience_intelligence import audience_intelligence
from core.learning.prompt_model_tracker import prompt_model_tracker
from core.learning.learning_maturity import learning_maturity
from core.learning.strategy_versioning import strategy_versioning
from core.learning.autonomy_controller import autonomy_controller, AutonomyLevel

logger = logging.getLogger("pita_media.learning.engine")


class LearningIntelligenceEngine:
    """
    Otak sentral kecerdasan pembelajaran konten Pita Media.
    """

    def __init__(self):
        self.quality_gate = data_quality_gate
        self.failure_classifier = failure_classifier
        self.memory = long_term_memory
        self.knowledge = knowledge_base
        self.postmortem = postmortem_engine
        self.strategy_scoring = strategy_scoring
        self.confidence = decision_confidence
        self.audience = audience_intelligence
        self.tracker = prompt_model_tracker
        self.maturity = learning_maturity
        self.strategy_versioning = strategy_versioning
        self.autonomy = autonomy_controller

    def process_content_creation_phase(
        self,
        pilar: str,
        candidate_ideas: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Tahap 1: Evaluasi & Pemilihan Ide Terbaik sebelum diproduksi.
        """
        # Ambil ringkasan memori dan knowledge base
        memory_summary = self.memory.get_recent_memory_summary(days=30)
        
        # Skor seluruh kandidat
        scored_candidates = self.strategy_scoring.score_candidate_ideas(
            pilar=pilar,
            candidates=candidate_ideas,
            recent_memory_summary=memory_summary,
        )
        
        best_candidate = self.strategy_scoring.select_best_candidate(scored_candidates)
        
        return {
            "selected_candidate": best_candidate,
            "all_candidates": scored_candidates,
            "total_evaluated": len(scored_candidates),
        }

    def process_published_telemetry(
        self,
        content_id: str,
        pilar: str,
        title: str,
        metrics: Dict[str, Any],
        cost_usd: float = 0.0,
        is_dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Tahap 2: Menerima hasil performa aktual, memvalidasi kualitas data,
        memperbarui prediksi, dan menjalankan evaluasi postmortem otomatis.
        """
        # 1. Validasi via Data Quality Gate
        is_valid, reject_reason = self.quality_gate.validate_content_metric(
            metrics=metrics,
            is_dry_run=is_dry_run,
        )
        if not is_valid:
            logger.warning(f"[LEARNING] Data diabaikan oleh Quality Gate: {reject_reason}")
            return {"status": "SKIPPED_INVALID_DATA", "reason": reject_reason}

        # 2. Update Prediction vs Reality
        views = metrics.get("views", 0) or metrics.get("views_count", 0)
        likes = metrics.get("likes", 0) or metrics.get("likes_count", 0)
        shares = metrics.get("shares", 0) or metrics.get("shares_count", 0)
        comments = metrics.get("comments", 0) or metrics.get("comments_count", 0)

        actual_engagement = (likes * 1.0 + comments * 2.0) / max(1, views / 100.0) if views > 0 else 0.0
        actual_share_score = (shares * 5.0) / max(1, views / 100.0) if views > 0 else 0.0
        actual_retention = "HIGH" if actual_engagement > 5.0 else "MEDIUM"

        pred_result = self.strategy_scoring.update_prediction_with_actual(
            content_id=content_id,
            actual_engagement=round(actual_engagement, 2),
            actual_share_score=round(actual_share_score, 2),
            actual_retention=actual_retention,
        )

        # 3. Rekam ke Long-Term Memory
        mem_id = self.memory.record_memory(
            pilar=pilar,
            title=title,
            content_id=content_id,
            performance_summary=metrics,
            lessons_learned=f"Views: {views}, Shares: {shares}, Likes: {likes}",
        )

        # 4. Jalankan Automatic Postmortem jika memenuhi kriteria
        pm_result = self.postmortem.analyze_content_postmortem(
            content_id=content_id,
            pilar=pilar,
            title=title,
            metrics=metrics,
            cost_usd=cost_usd,
        )

        return {
            "status": "PROCESSED",
            "memory_id": mem_id,
            "prediction_feedback": pred_result,
            "postmortem": pm_result,
        }

    def get_learning_dashboard_overview(self) -> Dict[str, Any]:
        """
        Mengambil rekapitulasi data lengkap untuk ditampilkan di Dashboard Learning Center.
        """
        mat = self.maturity.calculate_maturity_score()
        strat = self.strategy_versioning.get_active_strategy()
        top_knowledge = self.knowledge.get_active_knowledge(min_confidence=0.5)
        recent_pms = self.postmortem.get_recent_postmortems(limit=5)
        community_ideas = self.audience.get_community_content_ideas(limit=5)
        rec = self.autonomy.evaluate_maturity_recommendation()

        return {
            "autonomy_level": self.autonomy.current_level,
            "is_paused": self.autonomy.is_paused,
            "maturity_score": mat.get("total_score", 0.0),
            "maturity_stage": mat.get("stage", ""),
            "maturity_breakdown": mat.get("breakdown", {}),
            "active_strategy": strat,
            "maturity_recommendation": rec,
            "top_lessons_learned": top_knowledge[:6],
            "recent_postmortems": recent_pms,
            "community_ideas": community_ideas,
        }


learning_engine = LearningIntelligenceEngine()
