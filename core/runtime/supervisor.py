"""
Pita Media Autonomous Runtime - Daemon Supervisor
Main background process manager: handles single-instance locking, startup self-check,
graceful signal handling, concurrent services (Scheduler, Telegram C2, Health Monitor),
and crash recovery.
"""

import os
import sys
import time
import signal
import asyncio
import logging
from typing import Optional

from core.runtime.instance_lock import instance_lock
from core.runtime.self_check import run_startup_self_check, format_self_check_cli
from core.runtime.maintenance import setup_rotating_logger, StorageMaintenance
from core.runtime.health_monitor import CredentialHealthMonitor
from core.runtime.persistent_scheduler import persistent_scheduler
from monitoring.telegram_bot import telegram_c2

from core.runtime.control_bus import control_bus
from core.security.credential_manager import credential_manager

logger = logging.getLogger("pita.runtime.supervisor")

class DaemonSupervisor:
    def __init__(self):
        self.is_running = False
        self.health_monitor = CredentialHealthMonitor(telegram_notifier=telegram_c2)
        self.storage_maint = StorageMaintenance()
        self._tasks = []

    def handle_shutdown_signal(self, signum, frame):
        """Signal handler for graceful Windows shutdown (SIGINT, SIGTERM, SIGBREAK)."""
        logger.info(f"Received termination signal ({signum}). Initiating graceful shutdown...")
        self.is_running = False
        control_bus.set_worker_stopped()
        persistent_scheduler.stop()
        telegram_c2.stop_polling()
        instance_lock.release()
        logger.info("Pita Media daemon shutdown complete.")
        sys.exit(0)

    async def _health_monitor_loop(self):
        """Periodic background health monitor loop (runs every 60 minutes)."""
        logger.info("Health monitor background loop started.")
        while self.is_running:
            try:
                res = self.health_monitor.run_health_cycle()
                if not res.get("all_healthy"):
                    logger.warning(f"Health monitor warning: {res}")
            except Exception as e:
                logger.error(f"Error in health monitor cycle: {e}")
            await asyncio.sleep(3600)

    async def _maintenance_loop(self):
        """Daily maintenance loop: storage retention cleanup and DB backup."""
        logger.info("Maintenance background loop started.")
        while self.is_running:
            try:
                # Run cleanup & backup every 24 hours
                await asyncio.sleep(86400)
                logger.info("Running scheduled daily maintenance...")
                self.storage_maint.backup_database()
                self.storage_maint.cleanup_old_temp_files(days=14)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in maintenance loop: {e}")

    async def start(self, skip_self_check: bool = False):
        """Main startup sequence for Autonomous Daemon."""
        # 1. Setup rotating logging
        setup_rotating_logger(log_dir="storage/logs", log_file="pita_media.log")
        logger.info("=" * 60)
        logger.info("Starting Pita Media Autonomous Background Daemon...")
        logger.info("=" * 60)

        # 2. Acquire single-instance lock
        if not instance_lock.acquire():
            logger.error("Another Pita Media daemon instance is already active. Exiting.")
            sys.exit(1)

        # 3. Register OS signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self.handle_shutdown_signal)
            except Exception:
                pass
        if hasattr(signal, "SIGBREAK"):
            try:
                signal.signal(signal.SIGBREAK, self.handle_shutdown_signal)
            except Exception:
                pass

        # 4. Run Self-Check
        if not skip_self_check:
            logger.info("Executing startup subsystem self-check...")
            check_results = run_startup_self_check()
            logger.info(f"Self-check summary: {check_results['summary']}")
            if check_results["summary"]["failed"] > 0:
                logger.critical("Startup self-check encountered fatal failures. Aborting startup.")
                instance_lock.release()
                sys.exit(1)

        # 4.5. Restore and verify persisted operating mode (DRY_RUN vs PRODUCTION)
        persisted_mode = os.environ.get("APP_MODE") or credential_manager.read_env_file().get("APP_MODE", "DRY_RUN")
        if str(persisted_mode).upper() == "PRODUCTION":
            from core.security.secret_store import secret_store
            from config.settings import settings
            from core.runtime.storage_guard import storage_guard
            
            env_data = credential_manager.read_env_file()
            meta_tok = bool(secret_store.get_secret("META_SYSTEM_USER_TOKEN"))
            fb_page_id = bool(env_data.get("FB_PAGE_ID") or getattr(settings, "FB_PAGE_ID", ""))
            ai_key = bool(secret_store.get_secret("GEMINI_PRIMARY_API_KEY") or secret_store.get_secret("XAI_API_KEY"))
            disk = storage_guard.check_disk_usage()
            
            preflight_passed = meta_tok and fb_page_id and ai_key and (not secret_store.is_safe_mode) and (disk.get("status") != "CRITICAL")
            if preflight_passed:
                os.environ["APP_MODE"] = "PRODUCTION"
                logger.info("Persisted APP_MODE is PRODUCTION and preflight PASSED. Resuming 24/7 PRODUCTION mode automatically.")
            else:
                os.environ["APP_MODE"] = "DRY_RUN"
                reasons = []
                if not meta_tok: reasons.append("META_SYSTEM_USER_TOKEN missing")
                if not fb_page_id: reasons.append("FB_PAGE_ID missing")
                if not ai_key: reasons.append("AI key missing")
                if secret_store.is_safe_mode: reasons.append("Vault in safe mode")
                if disk.get("status") == "CRITICAL": reasons.append("Disk critical")
                logger.warning(f"Persisted APP_MODE was PRODUCTION but preflight failed ({', '.join(reasons)}). Entering safe state (DRY_RUN).")
        else:
            os.environ["APP_MODE"] = "DRY_RUN"
            logger.info("Operating mode initialized as DRY_RUN (Simulated Sandbox).")

        # 5. Take initial database backup
        self.storage_maint.backup_database()

        # 6. Mark running and spawn concurrent background tasks
        self.is_running = True
        control_bus.set_worker_running(os.getpid())

        # Send Telegram startup notification
        try:
            startup_msg = (
                "🚀 *PITA MEDIA AUTONOMOUS DAEMON STARTED*\n"
                "═══════════════════════════\n"
                "• Mode: 24/7 Autonomous Windows Background\n"
                "• Web Dashboard: http://pitamedia.localhost\n"
                "• Scheduler: Persistent SQLite WAL Queue\n"
                "• Health Monitor: Active (Hourly Check)\n"
                "• Telegram C2: Online & Listening\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "💡 _Ketik /status atau /help untuk kontrol._"
            )
            await telegram_c2.send_raw_message(startup_msg)
        except Exception as e:
            logger.warning(f"Could not send Telegram startup notification: {e}")

        # Gather main async workers
        tasks = [
            asyncio.create_task(persistent_scheduler.run_loop(), name="PersistentScheduler"),
            asyncio.create_task(telegram_c2.start_polling(), name="TelegramC2"),
            asyncio.create_task(self._health_monitor_loop(), name="HealthMonitor"),
            asyncio.create_task(self._maintenance_loop(), name="MaintenanceLoop")
        ]

        logger.info("All autonomous runtime services successfully launched.")

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled. Shutting down supervisor...")
        except Exception as e:
            logger.critical(f"Unhandled exception in supervisor task pool: {e}", exc_info=True)
        finally:
            control_bus.set_worker_stopped()
            instance_lock.release()
            logger.info("Daemon supervisor terminated.")

def run_daemon():
    """CLI entry point for running the daemon with crash auto-restart."""
    supervisor = DaemonSupervisor()
    max_retries = 5
    retry_count = 0

    while retry_count < max_retries:
        try:
            asyncio.run(supervisor.start())
            break
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received. Exiting.")
            break
        except Exception as e:
            retry_count += 1
            wait_time = min(60, 2 ** retry_count)
            logger.error(f"Daemon crashed (Attempt {retry_count}/{max_retries}): {e}. Restarting in {wait_time}s...")
            time.sleep(wait_time)
