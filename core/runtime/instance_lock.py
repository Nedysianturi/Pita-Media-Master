"""
Single-Instance Lock Manager untuk Sistem Pita Media di Windows.
Mencegah dua daemon/service berjalan bersamaan menggunakan PID lockfile dan file locking.
"""

import os
import sys
from pathlib import Path
from typing import Optional

from config.settings import settings


class InstanceLockError(RuntimeError):
    """Exception yang dilemparkan saat instance Pita Media lain terdeteksi sedang berjalan."""
    pass


class InstanceLock:
    def __init__(self, lockfile_name: str = "pita_media.lock"):
        self.lock_path = settings.storage_dir / lockfile_name
        self.is_locked = False

    def acquire(self) -> bool:
        """
        Mencoba mengunci instance. Jika instance lama masih hidup, tolak eksekusi.
        Jika instance lama mati (stale lock), bersihkan dan ambil alih.
        """
        if self.lock_path.exists():
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    old_pid = int(content)
                    if self._is_process_running(old_pid):
                        raise InstanceLockError(
                            f"DAEMON SUDAH BERJALAN: Instance Pita Media lain sedang aktif dengan PID {old_pid}. "
                            f"Hentikan instance tersebut terlebih dahulu atau gunakan 'scripts/stop_daemon.bat'."
                        )
            except (ValueError, OSError):
                pass

        # Tulis PID saat ini ke lockfile
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.lock_path, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            self.is_locked = True
            return True
        except Exception as e:
            raise InstanceLockError(f"Gagal membuat file kunci instance di {self.lock_path}: {e}")

    def release(self):
        """Melepaskan kunci instance saat shutdown."""
        if self.is_locked and self.lock_path.exists():
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    pid = f.read().strip()
                if pid == str(os.getpid()):
                    self.lock_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.is_locked = False

    def get_running_pid(self) -> Optional[int]:
        """Mengambil PID dari daemon yang sedang berjalan jika ada."""
        if self.lock_path.exists():
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    pid = int(f.read().strip())
                if self._is_process_running(pid):
                    return pid
            except Exception:
                pass
        return None

    @staticmethod
    def _is_process_running(pid: int) -> bool:
        """Memeriksa apakah proses dengan PID tertentu masih aktif di Windows secara native."""
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            STILL_ACTIVE = 259

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                kernel32.CloseHandle(handle)
                return exit_code.value == STILL_ACTIVE
            kernel32.CloseHandle(handle)
            return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False


instance_lock = InstanceLock()
