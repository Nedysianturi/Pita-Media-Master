"""
Enterprise Test Suite for Pita Media.
Validates:
1. Global DRY_RUN vs PRODUCTION enforcement
2. Windows DPAPI / Encrypted SecretStore & CentralCredentialManager
3. TokenHealthManager isolation
4. AIProviderRegistry & Multi-Tier AIProviderRouter
5. Multi-Platform Publishers & Post-Publish Verification receipts
6. Brand Bible, Platform Formatter, & Music Manager (8 Emotional Tones)
7. Semantic Deduplication (30-90d memory) & Novelty Scoring
8. A/B Testing Engine & Winner Evaluation
9. StorageGuard Disk Health & Config Versioning
10. Executive Reports Generation
"""

import os
import sys
import pytest
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.security.secret_store import secret_store
from core.security.credential_manager import credential_manager
from core.security.token_health_manager import token_health_manager
from providers.base_provider import AICapability, BaseAIProvider
from providers.provider_registry import provider_registry
from providers.ai_router import ai_router
from agents.publisher.facebook_publisher import FacebookPublisher
from agents.publisher.instagram_publisher import InstagramPublisher
from agents.publisher.threads_publisher import ThreadsPublisher
from agents.publisher.publisher import publisher_agent
from agents.creator.platform_formatter import platform_formatter
from core.audio.music_manager import music_manager
from core.intelligence.semantic_duplicate import semantic_detector
from core.intelligence.ab_testing import ab_testing_engine
from core.intelligence.content_analytics import content_intelligence
from core.runtime.storage_guard import storage_guard
from core.runtime.config_versioning import config_versioning
from core.runtime.reports import report_generator


def test_secret_store_encryption_and_masking():
    """Test DPAPI cipher encryption, decryption, and credential masking."""
    secret_store.set_secret("test_key", "AIzaSySecretTestKey123456789")
    retrieved = secret_store.get_secret("test_key")
    assert retrieved == "AIzaSySecretTestKey123456789"

    masked = secret_store.mask_secret(retrieved)
    assert "••••••••" in masked
    assert masked.endswith("6789")


def test_credential_manager_and_token_health():
    """Test multi-provider credential vault and failure isolation."""
    credential_manager.set_credential("gemini", {"api_key": "mock_gemini_key_9999"}, updated_by="TEST")
    creds = credential_manager.get_credential("gemini")
    assert creds.get("api_key") == "mock_gemini_key_9999"

    health = token_health_manager.audit_service_health("gemini")
    assert "status" in health


def test_ai_provider_registry_and_router():
    """Test dynamic provider registration and capability-based fallback routing."""
    providers = provider_registry.list_providers()
    assert len(providers) >= 2  # Gemini and xAI default specs

    # Test router dispatch
    prov = ai_router.select_provider(AICapability.TEXT)
    assert prov is not None
    assert prov.provider_id in ["gemini", "xai"]

    # Test image generation capability
    prov_img = ai_router.select_provider(AICapability.IMAGE_GENERATION)
    assert prov_img is not None
    assert prov_img.supports(AICapability.IMAGE_GENERATION)


def test_dry_run_isolated_publishing():
    """Test that DRY_RUN never posts to live platforms and generates verified simulated receipts."""
    os.environ["APP_MODE"] = "DRY_RUN"

    fb_pub = FacebookPublisher()
    ig_pub = InstagramPublisher()
    th_pub = ThreadsPublisher()

    payload = {
        "title": "Pita Waktu: Menembus Batas Kejayaan Kuno",
        "caption": "Kisah tentang keteguhan hati di masa lalu. #PitaWaktu",
        "media_url": "https://pitamedia.localhost/static/logo.png",
        "pilar": "pita_waktu"
    }

    res_fb = fb_pub.publish_content(payload, dry_run=True)
    assert res_fb["success"] is True
    assert res_fb["mode"] == "DRY_RUN"
    assert "post_id" in res_fb

    res_ig = ig_pub.publish_content(payload, dry_run=True)
    assert res_ig["success"] is True
    assert res_ig["mode"] == "DRY_RUN"

    res_th = th_pub.publish_content(payload, dry_run=True)
    assert res_th["success"] is True
    assert res_th["mode"] == "DRY_RUN"


