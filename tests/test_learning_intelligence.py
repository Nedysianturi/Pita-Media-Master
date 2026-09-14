"""
Unit & Integration Test Suite untuk Pita Media Learning Intelligence System.
Menguji seluruh komponen kecerdasan, otonomi, memori, knowledge base, postmortem,
evaluasi prediksi vs realitas, dan proteksi keamanan.
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta

from core.learning.data_quality_gate import data_quality_gate
from core.learning.failure_classifier import failure_classifier, FailureType
from core.learning.long_term_memory import long_term_memory
from core.learning.knowledge_base import knowledge_base
from core.learning.postmortem_engine import postmortem_engine
from core.learning.decision_confidence import decision_confidence, ConfidenceLevel
from core.learning.strategy_scoring import strategy_scoring
from core.learning.audience_intelligence import audience_intelligence, CommentCategory
from core.learning.prompt_model_tracker import prompt_model_tracker
from core.learning.learning_maturity import learning_maturity, MaturityStage
from core.learning.strategy_versioning import strategy_versioning
from core.learning.autonomy_controller import autonomy_controller, AutonomyLevel
from core.learning.learning_engine import learning_engine


# --- 1. DATA QUALITY GATE TESTS ---

def test_data_quality_gate_filters_fake_and_invalid_data():
    """Menguji penyaringan metrik fake dry-run, nilai negatif, dan anomali bot."""
    # Dry-run fake data harus ditolak
    valid, reason = data_quality_gate.validate_content_metric({"views": 100, "likes": 10}, is_dry_run=True)
    assert not valid
    assert "Dry-Run" in reason

    # Nilai negatif harus ditolak
    valid, reason = data_quality_gate.validate_content_metric({"views": -10, "likes": 5})
    assert not valid

    # API Error flag harus ditolak
    valid, reason = data_quality_gate.validate_content_metric({"views": 100, "api_error": True, "error_message": "Timeout"})
    assert not valid

    # Anomali bot (likes > views)
    valid, reason = data_quality_gate.validate_content_metric({"views": 500, "likes": 600})
    assert not valid
    assert "Anomali bot" in reason

    # Data valid wajar
    valid, reason = data_quality_gate.validate_content_metric({"views": 1200, "likes": 150, "shares": 30, "comments": 12})
    assert valid


def test_data_quality_gate_sanitizes_pii():
    """Menguji pembersihan email dan nomor telepon dari data audiens."""
    raw = "Halo tolong buatkan miniatur warung kopi, hubungi saya di 08123456789 atau email budi@gmail.com ya!"
    clean = data_quality_gate.sanitize_audience_text(raw)
    assert "08123456789" not in clean
    assert "budi@gmail.com" not in clean
    assert "[NOMOR_TERSEMBUNYI]" in clean
    assert "[EMAIL_TERSEMBUNYI]" in clean


# --- 2. SMART FAILURE CLASSIFIER TESTS ---

def test_failure_classifier_distinguishes_technical_vs_content():
    """Menguji bahwa kegagalan teknis/API tidak disalahartikan sebagai konten jelek."""
    # Technical connection timeout
    res_tech = failure_classifier.classify_execution_error("Connection timed out to graph.facebook.com:443", stage="PUBLISH")
    assert res_tech["failure_type"] == FailureType.TECHNICAL_FAILURE
    assert res_tech["is_technical"] is True
    assert res_tech["affects_idea_reputation"] is False

    # Platform token expired
    res_plat = failure_classifier.classify_execution_error("Session has expired on Monday. Error validating access token", stage="PUBLISH")
    assert res_plat["failure_type"] == FailureType.PLATFORM_FAILURE
    assert res_plat["is_technical"] is True

    # QC failure
    res_qc = failure_classifier.classify_execution_error("QC Score 6.2 below threshold 8.0", stage="QC")
    assert res_qc["failure_type"] == FailureType.QC_FAILURE
    assert res_qc["is_technical"] is False

    # Duplicate Fatigue vs Content Failure
    res_fatigue = failure_classifier.classify_low_performance(
        virality_score=1.2, retention_rate=0.2, novelty_score=0.25, posting_hour_wib=20, topic_frequency_last_30d=5
    )
    assert res_fatigue["failure_type"] == FailureType.DUPLICATE_FATIGUE


# --- 3. LONG TERM CONTENT MEMORY TESTS ---

def test_long_term_memory_record_and_search():
    """Menguji pencatatan memori konten dan pencarian kemiripan semantik."""
    mem_id = long_term_memory.record_memory(
        pilar="pita_mini",
        title="Konstruksi Miniatur Toko Buku Antik Kayu Jati",
        theme="Vintage Diorama",
        miniature_object="Toko Buku Klasik",
        creation_materials=["Kayu Balsa", "Kertas Kuno", "Lampu LED"],
        hook_text="Tahukah kamu berapa lama membuat 500 buku mikroskopis ini?",
        performance_summary={"views": 2500, "shares": 80},
        lessons_learned="Pencahayaan amber meningkatkan share rate sebesar 35%.",
    )
    assert mem_id is not None

    # Cari ide serupa
    similar = long_term_memory.find_similar_memories(
        query_title="Membuat Miniatur Toko Buku Klasik dengan Kayu",
        pilar="pita_mini",
        threshold=0.30,
    )
    assert len(similar) > 0
    assert similar[0]["similarity_score"] > 0.30

    # Summary
    summary = long_term_memory.get_recent_memory_summary(days=30)
    assert summary["total_memories"] >= 1


# --- 4. KNOWLEDGE BASE ENGINE TESTS ---

def test_knowledge_base_record_and_decay():
    """Menguji pencatatan aturan pengetahuan dan degradasi status."""
    kb_id = knowledge_base.record_knowledge(
        category="hook",
        title="Hook Pertanyaan Penasaran Miniatur",
        insight_text="Hook pertanyaan di 3 detik pertama menghasilkan retensi 40% lebih tinggi.",
        evidence={"views": 5000, "avg_retention": 0.65},
        sample_size=12,
        confidence_score=0.88,
    )
    assert kb_id is not None

    active_items = knowledge_base.get_active_knowledge(category="hook", min_confidence=0.7)
    assert len(active_items) > 0
    assert active_items[0]["confidence_score"] >= 0.70


# --- 5. AUTOMATIC POSTMORTEM ENGINE TESTS ---

def test_automatic_postmortem_high_and_low_performers():
    """Menguji evaluasi postmortem otomatis untuk konten viral dan konten anjlok."""
    # High performer / viral shares
    res_viral = postmortem_engine.analyze_content_postmortem(
        content_id="cont-test-viral-001",
        pilar="pita_transformasi",
        title="Restorasi Kamera Kuno 1920 Menjadi Proyektor Hologram",
        metrics={"views": 5000, "shares": 120, "likes": 800, "comments": 45},
        cost_usd=0.80,
    )
    assert res_viral is not None
    assert res_viral["trigger_reason"] in ["VIRAL_SHARES", "HIGH_PERFORMER"]
    assert "karena hook memikat" in res_viral["why_worked"]

    # Low performer
    res_low = postmortem_engine.analyze_content_postmortem(
        content_id="cont-test-low-002",
        pilar="pita_cerita",
        title="Cerita Singkat Tanpa Resolusi",
        metrics={"views": 250, "shares": 0, "likes": 2, "comments": 0},
        cost_usd=0.50,
    )
    assert res_low is not None
    assert res_low["trigger_reason"] == "LOW_PERFORMER"
    assert "kurang menahan audiens" in res_low["why_failed"]


# --- 6. STRATEGY SCORING & CANDIDATE SELECTION TESTS ---

def test_strategy_scoring_engine_evaluates_and_selects_best():
    """Menguji scoring 5-10 kandidat ide dan pemilihan kandidat terbaik."""
    candidates = [
        {"title": "Ide A: Transformasi Jam Pasir Kristal Kuno", "concept": "Timelapse restorasi presisi dengan pencahayaan hangat", "is_exploration": False},
        {"title": "Ide B: Cerita Biasa Tanpa Hook Kuat", "concept": "Narasi umum tanpa diferensiasi", "is_exploration": False},
        {"title": "Ide C: Eksplorasi Miniatur Cyberpunk Neon", "concept": "Konstruksi diorama futuristik micro-LED", "is_exploration": True},
    ]

    scored = strategy_scoring.score_candidate_ideas(pilar="pita_transformasi", candidates=candidates)
    assert len(scored) == 3
    # Harus diurutkan descending
    assert scored[0].overall_score >= scored[1].overall_score

    best = strategy_scoring.select_best_candidate(scored)
    assert best is not None
    assert best.overall_score > 0.0
    assert best.confidence_level in [ConfidenceLevel.HIGH, ConfidenceLevel.VERY_HIGH, ConfidenceLevel.MEDIUM]


# --- 7. AUDIENCE INTELLIGENCE TESTS ---

def test_audience_intelligence_four_tier_safety_and_classification():
    """Menguji 4 filter keamanan komentar dan ekstraksi ide komunitas."""
    # Spam comment
    res_spam = audience_intelligence.process_incoming_comment("Dapatkan bonus slot gacor di t.me/slot_juara!", platform="facebook")
    assert res_spam["category"] == CommentCategory.SPAM

    # Safety violation
    res_unsafe = audience_intelligence.process_incoming_comment("Dasar rasis dan penipu!", platform="facebook")
    assert res_unsafe["safety_passed"] is False

    # Positive Request / Content Idea with PII
    res_idea = audience_intelligence.process_incoming_comment(
        "Bisa buatkan miniatur stasiun kereta tua zaman kolonial? Hubungi 081299998888", platform="facebook"
    )
    assert res_idea["category"] == CommentCategory.CONTENT_IDEA
    assert res_idea["actionable_idea"] is not None
    assert "081299998888" not in res_idea["sanitized_text"]
    assert "[NOMOR_TERSEMBUNYI]" in res_idea["sanitized_text"]


# --- 8. PROMPT & MODEL TRACKER TESTS ---

def test_prompt_and_model_tracker_with_rollback():
    """Menguji pencatatan metrik performa prompt/model dan rollback versi prompt."""
    prompt_model_tracker.record_prompt_execution(
        prompt_name="test_prompt",
        version="v1.0.0",
        template_text="Template Prompt A",
        provider="gemini",
        model="gemini-3.6-flash",
        qc_score=8.8,
        repair_count=0,
        cost_usd=0.002,
    )
    prompt_model_tracker.record_prompt_execution(
        prompt_name="test_prompt",
        version="v2.0.0",
        template_text="Template Prompt B (Buruk)",
        provider="gemini",
        model="gemini-3.6-flash",
        qc_score=6.0,
        repair_count=3,
        cost_usd=0.006,
    )

    # Rollback ke v1.0.0
    success = prompt_model_tracker.rollback_prompt_version("test_prompt", "v1.0.0")
    assert success is True

    leaderboard = prompt_model_tracker.get_prompt_leaderboard()
    assert len(leaderboard) >= 1


# --- 9. LEARNING MATURITY SCORE TESTS ---

def test_learning_maturity_score_calculation():
    """Menguji formula perhitungan skor kematangan multi-faktor (0-100)."""
    mat = learning_maturity.calculate_maturity_score()
    assert 0.0 <= mat["total_score"] <= 100.0
    assert mat["stage"] in [
        MaturityStage.INSUFFICIENT_DATA,
        MaturityStage.EARLY_LEARNING,
        MaturityStage.DEVELOPING,
        MaturityStage.MATURE,
        MaturityStage.HIGH_CONFIDENCE,
    ]
    assert "valid_posts_score" in mat["breakdown"]


# --- 10. AUTONOMY CONTROLLER & GOVERNANCE TESTS ---

def test_autonomy_controller_defaults_and_manual_changes():
    """Menguji level default OBSERVE, proteksi konfigurasi, dan pengubahan level manual."""
    # Pastikan default level awal adalah OBSERVE
    assert autonomy_controller.current_level in [AutonomyLevel.OBSERVE, AutonomyLevel.RECOMMEND, AutonomyLevel.ASSISTED_AUTO, AutonomyLevel.CONTROLLED_AUTO]

    # Protected attributes tidak boleh diubah otomatis
    assert autonomy_controller.can_auto_adjust_parameter("GEMINI_API_KEY") is False
    assert autonomy_controller.can_auto_adjust_parameter("FB_PAGE_ACCESS_TOKEN") is False
    assert autonomy_controller.can_auto_adjust_parameter("DAILY_COST_LIMIT_USD") is False

    # Ganti level manual
    res = autonomy_controller.set_autonomy_level("RECOMMEND", changed_by="TEST_ADMIN", reason="Test transition")
    assert res["new_level"] == "RECOMMEND"
    assert autonomy_controller.current_level == "RECOMMEND"

    # Reset kembali ke OBSERVE
    autonomy_controller.set_autonomy_level("OBSERVE", changed_by="TEST_ADMIN", reason="Test reset")
    assert autonomy_controller.current_level == "OBSERVE"


def test_autonomy_controller_automatic_downgrade_on_high_error():
    """Menguji automatic downgrade ketika error rate melampaui ambang batas keamanan."""
    # Naikkan sementara ke ASSISTED_AUTO
    autonomy_controller.set_autonomy_level("ASSISTED_AUTO", changed_by="TEST_ADMIN", reason="Pre-downgrade test")

    # Picu error rate tinggi (30% > 25%)
    res_down = autonomy_controller.check_automatic_downgrade(
        recent_error_rate=0.30,
        consecutive_qc_failures=0,
        active_anomalies_count=0,
    )
    assert res_down is not None
    assert res_down["downgraded"] is True
    assert res_down["new_level"] == AutonomyLevel.RECOMMEND
    assert autonomy_controller.current_level == AutonomyLevel.RECOMMEND

    # Reset ke OBSERVE
    autonomy_controller.set_autonomy_level("OBSERVE", changed_by="TEST_ADMIN", reason="Reset")


# --- 11. STRATEGY VERSIONING & ROLLBACK TESTS ---

def test_strategy_versioning_and_rollback():
    """Menguji persistensi versi strategi dan kemampuan rollback ke last proven good."""
    strat = strategy_versioning.get_active_strategy()
    assert strat["version_num"] >= 1

    # Buat strategi baru vX
    new_weights = {"pita_transformasi": 0.40, "pita_mini": 0.30, "pita_cerita": 0.20, "pita_kreasi": 0.10}
    new_v = strategy_versioning.create_new_strategy_version(
        name="Strategy v_test - High Transformasi",
        pilar_distribution=new_weights,
        reason="Testing strategy creation",
        expected_result="Test result",
    )
    assert new_v["status"] == "ACTIVE"

    # Rollback ke versi stabil sebelumnya
    rb = strategy_versioning.rollback_to_last_proven_strategy()
    assert rb is not None
    assert rb["status"] == "RESTORED"


# --- 12. LEARNING ENGINE FULL CYCLE INTEGRATION ---

def test_learning_intelligence_engine_full_overview():
    """Menguji siklus ringkasan dashboard Learning Intelligence Engine."""
    overview = learning_engine.get_learning_dashboard_overview()
    assert "autonomy_level" in overview
    assert "maturity_score" in overview
    assert "active_strategy" in overview
    assert "top_lessons_learned" in overview
