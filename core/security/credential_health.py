"""
Pita Media Enterprise Security - Credential Health & Expiration Engine.
Monitors token status, expiry thresholds, and capability permissions without leaking secrets.
Differentiates between rate limits / quota exhaustion and truly invalid credentials.
"""

import os
import time
import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum

from core.security.secret_store import secret_store

logger = logging.getLogger("pita.security.credential_health")


class CredentialStatus(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    VALID = "VALID"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    EXPIRING_SOON = "EXPIRING_SOON"
    MISSING_PERMISSION = "MISSING_PERMISSION"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    DISABLED = "DISABLED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    UNKNOWN = "UNKNOWN"
    VALID_EXPIRY_UNKNOWN = "VALID_EXPIRY_UNKNOWN"

CredentialHealthStatus = CredentialStatus



class CredentialHealthEngine:
    def __init__(self):
        self.secret_store = secret_store
        self._health_cache: Dict[str, Dict[str, Any]] = {}

    def get_expiry_status(self, expires_at_iso: Optional[str]) -> Tuple[CredentialStatus, Optional[int]]:
        """Evaluates expiry status and days remaining."""
        if not expires_at_iso or expires_at_iso in ["-", "never", "unknown"]:
            return CredentialStatus.VALID_EXPIRY_UNKNOWN, None

        try:
            exp_dt = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00"))
            now_dt = datetime.now(timezone.utc)
            delta = exp_dt - now_dt
            days_left = delta.days

            if days_left < 0:
                return CredentialStatus.EXPIRED, days_left
            elif days_left <= 7:
                return CredentialStatus.EXPIRING_SOON, days_left
            elif days_left <= 30:
                return CredentialStatus.NEEDS_ATTENTION, days_left
            else:
                return CredentialStatus.VALID, days_left
        except Exception:
            return CredentialStatus.VALID_EXPIRY_UNKNOWN, None

    async def test_gemini_credential(self, api_key: Optional[str] = None) -> Dict[str, Any]:
        """Tests Google Gemini API Key validity without logging key."""
        key = api_key or self.secret_store.get_secret("GEMINI_PRIMARY_API_KEY") or self.secret_store.get_secret("GEMINI_API_KEY")
        if not key:
            return {"status": CredentialStatus.NOT_CONFIGURED.value, "message": "Gemini API Key belum diatur."}

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        start_t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url)
                latency = int((time.perf_counter() - start_t) * 1000)

                if res.status_code == 200:
                    return {
                        "status": CredentialStatus.VALID.value,
                        "latency_ms": latency,
                        "message": f"Koneksi Google Gemini API aktif ({latency}ms).",
                        "provider": "gemini"
                    }
                elif res.status_code == 429:
                    return {
                        "status": CredentialStatus.RATE_LIMITED.value,
                        "latency_ms": latency,
                        "message": "Batas laju permintaan (Rate Limit) tercapai. Kredensial tetap valid.",
                        "provider": "gemini"
                    }
                elif res.status_code in [400, 403]:
                    err_msg = res.json().get("error", {}).get("message", "API Key Invalid")
                    return {
                        "status": CredentialStatus.INVALID.value,
                        "latency_ms": latency,
                        "message": f"Kredensial ditolak oleh Google: {err_msg}",
                        "provider": "gemini"
                    }
                else:
                    return {
                        "status": CredentialStatus.UNKNOWN.value,
                        "latency_ms": latency,
                        "message": f"Respon HTTP {res.status_code} dari Google API.",
                        "provider": "gemini"
                    }
        except Exception as e:
            return {
                "status": CredentialStatus.UNKNOWN.value,
                "message": f"Gagal menghubungi server Gemini: {str(e)}",
                "provider": "gemini"
            }

    async def test_meta_credential(self, token: Optional[str] = None, page_id: Optional[str] = None) -> Dict[str, Any]:
        """Tests Meta Facebook / Instagram Access Token."""
        tok = token or self.secret_store.get_secret("META_SYSTEM_USER_TOKEN") or self.secret_store.get_secret("FB_PAGE_ACCESS_TOKEN")
        pid = page_id or os.environ.get("FB_PAGE_ID", "") or self.secret_store.get_secret("FB_PAGE_ID")
        
        if not tok:
            return {"status": CredentialStatus.NOT_CONFIGURED.value, "message": "Meta Access Token belum diatur."}

        # 1. Test token via debug_token or /me
        url = f"https://graph.facebook.com/v26.0/me?fields=id,name&access_token={tok}"
        start_t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url)
                latency = int((time.perf_counter() - start_t) * 1000)

                if res.status_code == 200:
                    d = res.json()
                    name = d.get("name", "Meta Account")
                    
                    # Check Page access if page_id configured
                    page_accessible = True
                    if pid:
                        page_url = f"https://graph.facebook.com/v26.0/{pid}?fields=id,name&access_token={tok}"
                        p_res = await client.get(page_url)
                        if p_res.status_code != 200:
                            page_accessible = False

                    return {
                        "status": CredentialStatus.VALID.value if page_accessible else CredentialStatus.MISSING_PERMISSION.value,
                        "latency_ms": latency,
                        "account_name": name,
                        "page_accessible": page_accessible,
                        "message": f"Meta token valid untuk {name}." if page_accessible else f"Token valid tapi tidak memiliki akses ke Page ID {pid}."
                    }
                elif res.status_code == 400:
                    err_obj = res.json().get("error", {})
                    err_code = err_obj.get("code")
                    err_msg = err_obj.get("message", "")
                    
                    if "Session has expired" in err_msg or "expired" in err_msg.lower() or err_code == 190:
                        return {
                            "status": CredentialStatus.EXPIRED.value,
                            "latency_ms": latency,
                            "message": "Sesi token Meta telah kedaluwarsa. Silakan perbarui Page Access Token."
                        }
                    return {
                        "status": CredentialStatus.INVALID.value,
                        "latency_ms": latency,
                        "message": f"Token Meta tidak valid: {err_msg}"
                    }
                elif res.status_code == 429:
                    return {
                        "status": CredentialStatus.RATE_LIMITED.value,
                        "latency_ms": latency,
                        "message": "Meta Graph API Rate Limit tercapai."
                    }
                else:
                    return {
                        "status": CredentialStatus.UNKNOWN.value,
                        "latency_ms": latency,
                        "message": f"Meta API HTTP {res.status_code}"
                    }
        except Exception as e:
            return {
                "status": CredentialStatus.UNKNOWN.value,
                "message": f"Gagal menghubungi Meta API: {str(e)}"
            }

    async def test_telegram_credential(self, bot_token: Optional[str] = None) -> Dict[str, Any]:
        """Tests Telegram Bot Token validity."""
        tok = bot_token or self.secret_store.get_secret("TELEGRAM_BOT_TOKEN")
        if not tok:
            return {"status": CredentialStatus.NOT_CONFIGURED.value, "message": "Telegram Bot Token belum diatur."}

        url = f"https://api.telegram.org/bot{tok}/getMe"
        start_t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url)
                latency = int((time.perf_counter() - start_t) * 1000)

                if res.status_code == 200:
                    d = res.json()
                    bot_user = d.get("result", {}).get("username", "bot")
                    return {
                        "status": CredentialStatus.VALID.value,
                        "latency_ms": latency,
                        "bot_username": f"@{bot_user}",
                        "message": f"Bot Telegram terhubung: @{bot_user}."
                    }
                elif res.status_code == 401 or res.status_code == 404:
                    return {
                        "status": CredentialStatus.INVALID.value,
                        "latency_ms": latency,
                        "message": "Token Bot Telegram tidak valid atau tidak ditemukan."
                    }
                else:
                    return {
                        "status": CredentialStatus.UNKNOWN.value,
                        "latency_ms": latency,
                        "message": f"Telegram API HTTP {res.status_code}"
                    }
        except Exception as e:
            return {
                "status": CredentialStatus.UNKNOWN.value,
                "message": f"Gagal menghubungi Telegram API: {str(e)}"
            }

    async def test_openrouter_credential(self, api_key: Optional[str] = None) -> Dict[str, Any]:
        """Tests OpenRouter API Key validity without logging key."""
        key = api_key or self.secret_store.get_secret("OPENROUTER_API_KEY")
        if not key:
            return {"status": CredentialStatus.NOT_CONFIGURED.value, "message": "OPENROUTER_API_KEY belum diatur di Vault."}

        url = "https://openrouter.ai/api/v1/models"
        headers = {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://pitamedia.localhost",
            "X-Title": "Pita Media Health Check"
        }
        start_t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(url, headers=headers)
                latency = int((time.perf_counter() - start_t) * 1000)

                if res.status_code == 200:
                    d = res.json()
                    models_count = len(d.get("data", []))
                    return {
                        "status": CredentialStatus.VALID.value,
                        "latency_ms": latency,
                        "message": f"Koneksi OpenRouter aktif ({models_count} model tersedia).",
                        "provider": "openrouter"
                    }
                elif res.status_code == 429:
                    return {
                        "status": CredentialStatus.RATE_LIMITED.value,
                        "latency_ms": latency,
                        "message": "OpenRouter terhubung (Rate limit sementara/429).",
                        "provider": "openrouter"
                    }
                elif res.status_code == 402:
                    return {
                        "status": CredentialStatus.QUOTA_EXHAUSTED.value,
                        "latency_ms": latency,
                        "message": "Saldo kredit OpenRouter tidak mencukupi (HTTP 402).",
                        "provider": "openrouter"
                    }
                elif res.status_code in [401, 403]:
                    return {
                        "status": CredentialStatus.INVALID.value,
                        "latency_ms": latency,
                        "message": "OPENROUTER_API_KEY tidak valid atau ditolak oleh OpenRouter.",
                        "provider": "openrouter"
                    }
                else:
                    return {
                        "status": CredentialStatus.UNKNOWN.value,
                        "latency_ms": latency,
                        "message": f"OpenRouter HTTP {res.status_code}",
                        "provider": "openrouter"
                    }
        except Exception as e:
            return {
                "status": CredentialStatus.UNKNOWN.value,
                "message": f"Gagal menghubungi server OpenRouter: {str(e)}",
                "provider": "openrouter"
            }

    async def run_comprehensive_credential_check(self) -> Dict[str, Any]:
        """Runs health tests across all configured credentials."""
        results = {}
        
        # 1. Gemini Primary & Backup
        gem_prim = await self.test_gemini_credential(self.secret_store.get_secret("GEMINI_PRIMARY_API_KEY") or self.secret_store.get_secret("GEMINI_API_KEY"))
        results["gemini_primary"] = gem_prim
        
        if self.secret_store.has_secret("GEMINI_BACKUP_API_KEY") or self.secret_store.has_secret("GEMINI_API_KEY_2"):
            gem_bak = await self.test_gemini_credential(self.secret_store.get_secret("GEMINI_BACKUP_API_KEY") or self.secret_store.get_secret("GEMINI_API_KEY_2"))
            results["gemini_backup"] = gem_bak
        else:
            results["gemini_backup"] = {"status": CredentialStatus.NOT_CONFIGURED.value, "message": "Key cadangan belum diatur."}

        # 2. OpenRouter
        if self.secret_store.has_secret("OPENROUTER_API_KEY"):
            results["openrouter"] = await self.test_openrouter_credential()
        else:
            results["openrouter"] = {"status": CredentialStatus.NOT_CONFIGURED.value, "message": "OPENROUTER_API_KEY belum diatur."}

        # 3. Meta
        results["meta"] = await self.test_meta_credential()

        # 4. Telegram
        results["telegram"] = await self.test_telegram_credential()

        # 5. Threads
        if self.secret_store.has_secret("THREADS_ACCESS_TOKEN"):
            results["threads"] = {"status": CredentialStatus.VALID_EXPIRY_UNKNOWN.value, "message": "Threads Token terkonfigurasi di Vault."}
        else:
            results["threads"] = {"status": CredentialStatus.NOT_CONFIGURED.value, "message": "Threads Token belum diatur."}

        # 6. Grok / xAI
        if self.secret_store.has_secret("XAI_API_KEY"):
            results["xai"] = {"status": CredentialStatus.VALID_EXPIRY_UNKNOWN.value, "message": "xAI API Key terkonfigurasi di Vault."}
        else:
            results["xai"] = {"status": CredentialStatus.NOT_CONFIGURED.value, "message": "xAI API Key belum diatur."}

        self._health_cache = results
        return results


credential_health_engine = CredentialHealthEngine()
