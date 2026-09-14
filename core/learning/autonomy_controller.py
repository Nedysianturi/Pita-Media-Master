"""
Autonomy Controller untuk Sistem Pita Media.
Mengelola 4 Level Otonomi Sistem:
1. OBSERVE        : Sistem hanya mengumpulkan data, belajar, dan mencatat pola. Tidak ada perubahan otomatis. (DEFAULT WAJIB)
2. RECOMMEND      : Sistem menganalisis dan menyajikan rekomendasi ke Admin.
3. ASSISTED_AUTO  : Sistem boleh mengubah parameter minor dalam batas aman terkonfigurasi.
4. CONTROLLED_AUTO: Sistem mengoptimalkan strategi mandiri dalam batas toleransi ketat.

Proteksi Pengaturan (PROTECTED SETTINGS):
Sistem DILARANG KERAS mengubah sendiri:
- Credentials & API Keys
- Safety Gate
- Hard Cost Limit
- Windows Service Config
- Database Security
- Telegram Admin ID
- APP_MODE (DRY_RUN / PRODUCTION)
- Source code inti
- Emergency stop rules

Fitur Keamanan Tambahan:
- Automatic Downgrade jika error rate naik atau terjadi anomali
- Manual Autonomy Control (Naik, Turun, Pause, Reset, Rollback)
- Automatic Maturity Recommendation (Hanya menyarankan ke Admin, tidak mengaktifkan sendiri)
"""

import logging
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone
from config.settings import settings
from core.database import get_db
from database.models import AutonomyLog, AuditLog
from core.learning.learning_maturity import learning_maturity, MaturityStage

logger = logging.getLogger("pita_media.learning.autonomy")


class AutonomyLevel:
    OBSERVE = "OBSERVE"
    RECOMMEND = "RECOMMEND"
    ASSISTED_AUTO = "ASSISTED_AUTO"
    CONTROLLED_AUTO = "CONTROLLED_AUTO"


