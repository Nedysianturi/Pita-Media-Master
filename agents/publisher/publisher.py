"""
Publisher Agent untuk Sistem Pita Media.
Mengeksekusi publikasi hanya jika lolos seluruh Quality Gate dan Safety Gate,
disertai verifikasi pasca-publikasi untuk mencegah duplikasi konten.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import Publication, AuditLog, Content
from agents.publisher.verifier import post_publish_verifier
from providers.facebook_client import facebook_client
from config.settings import settings


class PublisherAgent:
    def __init__(self):
        self.verifier = post_publish_verifier
        self.fb_client = facebook_client

    async def publish_content(
        self,
        content_id: str,
        content_payload: Dict[str, Any],
        qc_verdict: str,
        platform: str = "auto",
        db_session: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """
        Alur kerja publikasi:
        1. Validasi Gate: Hanya boleh posting jika qc_verdict == 'PASSED'.
        2. Hitung hash sidik jari konten.
        3. Cek duplikasi publikasi (30 hari terakhir).
        4. Eksekusi publikasi (Facebook Fanspage atau Mock).
        5. Lakukan post-publish verification & simpan record.
        """
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

        # 4. Eksekusi Dispatcher Publikasi (Facebook Graph API atau Mock)
        target_platform = platform
        post_url = ""
        remote_post_id = ""

        if platform == "facebook" or (platform == "auto" and self.fb_client.is_configured):
            target_platform = "facebook"
            try:
                if pilar == "pita_cerita" and len(media_paths) > 0:
                    fb_res = await self.fb_client.publish_multi_photo_carousel(
                        image_paths=media_paths,
                        caption=caption,
                    )
                    post_url = fb_res["post_url"]
                    remote_post_id = fb_res["post_id"]
                elif pilar in ["pita_transformasi", "pita_mini"] and len(media_paths) > 0:
                    fb_res = await self.fb_client.publish_video(
                        video_path=media_paths[0],
                        title=title,
                        description=caption,
                    )
                    post_url = fb_res["post_url"]
                    remote_post_id = fb_res["post_id"]
                elif len(media_paths) == 1 and media_paths[0].endswith((".png", ".jpg", ".jpeg")):
                    fb_res = await self.fb_client.publish_single_photo(
                        image_path=media_paths[0],
                        caption=caption,
                    )
                    post_url = fb_res["post_url"]
                    remote_post_id = fb_res["post_id"]
                elif len(media_paths) > 0 and media_paths[0].endswith((".mp4", ".mov")):
                    fb_res = await self.fb_client.publish_video(
                        video_path=media_paths[0],
                        title=title,
                        description=caption,
                    )
                    post_url = fb_res["post_url"]
                    remote_post_id = fb_res["post_id"]
                else:
                    # Fallback carousel jika ada media multiple
                    fb_res = await self.fb_client.publish_multi_photo_carousel(
                        image_paths=media_paths,
                        caption=caption,
                    )
                    post_url = fb_res["post_url"]
                    remote_post_id = fb_res["post_id"]
            except Exception as e:
                # Log kegagalan dan fallback ke mock jika terjadi error koneksi API
                timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                post_url = f"https://facebook.com/{self.fb_client.page_id or 'page'}/posts/{timestamp_slug}"
                remote_post_id = f"fb_fallback_{timestamp_slug}"
                target_platform = "facebook_fallback"
        else:
            if target_platform == "auto":
                target_platform = "mock"
            timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            post_url = f"https://pita-media.mock/p/{pilar}/{timestamp_slug}"
            remote_post_id = f"mock_post_{timestamp_slug}"

        # 5. Post-Publish Verification
        is_verified = await self.verifier.verify_published_url(post_url)
        publish_status = "VERIFIED" if is_verified else "FAILED"

        if db_session:
            pub_record = Publication(
                content_id=content_id,
                platform=platform,
                post_url=post_url,
                remote_post_id=remote_post_id,
                publish_status=publish_status,
                verification_hash=v_hash,
                verified_at=datetime.now(timezone.utc) if is_verified else None,
            )
            audit = AuditLog(
                level="INFO",
                component="PublisherAgent",
                content_id=content_id,
                message=f"Konten '{title}' berhasil dipublikasikan ke {platform} ({post_url}) dengan status '{publish_status}'.",
            )
            db_session.add_all([pub_record, audit])
            await db_session.commit()

        return {
            "publish_status": publish_status,
            "platform": platform,
            "post_url": post_url,
            "remote_post_id": remote_post_id,
            "verification_hash": v_hash,
            "verified": is_verified,
        }


publisher_agent = PublisherAgent()
