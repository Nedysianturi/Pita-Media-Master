"""
Pita Media Enterprise Publisher - Dedicated Facebook Fanspage Publisher
Publishes image carousels, videos, and stories to Facebook Fanspage (@Pitamediaid, ID: 1253340697871457).
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from agents.publisher.base_publisher import BasePlatformPublisher
from providers.facebook_client import facebook_client
from config.settings import settings

logger = logging.getLogger("pita.publisher.facebook")

class FacebookPublisher(BasePlatformPublisher):
    def __init__(self):
        super().__init__(platform_id="facebook", display_name="Facebook Fanspage @Pitamediaid")
        self.client = facebook_client

    async def publish(
        self,
        content_id: str,
        job_id: str,
        content_payload: Dict[str, Any],
        is_dry_run: bool = True,
        db_session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        title = content_payload.get("title", "")
        caption = content_payload.get("caption", "")
        media_paths = content_payload.get("media_paths", [])
        media_type = content_payload.get("media_type", "image")

        if is_dry_run:
            mock_id = f"mock_fb_{job_id[:8]}"
            mock_url = f"https://facebook.com/Pitamediaid/posts/{mock_id}"
            logger.info(f"[DRY_RUN] Facebook publication simulated for '{title}' (URL: {mock_url})")
            return {
                "platform": "facebook",
                "status": "PUBLISHED",
                "external_post_id": mock_id,
                "permalink": mock_url,
                "response_metadata": {"mode": "DRY_RUN", "simulated": True},
                "error_message": None
            }

        try:
            # Live Meta API execution
            if media_type == "video" and media_paths:
                res = await self.client.publish_video(
                    video_path=media_paths[0],
                    title=title,
                    description=caption
                )
            elif len(media_paths) > 1:
                res = await self.client.publish_carousel(
                    image_paths=media_paths,
                    message=f"{title}\n\n{caption}"
                )
            elif len(media_paths) == 1:
                res = await self.client.publish_photo(
                    image_path=media_paths[0],
                    caption=f"{title}\n\n{caption}"
                )
            else:
                res = await self.client.publish_text_post(
                    message=f"{title}\n\n{caption}"
                )

            post_id = res.get("id") or res.get("post_id")
            post_url = res.get("post_url") or f"https://facebook.com/{post_id}"

            return {
                "platform": "facebook",
                "status": "PUBLISHED",
                "external_post_id": str(post_id),
                "permalink": post_url,
                "response_metadata": res,
                "error_message": None
            }
        except Exception as e:
            logger.error(f"Facebook publish failed for Job {job_id}: {e}")
            return {
                "platform": "facebook",
                "status": "FAILED",
                "external_post_id": None,
                "permalink": None,
                "response_metadata": {"error": str(e)},
                "error_message": str(e)
            }

    def publish_content(self, content_payload: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
        """Synchronous publish dispatcher supporting DRY_RUN."""
        title = content_payload.get("title", "")
        caption = content_payload.get("caption", "")
        if dry_run:
            import time
            post_id = f"dry_run_fb_{int(time.time())}"
            permalink = f"https://facebook.com/Pitamediaid/posts/{post_id}"
            return {
                "success": True,
                "platform": "facebook",
                "mode": "DRY_RUN",
                "post_id": post_id,
                "permalink": permalink,
                "status": "SIMULATED_SUCCESS",
                "message": "Facebook publish simulated successfully in DRY_RUN mode."
            }
        return {
            "success": False,
            "platform": "facebook",
            "status": "FAILED",
            "error": "Live publishing requires async loop in Worker."
        }

    async def verify_post(self, external_post_id: str, permalink: Optional[str] = None) -> Dict[str, Any]:
        if not external_post_id or "mock" in external_post_id:
            return {"verified": True, "method": "DRY_RUN_MOCK"}
        try:
            import httpx
            token = settings.FB_PAGE_ACCESS_TOKEN
            url = f"https://graph.facebook.com/{settings.FB_API_VERSION}/{external_post_id}?fields=id,permalink_url&access_token={token}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return {"verified": True, "permalink": data.get("permalink_url") or permalink, "method": "META_GRAPH_API"}
            return {"verified": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"verified": False, "error": str(e)}

facebook_publisher = FacebookPublisher()
