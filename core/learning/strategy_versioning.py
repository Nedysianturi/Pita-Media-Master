"""
Strategy Versioning & Rollback Engine untuk Pita Media.
Menyimpan riwayat versi strategi ke dalam database terstruktur (StrategyVersion):
- Pilar distribution
- Posting schedule
- Hook strategy
- Visual style
- Provider routing
- Experimentation policy
Mendukung Rollback instan ke 'last-known-good' strategy jika performa strategi baru menurun.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from core.database import get_db
from database.models import StrategyVersion

logger = logging.getLogger("pita_media.learning.strategy_versioning")


class StrategyVersioningEngine:
    """
    Manajer versi strategi konten dengan persistensi database dan proteksi rollback.
    """

    DEFAULT_PILAR_DISTRIBUTION = {
        "pita_transformasi": 0.30,
        "pita_mini": 0.25,
        "pita_cerita": 0.25,
        "pita_kreasi": 0.20,
    }

    def ensure_initial_strategy(self):
        """Memastikan strategi baseline (v1) tersedia di database."""
        with get_db() as db:
            active = db.query(StrategyVersion).filter(StrategyVersion.is_active == True).first()
            if not active:
                now = datetime.now(timezone.utc)
                v1 = StrategyVersion(
                    version_num=1,
                    name="Strategy v1 - Baseline Balanced Distribution",
                    pilar_distribution=self.DEFAULT_PILAR_DISTRIBUTION,
                    posting_schedule={"primary_slots": ["12:00 WIB", "19:30 WIB"]},
                    hook_strategy={"primary_hook": "curiosity_gap", "visual_pacing": "dynamic"},
                    content_style={"lighting": "cinematic_warm", "aspect_ratio": "9:16"},
                    provider_routing={"primary_text": "gemini", "primary_image": "imagen"},
                    experimentation_policy={"quota_percent": 25.0},
                    reason="Inisialisasi baseline strategi standar 4 pilar.",
                    expected_result="Mempertahankan variasi seimbang antar 4 pilar.",
                    is_active=True,
                    is_proven_good=True,
                    created_by="SYSTEM_INITIALIZATION",
                    created_at=now,
                )
                db.add(v1)
                db.commit()

    def get_active_strategy(self) -> Dict[str, Any]:
        """Mengambil strategi yang saat ini aktif."""
        self.ensure_initial_strategy()
        with get_db() as db:
            active = db.query(StrategyVersion).filter(StrategyVersion.is_active == True).first()
            if not active:
                active = db.query(StrategyVersion).order_by(StrategyVersion.version_num.desc()).first()

            return {
                "id": active.id,
                "version_num": active.version_num,
                "name": active.name,
                "pilar_distribution": active.pilar_distribution,
                "posting_schedule": active.posting_schedule,
                "hook_strategy": active.hook_strategy,
                "reason": active.reason,
                "expected_result": active.expected_result,
                "is_active": active.is_active,
                "is_proven_good": active.is_proven_good,
                "created_at": active.created_at.isoformat() if active.created_at else "",
            }

    def create_new_strategy_version(
        self,
        name: str,
        pilar_distribution: Dict[str, float],
        reason: str,
        expected_result: str,
        created_by: str = "SYSTEM_LEARNING",
        posting_schedule: Optional[Dict[str, Any]] = None,
        hook_strategy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Membuat versi strategi baru dan menjadikannya strategi aktif.
        """
        now = datetime.now(timezone.utc)
        # Normalisasi bobot
        total_w = sum(pilar_distribution.values())
        norm_dist = {k: round(v / total_w, 2) for k, v in pilar_distribution.items()}

        with get_db() as db:
            last_ver = db.query(StrategyVersion).order_by(StrategyVersion.version_num.desc()).first()
            next_ver_num = (last_ver.version_num + 1) if last_ver else 1

            # Nonaktifkan strategi aktif saat ini
            db.query(StrategyVersion).update({StrategyVersion.is_active: False})

            new_v = StrategyVersion(
                version_num=next_ver_num,
                name=name,
                pilar_distribution=norm_dist,
                posting_schedule=posting_schedule or {"primary_slots": ["12:00 WIB", "19:30 WIB"]},
                hook_strategy=hook_strategy or {"primary_hook": "curiosity_gap"},
                reason=reason,
                expected_result=expected_result,
                is_active=True,
                is_proven_good=False,
                created_by=created_by,
                created_at=now,
            )
            db.add(new_v)
            db.commit()

            return {
                "version_num": next_ver_num,
                "name": name,
                "pilar_distribution": norm_dist,
                "reason": reason,
                "status": "ACTIVE",
            }

    def rollback_to_last_proven_strategy(self) -> Optional[Dict[str, Any]]:
        """
        Mengembalikan strategi aktif ke versi sebelumnya yang terbukti stabil (proven good).
        """
        with get_db() as db:
            proven = (
                db.query(StrategyVersion)
                .filter(StrategyVersion.is_proven_good == True)
                .order_by(StrategyVersion.version_num.desc())
                .first()
            )
            if not proven:
                # Fallback ke v1
                proven = db.query(StrategyVersion).filter(StrategyVersion.version_num == 1).first()

            if not proven:
                return None

            db.query(StrategyVersion).update({StrategyVersion.is_active: False})
            proven.is_active = True
            db.commit()

            return {
                "version_num": proven.version_num,
                "name": proven.name,
                "pilar_distribution": proven.pilar_distribution,
                "reason": f"Rollback ke versi stabil v{proven.version_num}",
                "status": "RESTORED",
            }

    def list_strategy_history(self) -> List[Dict[str, Any]]:
        """Mengambil seluruh riwayat versi strategi."""
        with get_db() as db:
            items = db.query(StrategyVersion).order_by(StrategyVersion.version_num.desc()).all()
            return [
                {
                    "version_num": it.version_num,
                    "name": it.name,
                    "pilar_distribution": it.pilar_distribution,
                    "reason": it.reason,
                    "is_active": it.is_active,
                    "is_proven_good": it.is_proven_good,
                    "created_by": it.created_by,
                    "created_at": it.created_at.isoformat() if it.created_at else "",
                }
                for it in items
            ]


strategy_versioning = StrategyVersioningEngine()
