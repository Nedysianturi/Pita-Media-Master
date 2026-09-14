"""
Pita Media Autonomous Runtime - Credential Health Monitor
Monitors Meta tokens, Gemini API quotas, and Telegram connectivity.
Detects expiring/invalid tokens, sends proactive alerts with renewal instructions.
"""

import os
import time
import logging
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger("pita.runtime.health_monitor")

class CredentialHealthMonitor:
    def __init__(self, telegram_notifier=None):
        self.telegram = telegram_notifier
        self.last_check_time = 0
        self.check_interval_seconds = 3600  # Check every hour
        self.meta_token_status = {"valid": True, "expires_at": None, "warning_sent": False}
        self.gemini_status = {"valid": True, "consecutive_errors": 0}

    def check_meta_token_health(self) -> Dict[str, Any]:
        """
        Inspect Meta Access Token validity and expiration using debug_token or /me.
        Does not crash if offline or if token has issues.
        """
        token = os.getenv("META_PAGE_ACCESS_TOKEN") or os.getenv("META_ACCESS_TOKEN")
        if not token:
            return {"status": "MISSING", "message": "Meta Access Token is not set."}

        try:
            # We check token validity by calling /me?fields=id,name
            resp = requests.get(
                "https://graph.facebook.com/v19.0/me",
                params={"access_token": token, "fields": "id,name"},
                timeout=10
            )
            data = resp.json()

            if resp.status_code == 200:
                self.meta_token_status["valid"] = True
                self.meta_token_status["warning_sent"] = False
                return {
                    "status": "PASS",
                    "page_id": data.get("id"),
                    "page_name": data.get("name"),
                    "message": f"Token valid for '{data.get('name')}' (ID: {data.get('id')})"
                }
            else:
                error = data.get("error", {})
                code = error.get("code")
                error_subcode = error.get("error_subcode")
                msg = error.get("message", "Unknown Meta error")

                is_expired = code in (190, 102) or error_subcode in (463, 467)
                self.meta_token_status["valid"] = False

                if is_expired:
                    self._send_meta_expiration_alert(msg)

                return {
                    "status": "EXPIRED" if is_expired else "FAIL",
                    "code": code,
                    "subcode": error_subcode,
                    "message": msg
                }
        except Exception as e:
            logger.warning(f"Meta token health check network error: {e}")
            return {"status": "WARNING", "message": f"Network error checking Meta token: {str(e)}"}

    def _send_meta_expiration_alert(self, error_msg: str):
        """Sends actionable Telegram guide for Meta token renewal without crashing worker."""
        if self.meta_token_status.get("warning_sent"):
            return

        if self.telegram:
            msg = (
                "⚠️ *PITA MEDIA: META TOKEN EXPIRED / INVALID*\n\n"
                f"Detail: `{error_msg}`\n\n"
                "📌 *Instruksi Pembaruan Token:*\n"
                "1. Buka Meta for Developers > Graph API Explorer\n"
                "2. Generate Page Access Token baru dengan permission: `pages_manage_posts`, `pages_read_engagement`\n"
                "3. Buka file `.env` di folder Pita Media dan perbarui `META_PAGE_ACCESS_TOKEN`\n"
                "4. Kirim `/resume` di bot Telegram ini atau restart daemon.\n\n"
                "ℹ️ _Job publishing saat ini diubah statusnya menjadi WAITING agar tidak gagal total._"
            )
            try:
                self.telegram.send_alert(msg)
                self.meta_token_status["warning_sent"] = True
            except Exception as e:
                logger.error(f"Failed to send Telegram alert: {e}")

    def check_gemini_health(self) -> Dict[str, Any]:
        """Verify Gemini API availability."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"status": "MISSING", "message": "GEMINI_API_KEY is missing."}

        model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}?key={api_key}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                self.gemini_status["valid"] = True
                self.gemini_status["consecutive_errors"] = 0
                return {"status": "PASS", "message": f"Model {model} available"}
            elif resp.status_code == 429:
                return {"status": "RATE_LIMITED", "message": "Gemini rate limited (429). Will rotate model pool."}
            else:
                return {"status": "FAIL", "message": f"Gemini API returned status {resp.status_code}"}
        except Exception as e:
            return {"status": "WARNING", "message": f"Gemini check network error: {str(e)}"}

    def run_health_cycle(self) -> Dict[str, Any]:
        """Runs complete periodic health check cycle."""
        self.last_check_time = time.time()
        meta_res = self.check_meta_token_health()
        gemini_res = self.check_gemini_health()

        return {
            "timestamp": self.last_check_time,
            "meta": meta_res,
            "gemini": gemini_res,
            "all_healthy": meta_res.get("status") == "PASS" and gemini_res.get("status") in ("PASS", "RATE_LIMITED")
        }
