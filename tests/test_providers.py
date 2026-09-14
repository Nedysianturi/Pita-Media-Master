"""
Pengujian Unit untuk Provider AI Gemini, Veo, Imagen, dan FFmpeg Media Finisher.
"""

import os
import sys
from pathlib import Path
import pytest
import pytest_asyncio
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.media_finisher import MediaFinisher
from providers.gemini_client import GeminiClient
from providers.veo_client import VeoClient
from providers.imagen_client import ImagenClient
from config.settings import settings


class SamplePlanSchema(BaseModel):
    title: str = "Default Title"
    concept: str = "Default Concept"
    pillar: str = "pita_cerita"


@pytest.mark.asyncio
async def test_media_finisher_version_and_tools():
    """Memverifikasi ketersediaan executable ffmpeg."""
    finisher = MediaFinisher()
    ver = finisher.get_version()
    assert "ffmpeg" in ver.lower() or "version" in ver.lower()


@pytest.mark.asyncio
async def test_imagen_client_carousel_generation(tmp_path):
    """Menguji generasi kumpulan 3 gambar carousel."""
    client = ImagenClient(api_key="")  # Menggunakan fallback mock generator
    prompts = [
        "Slide 1: Halaman depan rumah tua di tengah hutan pinus",
        "Slide 2: Buku harian berdebu terbuka di atas meja kayu",
        "Slide 3: Cahaya fajar menembus jendela kaca patri antik",
    ]
    out_dir = tmp_path / "carousel_test"
    paths = await client.generate_carousel_images(prompts, str(out_dir))

    assert len(paths) == 3
    for p in paths:
        assert Path(p).exists()
        assert Path(p).stat().st_size > 0


@pytest.mark.asyncio
async def test_veo_client_video_generation(tmp_path):
    """Menguji pembuatan file video 9:16 untuk Veo."""
    client = VeoClient(api_key="")
    out_video = tmp_path / "veo_test.mp4"
    saved = await client.generate_video(
        prompt="Timelapse pembangunan struktur kayu presisi tinggi",
        output_path=str(out_video),
        aspect_ratio="9:16",
        duration_seconds=2,
    )
    assert Path(saved).exists()
    assert Path(saved).stat().st_size > 0


@pytest.mark.asyncio
async def test_media_finisher_watermark_application(tmp_path):
    """Menguji pengaplikasian watermark signature Pita Waktu pada file video."""
    finisher = MediaFinisher()
    # Buat video dasar dummy
    src_video = tmp_path / "src_video.mp4"
    client = VeoClient(api_key="")
    await client.generate_video("test", str(src_video), duration_seconds=1)

    watermarked_video = tmp_path / "watermarked_video.mp4"
    result = finisher.apply_signature_watermark(
        input_video_path=str(src_video),
        output_video_path=str(watermarked_video),
        watermark_text="Pita Waktu",
    )
    assert Path(result).exists()
    assert Path(result).stat().st_size > 0
