"""
Pengujian Unit untuk Cost Governor, Frequency Governor, Fatigue Engine, Strategist, dan Telegram C2.
"""

import os
import sys
from pathlib import Path
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.models import Base, CostRecord, Publication
from core.governors.cost_governor import CostGovernor
from core.governors.frequency_governor import FrequencyGovernor
from core.governors.fatigue_engine import FatigueEngine
from agents.strategist.anomaly_detector import AnomalyDetector
from agents.strategist.strategy_optimizer import StrategyOptimizer
from monitoring.telegram_bot import TelegramC2Bot


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
async def test_cost_governor_limits(test_session: AsyncSession):
    """Menguji bahwa Cost Governor mendeteksi pengeluaran dan memblokir bila melebihi hard limit."""
    gov = CostGovernor()
    gov.daily_limit = 5.0

    # Catat pengeluaran di database
    rec1 = CostRecord(service="gemini_text", token_count=1000, estimated_cost_usd=4.5)
    test_session.add(rec1)
    await test_session.commit()

    # Operasi $0.1 masih diizinkan (< $5.0)
    allowed, msg = await gov.can_proceed_with_paid_generation(test_session, estimated_addition_usd=0.1)
    assert allowed is True

    # Operasi $1.0 ditolak (> $5.0)
    allowed_blocked, msg_blocked = await gov.can_proceed_with_paid_generation(test_session, estimated_addition_usd=1.0)
    assert allowed_blocked is False
    assert "HARD LIMIT" in msg_blocked


@pytest.mark.asyncio
async def test_fatigue_engine_similarity_penalty():
    """Menguji kalkulasi penalti kesamaan tema pada Fatigue Engine."""
    fe = FatigueEngine()
    recent = ["Restorasi Jam Antik Kuno", "Pembangunan Miniatur Toko Buku"]

    # Judul yang mirip harus mendapatkan skor kemiripan tinggi
    sim_high = fe.calculate_title_similarity_penalty("Restorasi Jam Kuno Meja", recent)
    assert sim_high > 0.4

    # Judul yang sepenuhnya berbeda harus mendapatkan skor rendah
    sim_low = fe.calculate_title_similarity_penalty("Kisah Penjaga Mercusuar Laut Samudera", recent)
    assert sim_low == 0.0


@pytest.mark.asyncio
async def test_anomaly_detector_and_strategy_rollback():
    """Menguji deteksi anomali performa dan rollback strategi pilar."""
    ad = AnomalyDetector()
    history = [100.0, 105.0, 98.0, 102.0, 101.0, 99.0]
    
    # Nilai normal
    is_ano, _ = ad.detect_performance_anomaly(102.0, history)
    assert is_ano is False

    # Nilai lonjakan ekstrim (Spike)
    is_spike, msg = ad.detect_performance_anomaly(500.0, history)
    assert is_spike is True
    assert "POSITIVE_SPIKE" in msg

    # Uji Strategy Optimizer Rollback
    so = StrategyOptimizer()
    initial_w = dict(so.current_weights)
    so.update_strategy_weights({"pita_transformasi": 0.5, "pita_mini": 0.5, "pita_cerita": 0.0, "pita_kreasi": 0.0}, reason="Fokus video")
    assert so.current_weights["pita_transformasi"] == 0.5

    so.rollback_strategy()
    assert so.current_weights == initial_w


@pytest.mark.asyncio
async def test_telegram_c2_authentication_and_commands():
    """Menguji autentikasi whitelist admin dan perintah Telegram C2."""
    bot = TelegramC2Bot(token="", admin_ids={12345})

    # User tidak terdaftar (99999) harus ditolak
    unauth_res = bot.handle_status_command(99999, {}, {})
    assert "AKSES DITOLAK" in unauth_res

    # Admin terdaftar (12345) berhasil menjalankan status
    status_res = bot.handle_status_command(12345, {"PENDING": 2, "PUBLISHED": 5}, {"daily_spent": 1.2, "daily_limit": 10.0})
    assert "PITA MEDIA SYSTEM STATUS" in status_res

    # Uji Pause, Resume, Emergency Stop
    assert "DIJEDA" in bot.handle_pause_command(12345)
    assert bot.is_paused is True

    assert "BERJALAN KEMBALI" in bot.handle_resume_command(12345)
    assert bot.is_paused is False

    assert "EMERGENCY STOP" in bot.handle_emergency_stop_command(12345)
    assert bot.is_emergency_stopped is True
