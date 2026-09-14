"""
Centralized Gemini Rate Limiter untuk Sistem Pita Media.
Menerapkan pembatasan laju terpusat (Centralized Rate Limiter) yang dibagikan ke seluruh komponen:
Creator, Reviewer, Self-Repair, dan Strategist.

Fitur:
1. Concurrency limit = 1 (Mutex Lock) pada mode development untuk mencegah race conditions kuota.
2. Jeda aman minimal 15 detik (Free Tier 5 RPM) antar request ke model Gemini.
3. Parsing cerdas RetryInfo / retryDelay dari respons HTTP 429 Gemini API + Jitter + Exponential Backoff.
4. Logging lengkap waktu antar request tanpa membocorkan API key.
"""

import time
import asyncio
import random
import re
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

from config.settings import settings

logger = logging.getLogger("gemini_rate_limiter")


class GeminiRateLimiter:
    _instance: Optional["GeminiRateLimiter"] = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(GeminiRateLimiter, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._lock = asyncio.Lock()
        self._last_request_time: Dict[str, float] = {}
        self._global_last_request_time: float = 0.0
        self.min_interval_seconds: float = getattr(settings, "GEMINI_MIN_REQUEST_INTERVAL_SECONDS", 15.0)
        self.max_retries_429: int = getattr(settings, "GEMINI_MAX_RETRIES_429", 4)
        self._initialized = True

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def _now_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    async def acquire_slot(self, model_name: str) -> float:
        """
        Menjaga jarak aman minimal (15 detik) sebelum request dikirim ke Gemini.
        Mengembalikan gap waktu (dalam detik) sejak request sebelumnya.
        """
        current_time = time.time()
        last_time = self._last_request_time.get(model_name, self._global_last_request_time)
        elapsed = current_time - last_time if last_time > 0 else self.min_interval_seconds

        if elapsed < self.min_interval_seconds:
            wait_needed = self.min_interval_seconds - elapsed
            print(f"[{self._now_str()}] [RATE LIMITER] Menunggu {wait_needed:.2f}s agar memenuhi jeda aman {self.min_interval_seconds}s antar request Gemini...")
            await asyncio.sleep(wait_needed)
            elapsed = self.min_interval_seconds

        # Catat waktu request saat ini
        req_start = time.time()
        self._last_request_time[model_name] = req_start
        self._global_last_request_time = req_start

        print(f"[{self._now_str()}] [GEMINI REQUEST] Mengirim request ke model: '{model_name}' (Jeda sejak request sebelumnya: {elapsed:.2f}s | Concurrency: 1)")
        return elapsed

    def extract_retry_delay(self, error: Exception) -> Optional[float]:
        """
        Mengekstrak rekomendasi waktu tunggu (RetryInfo / retryDelay) dari error Gemini API.
        """
        err_str = str(error)

        # 1. Cek regex format 'Please retry in 35.6s' atau 'Please retry in 35s'
        match_msg = re.search(r"Please retry in (\d+(?:\.\d+)?)s", err_str, re.IGNORECASE)
        if match_msg:
            try:
                return float(match_msg.group(1))
            except ValueError:
                pass

        # 2. Cek regex format 'retryDelay': '35s'
        match_json = re.search(r"['\"]retryDelay['\"]\s*:\s*['\"](\d+(?:\.\d+)?)s?['\"]", err_str, re.IGNORECASE)
        if match_json:
            try:
                return float(match_json.group(1))
            except ValueError:
                pass

        return None

    async def handle_429_backoff(self, model_name: str, error: Exception, attempt: int) -> float:
        """
        Menangani error 429 RESOURCE_EXHAUSTED:
        Membaca retryDelay resmi dari API, menambahkan jitter, dan menunggu sebelum retry.
        """
        parsed_delay = self.extract_retry_delay(error)
        jitter = random.uniform(1.5, 3.5)

        if parsed_delay is not None:
            total_wait = parsed_delay + jitter
            print(
                f"[{self._now_str()}] [429 RATE LIMIT] Kuota Free Tier tercapai untuk model '{model_name}'. "
                f"API merekomendasikan jeda {parsed_delay:.1f}s (+jitter {jitter:.2f}s) = Total tunggu {total_wait:.2f}s sebelum retry #{attempt}..."
            )
        else:
            base_wait = self.min_interval_seconds * (2 ** (attempt - 1))
            total_wait = base_wait + jitter
            print(
                f"[{self._now_str()}] [429 RATE LIMIT] Kuota Free Tier tercapai untuk model '{model_name}'. "
                f"Exponential backoff: {base_wait:.1f}s (+jitter {jitter:.2f}s) = Total tunggu {total_wait:.2f}s sebelum retry #{attempt}..."
            )

        await asyncio.sleep(total_wait)
        # Reset last_request_time ke waktu lama agar acquire_slot langsung mengeksekusi tanpa double-wait
        self._last_request_time[model_name] = time.time() - self.min_interval_seconds
        self._global_last_request_time = time.time() - self.min_interval_seconds
        return total_wait


gemini_rate_limiter = GeminiRateLimiter()
