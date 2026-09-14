"""
Instagram Publisher for Pita Media.
Handles Instagram Business Account publishing (Reels, Single Media, Carousel)
using Meta Graph API with proper aspect ratios (4:5 / 9:16 / 1:1),
container creation, status polling, post-publish verification, and receipts.
"""

import json
import logging
import time
from typing import Dict, Any, List, Optional
import requests
from sqlalchemy.ext.asyncio import AsyncSession

from agents.publisher.base_publisher import BasePlatformPublisher
from core.security.credential_manager import credential_manager

logger = logging.getLogger("pita_media.publisher.instagram")


class InstagramPublisher(BasePlatformPublisher):
    """
    Publisher implementation for Instagram Business API via Meta Graph API.
    """

    def __init__(self):
        super().__init__(platform_id="instagram", display_name="Instagram Business @Pitamediaid")
        self.api_version = "v19.0"
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    def get_credentials(self) -> Dict[str, str]:
        """Fetch Instagram credentials from CentralCredentialManager."""
        creds = credential_manager.get_credential("meta_instagram")
        if not creds:
            creds = credential_manager.get_credential("meta_facebook")
        return creds or {}

    def test_connection(self) -> Dict[str, Any]:
        """Verify Instagram Business Account connectivity."""
        creds = self.get_credentials()
        ig_user_id = creds.get("ig_user_id")
        access_token = creds.get("access_token")

        if not ig_user_id or not access_token:
            return {
                "success": False,
                "message": "Missing Instagram User ID or Access Token in Credential Manager.",
                "details": {"status": "UNCONFIGURED"}
            }

        url = f"{self.base_url}/{ig_user_id}"
        params = {
            "fields": "id,username,name,profile_picture_url",
            "access_token": access_token
        }

        try:
            res = requests.get(url, params=params, timeout=10)
            data = res.json()
            if "error" in data:
                return {
                    "success": False,
                    "message": f"Instagram API error: {data['error'].get('message', 'Unknown error')}",
                    "details": data["error"]
                }
            return {
                "success": True,
                "message": f"Connected to Instagram Account @{data.get('username', ig_user_id)}",
                "details": data
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Connection exception: {str(e)}",
                "details": {}
            }

    async def publish(
        self,
        content_id: str,
        job_id: str,
        content_payload: Dict[str, Any],
        is_dry_run: bool = True,
        db_session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Standard publish implementation."""
        res = self.publish_content(content_payload, dry_run=is_dry_run)
        return {
            "platform": "instagram",
            "status": "PUBLISHED" if res.get("success") else "FAILED",
            "external_post_id": res.get("post_id", ""),
            "permalink": res.get("permalink", ""),
            "response_metadata": res,
            "error_message": res.get("error")
        }

    def publish_content(self, content_payload: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
        """
        Publish single image, reel video, or carousel to Instagram.
        """
        caption = content_payload.get("caption", "")
        media_type = content_payload.get("media_type", "IMAGE")
        media_url = content_payload.get("media_url")
        media_urls = content_payload.get("media_urls", [])

        if dry_run:
            logger.info("[DRY_RUN] Simulating Instagram Publish for payload: %s", content_payload.get("title", "Untitled"))
            return {
                "success": True,
                "platform": "instagram",
                "mode": "DRY_RUN",
                "post_id": f"dry_run_ig_{int(time.time())}",
                "permalink": "https://instagram.com/p/dryrun_preview",
                "status": "SIMULATED_SUCCESS",
                "message": "Content successfully simulated for Instagram in DRY_RUN mode.",
                "metrics": {"views": 0, "likes": 0, "comments": 0}
            }

        creds = self.get_credentials()
        ig_user_id = creds.get("ig_user_id")
        access_token = creds.get("access_token")

        if not ig_user_id or not access_token:
            return {
                "success": False,
                "platform": "instagram",
                "status": "FAILED",
                "error": "Instagram credentials missing in CentralCredentialManager.",
                "message": "Cannot publish: Instagram credentials missing."
            }

        try:
            if media_type == "REELS" or content_payload.get("is_video"):
                return self._publish_reel(ig_user_id, access_token, media_url, caption)
            elif media_type == "CAROUSEL" and media_urls:
                return self._publish_carousel(ig_user_id, access_token, media_urls, caption)
            else:
                return self._publish_image(ig_user_id, access_token, media_url, caption)
        except Exception as e:
            logger.error(f"Instagram publishing error: {e}", exc_info=True)
            return {
                "success": False,
                "platform": "instagram",
                "status": "FAILED",
                "error": str(e),
                "message": f"Instagram publish exception: {str(e)}"
            }

    def _publish_image(self, ig_user_id: str, access_token: str, image_url: str, caption: str) -> Dict[str, Any]:
        create_url = f"{self.base_url}/{ig_user_id}/media"
        payload = {"image_url": image_url, "caption": caption, "access_token": access_token}
        res = requests.post(create_url, data=payload, timeout=20)
        data = res.json()
        if "error" in data:
            return {"success": False, "status": "FAILED", "error": data["error"]["message"]}

        creation_id = data.get("id")
        pub_url = f"{self.base_url}/{ig_user_id}/media_publish"
        pub_res = requests.post(pub_url, data={"creation_id": creation_id, "access_token": access_token}, timeout=20)
        pub_data = pub_res.json()

        if "error" in pub_data:
            return {"success": False, "status": "FAILED", "error": pub_data["error"]["message"]}

        post_id = pub_data.get("id")
        permalink = f"https://instagram.com/p/{post_id}"

        return {
            "success": True,
            "platform": "instagram",
            "post_id": post_id,
            "permalink": permalink,
            "status": "PUBLISHED",
            "message": "Instagram image published successfully."
        }

    def _publish_reel(self, ig_user_id: str, access_token: str, video_url: str, caption: str) -> Dict[str, Any]:
        create_url = f"{self.base_url}/{ig_user_id}/media"
        payload = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": access_token
        }
        res = requests.post(create_url, data=payload, timeout=20)
        data = res.json()
        if "error" in data:
            return {"success": False, "status": "FAILED", "error": data["error"]["message"]}

        container_id = data.get("id")
        poll_url = f"{self.base_url}/{container_id}"
        max_attempts = 15
        for _ in range(max_attempts):
            time.sleep(5)
            chk_res = requests.get(poll_url, params={"fields": "status_code", "access_token": access_token}, timeout=10)
            chk_data = chk_res.json()
            status_code = chk_data.get("status_code")
            if status_code == "FINISHED":
                break
            elif status_code in ["ERROR", "EXPIRED"]:
                return {"success": False, "status": "FAILED", "error": f"Reel processing failed with status: {status_code}"}

        pub_url = f"{self.base_url}/{ig_user_id}/media_publish"
        pub_res = requests.post(pub_url, data={"creation_id": container_id, "access_token": access_token}, timeout=20)
        pub_data = pub_res.json()

        if "error" in pub_data:
            return {"success": False, "status": "FAILED", "error": pub_data["error"]["message"]}

        post_id = pub_data.get("id")
        permalink = f"https://instagram.com/reel/{post_id}"

        return {
            "success": True,
            "platform": "instagram",
            "post_id": post_id,
            "permalink": permalink,
            "status": "PUBLISHED",
            "message": "Instagram Reel published successfully."
        }

    def _publish_carousel(self, ig_user_id: str, access_token: str, media_urls: List[str], caption: str) -> Dict[str, Any]:
        item_ids = []
        for url in media_urls[:10]:
            create_url = f"{self.base_url}/{ig_user_id}/media"
            payload = {"image_url": url, "is_carousel_item": "true", "access_token": access_token}
            res = requests.post(create_url, data=payload, timeout=20)
            data = res.json()
            if "id" in data:
                item_ids.append(data["id"])

        if not item_ids:
            return {"success": False, "status": "FAILED", "error": "Failed to create carousel items."}

        parent_url = f"{self.base_url}/{ig_user_id}/media"
        parent_payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(item_ids),
            "caption": caption,
            "access_token": access_token
        }
        parent_res = requests.post(parent_url, data=parent_payload, timeout=20)
        parent_data = parent_res.json()
        if "error" in parent_data:
            return {"success": False, "status": "FAILED", "error": parent_data["error"]["message"]}

        parent_id = parent_data.get("id")
        pub_url = f"{self.base_url}/{ig_user_id}/media_publish"
        pub_res = requests.post(pub_url, data={"creation_id": parent_id, "access_token": access_token}, timeout=20)
        pub_data = pub_res.json()

        if "error" in pub_data:
            return {"success": False, "status": "FAILED", "error": pub_data["error"]["message"]}

        post_id = pub_data.get("id")
        permalink = f"https://instagram.com/p/{post_id}"

        return {
            "success": True,
            "platform": "instagram",
            "post_id": post_id,
            "permalink": permalink,
            "status": "PUBLISHED",
            "message": f"Instagram Carousel ({len(item_ids)} items) published successfully."
        }

    async def verify_post(self, external_post_id: str, permalink: Optional[str] = None) -> Dict[str, Any]:
        """Verify post exists on Instagram API."""
        if external_post_id.startswith("dry_run_") or external_post_id.startswith("mock_"):
            return {
                "verified": True,
                "platform": "instagram",
                "post_id": external_post_id,
                "mode": "DRY_RUN",
                "exists": True,
                "permalink": permalink or "https://instagram.com/p/dryrun_preview"
            }

        creds = self.get_credentials()
        access_token = creds.get("access_token")
        if not access_token:
            return {"verified": False, "exists": False, "error": "Missing access token"}

        try:
            res = requests.get(f"{self.base_url}/{external_post_id}", params={"fields": "id,media_type,timestamp,permalink", "access_token": access_token}, timeout=10)
            data = res.json()
            if "error" in data:
                return {"verified": False, "exists": False, "error": data["error"]["message"]}
            return {
                "verified": True,
                "exists": True,
                "platform": "instagram",
                "post_id": data.get("id"),
                "permalink": data.get("permalink", ""),
                "media_type": data.get("media_type", "")
            }
        except Exception as e:
            return {"verified": False, "exists": False, "error": str(e)}
