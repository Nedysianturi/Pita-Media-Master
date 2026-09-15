"""
Pipeline Creator untuk Pilar 4: Pita Kreasi.
Mengubah bahan sederhana/mentah sehari-hari menjadi sesuatu yang menarik dan tak terduga,
dengan variasi material dan konsep yang kuat, TANPA klaim palsu pembuatan fisik di dunia nyata.
"""

from pathlib import Path
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from agents.creator.ideator import ContentIdea
from agents.reviewer.self_repair import clean_ai_caption_fluff
from providers.gemini_client import gemini_client
from providers.veo_client import veo_client
from providers.media_finisher import media_finisher
from config.settings import settings


class PitaKreasiCreator:
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
        Menjalankan pipeline pembuatan konten Pita Kreasi.
        """
        prompt_instruction = (
            f"Rancang prompt video Veo 9:16 untuk pilar 'Pita Kreasi'.\n"
            f"Judul: {idea.title}\n"
            f"Konsep: {idea.concept}\n"
            f"Tema Visual: {idea.visual_theme}\n\n"
            f"PANDUAN MUTLAK PITA KREASI:\n"
            f"1. Eksplorasi rekayasa visual bahan sederhana (kertas, tali rami, serbuk arang, botol kaca, daun kering) "
            f"menjadi karya seni konsep tak terduga yang memanjakan mata.\n"
            f"2. TANPA KLAIM PALSU: Tidak boleh mengklaim ini adalah produk sains fisik instan nyata yang menyesatkan, "
            f"melainkan karya eksplorasi estetika/seni konsep kreatif.\n"
            f"3. Pacing dinamis: pengenalan bahan mentah -> proses penyusunan tak terduga -> payoff reveal kreasi yang megah."
        )

        veo_prompt = await self.gemini.generate_text(
            prompt=prompt_instruction,
            system_instruction="Anda adalah perancang konsep seni kreatif dan visual effects artist.",
            db_session=db_session,
            job_id=job_id,
        )

        caption_prompt = (
            f"Tulis caption kreatif untuk postingan kreasi seni berikut:\n"
            f"Judul: {idea.title}\n"
            f"Konsep: {idea.concept}\n"
            f"Panjang: 60-110 kata. Tekankan kekuatan imajinasi dalam melihat keindahan dari benda sederhana. (#PitaKreasi #CreativeConcept #ArtisticExploration #PitaMedia)"
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

        raw_video_path = settings.raw_media_dir / f"kreasi_{job_id[:8]}_raw.mp4"
        generated_video = await self.veo.generate_video(
            prompt=veo_prompt,
            output_path=str(raw_video_path),
            aspect_ratio="9:16",
            duration_seconds=5,
            db_session=db_session,
            job_id=job_id,
            pilar="pita_kreasi",
            title=idea.title,
            concept=idea.concept,
            mood="inspiratif",
        )

        processed_video_path = settings.processed_media_dir / f"kreasi_{job_id[:8]}_final.mp4"
        final_video = self.finisher.apply_signature_watermark(
            input_video_path=generated_video,
            output_video_path=str(processed_video_path),
            watermark_text=settings.SIGNATURE_TEXT,
            mood="inspiratif",
            duration=5,
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


pita_kreasi_creator = PitaKreasiCreator()
