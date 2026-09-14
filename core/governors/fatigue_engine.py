"""
Modul Novelty & Fatigue Engine untuk Sistem Pita Media.
Mencegah kejenuhan audiens dengan memonitor histori tema 7-30 hari,
dan mengalokasikan 20-30% kuota konten untuk eksperimen gaya/konsep baru.
"""

import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from database.models import Content
from config.settings import settings


class FatigueEngine:
    def __init__(self):
        self.exploration_quota = settings.EXPLORATION_QUOTA_PERCENT / 100.0

    async def get_recent_topics(
        self,
        db_session: AsyncSession,
        pilar: Optional[str] = None,
        days: int = 30,
        limit: int = 20,
    ) -> List[str]:
        """
        Mengambil daftar judul dan konsep konten dalam rentang 7-30 hari terakhir.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            select(Content.title)
            .where(Content.created_at >= cutoff)
            .order_by(desc(Content.created_at))
            .limit(limit)
        )
        if pilar:
            stmt = stmt.where(Content.pilar == pilar)

        res = await db_session.execute(stmt)
        return [row[0] for row in res.all()]

    def decide_exploration_slot(self) -> bool:
        """
        Menentukan apakah slot konten berikutnya harus dialokasikan sebagai eksperimen (20-30% probabilitas).
        """
        return random.random() < self.exploration_quota

    def calculate_title_similarity_penalty(self, new_title: str, recent_titles: List[str]) -> float:
        """
        Menghitung skor penalti kesamaan kata sederhana (Jaccard token similarity).
        Nilai mendekati 1.0 berarti sangat mirip (repetitif), nilai 0.0 berarti segar/novel.
        """
        if not recent_titles:
            return 0.0

        new_tokens = set(new_title.lower().split())
        max_sim = 0.0

        for t in recent_titles:
            tokens = set(t.lower().split())
            if not tokens or not new_tokens:
                continue
            intersection = len(new_tokens.intersection(tokens))
            union = len(new_tokens.union(tokens))
            sim = intersection / union if union > 0 else 0.0
            if sim > max_sim:
                max_sim = sim

        return round(max_sim, 2)


fatigue_engine = FatigueEngine()
