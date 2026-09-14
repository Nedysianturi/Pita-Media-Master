"""
Konfigurasi Sentral Sistem Pita Media berbasis Pydantic Settings.
Semua variabel lingkungan divalidasi dan model AI diambil murni dari .env tanpa hardcoding.
"""

from pathlib import Path
from typing import List, Set
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 1. Google Gemini Ecosystem (Diambil murni dari .env)
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API Key")
    GEMINI_TEXT_MODEL: str = Field(default="gemini-2.5-flash", description="Model teks dari .env")
    GEMINI_PRO_MODEL: str = Field(default="gemini-2.5-pro", description="Model pro/structured dari .env")
    GEMINI_IMAGE_MODEL: str = Field(default="imagen-3.0-generate-002", description="Model gambar dari .env")
    GEMINI_VIDEO_MODEL: str = Field(default="veo-2.0-generate-001", description="Model video dari .env")

    # Rate Limiting & Concurrency (Free Tier 5 RPM = jeda 15 detik)
    GEMINI_MIN_REQUEST_INTERVAL_SECONDS: float = Field(default=15.0, description="Jeda aman minimal antar request Gemini")
    GEMINI_MAX_CONCURRENCY: int = Field(default=1, description="Batas concurrency request Gemini")
    GEMINI_MAX_RETRIES_429: int = Field(default=4, description="Maksimal percobaan retry saat HTTP 429")

    # 2. Telegram C2
    TELEGRAM_BOT_TOKEN: str = Field(default="")
    TELEGRAM_ADMIN_IDS_RAW: str = Field(default="", alias="TELEGRAM_ADMIN_IDS")
    TELEGRAM_ALERT_CHAT_ID: str = Field(default="")

    # 3. Storage & Media
    STORAGE_PATH: str = Field(default="storage")
    FFMPEG_BINARY_PATH: str = Field(default="")
    SIGNATURE_TEXT: str = Field(default="Pita Waktu")
    ENABLE_SIGNATURE_WATERMARK: bool = Field(default=True)

    # 4. Database & Queue
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///storage/pita_media.db")
    SQLITE_WAL_MODE: bool = Field(default=True)
    MAX_WORKER_CONCURRENCY: int = Field(default=1)

    # 5. Governors
    DAILY_COST_LIMIT_USD: float = Field(default=10.0)
    MONTHLY_COST_LIMIT_USD: float = Field(default=200.0)
    COST_ALERT_THRESHOLD_PERCENT: float = Field(default=80.0)
    MIN_POSTING_INTERVAL_MINUTES: int = Field(default=60)
    EXPLORATION_QUOTA_PERCENT: float = Field(default=25.0)

    # 6. QC Thresholds
    QC_TOTAL_PASS_THRESHOLD: float = Field(default=8.0)
    QC_CATEGORY_PASS_THRESHOLD: float = Field(default=7.0)
    MAX_AUTO_REPAIR_ATTEMPTS: int = Field(default=3)

    # 7. Dashboard
    DASHBOARD_HOST: str = Field(default="127.0.0.1")
    DASHBOARD_PORT: int = Field(default=8080)
    DASHBOARD_SECRET_KEY: str = Field(default="change_this_to_a_random_secure_string")

    @property
    def admin_ids(self) -> Set[int]:
        """Parse TELEGRAM_ADMIN_IDS string into a set of integers."""
        if not self.TELEGRAM_ADMIN_IDS_RAW:
            return set()
        ids = set()
        for item in self.TELEGRAM_ADMIN_IDS_RAW.split(","):
            cleaned = item.strip()
            if cleaned and cleaned.isdigit():
                ids.add(int(cleaned))
        return ids

    @property
    def storage_dir(self) -> Path:
        p = BASE_DIR / self.STORAGE_PATH
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def raw_media_dir(self) -> Path:
        p = self.storage_dir / "raw"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def processed_media_dir(self) -> Path:
        p = self.storage_dir / "processed"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def backup_dir(self) -> Path:
        p = self.storage_dir / "backups"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
