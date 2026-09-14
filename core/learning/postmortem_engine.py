"""
Automatic Postmortem Engine untuk Pita Media.
Mengevaluasi secara terstruktur konten berkinerja tinggi, underperforming, berbiaya tinggi, atau anomali:
- WHY IT WORKED
- WHY IT FAILED
- WHAT TO REPEAT
- WHAT TO AVOID
- WHAT TO TEST NEXT
Menyaring kesimpulan yang valid untuk otomatis didistilasikan ke Knowledge Base.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from core.database import get_db
from database.models import ContentPostmortem, Content, PerformanceMetric
from core.learning.knowledge_base import knowledge_base

logger = logging.getLogger("pita_media.learning.postmortem")


class AutomaticPostmortemEngine:
    """
    Mesin evaluasi otomatis pasca-publikasi konten.
    """

    def analyze_content_postmortem(
        self,
        content_id: str,
        pilar: str,
        title: str,
        metrics: Dict[str, Any],
        cost_usd: float = 0.0,
        historical_avg_shares: float = 5.0,
        historical_avg_views: float = 500.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Menjalankan postmortem jika konten memenuhi kriteria pemicu (trigger condition).
        """
        views = metrics.get("views", 0) or metrics.get("views_count", 0)
        likes = metrics.get("likes", 0) or metrics.get("likes_count", 0)
        shares = metrics.get("shares", 0) or metrics.get("shares_count", 0)
        comments = metrics.get("comments", 0) or metrics.get("comments_count", 0)

        trigger_reason = None

        # Evaluasi pemicu
        if shares >= historical_avg_shares * 3 and shares >= 15:
            trigger_reason = "VIRAL_SHARES"
        elif views >= historical_avg_views * 2.5 and views >= 1000:
            trigger_reason = "HIGH_PERFORMER"
        elif comments >= 20:
            trigger_reason = "HIGH_COMMENTS"
        elif cost_usd > 2.0 and views < 100:
            trigger_reason = "HIGH_COST"
        elif views > 200 and likes < 5 and shares == 0:
            trigger_reason = "LOW_PERFORMER"

        if not trigger_reason:
            return None

        why_worked = ""
        why_failed = ""
        what_to_repeat = ""
        what_to_avoid = ""
        what_to_test_next = ""

        if trigger_reason in ["HIGH_PERFORMER", "VIRAL_SHARES", "HIGH_COMMENTS"]:
            why_worked = f"Konten berhasil memicu engagement tinggi ({views} views, {shares} shares) karena hook memikat dan resolusi visual yang memuaskan."
            what_to_repeat = f"Pertahankan struktur cerita dan visual style serupa pada pilar '{pilar}'."
            what_to_test_next = f"Uji coba variasi tema baru dengan struktur hook dan pacing yang sama."
        elif trigger_reason == "LOW_PERFORMER":
            why_failed = f"Penyampaian hook atau tempo di awal kurang menahan audiens (retensi rendah)."
            what_to_avoid = f"Hindari pembuka yang terlalu lambat atau visual yang monoton di 3 detik pertama."
            what_to_test_next = f"Gunakan dynamic hook atau visual shock factor di detik pertama."
        elif trigger_reason == "HIGH_COST":
            why_failed = f"Biaya pembuatan (${cost_usd:.2f}) tidak sebanding dengan perolehan penonton ({views} views)."
            what_to_avoid = f"Hindari pengulangan render model berbiaya tinggi tanpa validasi hook terlebih dahulu."
            what_to_test_next = f"Optimasi prompt agar selesai dalam 1 siklus render tanpa perbaikan berulang."

        # Simpan record postmortem
        now = datetime.now(timezone.utc)
        postmortem_id = None
        with get_db() as db:
            pm = ContentPostmortem(
                content_id=content_id,
                pilar=pilar,
                trigger_reason=trigger_reason,
                performance_metrics=metrics,
                why_worked=why_worked,
                why_failed=why_failed,
                what_to_repeat=what_to_repeat,
                what_to_avoid=what_to_avoid,
                what_to_test_next=what_to_test_next,
                created_at=now,
            )
            db.add(pm)
            db.commit()
            postmortem_id = pm.id

        # Distilasi ke Knowledge Base jika insight bernilai tinggi
        if trigger_reason in ["HIGH_PERFORMER", "VIRAL_SHARES"]:
            knowledge_base.record_knowledge(
                category="hook" if trigger_reason == "VIRAL_SHARES" else "pilar_trend",
                title=f"Pola Unggul: {title[:40]}... (#{pilar})",
                insight_text=what_to_repeat,
                evidence={"views": views, "shares": shares, "likes": likes, "cost_usd": cost_usd},
                sample_size=1,
                confidence_score=0.75,
                source_metrics=metrics,
            )

        return {
            "id": postmortem_id,
            "trigger_reason": trigger_reason,
            "why_worked": why_worked,
            "why_failed": why_failed,
            "what_to_repeat": what_to_repeat,
            "what_to_avoid": what_to_avoid,
            "what_to_test_next": what_to_test_next,
        }

    def get_recent_postmortems(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Mengambil daftar evaluasi postmortem terbaru."""
        with get_db() as db:
            items = db.query(ContentPostmortem).order_by(ContentPostmortem.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": it.id,
                    "content_id": it.content_id,
                    "pilar": it.pilar,
                    "trigger_reason": it.trigger_reason,
                    "why_worked": it.why_worked,
                    "why_failed": it.why_failed,
                    "what_to_repeat": it.what_to_repeat,
                    "what_to_avoid": it.what_to_avoid,
                    "what_to_test_next": it.what_to_test_next,
                    "created_at": it.created_at.isoformat() if it.created_at else "",
                }
                for it in items
            ]


postmortem_engine = AutomaticPostmortemEngine()
