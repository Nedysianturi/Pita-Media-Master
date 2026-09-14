"""
Storage Guard & Disk Health Monitor for Pita Media.
Monitors local disk capacity, warns on high usage (>80%),
and triggers safe cleanup of temporary artifacts/renders when usage exceeds critical thresholds (>90%).
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("pita_media.runtime.storage_guard")


class StorageGuard:
    """
    Guards workspace storage against disk-full crashes.
    """

    WARNING_THRESHOLD_PERCENT = 80.0
    CRITICAL_THRESHOLD_PERCENT = 90.0

    def __init__(self, target_dir: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.target_dir = target_dir or str(base_dir)

    def check_disk_usage(self) -> Dict[str, Any]:
        """Inspect disk space for the target storage volume."""
        try:
            total, used, free = shutil.disk_usage(self.target_dir)
            used_percent = (used / total) * 100.0

            status = "HEALTHY"
            if used_percent >= self.CRITICAL_THRESHOLD_PERCENT:
                status = "CRITICAL"
            elif used_percent >= self.WARNING_THRESHOLD_PERCENT:
                status = "WARNING"

            return {
                "status": status,
                "target_directory": self.target_dir,
                "total_gb": round(total / (1024**3), 2),
                "used_gb": round(used / (1024**3), 2),
                "free_gb": round(free / (1024**3), 2),
                "used_percent": round(used_percent, 2),
                "warning_threshold": self.WARNING_THRESHOLD_PERCENT,
                "critical_threshold": self.CRITICAL_THRESHOLD_PERCENT
            }
        except Exception as e:
            logger.error(f"Failed to check disk usage: {e}")
            return {"status": "ERROR", "error": str(e)}

    def cleanup_temporary_files(self) -> Dict[str, Any]:
        """
        Safely clears temporary render caches and staging artifacts
        without touching raw assets or databases.
        """
        base_path = Path(self.target_dir)
        temp_dirs = [
            base_path / "storage" / "temp",
            base_path / "storage" / "staging",
            base_path / "temp"
        ]

        deleted_count = 0
        freed_bytes = 0

        for t_dir in temp_dirs:
            if t_dir.exists() and t_dir.is_dir():
                for item in t_dir.glob("*"):
                    try:
                        if item.is_file():
                            freed_bytes += item.stat().st_size
                            item.unlink()
                            deleted_count += 1
                        elif item.is_dir():
                            shutil.rmtree(item)
                            deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to clean temp item {item}: {e}")

        freed_mb = round(freed_bytes / (1024**2), 2)
        logger.info(f"StorageGuard cleanup completed: {deleted_count} files removed ({freed_mb} MB freed).")

        return {
            "deleted_count": deleted_count,
            "freed_mb": freed_mb,
            "cleaned_dirs": [str(d) for d in temp_dirs]
        }


storage_guard = StorageGuard()
