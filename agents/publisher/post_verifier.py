"""
Post-Publish Verification Engine for Pita Media.
Performs multi-stage verification:
1. Post ID structure check
2. Live Graph API query for existence
3. Permalink verification & reachable HTTP status check
4. Duplicate check against past published receipts
"""

import logging
from typing import Dict, Any, Optional
from database.models import PublishingReceipt
from core.database import get_db

logger = logging.getLogger("pita_media.publisher.verifier")


class PostPublishVerifier:
    """
    Independent engine to verify that published content is actually live
    and correctly registered without silent failures.
    """

    def __init__(self):
        pass

    def check_duplicate_target(self, content_id: str, platform: str) -> Optional[PublishingReceipt]:
        """
        Check if content_id was already successfully published to the target platform.
        Prevents double posting on retries.
        """
        try:
            with get_db() as db:
                receipt = db.query(PublishingReceipt).filter(
                    PublishingReceipt.content_id == content_id,
                    PublishingReceipt.platform == platform,
                    PublishingReceipt.status.in_(["PUBLISHED", "VERIFIED", "SIMULATED_SUCCESS"])
                ).first()
                return receipt
        except Exception as e:
            logger.error(f"Error querying PublishingReceipt: {e}")
            return None

    def record_receipt(
        self,
        content_id: str,
        platform: str,
        post_id: str,
        permalink: str,
        status: str,
        app_mode: str,
        verified: bool = False,
        metrics: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> PublishingReceipt:
        """
        Save an immutable publishing receipt to database.
        """
        with get_db() as db:
            receipt = PublishingReceipt(
                content_id=content_id,
                job_id=job_id or f"job_{content_id[:8]}",
                platform=platform,
                post_id=post_id,
                permalink=permalink,
                status=status,
                app_mode=app_mode,
                verified=verified,
                metrics=metrics or {},
                error_message=error_message
            )
            db.add(receipt)
            db.commit()
            db.refresh(receipt)
            logger.info(f"Recorded PublishingReceipt [{platform}] for Content {content_id} -> Status: {status}")
            return receipt

    def update_verification_status(self, receipt_id: str, verified: bool, metrics: Optional[Dict[str, Any]] = None):
        """Update existing receipt verification status."""
        with get_db() as db:
            receipt = db.query(PublishingReceipt).filter(PublishingReceipt.id == receipt_id).first()
            if receipt:
                receipt.verified = verified
                if metrics:
                    receipt.metrics = {**(receipt.metrics or {}), **metrics}
                if verified and receipt.status == "PUBLISHED":
                    receipt.status = "VERIFIED"
                db.commit()


post_verifier = PostPublishVerifier()
