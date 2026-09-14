"""
Pengujian End-to-End (E2E) Sistem Pita Media.
Menguji seluruh siklus dari penjadwalan -> Creator -> Reviewer QC -> Auto-Repair -> Publisher -> Database & Audit.
"""

import os
import sys
from pathlib import Path
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.models import Base, Job, Content, Publication, QCRecord, Provenance
from database.connection import init_db, async_session_factory
from core.scheduler import content_orchestrator


@pytest.mark.asyncio
async def test_full_autonomous_content_cycle():
    """Menguji siklus penuh pembuatan konten mandiri end-to-end."""
    # 1. Inisialisasi DB
    await init_db()

    # 2. Jadwalkan slot konten untuk Pita Cerita
    job = await content_orchestrator.schedule_next_content_slot(pilar="pita_cerita")
    assert job is not None
    assert job.status == "PENDING"
    assert job.pilar == "pita_cerita"

    # 3. Proses job end-to-end
    success = await content_orchestrator.process_single_job(job.id)
    assert success is True

    # 4. Verifikasi status akhir di database
    async with async_session_factory() as session:
        # Cek Job
        stmt_job = select(Job).where(Job.id == job.id)
        res_job = await session.execute(stmt_job)
        updated_job = res_job.scalar_one()
        assert updated_job.status == "PUBLISHED"

        # Cek Content
        stmt_cont = select(Content).where(Content.job_id == job.id)
        res_cont = await session.execute(stmt_cont)
        content = res_cont.scalar_one()
        assert content.pilar == "pita_cerita"
        assert content.media_type == "carousel"
        assert len(content.media_paths) >= 3

        # Cek Provenance
        stmt_prov = select(Provenance).where(Provenance.content_id == content.id)
        res_prov = await session.execute(stmt_prov)
        provenance = res_prov.scalar_one()
        assert provenance.model_name is not None

        # Cek QC Record
        stmt_qc = select(QCRecord).where(QCRecord.content_id == content.id)
        res_qc = await session.execute(stmt_qc)
        qc_records = res_qc.scalars().all()
        assert len(qc_records) >= 1
        assert qc_records[-1].verdict == "PASSED"

        # Cek Publication
        stmt_pub = select(Publication).where(Publication.content_id == content.id)
        res_pub = await session.execute(stmt_pub)
        publication = res_pub.scalar_one()
        assert publication.publish_status == "VERIFIED"
        assert publication.verification_hash is not None
