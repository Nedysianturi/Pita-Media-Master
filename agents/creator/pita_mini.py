"""
Pipeline Creator untuk Pilar 2: Pita Mini.
Menghasilkan video konstruksi/transformasi miniatur yang memuaskan (satisfying diorama & craft),
dengan struktur material mikro menuju final reveal detail berbobot.
"""

from pathlib import Path
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from agents.creator.ideator import ContentIdea
from providers.gemini_client import gemini_client
from providers.veo_client import veo_client
from providers.media_finisher import media_finisher
from config.settings import settings


class PitaMiniCreator:
    def __init__(self):
        self.gemini = gemini_client
        self.veo = veo_client
        self.finisher = media_finisher

    async def create(
        self,
        idea: ContentIdea,
        job_id: str,
        db_session: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """
        Menjalankan pipeline pembuatan konten Pita Mini.
        """
        prompt_instruction = (
            f"Rancang prompt video makro Veo 9:16 untuk pembuatan miniatur diorama memuaskan (oddly satisfying craft).\n"
            f"Judul: {idea.title}\n"
            f"Konsep: {idea.concept}\n"
            f"Tema: {idea.visual_theme}\n\n"
            f"PANDUAN VISUAL PITA MINI:\n"
            f"- Fokus pada ketelitian tangan/alat mikroskopis menyusun material (kayu balsa, resin bening, lumut miniatur, bata mikro).\n"
            f"- Tekstur makro ultra-tajam, bayangan lembut, pencahayaan meja studio miniatur hangat.\n"
            f"- Gerakan perakitan yang presisi dan menenangkan penonton.\n"
            f"- Reveal akhir dengan kedalaman ruang (shallow depth of field) yang memukau."
        )

        veo_prompt = await self.gemini.generate_text(
            prompt=prompt_instruction,
            system_instruction="Anda adalah sutradara spesialis visualisasi diorama dan miniatur sinematik.",
            db_session=db_session,
            job_id=job_id,
        )

        caption_prompt = (
            f"Tulis caption apresiatif untuk video miniatur berikut:\n"
            f"Judul: {idea.title}\n"
            f"Konsep: {idea.concept}\n"
            f"Panjang: 60-100 kata. Tonjolkan ketelitian detail dan kepuasan melihat setiap elemen tersusun rapi. (#PitaMini #MiniatureWorld #DioramaArt #OddlySatisfying)"
        )
        caption = await self.gemini.generate_text(
            prompt=caption_prompt,
            db_session=db_session,
            job_id=job_id,
        )

        raw_video_path = settings.raw_media_dir / f"mini_{job_id[:8]}_raw.mp4"
        generated_video = await self.veo.generate_video(
            prompt=veo_prompt,
            output_path=str(raw_video_path),
            aspect_ratio="9:16",
            duration_seconds=5,
            db_session=db_session,
            job_id=job_id,
        )

        processed_video_path = settings.processed_media_dir / f"mini_{job_id[:8]}_final.mp4"
        final_video = self.finisher.apply_signature_watermark(
            input_video_path=generated_video,
            output_video_path=str(processed_video_path),
            watermark_text=settings.SIGNATURE_TEXT,
        )

        return {
            "title": idea.title,
            "caption": caption.strip(),
            "media_type": "video",
            "media_paths": [final_video],
            "raw_prompts": {
                "veo_prompt": veo_prompt,
                "caption_prompt": caption_prompt,
            },
            "model_name": settings.GEMINI_VIDEO_MODEL,
            "model_version": "veo-2.0",
            "veo_params": {"aspect_ratio": "9:16", "duration": 5},
            "ffmpeg_params": {"watermark": settings.SIGNATURE_TEXT, "format": "mp4"},
        }


pita_mini_creator = PitaMiniCreator()
