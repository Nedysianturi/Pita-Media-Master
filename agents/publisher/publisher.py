"""
Publisher Agent untuk Sistem Pita Media.
Mengeksekusi publikasi dengan isolasi kegagalan per platform (Facebook, Instagram, Threads),
menjaga kepatuhan APP_MODE (DRY_RUN vs PRODUCTION),
mencegah duplikasi posting (30-90 hari), dan mencatat PublishingReceipt untuk audit transparansi.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import Publication, AuditLog, Content, PublishingReceipt
from agents.publisher.verifier import post_publish_verifier
from agents.publisher.facebook_publisher import FacebookPublisher
from agents.publisher.instagram_publisher import InstagramPublisher
from agents.publisher.threads_publisher import ThreadsPublisher
from agents.publisher.post_verifier import post_verifier
from providers.facebook_client import facebook_client
from core.security.credential_manager import credential_manager
from config.settings import settings

logger = logging.getLogger("pita_media.publisher")


class PublisherAgent:
    def __init__(self):
        self.verifier = post_publish_verifier
        self.receipt_verifier = post_verifier
        self.fb_client = facebook_client
        self.publishers = {
            "facebook": FacebookPublisher(),
            "instagram": InstagramPublisher(),
            "threads": ThreadsPublisher()
        }

    def get_app_mode(self) -> str:
        """Fetch current global APP_MODE: DRY_RUN or PRODUCTION."""
        return os.environ.get("APP_MODE", getattr(settings, "APP_MODE", "DRY_RUN")).upper()

    def get_configured_platforms(self) -> List[str]:
        """Menemukan seluruh platform medsos yang aktif dan terkonfigurasi (Facebook, Instagram, Threads)."""
        active = []
        # 1. Facebook
        fb_creds = credential_manager.get_credential("meta_facebook") or {}
        if fb_creds.get("access_token") or getattr(settings, "FB_PAGE_ACCESS_TOKEN", None) or getattr(settings, "META_SYSTEM_USER_TOKEN", None):
            active.append("facebook")
        
        # 2. Instagram
        ig_creds = credential_manager.get_credential("meta_instagram") or {}
        if ig_creds.get("access_token") or getattr(settings, "INSTAGRAM_ACCOUNT_ID", None) or getattr(settings, "FB_PAGE_ACCESS_TOKEN", None) or getattr(settings, "META_SYSTEM_USER_TOKEN", None):
            active.append("instagram")

        # 3. Threads
        th_creds = credential_manager.get_credential("meta_threads") or credential_manager.get_credential("meta_facebook") or {}
        if th_creds.get("access_token") or getattr(settings, "THREADS_ACCESS_TOKEN", None) or getattr(settings, "META_SYSTEM_USER_TOKEN", None) or getattr(settings, "FB_PAGE_ACCESS_TOKEN", None):
            active.append("threads")

        if not active:
            active = ["facebook", "instagram", "threads"]
        return active

    async def publish_content(
        self,
        content_id: str,
        content_payload: Dict[str, Any],
        qc_verdict: str,
        platform: str = "auto",
        platforms: Optional[List[str]] = None,
        db_session: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """
        Alur kerja publikasi:
        1. Validasi Gate: Hanya boleh posting jika qc_verdict == 'PASSED'.
        2. Periksa APP_MODE (DRY_RUN / PRODUCTION).
        3. Hitung hash sidik jari konten & cek duplikasi (30-90 hari).
        4. Eksekusi publikasi terisolasi per platform (Facebook, Instagram, Threads).
        5. Lakukan post-publish verification & catat PublishingReceipt.
        """
        app_mode = self.get_app_mode()
        is_dry_run = (app_mode != "PRODUCTION")

        # 1. Gate Validation
        if qc_verdict != "PASSED":
            raise PermissionError(
                f"PUBLISHER DITOLAK: Konten tidak boleh dipublikasikan karena status QC adalah '{qc_verdict}'."
            )

        title = content_payload.get("title", "")
        pilar = content_payload.get("pilar", "")
        media_paths = content_payload.get("media_paths", [])
        caption = content_payload.get("caption", "")

        # 2. Hitung Verification Hash
        v_hash = self.verifier.compute_verification_hash(
            pilar=pilar,
            title=title,
            media_paths_count=len(media_paths),
            caption_preview=caption,
        )

        # 3. Cek Duplikasi di Database
        if db_session:
            is_dup = await self.verifier.check_for_duplicate_post(
                db_session=db_session,
                verification_hash=v_hash,
            )
            if is_dup:
                raise ValueError("DUPLICATE POST DETECTED: Konten dengan hash sidik jari yang sama sudah pernah diposting dalam 30 hari terakhir.")

        # Tentukan target platform
        target_platforms = platforms or []
        if not target_platforms:
            if platform in ["facebook", "instagram", "threads"]:
                target_platforms = [platform]
            elif platform == "all":
                target_platforms = ["facebook", "instagram", "threads"]
            else:
                target_platforms = self.get_configured_platforms()

        platform_results = {}
        primary_post_url = ""
        primary_remote_id = ""

        for target in target_platforms:
            # Periksa apakah sudah ada receipt sukses untuk content_id ini di target platform
            existing_receipt = self.receipt_verifier.check_duplicate_target(content_id, target)
            if existing_receipt and not is_dry_run:
                logger.warning(f"Skipping duplicate publish to {target}: already published with ID {existing_receipt.post_id}")
                platform_results[target] = {
                    "success": True,
                    "skipped": True,
                    "status": "ALREADY_PUBLISHED",
                    "post_id": existing_receipt.post_id,
                    "permalink": existing_receipt.permalink
                }
                continue

            pub = self.publishers.get(target)
            if not pub:
                platform_results[target] = {
                    "success": False,
                    "status": "FAILED",
                    "error": f"Unknown platform publisher: {target}"
                }
                continue

            try:
                import inspect
                if hasattr(pub, "publish") and inspect.iscoroutinefunction(pub.publish):
                    raw_res = await pub.publish(
                        content_id=content_id,
                        job_id=content_payload.get("job_id", ""),
                        content_payload=content_payload,
                        is_dry_run=is_dry_run,
                    )
                    post_id = raw_res.get("external_post_id") or raw_res.get("post_id")
                    permalink = raw_res.get("permalink")
                    if is_dry_run:
                        status = "SIMULATED"
                        if not permalink:
                            permalink = f"https://pita-media.simulated/{target}/{content_id[:8]}"
                    else:
                        post_id = post_id or ""
                        permalink = permalink or ""
                        raw_st = raw_res.get("status", "PUBLISHED")
                        status = "LIVE_VERIFIED" if raw_st in ["VERIFIED", "LIVE_VERIFIED"] else "LIVE_PUBLISHED"

                    res = {
                        "success": True,
                        "platform": target,
                        "post_id": post_id,
                        "permalink": permalink,
                        "status": status,
                        "real_publish": not is_dry_run,
                        "error": raw_res.get("error_message")
                    }
                else:
                    raw_res = pub.publish_content(content_payload, dry_run=is_dry_run)
                    post_id = raw_res.get("post_id")
                    permalink = raw_res.get("permalink")
                    if is_dry_run:
                        status = "SIMULATED"
                        if not permalink:
                            permalink = f"https://pita-media.simulated/{target}/{content_id[:8]}"
                    else:
                        status = "LIVE_PUBLISHED"
                    res = {
                        "success": True,
                        "platform": target,
                        "post_id": post_id,
                        "permalink": permalink,
                        "status": status,
                        "real_publish": not is_dry_run,
                        "error": None
                    }

                platform_results[target] = res

                # Record receipt with explicit status
                receipt = self.receipt_verifier.record_receipt(
                    content_id=content_id,
                    job_id=content_payload.get("job_id"),
                    platform=target,
                    post_id=res.get("post_id") or "",
                    permalink=res.get("permalink") or "",
                    status=res.get("status", "SIMULATED" if is_dry_run else "LIVE_PUBLISHED"),
                    app_mode=app_mode,
                    verified=(not is_dry_run and res.get("status") in ["LIVE_VERIFIED", "VERIFIED"]),
                    metrics=res.get("metrics", {}),
                    error_message=res.get("error")
                )

                if not primary_post_url and res.get("permalink"):
                    primary_post_url = res.get("permalink")
                    primary_remote_id = res.get("post_id") or ""

            except Exception as e:
                logger.error(f"Failed to publish to {target}: {e}", exc_info=True)
                platform_results[target] = {
                    "success": False,
                    "status": "FAILED",
                    "real_publish": not is_dry_run,
                    "error": str(e)
                }
                self.receipt_verifier.record_receipt(
                    content_id=content_id,
                    job_id=content_payload.get("job_id"),
                    platform=target,
                    post_id="",
                    permalink="",
                    status="FAILED",
                    app_mode=app_mode,
                    verified=False,
                    error_message=str(e)
                )

        # Fallback primary metadata if none succeeded
        if not primary_post_url:
            timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            primary_post_url = f"https://pita-media.simulated/{pilar}/{timestamp_slug}"
            primary_remote_id = f"sim_{timestamp_slug}"

        # 5. Simpan record Legacy Publication & Audit Log
        if is_dry_run:
            publish_status = "SIMULATED"
            is_verified = False
        else:
            all_succeeded = all(r.get("success", False) for r in platform_results.values())
            all_verified = all(r.get("status") == "LIVE_VERIFIED" for r in platform_results.values())
            if all_verified and len(target_platforms) > 1:
                publish_status = "LIVE_VERIFIED"
                is_verified = True
            elif all_succeeded:
                publish_status = "LIVE_PUBLISHED"
                is_verified = False
            else:
                publish_status = "FAILED"
                is_verified = False

        if db_session:
            pub_record = Publication(
                content_id=content_id,
                platform=",".join(target_platforms),
                post_url=primary_post_url,
                remote_post_id=primary_remote_id,
                publish_status=publish_status,
                verification_hash=v_hash,
                verified_at=datetime.now(timezone.utc) if is_verified else None,
            )
            audit = AuditLog(
                level="INFO" if (publish_status in ["SIMULATED", "LIVE_PUBLISHED", "LIVE_VERIFIED"]) else "WARNING",
                component="PublisherAgent",
                content_id=content_id,
                message=f"[{app_mode}] Content '{title}' dispatched to {target_platforms}. Status: '{publish_status}'.",
            )
            db_session.add_all([pub_record, audit])
            await db_session.commit()

        return {
            "app_mode": app_mode,
            "publish_status": publish_status,
            "platform": ",".join(target_platforms),
            "platform_results": platform_results,
            "post_url": primary_post_url,
            "remote_post_id": primary_remote_id,
            "verification_hash": v_hash,
            "verified": is_verified,
            "is_simulation": is_dry_run,
        }


publisher_agent = PublisherAgent()
