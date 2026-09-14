"""
Platform Formatter for Pita Media.
Translates master content and creative assets into tailored formats per platform:
- Facebook: 1:1 or 4:5, long-form emotional storytelling, reflective CTA.
- Instagram: 4:5 carousel or 9:16 Reels, punchy top-line hook, curated hashtags.
- Threads: Conversational short-form (< 500 chars), discussion prompt CTA.
Incorporates 'Pita Waktu' signature rubric and Brand Bible standards.
"""

import os
import yaml
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger("pita_media.creator.formatter")


class PlatformFormatter:
    """
    Formats captions, hooks, CTAs, and media dimensions according to Brand Bible.
    """

    def __init__(self, brand_bible_path: Optional[str] = None):
        if not brand_bible_path:
            base_dir = Path(__file__).resolve().parent.parent.parent
            brand_bible_path = str(base_dir / "config" / "brand_bible.yaml")

        self.brand_bible = self._load_brand_bible(brand_bible_path)

    def _load_brand_bible(self, path: str) -> Dict[str, Any]:
        """Load Brand Bible YAML safely."""
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Failed to load brand_bible.yaml: {e}")
        return {}

    def format_for_platform(
        self,
        platform: str,
        title: str,
        pilar: str,
        story_body: str,
        hashtags: Optional[List[str]] = None,
        tone: str = "inspiratif"
    ) -> Dict[str, Any]:
        """
        Produce specialized caption, CTA, target aspect ratio, and hook per platform.
        """
        hashtags = hashtags or ["#PitaMedia", "#PitaWaktu", "#KisahInspiratif", "#Storytelling"]
        platform_rules = self.brand_bible.get("platform_rules", {}).get(platform, {})
        rubrics = self.brand_bible.get("signature_rubrics", {})
        
        # Determine Rubric signature
        is_pita_waktu = (pilar == "pita_waktu" or "waktu" in title.lower())
        opening = "Tahukah kamu, di balik detik yang kita lewati hari ini, ada jejak kisah yang tak lekang oleh zaman..." if is_pita_waktu else ""
        closing = "Pita waktu terus berputar. Dari kisah ini, makna apa yang kamu bawa?" if is_pita_waktu else ""

        if platform == "facebook":
            # Facebook: Long-form narrative
            cta = platform_rules.get("cta", "Bagikan pendapatmu di kolom komentar & ikuti Pita Media untuk kisah bermakna setiap hari.")
            caption_parts = []
            if title:
                caption_parts.append(f"✨ {title.upper()} ✨\n")
            if opening:
                caption_parts.append(f"{opening}\n")
            caption_parts.append(story_body)
            if closing:
                caption_parts.append(f"\n{closing}")
            caption_parts.append(f"\n💬 {cta}")
            caption_parts.append(f"\n{' '.join(hashtags[:6])}")

            return {
                "platform": "facebook",
                "aspect_ratio": "4:5",
                "caption": "\n".join(caption_parts),
                "media_type": "CAROUSEL" if pilar in ["pita_cerita", "pita_waktu"] else "IMAGE"
            }

        elif platform == "instagram":
            # Instagram: Punchy Hook + Visual Focus
            cta = platform_rules.get("cta", "Double tap jika cerita ini menyentuh hatimu. Simpan untuk pengingat kelak!")
            # 1-line hook
            hook_line = f"✨ {title} — Simak kisahnya sampai slide terakhir ⏳\n"
            caption_parts = [
                hook_line,
                story_body,
                f"\n📌 {cta}",
                f"\n{' '.join(hashtags)}"
            ]
            return {
                "platform": "instagram",
                "aspect_ratio": "4:5",
                "caption": "\n\n".join(caption_parts),
                "media_type": "CAROUSEL" if pilar in ["pita_cerita", "pita_waktu"] else "IMAGE"
            }

        elif platform == "threads":
            # Threads: Crisp, conversational (< 500 chars)
            cta = platform_rules.get("cta", "Bagaimana sudut pandangmu? Mari diskusi di utas ini 🧵")
            short_body = story_body
            if len(short_body) > 300:
                short_body = short_body[:290] + "..."

            thread_text = f"⏳ {title}\n\n{short_body}\n\n{cta}\n\n#PitaWaktu #PitaMedia"
            return {
                "platform": "threads",
                "aspect_ratio": "1:1",
                "caption": thread_text,
                "text": thread_text,
                "media_type": "IMAGE"
            }

        else:
            # Generic fallback
            return {
                "platform": platform,
                "aspect_ratio": "1:1",
                "caption": f"{title}\n\n{story_body}\n\n{' '.join(hashtags)}",
                "media_type": "IMAGE"
            }


platform_formatter = PlatformFormatter()
