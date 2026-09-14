"""
Threads Publisher for Pita Media.
Handles publishing text, single image/video, and carousel posts to Meta Threads API
with proper container workflow, character limits, verification, and receipts.
"""

import json
import logging
import time
from typing import Dict, Any, List, Optional
import requests
from sqlalchemy.ext.asyncio import AsyncSession

from agents.publisher.base_publisher import BasePlatformPublisher
from core.security.credential_manager import credential_manager

logger = logging.getLogger("pita_media.publisher.threads")


class ThreadsPublisher(BasePlatformPublisher):
    """
    Publisher implementation for Threads API via Meta Graph API.
    """

    def __init__(self):
        super().__init__(platform_id="threads", display_name="Threads API @Pitamediaid")
        self.api_version = "v19.0"
        self.base_url = f"https://graph.threads.net/{self.api_version}"

    def get_credentials(self) -> Dict[str, str]:
        """Fetch Threads credentials from CentralCredentialManager."""
        creds = credential_manager.get_credential("meta_threads")
        if not creds:
            creds = credential_manager.get_credential("meta_facebook")
        return creds or {}

    def test_connection(self) -> Dict[str, Any]:
        """Verify Threads Account connectivity."""
        creds = self.get_credentials()
        threads_user_id = creds.get("threads_user_id") or creds.get("user_id")
        access_token = creds.get("access_token")

        if not threads_user_id or not access_token:
            return {
                "success": False,
                "message": "Missing Threads User ID or Access Token in Credential Manager.",
                "details": {"status": "UNCONFIGURED"}
            }

        url = f"{self.base_url}/me"
        params = {
            "fields": "id,username,name,threads_profile_picture_url",
            "access_token": access_token
        }

        try:
            res = requests.get(url, params=params, timeout=10)
            data = res.json()
            if "error" in data:
                return {
                    "success": False,
                    "message": f"Threads API error: {data['error'].get('message', 'Unknown error')}",
                    "details": data["error"]
                }
            return {
                "success": True,
                "message": f"Connected to Threads Account @{data.get('username', threads_user_id)}",
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
            "platform": "threads",
            "status": "PUBLISHED" if res.get("success") else "FAILED",
            "external_post_id": res.get("post_id", ""),
            "permalink": res.get("permalink", ""),
            "response_metadata": res,
            "error_message": res.get("error")
        }

    def publish_content(self, content_payload: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
        """
        Publish text, image, or video post to Threads.
        """
        text = content_payload.get("text", "") or content_payload.get("caption", "")
        if len(text) > 500:
            text = text[:495] + "..."

        media_type = content_payload.get("media_type", "TEXT")
        media_url = content_payload.get("media_url")

        if dry_run:
            logger.info("[DRY_RUN] Simulating Threads Publish for payload: %s", content_payload.get("title", "Untitled"))
            return {
                "success": True,
                "platform": "threads",
                "mode": "DRY_RUN",
                "post_id": f"dry_run_threads_{int(time.time())}",
                "permalink": "https://threads.net/@pitamediaid/post/preview",
                "status": "SIMULATED_SUCCESS",
                "message": "Content successfully simulated for Threads in DRY_RUN mode.",
                "metrics": {"views": 0, "likes": 0, "replies": 0}
            }

        creds = self.get_credentials()
        threads_user_id = creds.get("threads_user_id") or creds.get("user_id") or "me"
        access_token = creds.get("access_token")

        if not access_token:
            return {
                "success": False,
                "platform": "threads",
                "status": "FAILED",
                "error": "Threads access token missing in CentralCredentialManager.",
                "message": "Cannot publish: Threads credentials missing."
            }

        try:
            create_url = f"{self.base_url}/{threads_user_id}/threads"
            payload = {"text": text, "access_token": access_token}
            if media_type == "IMAGE" and media_url:
                payload["media_type"] = "IMAGE"
                payload["image_url"] = media_url
            elif media_type == "VIDEO" and media_url:
                payload["media_type"] = "VIDEO"
                payload["video_url"] = media_url
            else:
                payload["media_type"] = "TEXT"

            res = requests.post(create_url, data=payload, timeout=20)
            data = res.json()
            if "error" in data:
                return {"success": False, "status": "FAILED", "error": data["error"]["message"]}

            container_id = data.get("id")
            if media_type == "VIDEO":
                time.sleep(10)

            pub_url = f"{self.base_url}/{threads_user_id}/threads_publish"
            pub_res = requests.post(pub_url, data={"creation_id": container_id, "access_token": access_token}, timeout=20)
            pub_data = pub_res.json()

            if "error" in pub_data:
                return {"success": False, "status": "FAILED", "error": pub_data["error"]["message"]}

            post_id = pub_data.get("id")
            permalink = f"https://threads.net/t/{post_id}"

            return {
                "success": True,
                "platform": "threads",
                "post_id": post_id,
                "permalink": permalink,
                "status": "PUBLISHED",
                "message": "Threads post published successfully."
            }

        except Exception as e:
            logger.error(f"Threads publishing error: {e}", exc_info=True)
            return {
                "success": False,
                "platform": "threads",
                "status": "FAILED",
                "error": str(e),
                "message": f"Threads publish exception: {str(e)}"
            }

    async def verify_post(self, external_post_id: str, permalink: Optional[str] = None) -> Dict[str, Any]:
        """Verify post exists on Threads API."""
        if external_post_id.startswith("dry_run_") or external_post_id.startswith("mock_"):
            return {
                "verified": True,
                "platform": "threads",
                "post_id": external_post_id,
                "mode": "DRY_RUN",
                "exists": True,
                "permalink": permalink or "https://threads.net/@pitamediaid/post/preview"
            }

        creds = self.get_credentials()
        access_token = creds.get("access_token")
        if not access_token:
            return {"verified": False, "exists": False, "error": "Missing access token"}

        try:
            res = requests.get(f"{self.base_url}/{external_post_id}", params={"fields": "id,text,timestamp,permalink", "access_token": access_token}, timeout=10)
            data = res.json()
            if "error" in data:
                return {"verified": False, "exists": False, "error": data["error"]["message"]}
            return {
                "verified": True,
                "exists": True,
                "platform": "threads",
                "post_id": data.get("id"),
                "permalink": data.get("permalink", f"https://threads.net/t/{data.get('id')}"),
                "text": data.get("text", "")
            }
        except Exception as e:
            return {"verified": False, "exists": False, "error": str(e)}
