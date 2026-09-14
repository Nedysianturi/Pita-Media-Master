"""
Data Quality Gate untuk Sistem Pembelajaran Pita Media.
Menyaring dan memvalidasi metrik sebelum dikonsumsi oleh Learning Intelligence Engine.
Mencegah data kotor, fake metrics saat dry-run/test, error API, dan anomali bot.
"""

import logging
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone

logger = logging.getLogger("pita_media.learning.data_quality")


class DataQualityGate:
    """
    Memvalidasi integritas data telemetri performa konten dan audiens.
    Hanya data valid yang lolos ke Long-Term Memory dan Knowledge Base.
    """

    MIN_REASONABLE_VIEWS = 0
    MAX_REASONABLE_RATIO_LIKES_VIEWS = 1.0  # Likes tidak boleh lebih banyak dari views
    MAX_REASONABLE_RATIO_SHARES_VIEWS = 0.5  # Shares wajar <= 50% views

    def validate_content_metric(
        self,
        metrics: Dict[str, Any],
        is_dry_run: bool = False,
        is_test_post: bool = False,
    ) -> Tuple[bool, str]:
        """
        Memvalidasi apakah snapshot metrik performa memenuhi standar kualitas data.
        Returns: (is_valid, rejection_reason)
        """
        if is_dry_run or is_test_post:
            return False, "Data berasal dari simulasi Dry-Run atau Test Post (Fake Data)."

        if not metrics or not isinstance(metrics, dict):
            return False, "Metrik kosong atau format data tidak valid."

        views = metrics.get("views", 0) or metrics.get("views_count", 0)
        likes = metrics.get("likes", 0) or metrics.get("likes_count", 0)
        shares = metrics.get("shares", 0) or metrics.get("shares_count", 0)
        comments = metrics.get("comments", 0) or metrics.get("comments_count", 0)

        # Check for negative values
        if any(v < 0 for v in [views, likes, shares, comments]):
            return False, "Nilai metrik tidak boleh bernilai negatif."

        # Check for API error flags
        if metrics.get("api_error") or metrics.get("fetch_error"):
            return False, f"Terdeteksi error saat fetching data: {metrics.get('error_message', 'API Error')}"

        # Anomaly / Spam bot check
        if views > 100:
            if likes > views * self.MAX_REASONABLE_RATIO_LIKES_VIEWS:
                return False, f"Anomali bot terdeteksi: likes ({likes}) melebihi total views ({views})."
            if shares > views * self.MAX_REASONABLE_RATIO_SHARES_VIEWS:
                return False, f"Anomali share spam terdeteksi: shares ({shares}) tidak proporsional dengan views ({views})."

        return True, "Data valid dan lolos verifikasi Data Quality Gate."

    def sanitize_audience_text(self, text: str) -> str:
        """
        Membersihkan data komentar dari karakter berbahaya dan sensor PII dasar (Email, No HP).
        """
        import re
        if not text:
            return ""
        # Mask emails
        text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[EMAIL_TERSEMBUNYI]', text)
        # Mask phone numbers (Indonesia / international)
        text = re.sub(r'(\+62|62|08)[0-9]{8,12}', '[NOMOR_TERSEMBUNYI]', text)
        return text.strip()


data_quality_gate = DataQualityGate()
