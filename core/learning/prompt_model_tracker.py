"""
Prompt & Model Performance Tracker untuk Pita Media.
Merekam dan membandingkan performa berbagai versi prompt dan model/provider AI:
- Prompt versioning (QC score, repair count, cost, engagement, share rate, rollback)
- Model analytics (quality, latency, cost per call, failure rate, repair rate)
Mendukung mekanisme Rollback jika performa prompt baru menurun.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from core.database import get_db
from database.models import PromptVersionMetric, ModelPerformanceMetric

logger = logging.getLogger("pita_media.learning.tracker")


class PromptModelTracker:
    """
    Pelacak performa versi prompt dan keandalan provider/model AI.
    """

    def record_prompt_execution(
        self,
        prompt_name: str,
        version: str,
        template_text: str,
        provider: str,
        model: str,
        qc_score: float,
        repair_count: int,
        cost_usd: float,
        is_published: bool = True,
    ):
        """Mencatat hasil satu siklus eksekusi versi prompt."""
        now = datetime.now(timezone.utc)
        with get_db() as db:
            record = (
                db.query(PromptVersionMetric)
                .filter(PromptVersionMetric.prompt_name == prompt_name)
                .filter(PromptVersionMetric.version == version)
                .first()
            )
            if not record:
                record = PromptVersionMetric(
                    prompt_name=prompt_name,
                    version=version,
                    template_text=template_text,
                    provider=provider,
                    model=model,
                    total_invocations=1,
                    avg_qc_score=qc_score,
                    avg_repair_count=float(repair_count),
                    avg_cost_usd=cost_usd,
                    publish_success_rate=1.0 if is_published else 0.0,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                db.add(record)
            else:
                n = record.total_invocations
                record.total_invocations += 1
                record.avg_qc_score = round((record.avg_qc_score * n + qc_score) / (n + 1), 2)
                record.avg_repair_count = round((record.avg_repair_count * n + repair_count) / (n + 1), 2)
                record.avg_cost_usd = round((record.avg_cost_usd * n + cost_usd) / (n + 1), 4)
                record.publish_success_rate = round((record.publish_success_rate * n + (1.0 if is_published else 0.0)) / (n + 1), 2)
                record.updated_at = now

            db.commit()

    def record_model_call(
        self,
        provider: str,
        model_name: str,
        task_type: str,
        is_success: bool,
        latency_seconds: float,
        cost_usd: float,
        qc_score: Optional[float] = None,
        repaired: bool = False,
    ):
        """Mencatat metrik pemanggilan model AI."""
        now = datetime.now(timezone.utc)
        with get_db() as db:
            m = (
                db.query(ModelPerformanceMetric)
                .filter(ModelPerformanceMetric.provider == provider)
                .filter(ModelPerformanceMetric.model_name == model_name)
                .filter(ModelPerformanceMetric.task_type == task_type)
                .first()
            )
            if not m:
                m = ModelPerformanceMetric(
                    provider=provider,
                    model_name=model_name,
                    task_type=task_type,
                    total_calls=1,
                    successful_calls=1 if is_success else 0,
                    failed_calls=0 if is_success else 1,
                    avg_latency_seconds=latency_seconds,
                    avg_cost_per_call_usd=cost_usd,
                    avg_qc_score=qc_score or 0.0,
                    repair_rate=1.0 if repaired else 0.0,
                    created_at=now,
                    updated_at=now,
                )
                db.add(m)
            else:
                n = m.total_calls
                m.total_calls += 1
                if is_success:
                    m.successful_calls += 1
                else:
                    m.failed_calls += 1
                m.avg_latency_seconds = round((m.avg_latency_seconds * n + latency_seconds) / (n + 1), 2)
                m.avg_cost_per_call_usd = round((m.avg_cost_per_call_usd * n + cost_usd) / (n + 1), 4)
                if qc_score:
                    m.avg_qc_score = round((m.avg_qc_score * n + qc_score) / (n + 1), 2)
                m.repair_rate = round((m.repair_rate * n + (1.0 if repaired else 0.0)) / (n + 1), 2)
                m.updated_at = now

            db.commit()

    def rollback_prompt_version(self, prompt_name: str, target_version: str) -> bool:
        """Mengaktifkan kembali versi prompt lama dan menonaktifkan versi terkini."""
        with get_db() as db:
            prompts = db.query(PromptVersionMetric).filter(PromptVersionMetric.prompt_name == prompt_name).all()
            target_found = False
            for p in prompts:
                if p.version == target_version:
                    p.is_active = True
                    target_found = True
                else:
                    p.is_active = False
            if target_found:
                db.commit()
                return True
        return False

    def get_prompt_leaderboard(self) -> List[Dict[str, Any]]:
        """Mengambil rangkuman performa versi prompt."""
        with get_db() as db:
            items = db.query(PromptVersionMetric).order_by(PromptVersionMetric.avg_qc_score.desc()).all()
            return [
                {
                    "prompt_name": it.prompt_name,
                    "version": it.version,
                    "provider": it.provider,
                    "model": it.model,
                    "invocations": it.total_invocations,
                    "avg_qc": it.avg_qc_score,
                    "avg_repairs": it.avg_repair_count,
                    "is_active": it.is_active,
                }
                for it in items
            ]

    def get_model_leaderboard(self) -> List[Dict[str, Any]]:
        """Mengambil rangkuman perbandingan keandalan model AI."""
        with get_db() as db:
            items = db.query(ModelPerformanceMetric).all()
            return [
                {
                    "provider": it.provider,
                    "model_name": it.model_name,
                    "task_type": it.task_type,
                    "total_calls": it.total_calls,
                    "success_rate": round(it.successful_calls / max(1, it.total_calls) * 100, 1),
                    "avg_latency": it.avg_latency_seconds,
                    "avg_cost": it.avg_cost_per_call_usd,
                    "avg_qc": it.avg_qc_score,
                }
                for it in items
            ]


prompt_model_tracker = PromptModelTracker()
