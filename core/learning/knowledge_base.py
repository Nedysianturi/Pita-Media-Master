"""
Knowledge Base Engine untuk Pita Media.
Menyimpan, memperbarui, dan mendegradasi (decay) sari pengetahuan yang disimpulkan dari data performa.
Bukan sekadar log, melainkan aturan/insight terdistilasi yang dapat diandalkan oleh Strategist dan Ideator.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from core.database import get_db
from database.models import KnowledgeItem

logger = logging.getLogger("pita_media.learning.knowledge_base")


class KnowledgeBaseEngine:
    """
    Manajer basis pengetahuan cerdas dengan sistem pembobotan, evidence tracking,
    dan degradasi otomatis (ACTIVE -> WEAK -> EXPIRED).
    """

    def record_knowledge(
        self,
        category: str,
        title: str,
        insight_text: str,
        evidence: Dict[str, Any],
        sample_size: int,
        confidence_score: float,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        source_metrics: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Menambahkan item pengetahuan baru atau memperbarui pengetahuan sejenis.
        """
        now = datetime.now(timezone.utc)
        item = KnowledgeItem(
            category=category,
            title=title,
            insight_text=insight_text,
            evidence=evidence or {},
            sample_size=sample_size,
            confidence_score=max(0.0, min(1.0, float(confidence_score))),
            period_start=period_start or (now - timedelta(days=30)),
            period_end=period_end or now,
            source_metrics=source_metrics or {},
            status="ACTIVE",
            decay_factor=1.0,
            created_at=now,
            updated_at=now,
        )

        with get_db() as db:
            db.add(item)
            db.commit()
            return item.id

    def get_active_knowledge(
        self,
        category: Optional[str] = None,
        min_confidence: float = 0.60,
    ) -> List[Dict[str, Any]]:
        """
        Mengambil daftar pengetahuan yang berstatus ACTIVE dan memenuhi batas minimum confidence.
        """
        with get_db() as db:
            query = db.query(KnowledgeItem).filter(KnowledgeItem.status == "ACTIVE")
            if category:
                query = query.filter(KnowledgeItem.category == category)
            if min_confidence > 0:
                query = query.filter(KnowledgeItem.confidence_score >= min_confidence)
            
            items = query.order_by(KnowledgeItem.confidence_score.desc()).all()
            return [
                {
                    "id": it.id,
                    "category": it.category,
                    "title": it.title,
                    "insight_text": it.insight_text,
                    "sample_size": it.sample_size,
                    "confidence_score": round(it.confidence_score, 2),
                    "status": it.status,
                    "evidence": it.evidence,
                    "created_at": it.created_at.isoformat() if it.created_at else "",
                }
                for it in items
            ]

    def decay_old_knowledge(self, days_threshold: int = 60) -> int:
        """
        Mendegradasi status pengetahuan lama jika sudah lebih dari days_threshold.
        ACTIVE -> WEAK -> EXPIRED.
        """
        now = datetime.now(timezone.utc)
        cutoff_weak = now - timedelta(days=days_threshold)
        cutoff_expired = now - timedelta(days=days_threshold * 2)

        decayed_count = 0
        with get_db() as db:
            items = db.query(KnowledgeItem).all()
            for it in items:
                if not it.created_at:
                    continue
                if it.created_at < cutoff_expired and it.status != "EXPIRED":
                    it.status = "EXPIRED"
                    it.decay_factor = 0.0
                    decayed_count += 1
                elif it.created_at < cutoff_weak and it.status == "ACTIVE":
                    it.status = "WEAK"
                    it.decay_factor = 0.5
                    decayed_count += 1
            db.commit()

        return decayed_count


knowledge_base = KnowledgeBaseEngine()
