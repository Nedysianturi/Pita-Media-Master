"""
Pita Media Autonomous Runtime - Synchronized Control Bus
Manages IPC control state between Web Control Dashboard, Worker Daemon, and Telegram C2 Bot.
Provides atomic state updates, audit logging, and process lifecycle synchronization.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("pita.runtime.control_bus")

STATE_FILE = Path("storage/control_state.json")

class ControlBus:
    def __init__(self):
        self.state_file = STATE_FILE
        self._ensure_state_file()

    def _ensure_state_file(self):
        """Initializes control state file if it doesn't exist."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            initial_state = {
                "status": "STOPPED",  # RUNNING, PAUSED, STOPPED, WAITING_QUOTA, NEEDS_ATTENTION, ERROR, EMERGENCY_STOPPED
                "is_paused": False,
                "is_emergency_stopped": False,
                "last_command": "INITIALIZE",
                "command_source": "system",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "uptime_start": None,
                "active_job_id": None,
                "active_pilar": None,
                "worker_pid": None
            }
            self._write_raw(initial_state)

    def _read_raw(self) -> Dict[str, Any]:
        """Reads control state JSON."""
        try:
            if self.state_file.exists():
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed reading control state: {e}")
        return {
            "status": "STOPPED",
            "is_paused": False,
            "is_emergency_stopped": False,
            "last_command": "UNKNOWN",
            "command_source": "system",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "uptime_start": None,
            "active_job_id": None,
            "active_pilar": None,
            "worker_pid": None
        }

    def _write_raw(self, state: Dict[str, Any]):
        """Writes control state JSON atomically."""
        try:
            temp_file = self.state_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            temp_file.replace(self.state_file)
        except Exception as e:
            logger.error(f"Failed writing control state: {e}")

    def get_state(self) -> Dict[str, Any]:
        """Returns current system control state."""
        state = self._read_raw()
        # Verify if worker is actually alive if state says RUNNING
        lock_file = Path("storage/pita_media.lock")
        if not lock_file.exists() and state.get("status") in ["RUNNING", "PAUSED"]:
            state["status"] = "STOPPED"
            state["worker_pid"] = None
        return state

    def set_worker_running(self, pid: int):
        """Marks worker as active/running with PID."""
        state = self._read_raw()
        state["status"] = "RUNNING"
        state["is_paused"] = False
        state["is_emergency_stopped"] = False
        state["worker_pid"] = pid
        state["uptime_start"] = state.get("uptime_start") or datetime.now(timezone.utc).isoformat()
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_raw(state)

    def set_worker_stopped(self):
        """Marks worker as stopped."""
        state = self._read_raw()
        state["status"] = "STOPPED"
        state["worker_pid"] = None
        state["uptime_start"] = None
        state["active_job_id"] = None
        state["active_pilar"] = None
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_raw(state)

    def set_active_job(self, job_id: Optional[str], pilar: Optional[str]):
        """Updates active running job information."""
        state = self._read_raw()
        state["active_job_id"] = job_id
        state["active_pilar"] = pilar
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_raw(state)

    def send_command(self, command: str, source: str = "dashboard") -> Dict[str, Any]:
        """
        Processes command (START, PAUSE, RESUME, STOP, RESTART, EMERGENCY_STOP).
        Synchronizes state for both Dashboard and Telegram.
        """
        cmd = command.upper()
        state = self._read_raw()
        state["last_command"] = cmd
        state["command_source"] = source
        state["updated_at"] = datetime.now(timezone.utc).isoformat()

        if cmd == "PAUSE":
            state["is_paused"] = True
            if state["status"] == "RUNNING":
                state["status"] = "PAUSED"
        elif cmd == "RESUME":
            state["is_paused"] = False
            state["is_emergency_stopped"] = False
            if state["status"] in ["PAUSED", "STOPPED"]:
                state["status"] = "RUNNING"
        elif cmd == "STOP":
            state["status"] = "STOPPED"
            state["is_paused"] = False
            state["worker_pid"] = None
        elif cmd == "START":
            state["status"] = "RUNNING"
            state["is_paused"] = False
            state["is_emergency_stopped"] = False
            state["uptime_start"] = datetime.now(timezone.utc).isoformat()
        elif cmd == "EMERGENCY_STOP":
            state["status"] = "EMERGENCY_STOPPED"
            state["is_emergency_stopped"] = True
            state["is_paused"] = True

        self._write_raw(state)
        self._record_audit_log(cmd, source)
        return state

    def _record_audit_log(self, command: str, source: str):
        """Asynchronously or synchronously records audit action to SQLite DB."""
        try:
            import sqlite3
            db_file = Path("storage/pita_media.db")
            if db_file.exists():
                conn = sqlite3.connect(str(db_file), timeout=2.0)
                cursor = conn.cursor()
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute(
                    "INSERT INTO audit_logs (id, timestamp, created_at, level, component, message) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"audit_{int(time.time()*1000)}",
                        now,
                        now,
                        "INFO" if command != "EMERGENCY_STOP" else "CRITICAL",
                        f"ControlBus.{source.capitalize()}",
                        f"Command '{command}' triggered from {source}."
                    )
                )
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"Could not write audit log to DB: {e}")

control_bus = ControlBus()
