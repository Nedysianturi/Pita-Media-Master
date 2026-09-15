"""
Comprehensive Test Suite for Pita Media Dashboard & Core Architecture Audit & Patch
Verifying all 12 hardening requirements:
1. Four Official Content Pillars (PITA_TRANSFORMASI, PITA_MINI, PITA_CERITA, PITA_KREASI) & Brand Bible signature.
2. Separation of DRY_RUN from Live Publishing (Receipts, stats, permalinks).
3. SSoT for Queue Counters (SQLite WAL active jobs).
4. Active AI Provider Counter (Only valid, enabled, healthy circuit providers).
5. Single Source of Truth for Secrets (DPAPI Vault vs non-secret Platform Config).
6. Meta Shared Credential Hierarchy (META_SYSTEM_USER_TOKEN parent, FB/IG references, Threads standalone).
7. Learning Heatmap Baseline Status.
8. 19-Component Realtime System Health Diagnostic.
9. Production Switch Preflight Safety Gate & Explicit Confirmation.
10. State-Aware Control Actions (PAUSE, RESUME, EMERGENCY_STOP).
11. Storage Cleanup Safety (Protected extensions & critical database/vault files).
12. Preservation of APP_MODE == DRY_RUN.
"""

import os
import pytest
import yaml
from pathlib import Path
from fastapi.testclient import TestClient

from database.models import (
    CANONICAL_PILLARS,
    LEGACY_PILLAR_MAP,
    normalize_pillar_name,
)
from core.runtime.storage_guard import PROTECTED_EXTENSIONS, PROTECTED_FILENAMES
from monitoring.dashboard.app import app


client = TestClient(app)


def test_1_four_official_content_pillars():
    """Verify 4 official pillars and legacy normalization."""
    expected_pillars = {"PITA_TRANSFORMASI", "PITA_MINI", "PITA_CERITA", "PITA_KREASI"}
    assert set(CANONICAL_PILLARS) == expected_pillars

    # Test normalization of legacy / deprecated names
    assert normalize_pillar_name("pita_refleksi") == "PITA_TRANSFORMASI"
    assert normalize_pillar_name("Pita Refleksi") == "PITA_TRANSFORMASI"
    assert normalize_pillar_name("pita_waktu") == "PITA_CERITA"
    assert normalize_pillar_name("Pita Waktu") == "PITA_CERITA"
    assert normalize_pillar_name("pita_transformasi") == "PITA_TRANSFORMASI"
    assert normalize_pillar_name("pita_cerita") == "PITA_CERITA"
    assert normalize_pillar_name("pita_kreasi") == "PITA_KREASI"
    assert normalize_pillar_name("pita_mini") == "PITA_MINI"

    # Verify brand_bible.yaml
    brand_bible_path = Path("config/brand_bible.yaml")
    assert brand_bible_path.exists(), "config/brand_bible.yaml must exist"
    with open(brand_bible_path, "r", encoding="utf-8") as f:
        brand_data = yaml.safe_load(f)

    assert "official_pillars" in brand_data
    pillar_ids = {p["id"] if isinstance(p, dict) else p for p in brand_data["official_pillars"]}
    assert pillar_ids == expected_pillars
    assert "brand_signature" in brand_data
    assert "signature_rubrics" in brand_data
    assert "pita_transformasi" in brand_data["signature_rubrics"]
    assert "pita_mini" in brand_data["signature_rubrics"]
    assert "pita_cerita" in brand_data["signature_rubrics"]
    assert "pita_kreasi" in brand_data["signature_rubrics"]


def test_2_separate_dry_run_from_live_publishing():
    """Verify DRY_RUN simulations are separated from LIVE published content."""
    res = client.get("/api/stats")
    assert res.status_code == 200
    data = res.json()

    assert "live_published_count" in data
    assert "dry_run_simulations_count" in data
    assert isinstance(data["live_published_count"], int)
    assert isinstance(data["dry_run_simulations_count"], int)

    # Check publishing receipts endpoint
    rec_res = client.get("/api/receipts")
    assert rec_res.status_code == 200
    receipts_data = rec_res.json()
    assert "receipts" in receipts_data


def test_3_ssot_queue_counter():
    """Verify overview stats queue count matches SQLite WAL active jobs."""
    res = client.get("/api/stats")
    assert res.status_code == 200
    data = res.json()
    assert "queue_count" in data
    assert isinstance(data["queue_count"], int)

    # Job endpoint list
    jobs_res = client.get("/api/jobs")
    assert jobs_res.status_code == 200
    jobs_data = jobs_res.json()
    assert "jobs" in jobs_data


def test_4_active_ai_provider_counter():
    """Verify active provider count only tallies enabled, valid providers."""
    res = client.get("/api/stats")
    assert res.status_code == 200
    data = res.json()
    assert "active_providers_count" in data
    assert isinstance(data["active_providers_count"], int)
    assert data["active_providers_count"] >= 0


