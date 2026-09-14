"""
Modul Strategy Optimizer & Profitability Engine untuk Strategist Agent.
Menganalisis performa rolling 7-30 hari, menghitung skor profitabilitas (ROI),
dan merekomendasikan penyesuaian bobot pilar yang dapat di-rollback.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from database.models import Content, PerformanceMetric, CostRecord


class StrategyOptimizer:
    def __init__(self):
        # Default distribution bobot 4 pilar
        self.current_weights = {
            "pita_transformasi": 0.30,
            "pita_mini": 0.25,
            "pita_cerita": 0.25,
            "pita_kreasi": 0.20,
        }
        self.strategy_history: List[Dict[str, Any]] = []

    def calculate_profitability_score(
        self,
        views: int,
        likes: int,
        shares: int,
        estimated_cost_usd: float,
        estimated_cpm_usd: float = 1.5,
    ) -> float:
        """
        Menghitung Skor Profitabilitas (ROI ratio atau Net Margin).
        Perkiraan pendapatan = (views / 1000) * estimated_cpm_usd + bonus engagement.
        """
        est_revenue = (views / 1000.0) * estimated_cpm_usd + (likes * 0.001) + (shares * 0.005)
        if estimated_cost_usd <= 0:
            return round(est_revenue, 2)
        profit_ratio = round((est_revenue - estimated_cost_usd) / estimated_cost_usd, 2)
        return profit_ratio

    async def analyze_pillar_performance(
        self,
        db_session: AsyncSession,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Menganalisis performa agregat per pilar selama 7-30 hari terakhir.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Query metrics per pilar
        stmt = (
            select(
                Content.pilar,
                func.count(Content.id).label("total_posts"),
                func.coalesce(func.avg(PerformanceMetric.views), 0).label("avg_views"),
                func.coalesce(func.avg(PerformanceMetric.likes), 0).label("avg_likes"),
                func.coalesce(func.avg(PerformanceMetric.profitability_score), 0.0).label("avg_profitability"),
            )
            .outerjoin(PerformanceMetric, Content.id == PerformanceMetric.content_id)
            .where(Content.created_at >= cutoff)
            .group_by(Content.pilar)
        )
        res = await db_session.execute(stmt)
        rows = res.all()

        analysis = {}
        for r in rows:
            analysis[r.pilar] = {
                "total_posts": r.total_posts,
                "avg_views": round(float(r.avg_views), 1),
                "avg_likes": round(float(r.avg_likes), 1),
                "avg_profitability": round(float(r.avg_profitability), 2),
            }

        return {
            "period_days": days,
            "pillars_performance": analysis,
            "current_weights": self.current_weights,
        }

    def update_strategy_weights(
        self,
        new_weights: Dict[str, float],
        reason: str,
    ) -> Dict[str, Any]:
        """
        Memperbarui bobot distribusi pilar dengan mencatat versi riwayat untuk rollback.
        """
        # Normalisasi bobot agar total == 1.0
        total_w = sum(new_weights.values())
        normalized = {k: round(v / total_w, 2) for k, v in new_weights.items()}

        history_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "previous_weights": dict(self.current_weights),
            "new_weights": normalized,
            "reason": reason,
        }
        self.strategy_history.append(history_entry)
        self.current_weights = normalized

        return history_entry

    def rollback_strategy(self) -> Optional[Dict[str, Any]]:
        """
        Mengembalikan bobot strategi ke versi sebelumnya jika tersedia.
        """
        if not self.strategy_history:
            return None

        last_change = self.strategy_history.pop()
        self.current_weights = last_change["previous_weights"]
        return last_change


strategy_optimizer = StrategyOptimizer()
