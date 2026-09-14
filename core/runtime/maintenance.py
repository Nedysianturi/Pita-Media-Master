"""
Pita Media Autonomous Runtime - System Maintenance
Handles log rotation, storage retention cleanup, and automated SQLite DB backups.
"""

import os
import shutil
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from typing import Dict, Any, List

logger = logging.getLogger("pita.runtime.maintenance")

def setup_rotating_logger(log_dir: str = "storage/logs", log_file: str = "pita_media.log", level: int = logging.INFO) -> logging.Logger:
    """Configures root/pita logging with RotatingFileHandler (10MB limit, 5 backups)."""
    os.makedirs(log_dir, exist_ok=True)
    full_path = os.path.join(log_dir, log_file)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid duplicate handlers
    for handler in root_logger.handlers[:]:
        if isinstance(handler, RotatingFileHandler):
            return root_logger

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Rotating File Handler: 10 MB per file, max 5 backup files
    file_handler = RotatingFileHandler(
        full_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root_logger.addHandler(file_handler)

    # Console stream handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)

    return root_logger

class StorageMaintenance:
    def __init__(self, storage_dir: str = "storage", db_path: str = "storage/pita_media.db"):
        self.storage_dir = storage_dir
        self.db_path = db_path
        self.backup_dir = os.path.join(storage_dir, "backups")
        os.makedirs(self.backup_dir, exist_ok=True)

    def backup_database(self) -> str:
        """Creates a timestamped snapshot backup of the SQLite database."""
        if not os.path.exists(self.db_path):
            logger.warning(f"Database file {self.db_path} does not exist. Skipping backup.")
            return ""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"pita_media_backup_{timestamp}.db"
        backup_dest = os.path.join(self.backup_dir, backup_filename)

        try:
            # Use SQLite backup API for online safe copy
            src_conn = sqlite3.connect(self.db_path)
            dst_conn = sqlite3.connect(backup_dest)
            with dst_conn:
                src_conn.backup(dst_conn)
            src_conn.close()
            dst_conn.close()
            logger.info(f"Database successfully backed up to {backup_dest}")
            self._prune_old_backups(keep_count=7)
            return backup_dest
        except Exception as e:
            logger.error(f"Failed to backup database: {e}")
            return ""

    def restore_database(self, backup_file: str) -> bool:
        """Restores database from a specified backup file."""
        if not os.path.exists(backup_file):
            logger.error(f"Backup file not found: {backup_file}")
            return False

        try:
            shutil.copy2(backup_file, self.db_path)
            logger.info(f"Database successfully restored from {backup_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to restore database: {e}")
            return False

    def _prune_old_backups(self, keep_count: int = 7):
        """Keep only the latest N database backups."""
        try:
            backups = [
                os.path.join(self.backup_dir, f) for f in os.listdir(self.backup_dir)
                if f.startswith("pita_media_backup_") and f.endswith(".db")
            ]
            backups.sort(key=os.path.getmtime, reverse=True)
            for old in backups[keep_count:]:
                os.remove(old)
                logger.debug(f"Removed old backup: {old}")
        except Exception as e:
            logger.warning(f"Failed pruning old backups: {e}")

    def cleanup_old_temp_files(self, days: int = 14) -> Dict[str, Any]:
        """Cleans up temporary generated media files older than retention days while preserving referenced files."""
        deleted_count = 0
        deleted_bytes = 0
        cutoff_time = datetime.now() - timedelta(days=days)

        temp_dirs = [
            os.path.join(self.storage_dir, "temp"),
            os.path.join(self.storage_dir, "preview"),
            os.path.join(self.storage_dir, "cache")
        ]

        for tdir in temp_dirs:
            if not os.path.exists(tdir):
                continue
            for root, _, files in os.walk(tdir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                        if mtime < cutoff_time:
                            size = os.path.getsize(fpath)
                            os.remove(fpath)
                            deleted_count += 1
                            deleted_bytes += size
                    except Exception as e:
                        logger.warning(f"Could not remove temporary file {fpath}: {e}")

        logger.info(f"Storage cleanup completed: {deleted_count} files removed ({deleted_bytes / (1024*1024):.2f} MB).")
        return {
            "deleted_count": deleted_count,
            "deleted_mb": round(deleted_bytes / (1024 * 1024), 2)
        }
