"""
Pengujian Unit untuk Job Queue, Crash Recovery, Circuit Breaker, dan Publisher Agent.
"""

import os
import sys
from pathlib import Path
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.models import Base, Job, Content, Publication
from core.queue import job_queue
from core.resilience.circuit_breaker import CircuitBreaker, CircuitState
from core.resilience.crash_recovery import crash_recovery_engine
from agents.publisher import publisher_agent, post_publish_verifier


@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_job_queue_enqueue_and_acquire(test_session: AsyncSession):
    """Menguji pendaftaran job dan pengambilan transaksional oleh worker."""
    job, is_created = await job_queue.enqueue_job(
        db_session=test_session,
        pilar="pita_mini",
        schedule_slot="2026-09-14-10:00",
        seed_or_title="Toko Buku Miniatur",
    )
    assert is_created is True
    assert job.status == "PENDING"

    # Enqueue ulang dengan parameter sama -> Idempotent, tidak buat baru
    job_dup, is_created_dup = await job_queue.enqueue_job(
        db_session=test_session,
        pilar="pita_mini",
        schedule_slot="2026-09-14-10:00",
        seed_or_title="Toko Buku Miniatur",
    )
    assert is_created_dup is False
    assert job_dup.id == job.id

    # Worker acquire job
    acquired_job = await job_queue.acquire_next_job(db_session=test_session)
    assert acquired_job is not None
    assert acquired_job.id == job.id
    assert acquired_job.status == "IN_CREATOR"


@pytest.mark.asyncio
async def test_circuit_breaker_trip_and_recover():
    """Menguji circuit breaker membuka proteksi setelah ambang batas kegagalan."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=0.1)
    assert cb.can_execute() is True
    assert cb.state == CircuitState.CLOSED

    # Rekam 3 kegagalan berturut-turut
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()

    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Tunggu timeout pemulihan
    import asyncio
    await asyncio.sleep(0.15)
    assert cb.can_execute() is True  # Masuk HALF_OPEN

    # Sukses memulihkan circuit
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_crash_recovery_engine(test_session: AsyncSession):
    """Menguji pemulihan otomatis job yang ditinggalkan saat crash."""
    # Simulasikan job yang tertinggal dalam status 'IN_CREATOR'
    stranded_job = Job(
        idempotency_key="key_crash_1",
        pilar="pita_transformasi",
        status="IN_CREATOR",
    )
    test_session.add(stranded_job)
    await test_session.commit()

    recovered_list = await crash_recovery_engine.recover_orphaned_jobs(db_session=test_session)
    assert len(recovered_list) == 1
    assert recovered_list[0]["job_id"] == stranded_job.id

    # Verifikasi status berubah ke PENDING
    await test_session.refresh(stranded_job)
    assert stranded_job.status == "PENDING"
    assert stranded_job.retry_count == 1


@pytest.mark.asyncio
async def test_publisher_gate_and_duplicate_prevention(test_session: AsyncSession):
    """Menguji bahwa Publisher menolak konten yang belum PASSED QC dan menolak duplikasi."""
    content_payload = {
        "title": "Kisah mercusuar klasik",
        "pilar": "pita_cerita",
        "caption": "Cerita lengkap tentang penjaga mercusuar...",
        "media_paths": ["slide1.png", "slide2.png", "slide3.png"],
    }

    # 1. Tolak jika QC != PASSED
    with pytest.raises(PermissionError):
        await publisher_agent.publish_content(
            content_id="cont_1",
            content_payload=content_payload,
            qc_verdict="REPAIR_REQUIRED",
            db_session=test_session,
        )

    # 2. Sukses jika QC == PASSED
    pub_res = await publisher_agent.publish_content(
        content_id="cont_1",
        content_payload=content_payload,
        qc_verdict="PASSED",
        db_session=test_session,
    )
    assert pub_res["publish_status"] == "VERIFIED"

    # 3. Publikasi kedua dengan konten identik harus ditolak sebagai duplikat
    with pytest.raises(ValueError, match="DUPLICATE POST DETECTED"):
        await publisher_agent.publish_content(
            content_id="cont_2",
            content_payload=content_payload,
            qc_verdict="PASSED",
            db_session=test_session,
        )
