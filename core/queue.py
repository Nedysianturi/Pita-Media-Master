"""
Job Queue Transaksional berbasis SQLite WAL untuk Sistem Pita Media.
Menyediakan state machine tangguh, manajemen kunci idempotensi, dan proteksi konkurensi.
"""

import hashlib
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from database.models import Job, AuditLog


class JobQueue:
    @staticmethod
    def generate_idempotency_key(pilar: str, schedule_slot: str, seed_or_title: str) -> str:
        """
        Menghasilkan kunci idempotensi unik SHA-256 untuk mencegah duplikasi konten ganda.
        """
        raw = f"{pilar}:{schedule_slot}:{seed_or_title}".strip().lower()
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def enqueue_job(
        self,
        db_session: AsyncSession,
        pilar: str,
        schedule_slot: str,
        seed_or_title: str,
        is_exploration: bool = False,
    ) -> Tuple[Job, bool]:
        """
        Menambahkan job baru ke antrean dengan proteksi idempotensi.
        Mengembalikan (Job, is_created: bool).
        """
        idempotency_key = self.generate_idempotency_key(pilar, schedule_slot, seed_or_title)

        # Cek apakah job dengan key ini sudah ada
        stmt = select(Job).where(Job.idempotency_key == idempotency_key)
        res = await db_session.execute(stmt)
        existing_job = res.scalar_one_or_none()

        if existing_job:
            return existing_job, False

        # Buat job baru
        new_job = Job(
            idempotency_key=idempotency_key,
            pilar=pilar,
            status="PENDING",
            is_exploration=is_exploration,
        )
        db_session.add(new_job)

        audit = AuditLog(
            level="INFO",
            component="JobQueue",
            message=f"Job {new_job.id} ({pilar}) berhasil di-enqueue dengan slot '{schedule_slot}'.",
        )
        db_session.add(audit)
        await db_session.commit()
        await db_session.refresh(new_job)

        return new_job, True

    async def acquire_next_job(self, db_session: AsyncSession) -> Optional[Job]:
        """
        Mengambil satu job PENDING terlama dan mengubah statusnya menjadi IN_CREATOR secara transaksional.
        """
        stmt = (
            select(Job)
            .where(Job.status == "PENDING")
            .order_by(Job.created_at.asc())
            .limit(1)
            .with_for_update()
        )
        res = await db_session.execute(stmt)
        job = res.scalar_one_or_none()

        if job:
            job.status = "IN_CREATOR"
            await db_session.commit()
            await db_session.refresh(job)
            return job
        return None

    async def update_status(
        self,
        db_session: AsyncSession,
        job_id: str,
        new_status: str,
        error_message: Optional[str] = None,
    ) -> Optional[Job]:
        """
        Memperbarui status pengerjaan suatu job.
        """
        stmt = select(Job).where(Job.id == job_id)
        res = await db_session.execute(stmt)
        job = res.scalar_one_or_none()

        if job:
            job.status = new_status
            if error_message:
                job.error_message = error_message

            audit = AuditLog(
                level="INFO" if "FAIL" not in new_status and "BLOCK" not in new_status else "WARNING",
                component="JobQueue",
                job_id=job.id,
                message=f"Status job {job.id} diperbarui menjadi '{new_status}'.",
            )
            db_session.add(audit)
            await db_session.commit()
            await db_session.refresh(job)
            return job
        return None

    async def get_queue_stats(self, db_session: AsyncSession) -> Dict[str, int]:
        """
        Menghitung rekapitulasi jumlah job per status.
        """
        stmt = select(Job.status, func.count(Job.id)).group_by(Job.status)
        res = await db_session.execute(stmt)
        rows = res.all()
        stats = {row[0]: row[1] for row in rows}
        return stats


job_queue = JobQueue()
