"""
Modul Anomaly Detector untuk Strategist Agent.
Mendeteksi anomali performa dan menerapkan proteksi Anti-Knee-Jerk:
Mencegah perubahan strategi drastis hanya karena lonjakan atau penurunan pada satu konten tunggal.
"""

from typing import List, Dict, Any, Tuple
import statistics


class AnomalyDetector:
    def detect_performance_anomaly(
        self,
        current_metric: float,
        historical_metrics: List[float],
        z_threshold: float = 2.5,
    ) -> Tuple[bool, str]:
        """
        Mendeteksi apakah performa konten saat ini merupakan anomali statistik (Z-score).
        """
        if len(historical_metrics) < 5:
            return False, "Data historis belum cukup untuk deteksi anomali (< 5 entri)."

        mean_val = statistics.mean(historical_metrics)
        std_val = statistics.stdev(historical_metrics)

        if std_val == 0:
            return False, "Variansi historis nol."

        z_score = (current_metric - mean_val) / std_val

        if z_score > z_threshold:
            return True, f"POSITIVE_SPIKE: Performa melesat tinggi (+{z_score:.2f} SD di atas rata-rata)."
        elif z_score < -z_threshold:
            return True, f"NEGATIVE_DROP: Performa anjlok ({z_score:.2f} SD di bawah rata-rata)."

        return False, "Performa dalam batas fluktuasi normal."

    def is_sustained_trend(self, recent_metrics: List[float], baseline_mean: float, min_consecutive: int = 3) -> bool:
        """
        Memastikan tren penurunan/kenaikan terjadi secara konsisten (minimal 3 konten berturut-turut)
        sebelum merekomendasikan penyesuaian pilar besar.
        """
        if len(recent_metrics) < min_consecutive:
            return False

        last_n = recent_metrics[-min_consecutive:]
        # Cek jika seluruh n konten terakhir berada di bawah/atas baseline
        all_above = all(m > baseline_mean for m in last_n)
        all_below = all(m < baseline_mean for m in last_n)
        return all_above or all_below


anomaly_detector = AnomalyDetector()
