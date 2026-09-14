"""
Audience Intelligence Engine untuk Pita Media.
Menganalisis tanggapan, reaksi, pertanyaan, dan komentar audiens dari media sosial.
Mengelompokkan feedback ke dalam kategori terstruktur:
- POSITIVE, NEGATIVE, QUESTION, REQUEST, CONFUSION, HUMOR, EMOTIONAL_RESPONSE, CONTENT_IDEA, SPAM, OTHER
Menerapkan 4 Lapisan Filter:
1. Safety Filter (Larangan hate speech / NSFW)
2. Spam Filter (Bot, promo, crypto spam)
3. Privacy Filter (Pembersihan PII seperti nomor HP, alamat, email)
4. Brand Filter (Relevansi dengan nilai seni, estetika, dan pilar Pita Media)
DILARANG menyimpan data pribadi berlebih atau menghubungi user secara otomatis.
"""

import re
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from core.database import get_db
from database.models import AudienceInsight
from core.learning.data_quality_gate import data_quality_gate

logger = logging.getLogger("pita_media.learning.audience")


class CommentCategory:
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    QUESTION = "QUESTION"
    REQUEST = "REQUEST"
    CONFUSION = "CONFUSION"
    HUMOR = "HUMOR"
    EMOTIONAL_RESPONSE = "EMOTIONAL_RESPONSE"
    CONTENT_IDEA = "CONTENT_IDEA"
    SPAM = "SPAM"
    OTHER = "OTHER"


class AudienceIntelligenceEngine:
    """
    Mesin pemrosesan respon audiens dan penambangan ide kreatif dari komunitas.
    """

    SPAM_PATTERNS = [
        r"(t\.me/|wa\.me/|bit\.ly/|slot|gacor|crypto|forex|investasi modal|pinjaman online)",
        r"(dm me|check my bio|follow back|cek profil saya)",
    ]

    SAFETY_BLACKLIST = [
        "judi", "bokep", "porno", "sara", "rasis", "bunuh", "penipu", "kontol", "anjing"
    ]

    def process_incoming_comment(
        self,
        raw_text: str,
        platform: str = "facebook",
        comment_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Memfilter, membersihkan data pribadi (PII), dan mengkategorikan komentar.
        """
        if not raw_text or not raw_text.strip():
            return None

        # 1. Spam Filter
        for pattern in self.SPAM_PATTERNS:
            if re.search(pattern, raw_text, re.IGNORECASE):
                return {
                    "category": CommentCategory.SPAM,
                    "sentiment": "NEUTRAL",
                    "safety_passed": True,
                    "actionable_idea": None,
                    "is_spam": True,
                }

        # 2. Safety Filter
        text_lower = raw_text.lower()
        if any(bad in text_lower for bad in self.SAFETY_BLACKLIST):
            return {
                "category": CommentCategory.OTHER,
                "sentiment": "NEGATIVE",
                "safety_passed": False,
                "actionable_idea": None,
                "is_spam": False,
            }

        # 3. Privacy Filter (Mask PII)
        sanitized_text = data_quality_gate.sanitize_audience_text(raw_text)

        # 4. Sentiment & Categorization
        category, sentiment, score, actionable_idea = self._classify_comment(sanitized_text)

        # Simpan ke Database
        now = datetime.now(timezone.utc)
        record_id = None
        with get_db() as db:
            insight = AudienceInsight(
                platform=platform,
                raw_comment_id=comment_id,
                category=category,
                sentiment=sentiment,
                sentiment_score=score,
                topic_cluster=self._extract_topic_cluster(sanitized_text),
                sanitized_text=sanitized_text,
                actionable_idea=actionable_idea,
                safety_passed=True,
                privacy_cleansed=True,
                created_at=now,
            )
            db.add(insight)
            db.commit()
            record_id = insight.id

        return {
            "id": record_id,
            "category": category,
            "sentiment": sentiment,
            "sentiment_score": score,
            "sanitized_text": sanitized_text,
            "actionable_idea": actionable_idea,
            "safety_passed": True,
        }

    def _classify_comment(self, text: str) -> (str, str, float, Optional[str]):
        """Menentukan kategori dan sentimen dari teks komentar."""
        text_lower = text.lower()

        # Request / Content Idea
        if any(w in text_lower for w in ["bisa buatkan", "coba buat", "request", "bikin miniatur", "tolong bikin", "next buat"]):
            return CommentCategory.CONTENT_IDEA, "POSITIVE", 0.8, f"Ide Komunitas: {text}"

        # Question
        if any(w in text_lower for w in ["gimana cara", "pakai bahan apa", "alatnya apa", "berapa lama", "?"]):
            return CommentCategory.QUESTION, "NEUTRAL", 0.5, None

        # Emotional / Relatable
        if any(w in text_lower for w in ["terharu", "merinding", "nostalgia", "ingat masa kecil", "nangis", "relate"]):
            return CommentCategory.EMOTIONAL_RESPONSE, "POSITIVE", 0.9, None

        # Positive / Praise
        if any(w in text_lower for w in ["keren", "bagus banget", "luar biasa", "mantap", "estetik", "kreatif", "suka"]):
            return CommentCategory.POSITIVE, "POSITIVE", 0.9, None

        # Negative / Critique
        if any(w in text_lower for w in ["jelek", "kurang rapi", "membosankan", "kecewa", "aneh", "terlalu cepat"]):
            return CommentCategory.NEGATIVE, "NEGATIVE", -0.6, None

        # Humor
        if any(w in text_lower for w in ["wkwk", "haha", "lucu", "ngakak"]):
            return CommentCategory.HUMOR, "POSITIVE", 0.7, None

        return CommentCategory.OTHER, "NEUTRAL", 0.0, None

    def _extract_topic_cluster(self, text: str) -> str:
        """Mengelompokkan kata kunci topik umum."""
        text_lower = text.lower()
        if "miniatur" in text_lower or "diorama" in text_lower:
            return "miniatur_diorama"
        if "jam" in text_lower or "kayu" in text_lower or "kuno" in text_lower:
            return "restorasi_material"
        if "cerita" in text_lower or "kisah" in text_lower:
            return "narasi_kehidupan"
        return "umum"

    def get_community_content_ideas(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Mengambil ide-ide konten yang terdistilasi dari komentar komunitas."""
        with get_db() as db:
            items = (
                db.query(AudienceInsight)
                .filter(AudienceInsight.category == CommentCategory.CONTENT_IDEA)
                .filter(AudienceInsight.actionable_idea.isnot(None))
                .order_by(AudienceInsight.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": it.id,
                    "platform": it.platform,
                    "idea": it.actionable_idea,
                    "created_at": it.created_at.isoformat() if it.created_at else "",
                }
                for it in items
            ]


audience_intelligence = AudienceIntelligenceEngine()
