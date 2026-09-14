"""
Facebook / Meta Graph API Client untuk Sistem Pita Media.
Mendukung publikasi otomatis ke Fanspage Facebook:
1. Multi-Photo Carousel (Pita Cerita) dengan unlisted upload + feed attachment.
2. Video & Reels (Pita Transformasi & Pita Mini).
3. Single Photo & Post (Pita Kreasi).
"""

import os
from pathlib import Path
from typing import Dict, Any, List, Optional
import httpx

from config.settings import settings
from monitoring.audit_logger import audit_logger


class FacebookClient:
    def __init__(
        self,
        page_id: Optional[str] = None,
        access_token: Optional[str] = None,
        api_version: Optional[str] = None,
    ):
        self.page_id = page_id or settings.FB_PAGE_ID
        self.access_token = access_token or settings.FB_PAGE_ACCESS_TOKEN
        self.api_version = api_version or settings.FB_API_VERSION or "v21.0"
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    @property
    def is_configured(self) -> bool:
        """Cek apakah konfigurasi Facebook Fanspage sudah terisi lengkap."""
        return bool(self.page_id and self.access_token and self.access_token != "your_facebook_page_access_token_here")

    async def publish_multi_photo_carousel(
        self,
        image_paths: List[str],
        caption: str,
    ) -> Dict[str, Any]:
        """
        Publikasikan album / multi-photo carousel (Pita Cerita):
        1. Upload tiap foto ke /{PAGE_ID}/photos dengan published=false (unlisted).
        2. Buat post di /{PAGE_ID}/feed dengan melampirkan seluruh ID foto dan caption narasi.
        """
        if not self.is_configured:
            raise ValueError("Facebook Fanspage credentials (FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN) belum dikonfigurasi di .env")

        if not image_paths:
            raise ValueError("Tidak ada file gambar yang diberikan untuk carousel.")

        uploaded_media_ids = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. Upload individual photos as unlisted
            for idx, img_path_str in enumerate(image_paths):
                img_path = Path(img_path_str)
                if not img_path.exists():
                    raise FileNotFoundError(f"File gambar tidak ditemukan: {img_path_str}")

                url = f"{self.base_url}/{self.page_id}/photos"
                data = {
                    "access_token": self.access_token,
                    "published": "false",
                    "temporary": "false",
                }

                with open(img_path, "rb") as f:
                    files = {"source": (img_path.name, f, "image/png")}
                    resp = await client.post(url, data=data, files=files)

                if resp.status_code != 200:
                    err_body = resp.json().get("error", {}).get("message", resp.text)
                    await audit_logger.log(
                        level="ERROR",
                        component="FacebookClient",
                        message=f"Gagal mengunggah foto slide #{idx+1} ke Facebook: {err_body}",
                    )
                    raise RuntimeError(f"Gagal mengunggah slide #{idx+1} ke Fanspage: {err_body}")

                result = resp.json()
                uploaded_media_ids.append(result.get("id"))

            # 2. Publish container post to Page Feed with attached media
            feed_url = f"{self.base_url}/{self.page_id}/feed"
            feed_data: Dict[str, Any] = {
                "access_token": self.access_token,
                "message": caption,
            }
            for i, media_id in enumerate(uploaded_media_ids):
                feed_data[f"attached_media[{i}]"] = f'{{"media_fbid":"{media_id}"}}'

            feed_resp = await client.post(feed_url, data=feed_data)
            if feed_resp.status_code != 200:
                err_body = feed_resp.json().get("error", {}).get("message", feed_resp.text)
                await audit_logger.log(
                    level="ERROR",
                    component="FacebookClient",
                    message=f"Gagal membuat feed post carousel di Fanspage: {err_body}",
                )
                raise RuntimeError(f"Gagal mempublikasikan carousel ke Fanspage: {err_body}")

            feed_result = feed_resp.json()
            post_id = feed_result.get("id", "")
            post_url = f"https://www.facebook.com/{post_id}"

            await audit_logger.log(
                level="INFO",
                component="FacebookClient",
                message=f"Berhasil memposting carousel ({len(image_paths)} foto) ke Fanspage: {post_url}",
            )

            return {
                "platform": "facebook",
                "post_id": post_id,
                "post_url": post_url,
                "type": "carousel",
                "attached_photos": uploaded_media_ids,
            }

    async def publish_video(
        self,
        video_path: str,
        title: str,
        description: str,
    ) -> Dict[str, Any]:
        """
        Publikasikan video / Reels (Pita Transformasi & Pita Mini) ke Fanspage:
        POST https://graph.facebook.com/{PAGE_ID}/videos
        """
        if not self.is_configured:
            raise ValueError("Facebook Fanspage credentials (FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN) belum dikonfigurasi di .env")

        v_path = Path(video_path)
        if not v_path.exists():
            raise FileNotFoundError(f"File video tidak ditemukan: {video_path}")

        url = f"{self.base_url}/{self.page_id}/videos"
        data = {
            "access_token": self.access_token,
            "title": title,
            "description": description,
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            with open(v_path, "rb") as f:
                files = {"source": (v_path.name, f, "video/mp4")}
                resp = await client.post(url, data=data, files=files)

        if resp.status_code != 200:
            err_body = resp.json().get("error", {}).get("message", resp.text)
            await audit_logger.log(
                level="ERROR",
                component="FacebookClient",
                message=f"Gagal mengunggah video ke Fanspage: {err_body}",
            )
            raise RuntimeError(f"Gagal mempublikasikan video ke Fanspage: {err_body}")

        result = resp.json()
        video_id = result.get("id", "")
        post_url = f"https://www.facebook.com/{self.page_id}/videos/{video_id}"

        await audit_logger.log(
            level="INFO",
            component="FacebookClient",
            message=f"Berhasil memposting video ke Fanspage: {post_url}",
        )

        return {
            "platform": "facebook",
            "post_id": video_id,
            "post_url": post_url,
            "type": "video",
        }

    async def publish_single_photo(
        self,
        image_path: str,
        caption: str,
    ) -> Dict[str, Any]:
        """
        Publikasikan single photo ke Fanspage:
        POST https://graph.facebook.com/{PAGE_ID}/photos
        """
        if not self.is_configured:
            raise ValueError("Facebook Fanspage credentials (FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN) belum dikonfigurasi di .env")

        img_path = Path(image_path)
        if not img_path.exists():
            raise FileNotFoundError(f"File gambar tidak ditemukan: {image_path}")

        url = f"{self.base_url}/{self.page_id}/photos"
        data = {
            "access_token": self.access_token,
            "caption": caption,
            "published": "true",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(img_path, "rb") as f:
                files = {"source": (img_path.name, f, "image/png")}
                resp = await client.post(url, data=data, files=files)

        if resp.status_code != 200:
            err_body = resp.json().get("error", {}).get("message", resp.text)
            await audit_logger.log(
                level="ERROR",
                component="FacebookClient",
                message=f"Gagal mengunggah foto ke Fanspage: {err_body}",
            )
            raise RuntimeError(f"Gagal mempublikasikan foto ke Fanspage: {err_body}")

        result = resp.json()
        photo_id = result.get("id", "")
        post_id = result.get("post_id", photo_id)
        post_url = f"https://www.facebook.com/{post_id}"

        await audit_logger.log(
            level="INFO",
            component="FacebookClient",
            message=f"Berhasil memposting single photo ke Fanspage: {post_url}",
        )

        return {
            "platform": "facebook",
            "post_id": post_id,
            "post_url": post_url,
            "type": "photo",
        }


facebook_client = FacebookClient()