class AutonomyController:
    """
    Pengendali Otonomi Cerdas dengan Pengawasan Keamanan Ketat (Strict Governance).
    """

    PROTECTED_ATTRIBUTES = {
        "GEMINI_API_KEY", "FB_PAGE_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ADMIN_IDS", "APP_MODE", "DAILY_COST_LIMIT_USD",
        "MONTHLY_COST_LIMIT_USD", "QC_TOTAL_PASS_THRESHOLD",
        "DATABASE_URL", "DASHBOARD_SECRET_KEY"
    }

    def __init__(self):
        self._current_level = getattr(settings, "AUTONOMY_LEVEL", AutonomyLevel.OBSERVE)
        self._is_paused = False

    @property
    def current_level(self) -> str:
        return self._current_level

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def set_autonomy_level(
        self,
        new_level: str,
        changed_by: str = "ADMIN",
        reason: str = "Manual level adjustment",
    ) -> Dict[str, Any]:
        """
        Mengubah level otonomi secara manual oleh admin dengan pencatatan audit log lengkap.
        """
        valid_levels = [
            AutonomyLevel.OBSERVE,
            AutonomyLevel.RECOMMEND,
            AutonomyLevel.ASSISTED_AUTO,
            AutonomyLevel.CONTROLLED_AUTO,
        ]
        target = new_level.upper().strip()
        if target not in valid_levels:
            raise ValueError(f"Level otonomi '{new_level}' tidak valid. Pilihan: {valid_levels}")

        old_level = self._current_level
        self._current_level = target
        now = datetime.now(timezone.utc)

        # Hitung maturity saat ini
        mat = learning_maturity.calculate_maturity_score()

        # Simpan record log
        with get_db() as db:
            log = AutonomyLog(
                previous_level=old_level,
                new_level=target,
                action_type="MANUAL_CHANGE",
                trigger_reason=reason,
                maturity_score=mat.get("total_score", 0.0),
                strategy_confidence=0.85,
                changed_by=changed_by,
                details={"maturity_stage": mat.get("stage", "")},
                created_at=now,
            )
            audit = AuditLog(
                timestamp=now,
                created_at=now,
                level="INFO",
                component="AutonomyController",
                message=f"Level Otonomi diubah dari {old_level} menjadi {target} oleh {changed_by}. Alasan: {reason}",
            )
            db.add(log)
            db.add(audit)
            db.commit()

        logger.info(f"[AUTONOMY] Level changed: {old_level} -> {target} by {changed_by}")
        return {
            "previous_level": old_level,
            "new_level": target,
            "changed_by": changed_by,
            "reason": reason,
            "timestamp": now.isoformat(),
        }

    def check_automatic_downgrade(
        self,
        recent_error_rate: float,
        consecutive_qc_failures: int,
        active_anomalies_count: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Pemeriksaan keamanan otomatis: Menurunkan level jika terdeteksi risiko tinggi.
        """
        should_downgrade = False
        reason = ""

        if recent_error_rate >= settings.AUTO_DOWNGRADE_ERROR_RATE_THRESHOLD:
            should_downgrade = True
            reason = f"Error rate ({recent_error_rate*100:.1f}%) melampaui batas aman ({settings.AUTO_DOWNGRADE_ERROR_RATE_THRESHOLD*100:.0f}%)."
        elif consecutive_qc_failures >= 3:
            should_downgrade = True
            reason = f"Terdeteksi {consecutive_qc_failures} kegagalan QC berturut-turut."
        elif active_anomalies_count >= 2:
            should_downgrade = True
            reason = f"Terdeteksi {active_anomalies_count} anomali performa aktif."

        if should_downgrade and self._current_level != AutonomyLevel.OBSERVE:
            old_level = self._current_level
            # Step down level
            if old_level == AutonomyLevel.CONTROLLED_AUTO:
                new_lvl = AutonomyLevel.ASSISTED_AUTO
            elif old_level == AutonomyLevel.ASSISTED_AUTO:
                new_lvl = AutonomyLevel.RECOMMEND
            else:
                new_lvl = AutonomyLevel.OBSERVE

            self._current_level = new_lvl
            now = datetime.now(timezone.utc)

            with get_db() as db:
                log = AutonomyLog(
                    previous_level=old_level,
                    new_level=new_lvl,
                    action_type="AUTO_DOWNGRADE",
                    trigger_reason=reason,
                    changed_by="SYSTEM_SAFETY_MONITOR",
                    created_at=now,
                )
                audit = AuditLog(
                    timestamp=now,
                    created_at=now,
                    level="WARNING",
                    component="AutonomyController",
                    message=f"SAFETY DOWNGRADE: Level diturunkan dari {old_level} ke {new_lvl}. Alasan: {reason}",
                )
                db.add(log)
                db.add(audit)
                db.commit()

            return {
                "downgraded": True,
                "previous_level": old_level,
                "new_level": new_lvl,
                "reason": reason,
            }

        return None

    def evaluate_maturity_recommendation(self) -> Optional[Dict[str, Any]]:
        """
        Mengevaluasi apakah sistem memenuhi syarat untuk merekomendasikan kenaikan level otonomi.
        TIDAK BOLEH mengaktifkannya sendiri; hanya menyajikan ke admin.
        """
        mat = learning_maturity.calculate_maturity_score()
        score = mat.get("total_score", 0.0)
        stage = mat.get("stage", "")

        curr = self._current_level

        recommendation = None
        if curr == AutonomyLevel.OBSERVE and score >= 40.0:
            recommendation = {
                "current_level": curr,
                "recommended_level": AutonomyLevel.RECOMMEND,
                "maturity_score": score,
                "maturity_stage": stage,
                "message": f"Sistem telah mengumpulkan data dasar yang memadai (Skor: {score}/100, Tahap: {stage}). Disarankan menaikkan level ke RECOMMEND.",
            }
        elif curr == AutonomyLevel.RECOMMEND and score >= 65.0:
            recommendation = {
                "current_level": curr,
                "recommended_level": AutonomyLevel.ASSISTED_AUTO,
                "maturity_score": score,
                "maturity_stage": stage,
                "message": f"Sistem telah matang dengan pola teruji (Skor: {score}/100). Disarankan memberikan wewenang optimasi minor (ASSISTED_AUTO).",
            }
        elif curr == AutonomyLevel.ASSISTED_AUTO and score >= 85.0:
            recommendation = {
                "current_level": curr,
                "recommended_level": AutonomyLevel.CONTROLLED_AUTO,
                "maturity_score": score,
                "maturity_stage": stage,
                "message": f"Tingkat keyakinan tinggi ({score}/100). Disarankan membuka mode CONTROLLED_AUTO.",
            }

        return recommendation

    def pause_learning(self, paused: bool = True):
        """Menjeda atau mengaktifkan kembali proses pembelajaran."""
        self._is_paused = paused

    def can_auto_adjust_parameter(self, param_name: str) -> bool:
        """
        Memastikan atribut yang ingin diubah BUKAN protected attribute dan diizinkan oleh level aktif.
        """
        if param_name in self.PROTECTED_ATTRIBUTES:
            return False
        if self._is_paused:
            return False
        return self._current_level in [AutonomyLevel.ASSISTED_AUTO, AutonomyLevel.CONTROLLED_AUTO]


autonomy_controller = AutonomyController()
