"""
Pita Media Enterprise Security - Token Health Manager
Monitors multi-service token health with granular failure isolation.
Ensures failure in one service (e.g. Meta) does not crash independent systems (Gemini/Telegram).
"""

import time
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone

from core.security.credential_manager import credential_manager
from monitoring.telegram_bot import telegram_c2

logger = logging.getLogger("pita.security.token_health")

class TokenHealthManager:
    def __init__(self, telegram_notifier=None):
        self.telegram = telegram_notifier or telegram_c2
        self.credential_mgr = credential_manager
        self.last_check_timestamp = 0
        self.service_health_cache: Dict[str, Dict[str, Any]] = {}

    def audit_service_health(self, service_name: str) -> Dict[str, Any]:
        """Audits the health status of a specific service."""
        res = self.credential_mgr.test_connection(service_name)
        self.service_health_cache[service_name] = res
        return res

    def run_full_health_audit(self) -> Dict[str, Any]:
        """Alias helper for comprehensive health check."""
        audit = self.run_comprehensive_health_audit()
        return audit.get("services", {})

    def run_comprehensive_health_audit(self) -> Dict[str, Any]:
        """Runs live connection and token validity checks across all configured providers."""
        self.last_check_timestamp = time.time()
        providers = ["google", "xai", "facebook", "instagram", "threads", "telegram"]
        results = {}
        all_passed = True

        for p in providers:
            res = self.credential_mgr.test_connection(p)
            results[p] = res
            self.service_health_cache[p] = res

            if res.get("status") in ["INVALID", "EXPIRED", "MISSING_PERMISSION"]:
                all_passed = False
                logger.warning(f"Service health failure on [{p.upper()}]: {res.get('message')}")
                self._handle_isolated_service_failure(p, res)

        return {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "all_healthy": all_passed,
            "services": results
        }

    def _handle_isolated_service_failure(self, service_name: str, test_result: Dict[str, Any]):
        """Isolates failure: alerts via Telegram and keeps other systems active."""
        msg = (
            f"⚠️ *PITA MEDIA: SERVICE ALERT [{service_name.upper()}]*\n"
            f"═══════════════════════════\n"
            f"• Status: *{test_result.get('status')}*\n"
            f"• Detail: `{test_result.get('message')}`\n\n"
            f"📌 *Tindakan Otomatis Sistem:*\n"
            f"- Job yang bergantung pada {service_name.upper()} dialihkan ke status `WAITING`.\n"
            f"- Layanan lain (AI Creator / Telegram / Database) tetap BERJALAN NORMAL.\n"
            f"- Buka Dashboard di `http://pitamedia.localhost` > *Credentials* untuk memperbarui token."
        )
        try:
            self.telegram.send_alert(msg)
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    def is_service_healthy(self, service_name: str) -> bool:
        """Checks if a specific service is currently marked healthy."""
        pname = service_name.lower().strip()
        cached = self.service_health_cache.get(pname)
        if cached:
            return cached.get("status") in ["VALID", "RATE_LIMITED", "NOT_CONFIGURED"]
        # If not cached, run quick test
        res = self.credential_mgr.test_connection(pname)
        self.service_health_cache[pname] = res
        return res.get("status") in ["VALID", "RATE_LIMITED", "NOT_CONFIGURED"]

token_health_manager = TokenHealthManager()
