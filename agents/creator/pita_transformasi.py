"""
Pipeline Creator untuk Pilar 1: Pita Transformasi.
Menghasilkan video transformasi / timelapse dengan 6 fase progresif yang ketat:
1. Kondisi awal -> 2. Proses -> 3. Perubahan bertahap -> 4. Detail -> 5. Finishing -> 6. Final reveal.
"""

from pathlib import Path
from typing import Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from agents.creator.ideator import ContentIdea
from agents.reviewer.self_repair import clean_ai_caption_fluff
from providers.gemini_client import gemini_client
from providers.veo_client import veo_client
from providers.media_finisher import media_finisher
from config.settings import settings


class PitaTransformasiCreator:
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
        Menjalankan pipeline pembuatan konten Pita Transformasi.
        """
        # 1. Rancang Prompt Video Veo Progresif 6 Fase
        prompt_instruction = (
            f"Rancang prompt video timelapse Veo 9:16 untuk 'Pita Transformasi'.\n"
            f"Judul: {idea.title}\n"
            f"Konsep: {idea.concept}\n"
            f"Gaya Visual: {idea.visual_theme}\n\n"
            f"STRUKTUR WAJIB 6 FASE DALAM 1 SHOT SINEMATIK:\n"
            f"1. INITIAL STATE: Kondisi awal objek mentah/rusak/antik.\n"
            f"2. PROCESS: Proses intervensi/pengerjaan dengan presisi.\n"
            f"3. GRADUAL CHANGE: Perubahan bertahap yang dinamis & mulus.\n"
            f"4. DETAIL FOCUS: Close-up makro pada tekstur detail & mekanisme.\n"
            f"5. FINISHING: Sentuhan akhir pemolesan dan penyempurnaan.\n"
            f"6. FINAL REVEAL: Hasil akhir memukau dengan pencahayaan sempurna.\n\n"
            f"Pastikan kontinuitas pencahayaan studio, sudut kamera terkunci stabil, dan konsistensi warna."
        )

        veo_prompt = await self.gemini.generate_text(
            prompt=prompt_instruction,
            system_instruction="Anda adalah pakar prompt engineering video cinematic Veo AI.",
            db_session=db_session,
            job_id=job_id,
        )

        # 2. Rancang Caption Teks yang Menarik
        caption_prompt = (
            f"Tulis caption Instagram/TikTok yang memukau untuk video transformasi timelapse berikut:\n"
            f"Judul: {idea.title}\n"
            f"Konsep: {idea.concept}\n"
            f"Panjang: 60-120 kata. Akhiri dengan ajakan interaksi bernada reflektif dan hashtag relevan (#PitaTransformasi #Timelapse #ArtisticTransformation)."
        )
        caption_raw = await self.gemini.generate_text(
            prompt=caption_prompt,
            system_instruction=(
                "Anda adalah copywriter profesional media sosial. Kembalikan HANYA teks caption final murni "
                "yang siap dipublikasikan tanpa kalimat pengantar, basa-basi, tanda kutip, maupun catatan seperti "
                "'Berikut adalah draf caption...'. Langsung mulai dari baris pertama naskah."
            ),
            db_session=db_session,
            job_id=job_id,
        )
        caption = clean_ai_caption_fluff(caption_raw)

        # 3. Render Video via Veo
        raw_video_path = settings.raw_media_dir / f"transformasi_{job_id[:8]}_raw.mp4"
        generated_video = await self.veo.generate_video(
            prompt=veo_prompt,
            output_path=str(raw_video_path),
            aspect_ratio="9:16",
            duration_seconds=5,
            db_session=db_session,
            job_id=job_id,
        )

        # 4. Finishing dengan FFmpeg (Watermark 'Pita Waktu' & Normalisasi 9:16)
        processed_video_path = settings.processed_media_dir / f"transformasi_{job_id[:8]}_final.mp4"
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


pita_transformasi_creator = PitaTransformasiCreator()
