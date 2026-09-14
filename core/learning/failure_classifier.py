"""
Smart Failure Classifier untuk Sistem Pembelajaran Pita Media.
Mengklasifikasikan penyebab kegagalan job atau underperformance secara akurat:
- TECHNICAL_FAILURE (Jaringan, API Down, Timeout, FFmpeg, Auth) -> BUKAN kesalahan ide konten
- PLATFORM_FAILURE (Rate limit platform, token expired, kebijakan sementara)
- QC_FAILURE (Gagal memenuhi standar estetika/safety sebelum dipublish)
- PROMPT_FAILURE (Instruksi prompt menghasilkan halusinasi/format salah)
- MODEL_FAILURE (Model AI downtime/error internal 500)
- TIMING_FAILURE (Diposting saat dead hours audiens)
- DUPLICATE_FATIGUE (Audiens jenuh karena topik terlalu sering diulang)
- CONTENT_FAILURE (Ide/konten murni kurang memikat audiens setelah lolos teknis)
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("pita_media.learning.failure_classifier")


class FailureType:
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    PLATFORM_FAILURE = "PLATFORM_FAILURE"
    QC_FAILURE = "QC_FAILURE"
    PROMPT_FAILURE = "PROMPT_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"
    TIMING_FAILURE = "TIMING_FAILURE"
    DUPLICATE_FATIGUE = "DUPLICATE_FATIGUE"
    CONTENT_FAILURE = "CONTENT_FAILURE"
    UNKNOWN = "UNKNOWN"


class SmartFailureClassifier:
    """
    Menganalisis pesan error, response code, dan status eksekusi untuk
    menentukan kategori kegagalan yang tepat.
    """

    TECHNICAL_KEYWORDS = [
        "connection", "timeout", "timed out", "unreachable", "dns", "socket",
        "ffmpeg", "file not found", "disk full", "permission denied",
        "database locked", "wal", "corrupt", "memory error"
    ]

    PLATFORM_KEYWORDS = [
        "access token", "expired", "rate limit", "session has expired",
        "graph api", "meta error", "401", "403", "oauth", "permissions"
    ]

    MODEL_KEYWORDS = [
        "500 internal", "503 service unavailable", "overloaded", "quota exceeded",
        "resource exhausted", "429"
    ]

    PROMPT_KEYWORDS = [
        "json decode error", "invalid json", "schema validation error",
        "unsupported format", "pydantic"
    ]

    def classify_execution_error(
        self,
        error_message: str,
        stage: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Mengklasifikasikan error saat eksekusi pembuatan atau publikasi.
        """
        err_lower = (error_message or "").lower()
        details = details or {}

        if stage == "QC":
            return {
                "failure_type": FailureType.QC_FAILURE,
                "is_technical": False,
                "affects_idea_reputation": False,
                "description": f"Gagal pada tahap QC/Safety: {error_message}",
                "retryable": True,
            }

        if any(kw in err_lower for kw in self.PLATFORM_KEYWORDS):
            return {
                "failure_type": FailureType.PLATFORM_FAILURE,
                "is_technical": True,
                "affects_idea_reputation": False,
                "description": f"Kendala autentikasi/kuota platform media sosial: {error_message}",
                "retryable": True,
            }

        if any(kw in err_lower for kw in self.MODEL_KEYWORDS):
            return {
                "failure_type": FailureType.MODEL_FAILURE,
                "is_technical": True,
                "affects_idea_reputation": False,
                "description": f"Provider AI mengalami kendala infrastruktur: {error_message}",
                "retryable": True,
            }

        if any(kw in err_lower for kw in self.PROMPT_KEYWORDS):
            return {
                "failure_type": FailureType.PROMPT_FAILURE,
                "is_technical": True,
                "affects_idea_reputation": False,
                "description": f"Struktur output prompt tidak sesuai schema: {error_message}",
                "retryable": True,
            }

        if any(kw in err_lower for kw in self.TECHNICAL_KEYWORDS):
            return {
                "failure_type": FailureType.TECHNICAL_FAILURE,
                "is_technical": True,
                "affects_idea_reputation": False,
                "description": f"Kendala teknis lokal/jaringan: {error_message}",
                "retryable": True,
            }

        return {
            "failure_type": FailureType.UNKNOWN,
            "is_technical": True,
            "affects_idea_reputation": False,
            "description": f"Error tidak teridentifikasi: {error_message}",
            "retryable": False,
        }

    def classify_low_performance(
        self,
        virality_score: float,
        retention_rate: float,
        novelty_score: float,
        posting_hour_wib: int,
        topic_frequency_last_30d: int,
    ) -> Dict[str, Any]:
        """
        Mengklasifikasikan konten yang performanya di bawah rata-rata.
        """
        if topic_frequency_last_30d >= 4 and novelty_score < 0.35:
            return {
                "failure_type": FailureType.DUPLICATE_FATIGUE,
                "reason": f"Audiens mengalami kejenuhan topik (muncul {topic_frequency_last_30d}x dalam 30 hari).",
                "recommendation": "Gunakan tema atau material yang berbeda untuk pilar ini.",
            }

        if posting_hour_wib in [1, 2, 3, 4, 5]:
            return {
                "failure_type": FailureType.TIMING_FAILURE,
                "reason": f"Waktu publikasi ({posting_hour_wib}:00 WIB) berada di luar jam aktif audiens.",
                "recommendation": "Jadwalkan di prime time (12:00-13:00 atau 19:00-21:30 WIB).",
            }

        return {
            "failure_type": FailureType.CONTENT_FAILURE,
            "reason": "Hook atau penyampaian cerita kurang menghasilkan share/retensi tinggi.",
            "recommendation": "Evaluasi formula hook dan gunakan hook type yang lebih memicu rasa penasaran.",
        }


failure_classifier = SmartFailureClassifier()
