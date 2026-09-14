"""
Semantic Duplicate & Idea Memory Engine for Pita Media.
Performs 30-90 day semantic deduplication, novelty scoring, and hook-fatigue detection
to ensure topics, angles, and opening hooks remain fresh and engaging.
"""

import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from core.database import get_db
from database.models import Content

logger = logging.getLogger("pita_media.intelligence.semantic")


class SemanticDuplicateDetector:
    """
    Evaluates new content proposals against historical archive (30-90 days)
    to calculate Novelty Score and prevent repetitive content.
    """

    def __init__(self, memory_days: int = 60):
        self.memory_days = memory_days

    def _tokenize(self, text: str) -> set:
        """Tokenize text into lowercased word set for word overlap similarity."""
        import re
        words = re.findall(r'\b[a-zA-Z0-9_]{3,}\b', text.lower())
        stopwords = {"yang", "dan", "dari", "untuk", "dengan", "pada", "dalam", "adalah", "ini", "itu", "akan"}
        return {w for w in words if w not in stopwords}

    def _jaccard_similarity(self, set_a: set, set_b: set) -> float:
        """Compute Jaccard similarity index between two sets."""
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a.intersection(set_b))
        union = len(set_a.union(set_b))
        return intersection / union if union > 0 else 0.0

    def evaluate_novelty(self, title: str, summary: str, pilar: str) -> Dict[str, Any]:
        """
        Calculates novelty score (0.0 - 1.0), similarity index, and detects recent duplicates.
        """
        candidate_text = f"{title} {summary}"
        candidate_tokens = self._tokenize(candidate_text)

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.memory_days)
        max_similarity = 0.0
        most_similar_content = None

        try:
            with get_db() as db:
                past_items = db.query(Content).filter(Content.created_at >= cutoff).all()
                db.rollback()
                for item in past_items:
                    item_text = f"{item.title or ''} {getattr(item, 'caption', '') or getattr(item, 'story_content', '') or ''}"
                    item_tokens = self._tokenize(item_text)
                    sim = self._jaccard_similarity(candidate_tokens, item_tokens)
                    if sim > max_similarity:
                        max_similarity = sim
                        most_similar_content = {
                            "id": item.id,
                            "title": item.title,
                            "pilar": item.pilar,
                            "created_at": item.created_at.isoformat() if item.created_at else ""
                        }
        except Exception as e:
            logger.error(f"Error checking semantic duplicates in database: {e}")

        # Novelty score is inverse of max similarity
        novelty_score = max(0.0, min(1.0, 1.0 - max_similarity))
        is_repetitive = max_similarity > 0.65  # More than 65% overlap

        verdict = "PASSED"
        reason = "Ide segar dan orisinal."
        if is_repetitive:
            verdict = "REJECTED_TOO_SIMILAR"
            reason = f"Ide terlalu mirip dengan konten sebelumnya: '{most_similar_content.get('title', '')}' ({int(max_similarity*100)}% kesamaan)."

        return {
            "novelty_score": round(novelty_score, 3),
            "similarity_index": round(max_similarity, 3),
            "is_repetitive": is_repetitive,
            "verdict": verdict,
            "reason": reason,
            "most_similar_item": most_similar_content,
            "memory_horizon_days": self.memory_days
        }


semantic_detector = SemanticDuplicateDetector()
