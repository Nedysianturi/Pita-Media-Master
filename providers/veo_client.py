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
        concept: str = "",
        mood: str = "inspiratif",
    ) -> str:
        """
        Memanggil Veo Video Generation API atau fallback visual generator dengan audio soundtrack.
        """
        dest_file = Path(output_path)
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        app_mode = os.environ.get("APP_MODE", getattr(settings, "APP_MODE", "DRY_RUN")).upper()
        if not self.is_configured() or not self.client or app_mode != "PRODUCTION" or os.environ.get("PYTEST_CURRENT_TEST"):
            return await self._create_mock_video(str(dest_file), prompt, duration_seconds, pilar=pilar, title=title, concept=concept, mood=mood)

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
                return await self._create_mock_video(str(dest_file), prompt, duration_seconds, pilar=pilar, title=title, concept=concept, mood=mood)

            if not hasattr(operation, "result") or not operation.result or not operation.result.generated_videos:
                return await self._create_mock_video(str(dest_file), prompt, duration_seconds, pilar=pilar, title=title, concept=concept, mood=mood)

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
            return await self._create_mock_video(str(dest_file), prompt, duration_seconds, pilar=pilar, title=title, concept=concept, mood=mood)

    async def _create_mock_video(
        self,
        output_path: str,
        prompt_text: str,
        duration: int = 5,
        pilar: str = "pita_transformasi",
        title: str = "Pita Media Visual",
        concept: str = "",
        mood: str = "inspiratif",
    ) -> str:
        """
        Menghasilkan video animasi vertikal 9:16 (720x1280) dengan 4 Keyframe Scene Visual
        yang menceritakan proses ide & konsep secara kronologis, dilengkapi audio stereo AAC 192k.
        """
        import subprocess
        from PIL import Image, ImageDraw, ImageFont
        from providers.media_finisher import media_finisher
        from core.audio.music_manager import music_manager

        p_clean = (pilar or "pita_transformasi").lower()
        if "transformasi" in p_clean:
            pill_label = "PITA TRANSFORMASI"
            pilar_desc = "Timelapse & Restorasi Bertahap"
            theme_color = (236, 72, 153)  # Pink Neon
        elif "mini" in p_clean:
            pill_label = "PITA MINI"
            pilar_desc = "Kreasi Miniatur & Diorama Presisi"
            theme_color = (245, 158, 11)   # Warm Amber
        elif "kreasi" in p_clean:
            pill_label = "PITA KREASI"
            pilar_desc = "Eksplorasi Konsep Seni Kreatif"
            theme_color = (16, 185, 129)  # Emerald
        else:
            pill_label = "PITA MEDIA"
            pilar_desc = "Refleksi Sinematik & Waktu"
            theme_color = (99, 102, 241)  # Indigo

        # Bersihkan & siapkan narasi per fase cerita
        clean_concept = concept or title or "Eksplorasi estetika dan proses pengerjaan karya"
        clean_title = (title or "Karya Visual Eksklusif").strip()

        # Ekstrak atau formulasikan 4 fase cerita yang relevan dengan konsep
        scene_definitions = [
            (
                "FASE 1: KONDISI AWAL (INITIAL STATE)",
                f"Kondisi awal bahan mentah: {clean_concept.split('.')[0]}.",
                theme_color
            ),
            (
                "FASE 2: PROSES PENATAAN (CRAFT PROCESS)",
                f"Pengerjaan bertahap dengan ketelitian dan presisi tinggi pada setiap elemen.",
                (96, 165, 250)  # Sky Blue
            ),
            (
                "FASE 3: DETAIL MAKRO (MACRO FOCUS)",
                f"Fokus close-up pada tekstur detail, kontur material, dan kedalaman bayangan.",
                (52, 211, 153)  # Green Emerald
            ),
            (
                "FASE 4: HASIL REVEAL (FINAL MASTERPIECE)",
                f"Karya akhir terwujud memukau dengan komposisi dan pencahayaan sinematik sempurna.",
                (251, 191, 36)  # Golden Yellow
            )
        ]

        temp_dir = Path(output_path).parent / f"temp_scenes_{Path(output_path).stem}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        img_paths = []

        def get_font(size: int, bold: bool = False):
            font_names = ["segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf", "calibrib.ttf" if bold else "calibri.ttf"]
            for fn in font_names:
                try:
                    return ImageFont.truetype(fn, size)
                except Exception:
                    pass
            try:
                return ImageFont.load_default()
            except Exception:
                return None

        font_badge = get_font(22, bold=True)
        font_pill = get_font(18, bold=True)
        font_title = get_font(32, bold=True)
        font_body = get_font(24, bold=False)
        font_footer = get_font(18, bold=False)

        def wrap_text(draw_ctx, text, font, max_w):
            words = text.split()
            lines = []
            curr = []
            for w in words:
                curr.append(w)
                bbox = draw_ctx.textbbox((0, 0), " ".join(curr), font=font)
                if (bbox[2] - bbox[0]) > max_w:
                    curr.pop()
                    if curr:
                        lines.append(" ".join(curr))
                    curr = [w]
            if curr:
                lines.append(" ".join(curr))
            return lines

        w, h = 720, 1280
        for idx, (stage_name, stage_narrative, stage_color) in enumerate(scene_definitions, start=1):
            img = Image.new("RGB", (w, h), color=(10, 14, 26))
            draw = ImageDraw.Draw(img)

            # 1. Background Gradient Sinematik 9:16
            for y in range(h):
                ratio = y / h
                r = int(10 + (stage_color[0] // 9) * ratio)
                g = int(14 + (stage_color[1] // 9) * ratio)
                b = int(26 + (stage_color[2] // 9) * ratio)
                draw.line([(0, y), (w, y)], fill=(r, g, b))

            # 2. Border Garis Ganda Elegan
            draw.rounded_rectangle([(24, 24), (w - 24, h - 24)], radius=20, outline=(30, 41, 59), width=2)
            draw.rounded_rectangle([(36, 36), (w - 36, h - 36)], radius=16, outline=stage_color, width=1)

            # 3. Header Top Bar
            # Pillar Badge
            draw.rounded_rectangle([(52, 64), (w - 52, 124)], radius=12, fill=(15, 23, 42), outline=stage_color, width=2)
            draw.text((70, 82), f"✦ {pill_label} • {pilar_desc}", fill=(241, 245, 249), font=font_pill)

            # 4. Main Story Presentation Card
            draw.rounded_rectangle([(52, 150), (w - 52, h - 140)], radius=18, fill=(15, 23, 42), outline=(51, 65, 85), width=2)

            # Stage Name Banner
            draw.rounded_rectangle([(76, 174), (w - 76, 234)], radius=10, fill=stage_color)
            draw.text((92, 190), f"{stage_name} (Adegan {idx}/4)", fill=(255, 255, 255), font=font_badge)

            # Judul Konten
            title_lines = wrap_text(draw, clean_title, font_title, w - 160)
            curr_y = 264
            for line in title_lines[:3]:
                draw.text((80, curr_y), line, fill=(248, 250, 252), font=font_title)
                curr_y += 42

            # Divider Line
            draw.line([(80, curr_y + 12), (w - 80, curr_y + 12)], fill=(51, 65, 85), width=1)
            curr_y += 32

            # Story Narrative Text
            narr_lines = wrap_text(draw, stage_narrative, font_body, w - 160)
            for line in narr_lines[:6]:
                draw.text((80, curr_y), line, fill=(203, 213, 225), font=font_body)
                curr_y += 34

            # Central Visual Aesthetic Icon / Box
            box_top = max(curr_y + 30, 720)
            box_bottom = min(box_top + 280, h - 190)
            draw.rounded_rectangle([(80, box_top), (w - 80, box_bottom)], radius=14, fill=(10, 15, 30), outline=stage_color, width=1)
            
            phase_icons = ["⏳", "⚙️", "🔍", "✨"]
            phase_labels = ["Tahap 1: Eksplorasi Awal", "Tahap 2: Transformasi Presisi", "Tahap 3: Penyempurnaan Detail", "Tahap 4: Mahakarya Terwujud"]
            
            draw.text((110, box_top + 30), phase_icons[idx - 1], fill=stage_color, font=font_title)
            draw.text((170, box_top + 38), phase_labels[idx - 1], fill=(226, 232, 240), font=font_badge)
            draw.text((110, box_top + 100), f"Soundtrack Mood: {mood.capitalize()}", fill=(148, 163, 184), font=font_pill)
            draw.text((110, box_top + 140), f"Resolusi Vertikal: 720x1280 (9:16) HD", fill=(148, 163, 184), font=font_pill)

            # 5. Footer Bar & Progress Indicator
            dots_text = "● " * idx + "○ " * (4 - idx)
            draw.text((54, h - 90), f"Alur Cerita: {dots_text.strip()}", fill=(148, 163, 184), font=font_footer)
            draw.text((w - 280, h - 90), "Pita Waktu (C) 2026 Official", fill=(99, 102, 241), font=font_footer)

            frame_file = temp_dir / f"scene_{idx:02d}.png"
            img.save(frame_file, format="PNG")
            img_paths.append(str(frame_file.resolve()))

        # 6. Concat scenes with FFmpeg and attach Stereo AAC Soundtrack
        concat_txt = temp_dir / "concat_list.txt"
        duration_per_scene = duration / len(img_paths)
        with open(concat_txt, "w", encoding="utf-8") as f:
            for p in img_paths:
                f.write(f"file '{Path(p).as_posix()}'\nduration {duration_per_scene:.2f}\n")
            f.write(f"file '{Path(img_paths[-1]).as_posix()}'\n")

        audio_expr = music_manager.get_audio_expression_for_mood(mood)
        fade_out_start = max(1.0, duration - 1.2)

        cmd = [
            media_finisher.ffmpeg_exe,
            "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-f", "lavfi", "-i", f"aevalsrc={audio_expr}:s=44100:d={duration}",
            "-vf", "fps=30,format=yuv420p",
            "-af", f"afade=t=in:ss=0:d=0.8,afade=t=out:st={fade_out_start}:d=1.2",
            "-c:v", "libx264",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            import shutil
            shutil.copy2(img_paths[0], output_path)

        return output_path


veo_client = VeoClient()


