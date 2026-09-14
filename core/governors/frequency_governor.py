"""
Modul Frequency Governor untuk Sistem Pita Media.
Mengatur jarak jeda minimum antar publikasi agar tidak terjadi banjir postingan (spam).
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from database.models import Publication
from config.settings import settings


class FrequencyGovernor:
    def __init__(self):
        self.min_interval_minutes = settings.MIN_POSTING_INTERVAL_MINUTES

    async def can_publish_now(
        self,
        db_session: AsyncSession,
        platform: str = "mock",
    ) -> Tuple[bool, Optional[int]]:
        """
        Memeriksa apakah waktu jeda sejak publikasi terakhir sudah terpenuhi.
        Mengembalikan (can_publish: bool, remaining_cooldown_seconds: Optional[int]).
        """
        stmt = (
            select(Publication)
            .where(Publication.platform == platform)
            .where(Publication.publish_status.in_(["SUCCESS", "VERIFIED"]))
            .order_by(desc(Publication.published_at))
            .limit(1)
        )
        res = await db_session.execute(stmt)
        last_pub = res.scalar_one_or_none()

        if not last_pub:
            return True, None

        elapsed = (datetime.now(timezone.utc) - last_pub.published_at.replace(tzinfo=timezone.utc)).total_seconds()
        min_seconds = self.min_interval_minutes * 60

        if elapsed < min_seconds:
            remaining = int(min_seconds - elapsed)
            return False, remaining

        return True, None


frequency_governor = FrequencyGovernor()
