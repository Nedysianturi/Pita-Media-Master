"""
Modul Crash Recovery untuk Sistem Pita Media.
Memulihkan state machine saat sistem mati mendadak / restart tanpa kehilangan data atau double-posting.
"""

import logging
from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from database.models import Job, AuditLog, Publication

logger = logging.getLogger(__name__)


class CrashRecoveryEngine:
    async def recover_orphaned_jobs(self, db_session: AsyncSession) -> List[Dict[str, Any]]:
        """
        Memindai database dan memulihkan job yang menggantung akibat crash.
        """
        orphaned_statuses = ["IN_CREATOR", "IN_REVIEW", "REPAIRING"]
        recovered = []

        # 1. Cari job yang tertinggal di tahap pembuatan/review
        stmt = select(Job).where(Job.status.in_(orphaned_statuses))
        result = await db_session.execute(stmt)
        stranded_jobs = result.scalars().all()

        for job in stranded_jobs:
            old_status = job.status
            job.status = "PENDING"
            job.retry_count += 1
            job.error_message = f"Otomatis dipulihkan dari crash saat berstatus '{old_status}'."

            audit = AuditLog(
                level="WARNING",
                component="CrashRecovery",
                job_id=job.id,
                message=f"Job {job.id} ({job.pilar}) dipulihkan dari state '{old_status}' ke 'PENDING'.",
            )
            db_session.add(audit)
            recovered.append({"job_id": job.id, "pilar": job.pilar, "old_status": old_status})

        # 2. Cari job yang tertinggal di tahap publikasi (IN_PUBLISH)
        stmt_pub = select(Job).where(Job.status == "IN_PUBLISH")
        result_pub = await db_session.execute(stmt_pub)
        stranded_publish_jobs = result_pub.scalars().all()

        for job in stranded_publish_jobs:
            # Periksa apakah sudah ada record publication sukses
            stmt_check = select(Publication).where(Publication.content_id.in_(
                select(Job.id).where(Job.id == job.id)
            ))
            pub_res = await db_session.execute(stmt_check)
            pub = pub_res.scalar_one_or_none()

            if pub and pub.publish_status in ["SUCCESS", "VERIFIED"]:
                job.status = "PUBLISHED"
            else:
                job.status = "APPROVED"  # Kembalikan ke siap publish, bukan create ulang
                job.error_message = "Dipulihkan dari crash saat publikasi. Siap diverifikasi ulang."

            recovered.append({"job_id": job.id, "pilar": job.pilar, "old_status": "IN_PUBLISH"})

        if recovered:
            await db_session.commit()
            logger.info(f"Crash Recovery berhasil memulihkan {len(recovered)} pekerjaan yang menggantung.")

        return recovered


crash_recovery_engine = CrashRecoveryEngine()
