"""
Modul Cost Governor untuk Sistem Pita Media.
Memantau pengeluaran token dan durasi API (Gemini, Veo, Imagen),
mengirimkan peringatan saat mendekati batas, dan memblokir job berbayar jika melewati hard limit.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from database.models import CostRecord
from config.settings import settings


class CostGovernor:
    def __init__(self):
        self.daily_limit = settings.DAILY_COST_LIMIT_USD
        self.monthly_limit = settings.MONTHLY_COST_LIMIT_USD
        self.alert_threshold = settings.COST_ALERT_THRESHOLD_PERCENT / 100.0

    async def get_spend_metrics(self, db_session: AsyncSession) -> Dict[str, float]:
        """
        Menghitung total pengeluaran hari ini (UTC) dan bulan ini (UTC).
        """
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

        # 1. Total Hari Ini
        stmt_day = (
            select(func.coalesce(func.sum(CostRecord.estimated_cost_usd), 0.0))
            .where(CostRecord.created_at >= start_of_day)
        )
        res_day = await db_session.execute(stmt_day)
        daily_spent = float(res_day.scalar_one())

        # 2. Total Bulan Ini
        stmt_month = (
            select(func.coalesce(func.sum(CostRecord.estimated_cost_usd), 0.0))
            .where(CostRecord.created_at >= start_of_month)
        )
        res_month = await db_session.execute(stmt_month)
        monthly_spent = float(res_month.scalar_one())

        return {
            "daily_spent": round(daily_spent, 4),
            "monthly_spent": round(monthly_spent, 4),
            "daily_limit": self.daily_limit,
            "monthly_limit": self.monthly_limit,
            "daily_percentage": round((daily_spent / self.daily_limit) * 100, 1) if self.daily_limit > 0 else 0,
            "monthly_percentage": round((monthly_spent / self.monthly_limit) * 100, 1) if self.monthly_limit > 0 else 0,
        }

    async def can_proceed_with_paid_generation(
        self,
        db_session: AsyncSession,
        estimated_addition_usd: float = 0.05,
    ) -> Tuple[bool, str]:
        """
        Memeriksa apakah operasi berbayar baru diizinkan.
        Mengembalikan (allowed: bool, reason: str).
        """
        metrics = await self.get_spend_metrics(db_session)

        # Cek Hard Limit Harian
        if metrics["daily_spent"] + estimated_addition_usd > self.daily_limit:
            return (
                False,
                f"HARD LIMIT BIAYA HARIAN TERCAPAI: Pengeluaran ${metrics['daily_spent']:.2f}/${self.daily_limit:.2f} USD.",
            )

        # Cek Hard Limit Bulanan
        if metrics["monthly_spent"] + estimated_addition_usd > self.monthly_limit:
            return (
                False,
                f"HARD LIMIT BIAYA BULANAN TERCAPAI: Pengeluaran ${metrics['monthly_spent']:.2f}/${self.monthly_limit:.2f} USD.",
            )

        # Cek Alert Threshold (misal 80%)
        if metrics["daily_percentage"] >= (self.alert_threshold * 100):
            return (
                True,
                f"PERINGATAN BIAYA: Pengeluaran harian telah mencapai {metrics['daily_percentage']}% dari limit.",
            )

        return True, "Dalam batas kuota aman."


cost_governor = CostGovernor()
