"""
Pipeline Creator untuk Pilar 3: Pita Cerita.
Menghasilkan 3-5 gambar statis dalam carousel (BUKAN video / slideshow bergerak),
dengan cerita lengkap berbobot 150-300 kata di caption postingan.
Fokus utama adalah kedalaman cerita & resonansi emosional.
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from agents.creator.ideator import ContentIdea
from agents.reviewer.self_repair import clean_ai_caption_fluff
from providers.gemini_client import gemini_client
from providers.imagen_client import imagen_client
from config.settings import settings


class StoryGenerationResult(BaseModel):
    story_caption: str = Field(default="", description="Cerita utuh dengan panjang 150 sampai 300 kata yang sarat makna dan emosi")
    slide_prompts: List[str] = Field(default_factory=list, description="Daftar 3 sampai 5 prompt gambar visual statis yang berkesinambungan")


class PitaCeritaCreator:
    def __init__(self):
        self.gemini = gemini_client
        self.imagen = imagen_client

    async def create(
        self,
        idea: ContentIdea,
        job_id: str,
        db_session: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """
        Menjalankan pipeline pembuatan konten Pita Cerita.
        """
        prompt_instruction = f"""
        Rancang Carousel Gambar Statis dan Narasi Panjang untuk 'Pita Cerita'.
        Judul: {idea.title}
        Konsep: {idea.concept}
        Gaya Visual: {idea.visual_theme}

        ATURAN MUTLAK PITA CERITA:
        1. CERITA CAPTION: Wajib berupa cerita lengkap, mendalam, dan reflektif dengan panjang ANTARA 150 SAMPAI 300 KATA. 
           Bercerita dengan kalimat indah, pembukaan memikat, konflik/makna batiniah, dan konklusi yang menyentuh.
           HANYA kembalikan teks narasi cerita murni tanpa pengantar seperti 'Berikut adalah draf caption...'.
        2. GAMBAR CAROUSEL: Rancang 3 sampai 5 deskripsi prompt gambar STATIS (aspect ratio 1:1 square).
           Gambar harus statis (bukan format video). Pastikan kohesi gaya seni, pencahayaan, dan tema antar slide.
        """

        system_instruction = (
            "Anda adalah Master Storyteller dan Art Director kelas dunia untuk 'Pita Cerita'. "
            "Kekuatan utama konten ini adalah cerita yang menggerakkan hati dan visual statis yang puitis. "
            "Keluaran narasi HANYA berupa cerita final murni tanpa basa-basi."
        )

        if not self.gemini.is_configured():
            story_res = self._get_mock_story(idea)
        else:
            try:
                story_res = await self.gemini.generate_structured(
                    prompt=prompt_instruction,
                    schema=StoryGenerationResult,
                    system_instruction=system_instruction,
                    model=settings.GEMINI_PRO_MODEL,
                    db_session=db_session,
                    job_id=job_id,
                )
            except Exception as e:
                logger.warning(f"Gagal generate cerita terstruktur ({e}). Menggunakan generator cerita lokal.")
                story_res = self._get_mock_story(idea)

        # Pastikan jumlah slide 3-5 dengan fallback aman jika respon kosong
        prompts = list(getattr(story_res, "slide_prompts", []) or [])
        story_caption = clean_ai_caption_fluff(getattr(story_res, "story_caption", "") or "")
        
        if not prompts or not story_caption:
            mock_s = self._get_mock_story(idea)
            if not prompts:
                prompts = mock_s.slide_prompts
            if not story_caption:
                story_caption = mock_s.story_caption

        if len(prompts) < 3:
            prompts = prompts + [f"{prompts[0]} - Variasi detail sudut pandang lain"] * (3 - len(prompts))

        # Render 3-5 gambar statis via Imagen
        carousel_dir = settings.processed_media_dir / f"cerita_{job_id[:8]}"
        image_paths = await self.imagen.generate_carousel_images(
            prompts=prompts,
            output_dir=str(carousel_dir),
            prefix=f"slide_{job_id[:6]}",
            title=idea.title,
            pilar="pita_cerita",
            db_session=db_session,
            job_id=job_id,
        )

        # Format caption akhir dengan hashtag
        full_caption = (
            f"{story_caption}\n\n"
            f"· · ·\n"
            f"#PitaCerita #KisahBermakna #VisualStorytelling #RefleksiWaktu #PitaMedia"
        )

        return {
            "title": idea.title,
            "caption": full_caption.strip(),
            "media_type": "carousel",
            "media_paths": image_paths,
            "raw_prompts": {
                "story_caption_length": len(story_caption.split()),
                "slide_prompts": prompts,
            },
            "model_name": settings.GEMINI_IMAGE_MODEL,
            "model_version": "imagen-3.0",
            "veo_params": None,
            "ffmpeg_params": {"type": "static_carousel", "count": len(image_paths)},
        }

    def _get_mock_story(self, idea: ContentIdea) -> StoryGenerationResult:
        mock_story = (
            "Di tepi tebing karang yang curam, mercusuar tua itu telah berdiri selama hampir satu abad. "
            "Pak Harun, sang penjaga lentera, telah menghabiskan sebagian besar usianya memastikan nyala cahaya tidak pernah padam, "
            "bahkan di malam-malam ketika badai samudera mengamuk tanpa ampun. Baginya, setiap putaran lentera bukan sekadar pemandu arah bagi "
            "para pelaut yang tersesat, melainkan sebuah janji bahwa selalu ada harapan yang menunggu di daratan.\n\n"
            "Waktu berganti, teknologi modern mulai menggantikan tugas manual manusia, namun jejak langkah dan ketulusan hati "
            "tidak pernah bisa digantikan oleh mesin mana pun. Dalam setiap goresan dinding batu yang tergerus asinnya angin laut, "
            "tersimpan ribuan doa tak terucap dari mereka yang berhasil pulang ke pelukan keluarga. "
            "Malam ini, di bawah gemerlap bintang dan hembusan angin malam yang dingin, ia tersenyum kecil melihat cakrawala yang tenang. "
            "Tugasnya mungkin akan usai, tetapi cahaya kebaikan yang pernah ia nyalakan akan terus abadi dalam ingatan sang samudera."
        )
        return StoryGenerationResult(
            story_caption=mock_story,
            slide_prompts=[
                "Lukisan cat minyak mercusuar tua di tepi tebing saat senja berkabut tebal",
                "Pak Harun memegang lentera kuning hangat menaiki tangga spiral mercusuar",
                "Pemandangan malam spektakuler dengan cahaya mercusuar membelah kegelapan laut lepas",
                "Siluet penjaga mercusuar menatap fajar keemasan di atas gelombang ombak tenang",
            ],
        )


pita_cerita_creator = PitaCeritaCreator()
