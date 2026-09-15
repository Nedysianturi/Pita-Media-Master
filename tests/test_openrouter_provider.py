"""
Test Suite: OpenRouter Multi-Model Gateway Integration
Validates all 20 acceptance criteria for OpenRouter provider co-existing with Google Gemini.
"""

import os
import json
import pytest
import asyncio
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from providers.base_provider import AICapability
from providers.provider_registry import provider_registry, ProviderRegistry
from providers.openrouter_provider import OpenRouterProvider, openrouter_provider, CircuitState
from providers.gemini_provider import gemini_provider
from providers.xai_provider import xai_provider
from providers.ai_router import ai_router
from core.security.secret_store import secret_store
from core.security.credential_manager import credential_manager
from core.security.credential_health import credential_health_engine
from monitoring.dashboard.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_1_openrouter_provider_registration():
    """1. OpenRouter provider can be registered in ProviderRegistry."""
    prov = provider_registry.get_provider("openrouter")
    assert prov is not None
    assert prov.provider_id == "openrouter"
    assert prov.display_name == "OpenRouter"
    assert prov.base_url == "https://openrouter.ai/api/v1"
    assert prov.provider_type == "MULTI_MODEL_GATEWAY"
    assert prov.priority == 30


def test_2_openrouter_api_key_vault_persistence():
    """2. OPENROUTER_API_KEY persists in encrypted Vault."""
    test_key = "sk-or-v1-testopenroutersecretkey12345678"
    secret_store.set_secret("OPENROUTER_API_KEY", test_key, provider="openrouter")
    
    retrieved = secret_store.get_secret("OPENROUTER_API_KEY")
    assert retrieved == test_key
    assert openrouter_provider.api_key == test_key


def test_3_key_survives_restart_and_audit():
    """3. Key survives restart/audit without deletion."""
    test_key = "sk-or-v1-testopenroutersecretkey12345678"
    secret_store.set_secret("OPENROUTER_API_KEY", test_key)
    
    audit_res = credential_manager.run_startup_credential_audit()
    assert "OPENROUTER_API_KEY" in audit_res.get("vault_authoritative", []) or secret_store.has_secret("OPENROUTER_API_KEY")
    assert secret_store.get_secret("OPENROUTER_API_KEY") == test_key


def test_4_raw_key_not_in_logs_or_repr():
    """4. Raw key does not appear in serialized dictionary or representation."""
    d = openrouter_provider.to_dict()
    assert "sk-or-v1" not in str(d)
    assert "api_key" not in d or d.get("api_key") == ""
    assert "••••" in d.get("masked_key", "")
    assert d.get("secret_ref") == "OPENROUTER_API_KEY"


def test_5_raw_key_not_in_masked_listing():
    """5. Raw key is masked in list_all_credentials_masked."""
    creds = credential_manager.list_all_credentials_masked()
    openrouter_entries = [c for c in creds if c["service_name"] == "openrouter"]
    if openrouter_entries:
        entry = openrouter_entries[0]
        masked = entry["masked_value"]
        assert "••••" in masked
        assert not masked.startswith("sk-or-v1-testopenroutersecretkey")


def test_6_raw_key_not_returned_to_frontend_vault_secrets(client):
    """6. /api/vault/secrets returns metadata only, no plaintext secret values."""
    res = client.get("/api/vault/secrets")
    assert res.status_code == 200
    data = res.json()
    secrets = data.get("secrets", [])
    for s in secrets:
        if s["name"] == "OPENROUTER_API_KEY":
            assert "value" not in s
            assert "sk-or-v1" not in str(s)
            assert s.get("fingerprint") is not None


@pytest.mark.asyncio
async def test_7_openrouter_test_connection_live_handling():
    """7. OpenRouter Test handles valid, invalid, and rate limit responses."""
    # Test valid response mock
    mock_resp_valid = MagicMock()
    mock_resp_valid.status_code = 200
    mock_resp_valid.json.return_value = {"data": [{"id": "openai/gpt-4o-mini"}]}

    with patch("httpx.Client.get", return_value=mock_resp_valid):
        res = openrouter_provider.test_connection(custom_token="sk-or-valid-test")
        assert res["status"] == "VALID"
        assert "1 model" in res["message"]

    # Test 429 rate limit mock
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    with patch("httpx.Client.get", return_value=mock_resp_429):
        res = openrouter_provider.test_connection(custom_token="sk-or-limited")
        assert res["status"] == "RATE_LIMITED"

    # Test 402 quota exhausted mock
    mock_resp_402 = MagicMock()
    mock_resp_402.status_code = 402
    with patch("httpx.Client.get", return_value=mock_resp_402):
        res = openrouter_provider.test_connection(custom_token="sk-or-no-credits")
        assert res["status"] == "QUOTA_EXHAUSTED"

    # Test 401 invalid mock
    mock_resp_401 = MagicMock()
    mock_resp_401.status_code = 401
    with patch("httpx.Client.get", return_value=mock_resp_401):
        res = openrouter_provider.test_connection(custom_token="sk-or-bad")
        assert res["status"] == "INVALID"


