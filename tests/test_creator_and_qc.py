"""
Pengujian Unit untuk Creator Agent (4 Pilar) dan Reviewer Agent (Hard Safety Gate, QC Scoring, Auto-Repair).
"""

import os
import sys
from pathlib import Path
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.creator import creator_agent, ContentIdea
from agents.reviewer import reviewer_agent, safety_gate, qc_engine


@pytest.mark.asyncio
async def test_creator_agent_all_four_pillars():
    """Menguji bahwa Creator Agent dapat memproduksi konten untuk ke-4 pilar."""
    pillars = ["pita_transformasi", "pita_mini", "pita_cerita", "pita_kreasi"]

    for pilar in pillars:
        job_id = f"test_job_{pilar}"
        idea = ContentIdea(
            pilar=pilar,
            title=f"Eksplorasi Mahakarya {pilar}",
            concept=f"Konsep visual mendalam untuk pilar {pilar}",
            target_audience="Pecinta estetika",
            visual_theme="Cinematic",
            is_exploration=False,
        )
        payload = await creator_agent.produce_content(pilar=pilar, job_id=job_id, custom_idea=idea)

        assert payload["pilar"] == pilar
        assert payload["title"] is not None
        assert len(payload["caption"]) > 0
        assert len(payload["media_paths"]) > 0

        # Verifikasi spesifikasi khusus pilar
        if pilar == "pita_cerita":
            assert payload["media_type"] == "carousel"
            assert len(payload["media_paths"]) >= 3  # Minimal 3 gambar statis
        elif pilar in ["pita_transformasi", "pita_mini", "pita_kreasi"]:
            assert payload["media_type"] == "video"



@pytest.mark.asyncio
async def test_hard_safety_gate_blocking():
    """Menguji bahwa konten yang melanggar kebijakan langsung diblokir."""
    violating_payload = {
        "pilar": "pita_kreasi",
        "title": "Tutorial Judi Online Slot Gacor Modal Kardus",
        "caption": "Dapatkan keuntungan instan dengan rahasia judi online terbaru!",
        "raw_prompts": {},
    }

    eval_res = await safety_gate.evaluate(violating_payload)
    assert eval_res.is_safe is False
    assert len(eval_res.violations) > 0


@pytest.mark.asyncio
async def test_qc_engine_evaluation_and_pass():
    """Menguji QC Engine memberikan skor dan verdict PASSED untuk konten berkualitas."""
    valid_payload = {
        "pilar": "pita_transformasi",
        "title": "Restorasi Jam Antik Berkarat",
        "caption": "Sebuah perjalanan visual menakjubkan tentang pemulihan mekanisme jam kuno.",
        "media_type": "video",
        "media_paths": ["sample.mp4"],
    }

    qc_res = await qc_engine.evaluate_content(valid_payload, iteration_number=1)
    assert qc_res["verdict"] in ["PASSED", "REPAIR_REQUIRED"]
    assert qc_res["total_score"] > 0.0


@pytest.mark.asyncio
async def test_reviewer_auto_repair_loop():
    """Menguji loop perbaikan otomatis saat konten butuh penyempurnaan."""
    # Konten dengan caption awal yang sengaja dibuat pendek
    initial_payload = {
        "pilar": "pita_cerita",
        "title": "Kisah Singkat Mercusuar",
        "caption": "Mercusuar tua berdiri di karang.",  # Terlalu pendek untuk pita cerita
        "media_type": "carousel",
        "media_paths": ["slide1.png", "slide2.png", "slide3.png"],
    }

    repaired_payload, history = await reviewer_agent.review_and_repair_loop(
        content_payload=initial_payload,
        job_id="job_repair_test",
    )

    assert len(history) >= 1
    assert "verdict" in history[0]
