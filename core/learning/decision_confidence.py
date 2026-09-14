"""
Decision Confidence Engine untuk Pita Media.
Menghitung tingkat keyakinan (Confidence Score & Level) untuk setiap rekomendasi dan keputusan strategis.
Level: LOW, MEDIUM, HIGH, VERY_HIGH.
Jika confidence score terlalu rendah, sistem menolak perubahan drastis dan merekomendasikan eksperimen/observasi.
"""

import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger("pita_media.learning.confidence")


class ConfidenceLevel:
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class DecisionConfidenceEngine:
    """
    Menghitung skor keyakinan keputusan berbasis sample size, konsistensi data, dan horizon waktu.
    """

    def evaluate_confidence(
        self,
        sample_size: int,
        min_required_samples: int = 10,
        data_consistency_score: float = 0.8,  # 0.0 - 1.0
        days_of_history: int = 14,
        has_active_anomalies: bool = False,
    ) -> Tuple[str, float, str]:
        """
        Menghitung tingkat confidence.
        Returns: (confidence_level, confidence_score_0_to_1, explanation)
        """
        if sample_size <= 0:
            return ConfidenceLevel.LOW, 0.1, "Belum ada sampel data historis yang memadai."

        # Factor 1: Sample size coverage (max 0.40)
        sample_ratio = min(1.0, sample_size / max(1, min_required_samples))
        sample_score = sample_ratio * 0.40

        # Factor 2: Data consistency & stability (max 0.35)
        consistency_score = max(0.0, min(1.0, data_consistency_score)) * 0.35

        # Factor 3: Time horizon (max 0.25)
        horizon_ratio = min(1.0, days_of_history / 30.0)
        horizon_score = horizon_ratio * 0.25

        total_score = sample_score + consistency_score + horizon_score

        if has_active_anomalies:
            total_score = max(0.1, total_score * 0.70)  # Potong 30% jika ada anomali

        total_score = round(total_score, 3)

        if total_score >= 0.85:
            level = ConfidenceLevel.VERY_HIGH
            explanation = f"Tingkat keyakinan SANGAT TINGGI ({total_score*100:.0f}%). Sampel ({sample_size}) dan kestabilan data sangat solid."
        elif total_score >= 0.70:
            level = ConfidenceLevel.HIGH
            explanation = f"Tingkat keyakinan TINGGI ({total_score*100:.0f}%). Data memadai untuk rekomendasi strategis terukur."
        elif total_score >= 0.45:
            level = ConfidenceLevel.MEDIUM
            explanation = f"Tingkat keyakinan SEDANG ({total_score*100:.0f}%). Disarankan menjalankan A/B testing atau eksperimen terkontrol."
        else:
            level = ConfidenceLevel.LOW
            explanation = f"Tingkat keyakinan RENDAH ({total_score*100:.0f}%). Data masih prematur, pertahankan strategi eksisting."

        return level, total_score, explanation


decision_confidence = DecisionConfidenceEngine()