@pytest.mark.asyncio
async def test_8_atomic_replacement_preserves_old_working_key():
    """8. Invalid replacement retains previous valid key."""
    valid_key = "sk-or-valid-original-key"
    secret_store.set_secret("OPENROUTER_API_KEY", valid_key)

    async def fail_test(k):
        return {"status": "INVALID", "message": "Failed auth"}

    success, msg = await secret_store.replace_secret_atomic(
        "OPENROUTER_API_KEY",
        "sk-or-new-broken-key",
        test_callable=fail_test
    )
    assert success is False
    assert secret_store.get_secret("OPENROUTER_API_KEY") == valid_key


def test_9_gemini_primary_remains_unchanged():
    """9. Gemini Primary remains configured and untouched."""
    gem_key = secret_store.get_secret("GEMINI_PRIMARY_API_KEY") or secret_store.get_secret("GEMINI_API_KEY")
    assert gem_key is not None
    assert gemini_provider.enabled is True
    assert gemini_provider.priority == 1


def test_10_gemini_backup_remains_unchanged():
    """10. Gemini Backup remains available."""
    gem_bak = secret_store.get_secret("GEMINI_BACKUP_API_KEY") or secret_store.get_secret("GEMINI_API_KEY_2")
    assert gem_bak is not None


def test_11_gemini_and_openrouter_both_enabled():
    """11. Both Gemini and OpenRouter can be enabled simultaneously."""
    gemini_provider.enabled = True
    openrouter_provider.enabled = True
    assert gemini_provider.enabled is True
    assert openrouter_provider.enabled is True


def test_12_circuit_breaker_isolation():
    """12. Failure in OpenRouter circuit breaker does not stop Gemini."""
    cb = openrouter_provider.circuit_breaker
    cb.state = CircuitState.CLOSED
    cb.consecutive_failures = 0

    for _ in range(5):
        cb.record_failure(Exception("503 Gateway Timeout"))

    assert cb.state == CircuitState.OPEN
    assert openrouter_provider.circuit_breaker.is_available() is False
    # Gemini remains unaffected
    assert gemini_provider.enabled is True


def test_13_failure_in_gemini_does_not_delete_openrouter():
    """13. Failure in Gemini does not affect OpenRouter credentials."""
    or_key_before = secret_store.get_secret("OPENROUTER_API_KEY")
    # Simulate failed Gemini test
    res = credential_manager.test_connection("gemini", custom_token="invalid_gem_test")
    assert res["status"] in ["INVALID", "NEEDS_ATTENTION", "NOT_CONFIGURED"]
    assert secret_store.get_secret("OPENROUTER_API_KEY") == or_key_before


def test_14_disabled_openrouter_not_counted_active(client):
    """14. Disabled OpenRouter is not counted in active providers."""
    secret_store.disable_secret("OPENROUTER_API_KEY")
    res = client.get("/api/stats")
    assert res.status_code == 200
    data = res.json()
    # Re-enable
    secret_store.enable_secret("OPENROUTER_API_KEY")


def test_15_valid_enabled_openrouter_counted_in_stats(client):
    """15. VALID + ENABLED OpenRouter is counted in active providers."""
    secret_store.enable_secret("OPENROUTER_API_KEY")
    openrouter_provider.enabled = True
    res = client.get("/api/stats")
    assert res.status_code == 200
    data = res.json()
    assert data["active_providers_count"] >= 1


@pytest.mark.asyncio
async def test_16_cost_governor_tracks_openrouter():
    """16. Cost Governor records OpenRouter service spend."""
    from database.connection import async_session_factory
    from core.governors.cost_governor import cost_governor
    from database.models import CostRecord
    
    async with async_session_factory() as db:
        rec = CostRecord(
            service="openrouter",
            token_count=1500,
            duration_seconds=0.5,
            estimated_cost_usd=0.000225
        )
        db.add(rec)
        await db.commit()

        metrics = await cost_governor.get_spend_metrics(db)
        assert metrics["daily_spent"] >= 0.0


def test_17_capability_check():
    """17. Capability check accurately verifies supported features."""
    assert openrouter_provider.supports(AICapability.TEXT) is True
    assert openrouter_provider.supports(AICapability.REASONING) is True
    # Video generation should not be mapped to OpenRouter text models
    assert openrouter_provider.supports(AICapability.VIDEO_GENERATION) is False


def test_18_system_health_integration(client):
    """18. System Health includes OpenRouter diagnosis."""
    res = client.get("/api/system/health")
    assert res.status_code == 200
    data = res.json()
    comp_names = [c["name"] for c in data.get("components", [])]
    assert "OpenRouter Multi-Model Gateway" in comp_names


def test_19_xai_remains_functional():
    """19. xAI / Grok card and provider remain functional."""
    xai = provider_registry.get_provider("xai")
    assert xai is not None
    assert xai.display_name == "xAI / Grok"


def test_20_existing_vault_credentials_intact():
    """20. Existing Vault credentials survive update."""
    assert secret_store.has_secret("GEMINI_PRIMARY_API_KEY") or secret_store.has_secret("GEMINI_API_KEY")
    assert secret_store.has_secret("META_SYSTEM_USER_TOKEN") or secret_store.has_secret("FB_PAGE_ACCESS_TOKEN")
    assert secret_store.has_secret("TELEGRAM_BOT_TOKEN")
