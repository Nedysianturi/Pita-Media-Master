"""
Config Versioning & Audit History for Pita Media.
Maintains an immutable snapshot history of system configuration changes,
allowing rollback, diff inspection, and change accountability.
"""

import os
import json
import time
import hashlib
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger("pita_media.runtime.config_versioning")


class ConfigVersioning:
    """
    Manages versioned snapshots of configuration files in storage/config_history.
    """

    def __init__(self, history_dir: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.history_dir = Path(history_dir or (base_dir / "storage" / "config_history"))
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.history_dir / "versions_index.json"
        self._init_index()

    def _init_index(self):
        if not self.index_file.exists():
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump([], f)

    def record_snapshot(self, config_name: str, config_data: Dict[str, Any], changed_by: str = "SYSTEM", reason: str = "") -> str:
        """Save a new config version snapshot."""
        timestamp = int(time.time())
        serialized = json.dumps(config_data, indent=2, sort_keys=True)
        content_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
        version_id = f"{config_name}_{timestamp}_{content_hash}"
        version_file = self.history_dir / f"{version_id}.json"

        with open(version_file, "w", encoding="utf-8") as f:
            f.write(serialized)

        entry = {
            "version_id": version_id,
            "config_name": config_name,
            "timestamp": timestamp,
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp)),
            "changed_by": changed_by,
            "reason": reason,
            "hash": content_hash,
            "file_path": str(version_file)
        }

        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                history = json.load(f)
            history.insert(0, entry)  # Prepend newest
            # Keep last 100 entries
            history = history[:100]
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to update config history index: {e}")

        logger.info(f"Recorded config version: {version_id} ({config_name})")
        return version_id

    def list_versions(self, config_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """List version history, optionally filtered by config name."""
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                history = json.load(f)
                if config_name:
                    return [h for h in history if h.get("config_name") == config_name]
                return history
        except Exception as e:
            logger.error(f"Failed to read version index: {e}")
            return []


config_versioning = ConfigVersioning()
