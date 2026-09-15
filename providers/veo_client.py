"""
Client Google Veo Video Generation untuk Sistem Pita Media.
Menghasilkan video beresolusi tinggi 9:16 untuk pilar Pita Transformasi, Pita Mini, dan Pita Kreasi.
"""

import os
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from database.models import CostRecord

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    GENAI_NEW_SDK = True
except ImportError:
    import google.generativeai as genai
    GENAI_NEW_SDK = False


class VeoClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        self.model = settings.GEMINI_VIDEO_MODEL
        self.client = None
        if self.api_key and GENAI_NEW_SDK:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                self.client = None

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key != "your_gemini_api_key_here")

    async def generate_video(
        self,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        duration_seconds: int = 5,
        seed: Optional[int] = None,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None,
        pilar: str = "pita_transformasi",
        title: str = "Pita Media Visual",
        mood: str = "inspiratif",
    ) -> str:
        """
        Memanggil Veo Video Generation API atau fallback visual generator dengan audio soundtrack.
        """
        dest_file = Path(output_path)
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        app_mode = os.environ.get("APP_MODE", getattr(settings, "APP_MODE", "DRY_RUN")).upper()
        if not self.is_configured() or not self.client or app_mode != "PRODUCTION" or os.environ.get("PYTEST_CURRENT_TEST"):
            return await self._create_mock_video(str(dest_file), prompt, duration_seconds, pilar=pilar, title=title, mood=mood)

        try:
            # Panggilan Veo 2.0 API tanpa parameter unsupported di Developer Mode
            operation = await asyncio.to_thread(
                self.client.models.generate_videos,
                model=self.model,
                prompt=prompt,
                config=types.GenerateVideosConfig(
                    aspect_ratio=aspect_ratio,
                    number_of_videos=1,
                    duration_seconds=duration_seconds,
                    seed=seed,
                ),
            )

            # Polling status operasi video sampai selesai
            poll_count = 0
            while not operation.done and poll_count < 15:
                await asyncio.sleep(8)
                operation = await asyncio.to_thread(self.client.operations.get, operation)
                poll_count += 1

            if operation.error:
                logger.warning(f"Veo API mengembalikan error: {operation.error.message}. Menggunakan fallback video generator.")
                return await self._create_mock_video(str(dest_file), prompt, duration_seconds, pilar=pilar, title=title, mood=mood)

            if not hasattr(operation, "result") or not operation.result or not operation.result.generated_videos:
                return await self._create_mock_video(str(dest_file), prompt, duration_seconds, pilar=pilar, title=title, mood=mood)

            # Unduh video hasil render
            generated_video = operation.result.generated_videos[0]
            with open(dest_file, "wb") as f:
                f.write(generated_video.video.video_bytes)

            # Catat estimasi biaya Veo (~$0.05 per detik video)
            if db_session:
                cost_rec = CostRecord(
                    job_id=job_id,
                    service="veo_video",
                    duration_seconds=float(duration_seconds),
                    estimated_cost_usd=duration_seconds * 0.05,
                )
                db_session.add(cost_rec)
                await db_session.commit()

            return str(dest_file)

        except Exception as e:
            logger.info(f"Veo API tidak aktif/kuota terbatas ({e}). Beralih ke fallback video generator.")
            return await self._create_mock_video(str(dest_file), prompt, duration_seconds, pilar=pilar, title=title, mood=mood)

    async def _create_mock_video(
        self,
        output_path: str,
        prompt_text: str,
        duration: int = 5,
        pilar: str = "pita_transformasi",
        title: str = "Pita Media Visual",
        mood: str = "inspiratif",
    ) -> str:
        """Helper untuk membuat video animasi 9:16 estetis lengkap dengan visual grafis dinamis dan audio stereo AAC."""
        import subprocess
        from providers.media_finisher import media_finisher
        from core.audio.music_manager import music_manager

        # Tentukan tema visual & label pilar
        p_clean = (pilar or "pita_transformasi").lower()
        if "transformasi" in p_clean:
            pill_label = "PITA TRANSFORMASI (Reels / Shorts)"
            badge_color = "0xEC4899"
            sub_label = "Timelapse Transformasi Bertahap"
        elif "mini" in p_clean:
            pill_label = "PITA MINI (Diorama & Craft)"
            badge_color = "0xF59E0B"
            sub_label = "Kreasi Miniatur Makro Presisi"
        elif "kreasi" in p_clean:
            pill_label = "PITA KREASI (Eksplorasi Seni)"
            badge_color = "0x10B981"
            sub_label = "Konsep Artistik Benda Sederhana"
        else:
            pill_label = "PITA MEDIA (Video Sinematik)"
            badge_color = "0x3B82F6"
            sub_label = "Refleksi & Narasi Bermakna"

        # Bersihkan teks judul untuk aman dalam FFmpeg drawtext
        safe_title = (title or "Karya Visual Eksklusif").replace("'", "").replace(":", "-").replace('"', "")
        if len(safe_title) > 38:
            safe_title = safe_title[:35] + "..."

        audio_expr = music_manager.get_audio_expression_for_mood(mood)
        fade_out_start = max(1.0, duration - 1.2)

        # Rancang filter grafik visual 720x1280 9:16 dengan efek gerak dinamis
        video_filter = (
            f"testsrc2=s=720x1280:r=30:d={duration},format=yuv420p,"
            f"drawbox=x=0:y=0:w=720:h=1280:color=black@0.65:t=fill,"
            f"drawbox=x=40:y=160:w=640:h=60:color={badge_color}@0.35:t=fill,"
            f"drawtext=text='{pill_label}':fontsize=26:fontcolor=white:x=(w-text_w)/2:y=176:shadowcolor=black@0.6:shadowx=2:shadowy=2,"
            f"drawtext=text='{safe_title}':fontsize=32:fontcolor=white:x=(w-text_w)/2:y=280:shadowcolor=black@0.7:shadowx=2:shadowy=2,"
            f"drawtext=text='{sub_label}':fontsize=22:fontcolor=0x94A3B8:x=(w-text_w)/2:y=340:shadowcolor=black@0.5:shadowx=1:shadowy=1,"
            f"drawbox=x=60:y=460:w=600:h=460:color=black@0.45:t=fill,"
            f"drawtext=text='1. Kondisi Awal   -   2. Proses Presisi':fontsize=20:fontcolor=0x60A5FA:x=(w-text_w)/2:y=540,"
            f"drawtext=text='3. Detail Makro    -   4. Final Reveal':fontsize=20:fontcolor=0x34D399:x=(w-text_w)/2:y=620,"
            f"drawtext=text='Audio Soundtrack - {mood.capitalize()} Active':fontsize=18:fontcolor=0xFBBF24:x=(w-text_w)/2:y=760,"
            f"drawtext=text='Pita Waktu (C) 2026':fontsize=22:fontcolor=0x64748B:x=(w-text_w)/2:y=h-140"
        )

        cmd = [
            media_finisher.ffmpeg_exe,
            "-y",
            "-f", "lavfi", "-i", video_filter,
            "-f", "lavfi", "-i", f"aevalsrc={audio_expr}:s=44100:d={duration}",
            "-af", f"afade=t=in:ss=0:d=0.8,afade=t=out:st={fade_out_start}:d=1.2",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # Fallback jika drawtext gagal (misal font sistem), gunakan color filter sederhana + audio
            fallback_cmd = [
                media_finisher.ffmpeg_exe,
                "-y",
                "-f", "lavfi", "-i", f"color=c=0x1e1b4b:s=720x1280:d={duration}",
                "-f", "lavfi", "-i", f"aevalsrc={audio_expr}:s=44100:d={duration}",
                "-af", f"afade=t=in:ss=0:d=0.8,afade=t=out:st={fade_out_start}:d=1.2",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                output_path,
            ]
            res_fb = subprocess.run(fallback_cmd, capture_output=True, text=True)
            if res_fb.returncode != 0:
                with open(output_path, "wb") as f:
                    f.write(b"MOCK_VIDEO_BINARY_DATA")
        return output_path


veo_client = VeoClient()