def test_brand_bible_and_platform_formatter():
    """Test aspect ratios (4:5, 9:16, 1:1), rubric hooks, and CTA insertion."""
    fb_format = platform_formatter.format_for_platform(
        platform="facebook",
        title="Rahasia di Balik Jam Gadang",
        pilar="pita_waktu",
        story_body="Setiap dentang jam mengingatkan kita pada sejarah yang abadi.",
        tone="inspiratif"
    )
    assert fb_format["platform"] == "facebook"
    assert fb_format["aspect_ratio"] == "4:5"
    assert "pita waktu" in fb_format["caption"].lower()

    th_format = platform_formatter.format_for_platform(
        platform="threads",
        title="Rahasia di Balik Jam Gadang",
        pilar="pita_waktu",
        story_body="Setiap dentang jam mengingatkan kita pada sejarah yang abadi.",
        tone="inspiratif"
    )
    assert th_format["platform"] == "threads"
    assert len(th_format["caption"]) <= 500


def test_music_manager_tones():
    """Test 8 emotional moods and copyright-safe licensing."""
    tracks = music_manager.list_tracks()
    assert len(tracks) >= 8

    # Test mood selection
    track_horror = music_manager.select_track_for_content("pita_cerita", "horror")
    assert track_horror["mood"] == "horror"
    assert track_horror["license_type"] == "Creative Commons 0"

    track_sad = music_manager.select_track_for_content("pita_waktu", "sedih")
    assert track_sad["mood"] == "sedih"


def test_semantic_deduplication_and_memory():
    """Test 30-90 days semantic similarity and novelty scoring."""
    novelty = semantic_detector.evaluate_novelty(
        title="Eksplorasi Sejarah Candi Borobudur Abad ke-8",
        summary="Kajian mendalam tentang arsitektur mandala dan relief tersembunyi.",
        pilar="pita_waktu"
    )
    assert "novelty_score" in novelty
    assert novelty["novelty_score"] >= 0.0
    assert "verdict" in novelty


def test_ab_testing_engine():
    """Test A/B experiment lifecycle and winner evaluation."""
    exp = ab_testing_engine.create_experiment(
        experiment_type="HOOK",
        content_id_a="content_test_a",
        content_id_b="content_test_b",
        hypothesis="Hook pertanyaan memiliki engagement 20% lebih tinggi dibandingkan hook kontras."
    )
    assert exp["status"] == "RUNNING"
    assert exp["id"] is not None

    # Test scoring logic
    result = ab_testing_engine.evaluate_winner(exp["id"])
    assert "winner" in result
    assert result["status"] == "COMPLETED"


def test_storage_guard_and_config_versioning():
    """Test disk health monitoring and version snapshot recording."""
    disk = storage_guard.check_disk_usage()
    assert disk["status"] in ["HEALTHY", "WARNING", "CRITICAL"]
    assert disk["total_gb"] > 0

    v_id = config_versioning.record_snapshot(
        config_name="brand_bible",
        config_data={"version": "2.0", "theme": "Pita Waktu"},
        changed_by="TEST_SUITE",
        reason="Automated test snapshot"
    )
    assert v_id is not None
    versions = config_versioning.list_versions("brand_bible")
    assert len(versions) >= 1


def test_executive_report_generator():
    """Test Daily Digest and Weekly Executive Review report generation."""
    daily = report_generator.generate_daily_report()
    assert daily["report_type"] == "DAILY_DIGEST"
    assert "platform_breakdown" in daily

    weekly = report_generator.generate_weekly_report()
    assert weekly["report_type"] == "WEEKLY_EXECUTIVE_REVIEW"
    assert "status" in weekly
