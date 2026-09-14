"""
A/B Testing & Optimization Engine for Pita Media.
Manages creative split-testing (Hooks, Captions, Visual Styles, Audio Tracks)
and tracks winner metrics across social channels.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from core.database import get_db
from database.models import ABExperiment

logger = logging.getLogger("pita_media.intelligence.ab_testing")


class ABTestingEngine:
    """
    Coordinates A/B experiment lifecycles and winner evaluations.
    """

    def create_experiment(
        self,
        experiment_type: str,  # HOOK, CAPTION, THUMBNAIL, AUDIO
        content_id_a: str,
        content_id_b: str,
        hypothesis: str,
        variant_a_meta: Optional[Dict[str, Any]] = None,
        variant_b_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        import uuid
        exp_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with get_db() as db:
            exp = ABExperiment(
                id=exp_id,
                experiment_type=experiment_type,
                content_id_a=content_id_a,
                content_id_b=content_id_b,
                hypothesis=hypothesis,
                metrics_a={"impressions": 0, "engagement": 0, "clicks": 0, "shares": 0, "meta": variant_a_meta or {}},
                metrics_b={"impressions": 0, "engagement": 0, "clicks": 0, "shares": 0, "meta": variant_b_meta or {}},
                status="RUNNING",
                created_at=now
            )
            db.add(exp)
            db.commit()
            return {
                "id": exp_id,
                "experiment_type": experiment_type,
                "hypothesis": hypothesis,
                "status": "RUNNING",
                "created_at": now.isoformat()
            }

    def update_metrics(
        self,
        experiment_id: str,
        metrics_a: Optional[Dict[str, Any]] = None,
        metrics_b: Optional[Dict[str, Any]] = None
    ):
        """Update live telemetry metrics for variants A and B."""
        with get_db() as db:
            exp = db.query(ABExperiment).filter(ABExperiment.id == experiment_id).first()
            if not exp:
                return
            if metrics_a:
                exp.metrics_a = {**(exp.metrics_a or {}), **metrics_a}
            if metrics_b:
                exp.metrics_b = {**(exp.metrics_b or {}), **metrics_b}
            db.commit()

    def evaluate_winner(self, experiment_id: str) -> Dict[str, Any]:
        """
        Evaluate performance metrics and declare winner variant (A vs B).
        """
        with get_db() as db:
            exp = db.query(ABExperiment).filter(ABExperiment.id == experiment_id).first()
            if not exp:
                return {"error": "Experiment not found"}

            mA = exp.metrics_a or {}
            mB = exp.metrics_b or {}

            score_a = mA.get("engagement", 0) * 2 + mA.get("shares", 0) * 3 + mA.get("impressions", 0) * 0.1
            score_b = mB.get("engagement", 0) * 2 + mB.get("shares", 0) * 3 + mB.get("impressions", 0) * 0.1

            winner = "TIE"
            if score_a > score_b * 1.05:
                winner = "VARIANT_A"
            elif score_b > score_a * 1.05:
                winner = "VARIANT_B"

            exp.winner = winner
            exp.status = "COMPLETED"
            db.commit()

            return {
                "experiment_id": exp.id,
                "winner": winner,
                "score_a": round(score_a, 2),
                "score_b": round(score_b, 2),
                "status": "COMPLETED"
            }

    def list_experiments(self) -> List[Dict[str, Any]]:
        """List all experiments."""
        with get_db() as db:
            items = db.query(ABExperiment).order_by(ABExperiment.created_at.desc()).all()
            return [
                {
                    "id": x.id,
                    "experiment_type": x.experiment_type,
                    "content_id_a": x.content_id_a,
                    "content_id_b": x.content_id_b,
                    "hypothesis": x.hypothesis,
                    "metrics_a": x.metrics_a,
                    "metrics_b": x.metrics_b,
                    "winner": x.winner,
                    "status": x.status,
                    "created_at": x.created_at.isoformat() if x.created_at else ""
                }
                for x in items
            ]


ab_testing_engine = ABTestingEngine()
