"""
Long-Term Content Memory untuk Pita Media.
Menyimpan memori komprehensif terstruktur tentang ide konten, tema, karakter,
plot cerita, objek miniatur, bahan kreasi, visual style, hook, dan performa historis.
Mendukung pencarian kemiripan semantik untuk mencegah repetisi dan kelelahan topik (fatigue).
"""

import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Set
from sqlalchemy import select, desc
from core.database import get_db
from database.models import LongTermMemoryItem, Content

logger = logging.getLogger("pita_media.learning.memory")


class LongTermContentMemory:
    """
    Manajer memori jangka panjang terstruktur untuk seluruh konten Pita Media.
    """

    def __init__(self, default_horizon_days: int = 90):
        self.default_horizon_days = default_horizon_days
        self.stopwords = {
            "yang", "dan", "dari", "untuk", "dengan", "pada", "dalam", "adalah", "ini", "itu",
            "akan", "bisa", "saat", "oleh", "atau", "kita", "kamu", "saya", "mereka", "tentang",
            "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "with"
        }

    def _tokenize(self, text: str) -> Set[str]:
        if not text:
            return set()
        words = re.findall(r'\b[a-zA-Z0-9_]{3,}\b', text.lower())
        return {w for w in words if w not in self.stopwords}

    def _calculate_overlap(self, set_a: Set[str], set_b: Set[str]) -> float:
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a.intersection(set_b))
        union = len(set_a.union(set_b))
        return intersection / union if union > 0 else 0.0

    def record_memory(
        self,
        pilar: str,
        title: str,
        content_id: Optional[str] = None,
        theme: Optional[str] = None,
        subtheme: Optional[str] = None,
        characters: Optional[List[str]] = None,
        story_plot: Optional[str] = None,
        transformation_type: Optional[str] = None,
        miniature_object: Optional[str] = None,
        creation_materials: Optional[List[str]] = None,
        hook_text: Optional[str] = None,
        hook_type: Optional[str] = None,
        visual_style: Optional[str] = None,
        music_recommendation: Optional[str] = None,
        platforms_published: Optional[List[str]] = None,
        performance_summary: Optional[Dict[str, Any]] = None,
        lessons_learned: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> str:
        """
        Menyimpan memori konten baru ke database terstruktur.
        """
        now = datetime.now(timezone.utc)
        item = LongTermMemoryItem(
            content_id=content_id,
            pilar=pilar,
            title=title,
            theme=theme,
            subtheme=subtheme,
            characters=characters or [],
            story_plot=story_plot,
            transformation_type=transformation_type,
            miniature_object=miniature_object,
            creation_materials=creation_materials or [],
            hook_text=hook_text,
            hook_type=hook_type,
            visual_style=visual_style,
            music_recommendation=music_recommendation,
            platforms_published=platforms_published or [],
            performance_summary=performance_summary or {},
            lessons_learned=lessons_learned,
            tags=tags or [],
            created_at=now,
        )

        with get_db() as db:
            db.add(item)
            db.commit()
            return item.id

    def find_similar_memories(
        self,
        query_title: str,
        pilar: Optional[str] = None,
        query_concept: Optional[str] = None,
        horizon_days: Optional[int] = None,
        threshold: float = 0.40,
    ) -> List[Dict[str, Any]]:
        """
        Mencari ide konten yang memiliki kemiripan semantik dalam rentang horizon hari.
        """
        days = horizon_days or self.default_horizon_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        target_tokens = self._tokenize(f"{query_title} {query_concept or ''}")

        matches = []
        with get_db() as db:
            query = db.query(LongTermMemoryItem).filter(LongTermMemoryItem.created_at >= cutoff)
            if pilar:
                query = query.filter(LongTermMemoryItem.pilar == pilar)
            items = query.all()

            for item in items:
                item_text = f"{item.title} {item.theme or ''} {item.story_plot or ''} {item.miniature_object or ''} {item.transformation_type or ''}"
                item_tokens = self._tokenize(item_text)
                sim = self._calculate_overlap(target_tokens, item_tokens)

                if sim >= threshold:
                    matches.append({
                        "id": item.id,
                        "title": item.title,
                        "pilar": item.pilar,
                        "similarity_score": round(sim, 3),
                        "theme": item.theme,
                        "created_at": item.created_at.isoformat() if item.created_at else "",
                        "performance_summary": item.performance_summary,
                        "lessons_learned": item.lessons_learned,
                    })

        matches.sort(key=lambda x: x["similarity_score"], reverse=True)
        return matches

    def get_recent_memory_summary(self, days: int = 30) -> Dict[str, Any]:
        """
        Mengambil rekap memori tema, hook, dan objek yang sering digunakan belakangan ini.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        with get_db() as db:
            items = db.query(LongTermMemoryItem).filter(LongTermMemoryItem.created_at >= cutoff).all()
            
            pilar_counts = {}
            themes = []
            objects = []
            hooks = []

            for it in items:
                pilar_counts[it.pilar] = pilar_counts.get(it.pilar, 0) + 1
                if it.theme:
                    themes.append(it.theme)
                if it.miniature_object:
                    objects.append(it.miniature_object)
                if it.hook_text:
                    hooks.append(it.hook_text)

            return {
                "horizon_days": days,
                "total_memories": len(items),
                "pilar_distribution": pilar_counts,
                "recent_themes": themes[:10],
                "recent_objects": objects[:10],
                "recent_hooks": hooks[:10],
            }


long_term_memory = LongTermContentMemory()
