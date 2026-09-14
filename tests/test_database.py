"""
Pengujian Unit Database: Inisialisasi, Relasi, Idempotensi, dan Backup.
"""

import os
import sys
from pathlib import Path
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

# Ensure pita-media root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.models import (
    Base,
    Job,
    Content,
    Provenance,
    QCRecord,
    Publication,
    PerformanceMetric,
    CostRecord,
    AuditLog,
)
from database.connection import backup_database
from config.settings import settings


@pytest_asyncio.fixture
async def test_db_session():
    # Use an in-memory SQLite database for fast, isolated testing
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with test_session_factory() as session:
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_job_idempotency_constraint(test_db_session: AsyncSession):
    """Memverifikasi bahwa dua job dengan idempotency_key yang sama akan ditolak."""
    key = "idemp_test_key_123"
    job1 = Job(idempotency_key=key, pilar="pita_transformasi", status="PENDING")
    test_db_session.add(job1)
    await test_db_session.commit()

    # Job kedua dengan key yang sama harus memicu IntegrityError
    job2 = Job(idempotency_key=key, pilar="pita_transformasi", status="PENDING")
    test_db_session.add(job2)
    with pytest.raises(IntegrityError):
        await test_db_session.commit()
    await test_db_session.rollback()


@pytest.mark.asyncio
async def test_content_and_provenance_lifecycle(test_db_session: AsyncSession):
    """Menguji siklus hidup pembuatan konten, provenance, QC record, dan publikasi."""
    # 1. Buat Job
    job = Job(idempotency_key="idemp_content_test_456", pilar="pita_cerita", status="IN_CREATOR")
    test_db_session.add(job)
    await test_db_session.commit()
    await test_db_session.refresh(job)

    # 2. Buat Content
    content = Content(
        job_id=job.id,
        pilar="pita_cerita",
        title="Kisah Rumah Kayu Tua",
        caption="Sebuah kisah mendalam tentang ketahanan...",
        media_type="carousel",
        media_paths=["storage/processed/slide1.png", "storage/processed/slide2.png"],
    )
    test_db_session.add(content)
    await test_db_session.commit()
    await test_db_session.refresh(content)

    # 3. Buat Provenance
    provenance = Provenance(
        content_id=content.id,
        prompt_version="1.0.0",
        raw_prompts={"story_prompt": "Buat cerita tentang...", "image_prompts": ["p1", "p2"]},
        model_name="gemini-2.5-pro",
        model_version="gemini-2.5-pro-001",
    )
    test_db_session.add(provenance)

    # 4. Buat QC Record
    qc = QCRecord(
        content_id=content.id,
        iteration_number=1,
        verdict="PASSED",
        total_score=8.7,
        scores_breakdown={"story_depth": 9.0, "image_quality": 8.5},
        feedback_text="Kualitas narasi sangat kuat.",
        safety_gate_passed=True,
    )
    test_db_session.add(qc)

    # 5. Buat Publikasi
    pub = Publication(
        content_id=content.id,
        platform="mock",
        post_url="https://pita-media.mock/post/123",
        publish_status="VERIFIED",
        verification_hash="hash_abc_123",
    )
    test_db_session.add(pub)

    # 6. Catat Cost & Audit
    cost = CostRecord(
        job_id=job.id,
        service="gemini_text",
        token_count=1500,
        estimated_cost_usd=0.003,
    )
    audit = AuditLog(
        component="CreatorAgent",
        job_id=job.id,
        content_id=content.id,
        message="Konten cerita berhasil dibuat dan lolos QC",
    )
    test_db_session.add_all([cost, audit])
    await test_db_session.commit()

    # Verifikasi Query
    result = await test_db_session.execute(select(Content).where(Content.id == content.id))
    fetched_content = result.scalar_one()
    assert fetched_content.title == "Kisah Rumah Kayu Tua"
    assert len(fetched_content.media_paths) == 2


@pytest.mark.asyncio
async def test_settings_admin_ids_parsing():
    """Memverifikasi parsing ID admin Telegram dari string ke set of int."""
    from config.settings import Settings

    custom_settings = Settings(TELEGRAM_ADMIN_IDS="111, 222 , 333")
    assert custom_settings.admin_ids == {111, 222, 333}

    empty_settings = Settings(TELEGRAM_ADMIN_IDS="")
    assert empty_settings.admin_ids == set()