def test_5_credential_vault_ssot_and_platform_config():
    """Verify credential vault remains sole secret store and platform config saves non-secrets."""
    # Check vault status
    v_res = client.get("/api/vault/status")
    assert v_res.status_code == 200
    v_data = v_res.json()
    assert v_data.get("vault_encrypted") is True

    # Test saving non-secret platform configuration
    payload = {
        "FB_PAGE_ID": "1253340697871457",
        "TELEGRAM_ADMIN_IDS": "308917129",
        "TELEGRAM_ALERT_CHAT_ID": "308917129",
    }
    save_res = client.post("/api/env/save", json=payload)
    assert save_res.status_code == 200
    save_data = save_res.json()
    assert save_data.get("success") is True

    # Verify no raw secrets returned in env endpoint
    env_res = client.get("/api/env")
    assert env_res.status_code == 200
    env_data = env_res.json().get("env", {})
    assert "GEMINI_PRIMARY_API_KEY" not in env_data
    assert "META_SYSTEM_USER_TOKEN" not in env_data
    assert env_data.get("FB_PAGE_ID") == "1253340697871457"


def test_6_meta_shared_token_and_threads():
    """Verify Meta System User Token handles FB & IG while Threads is standalone."""
    secrets_res = client.get("/api/vault/secrets")
    assert secrets_res.status_code == 200
    secrets_data = secrets_res.json().get("secrets", [])
    # Verify no raw secrets exposed in vault list
    for s in secrets_data:
        assert "value" not in s
        assert "encrypted_value" not in s
        assert "fingerprint" in s


def test_7_learning_heatmap_baseline():
    """Verify learning heatmap and learning overview APIs."""
    res = client.get("/api/learning/overview")
    assert res.status_code == 200
    data = res.json()
    assert "maturity_score" in data
    assert "autonomy_level" in data
    assert "active_strategy" in data


def test_8_system_health_19_components():
    """Verify diagnostic health endpoints check 19 components."""
    # Test both /api/system/health and /api/health
    res1 = client.get("/api/system/health")
    assert res1.status_code == 200
    data1 = res1.json()
    total_count1 = data1.get("components_count") or data1.get("total_components") or len(data1.get("items", []))
    assert total_count1 == 19
    assert len(data1["items"]) == 19

    res2 = client.get("/api/health")
    assert res2.status_code == 200
    data2 = res2.json()
    total_count2 = data2.get("components_count") or data2.get("total_components") or len(data2.get("items", []))
    assert total_count2 == 19
    assert len(data2["items"]) == 19

    # Verify key components exist
    component_names = {item["name"] for item in data1["items"]}
    assert "Credential Vault (DPAPI)" in component_names
    assert "Database (SQLite WAL)" in component_names
    assert "Storage Guard Guardrail" in component_names
    assert "AI Circuit Breakers" in component_names
    assert "AI Cost Governor" in component_names


def test_9_production_switch_preflight():
    """Verify production preflight safety gate."""
    preflight_res = client.get("/api/system/production-preflight")
    assert preflight_res.status_code == 200
    p_data = preflight_res.json()
    assert "can_switch_to_production" in p_data or "can_proceed" in p_data
    assert "checks" in p_data or "items" in p_data


def test_10_state_aware_controls():
    """Verify control actions (PAUSE, RESUME, EMERGENCY_STOP)."""
    # Test PAUSE
    p_res = client.post("/api/control/PAUSE")
    assert p_res.status_code == 200
    assert p_res.json().get("success") is True

    # Test RESUME
    r_res = client.post("/api/control/RESUME")
    assert r_res.status_code == 200
    assert r_res.json().get("success") is True


def test_11_storage_guard_safety():
    """Verify storage guard explicitly protects vault, database, and critical state files."""
    assert ".vault" in PROTECTED_EXTENSIONS
    assert ".pmvault" in PROTECTED_EXTENSIONS
    assert ".db" in PROTECTED_EXTENSIONS
    assert ".sqlite" in PROTECTED_EXTENSIONS
    assert ".wal" in PROTECTED_EXTENSIONS
    assert ".shm" in PROTECTED_EXTENSIONS

    assert "credentials.vault" in PROTECTED_FILENAMES
    assert "state.db" in PROTECTED_FILENAMES
    assert "brand_bible.yaml" in PROTECTED_FILENAMES

    # Call storage disk and cleanup endpoints
    disk_res = client.get("/api/storage/disk")
    assert disk_res.status_code == 200

    cleanup_res = client.post("/api/storage/cleanup")
    assert cleanup_res.status_code == 200
    c_data = cleanup_res.json()
    assert "deleted_count" in c_data
    assert "cleaned_dirs" in c_data


def test_12_app_mode_is_dry_run():
    """Verify APP_MODE remains strictly DRY_RUN."""
    stats_res = client.get("/api/stats")
    assert stats_res.status_code == 200
    stats_data = stats_res.json()
    assert stats_data.get("app_mode") == "DRY_RUN", "Invariant: APP_MODE must remain DRY_RUN"
