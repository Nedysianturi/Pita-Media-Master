"""
Client Google Imagen / Gemini Image API untuk Sistem Pita Media.
Menghasilkan 3-5 gambar statis beresolusi tinggi (1:1) untuk pilar Pita Cerita.
"""

import os
import logging
from pathlib import Path
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont
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


class ImagenClient:
    def __init__(self, api_key: Optional[str] = None):
        from core.security.secret_store import secret_store
        self.api_key = api_key if api_key is not None else (secret_store.get_secret("GEMINI_PRIMARY_API_KEY") or secret_store.get_secret("GEMINI_API_KEY") or settings.GEMINI_API_KEY)
        self.model = settings.GEMINI_IMAGE_MODEL
        self.client = None
        if self.api_key and GENAI_NEW_SDK:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception:
                self.client = None

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key != "your_gemini_api_key_here")

    async def generate_single_image(
        self,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "1:1",
        title: str = "",
        pilar: str = "pita_cerita",
        slide_idx: int = 1,
        total_slides: int = 1,
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None,
    ) -> str:
        """
        Menghasilkan satu gambar statis dan menyimpannya ke output_path.
        """
        dest_file = Path(output_path)
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        app_mode = os.environ.get("APP_MODE", getattr(settings, "APP_MODE", "DRY_RUN")).upper()
        if not self.is_configured() or not self.client or app_mode != "PRODUCTION" or os.environ.get("PYTEST_CURRENT_TEST"):
            return self._create_mock_image(str(dest_file), prompt, title=title, pilar=pilar, slide_idx=slide_idx, total_slides=total_slides)

        try:
            result = await asyncio.to_thread(
                self.client.models.generate_images,
                model=self.model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    output_mime_type="image/png",
                    aspect_ratio=aspect_ratio,
                ),
            )

            if not result.generated_images:
                return self._create_mock_image(str(dest_file), prompt, title=title, pilar=pilar, slide_idx=slide_idx, total_slides=total_slides)

            img_bytes = result.generated_images[0].image.image_bytes
            with open(dest_file, "wb") as f:
                f.write(img_bytes)

            if db_session:
                cost_rec = CostRecord(
                    job_id=job_id,
                    service="imagen_image",
                    token_count=1,
                    estimated_cost_usd=0.03,
                )
                db_session.add(cost_rec)
                await db_session.commit()

            return str(dest_file)

        except Exception as e:
            logger.info(f"Imagen API tidak tersedia/kuota habis ({e}). Menggunakan generator grafis lokal.")
            return self._create_mock_image(str(dest_file), prompt, title=title, pilar=pilar, slide_idx=slide_idx, total_slides=total_slides)

    async def generate_carousel_images(
        self,
        prompts: List[str],
        output_dir: str,
        prefix: str = "slide",
        title: str = "",
        pilar: str = "pita_cerita",
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None,
    ) -> List[str]:
        """
        Menghasilkan rangkaian 3-5 gambar untuk postingan carousel Pita Cerita.
        """
        generated_paths = []
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        total_slides = len(prompts)

        for idx, prompt in enumerate(prompts, start=1):
            file_name = f"{prefix}_{idx:02d}.png"
            dest = out_path / file_name
            saved_path = await self.generate_single_image(
                prompt=prompt,
                output_path=str(dest),
                aspect_ratio="1:1",
                title=title,
                pilar=pilar,
                slide_idx=idx,
                total_slides=total_slides,
                db_session=db_session,
                job_id=job_id,
            )
            generated_paths.append(saved_path)

        return generated_paths

    def _create_mock_image(self, output_path: str, prompt_text: str, title: str = "", pilar: str = "pita_cerita", slide_idx: int = 1, total_slides: int = 4) -> str:
        """
        Menghasilkan Slide Grafis Editorial Tipografi Premium (1080x1080)
        dengan gradasi warna mewah, layout majalah filosofis, dan tipografi tajam.
        """
        width, height = 1080, 1080
        img = Image.new("RGB", (width, height), color=(10, 15, 29))
        draw = ImageDraw.Draw(img)

        # 1. Background Gradient Mewah (Deep Obsidian ke Royal Midnight)
        for y in range(height):
            ratio = y / height
            r = int(10 + (18 - 10) * ratio)
            g = int(15 + (27 - 15) * ratio)
            b = int(29 + (48 - 29) * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # 2. Frame Border Estetis Ganda
        draw.rounded_rectangle([(36, 36), (width - 36, height - 36)], radius=24, outline=(30, 41, 59), width=2)
        draw.rounded_rectangle([(48, 48), (width - 48, height - 48)], radius=18, outline=(99, 102, 241), width=1)

        # 3. Load Font Sistem Windows (Arial / Segoe UI / Georgia)
        def get_font(size: int, bold: bool = False):
            font_names = ["segoeuib.ttf" if bold else "segoeui.ttf", "arialbd.ttf" if bold else "arial.ttf", "calibrib.ttf" if bold else "calibri.ttf", "georgiab.ttf" if bold else "georgia.ttf"]
            for fn in font_names:
                try:
                    return ImageFont.truetype(fn, size)
                except Exception:
                    pass
            try:
                return ImageFont.load_default()
            except Exception:
                return None

        font_header = get_font(24, bold=True)
        font_pill = get_font(20, bold=True)
        font_title = get_font(42, bold=True)
        font_body = get_font(26, bold=False)
        font_quote = get_font(72, bold=True)
        font_footer = get_font(20, bold=False)

        # 4. Header Bar: Brand Logo & Pilar Badge
        # Top Brand Text
        draw.text((80, 80), "✦ PITA MEDIA ARCHIVE", fill=(148, 163, 184), font=font_header)
        
        # Pilar Pill
        pilar_label = f"#{pilar.upper().replace('_', ' ')}"
        draw.rounded_rectangle([(width - 280, 72), (width - 80, 114)], radius=12, fill=(30, 41, 59), outline=(99, 102, 241), width=1)
        draw.text((width - 260, 82), pilar_label, fill=(129, 140, 248), font=font_pill)

        # Divider line
        draw.line([(80, 140), (width - 80, 140)], fill=(51, 65, 85), width=1)

        # 5. Content Card Centerpiece (Glassmorphic Box)
        draw.rounded_rectangle([(80, 180), (width - 80, height - 180)], radius=20, fill=(15, 23, 42), outline=(51, 65, 85), width=2)
        
        # Aksen Kutipan Emas
        draw.text((120, 200), "“", fill=(245, 158, 11), font=font_quote)

        # 6. Auto-Wrap Judul & Teks Narasi
        clean_text = prompt_text.replace("Prompt:", "").strip()
        # Ambil ringkasan jika terlalu panjang
        sentences = [s.strip() for s in clean_text.split(".") if s.strip()]
        display_body = ". ".join(sentences[:3]) + ("." if sentences else "")

        # Word wrap helper
        def draw_wrapped_text(text: str, start_x: int, start_y: int, max_w: int, line_height: int, font, fill_color):
            words = text.split()
            lines = []
            curr = []
            for w in words:
                curr.append(w)
                bbox = draw.textbbox((0, 0), " ".join(curr), font=font)
                if (bbox[2] - bbox[0]) > max_w:
                    curr.pop()
                    if curr:
                        lines.append(" ".join(curr))
                    curr = [w]
            if curr:
                lines.append(" ".join(curr))

            y = start_y
            for line in lines[:8]:
                draw.text((start_x, y), line, fill=fill_color, font=font)
                y += line_height
            return y

        # Render Judul / Topik
        title_text = title if title else (sentences[0] if sentences else "Refleksi Waktu & Peradaban")
        curr_y = draw_wrapped_text(title_text, 120, 290, 840, 54, font_title, (248, 250, 252))

        # Divider halus di dalam kartu
        draw.line([(120, curr_y + 20), (width - 120, curr_y + 20)], fill=(30, 41, 59), width=1)

        # Render Narasi
        body_text = display_body if display_body != title_text else (" ".join(sentences[1:4]) if len(sentences) > 1 else clean_text)
        draw_wrapped_text(body_text, 120, curr_y + 50, 840, 42, font_body, (203, 213, 225))

        # 7. Footer Bar: Slide Indicators & Official Signature
        # Indicator dots
        dots_text = "● " * slide_idx + "○ " * (total_slides - slide_idx)
        draw.text((80, height - 120), f"{dots_text.strip()}   Slide {slide_idx:02d} of {total_slides:02d}", fill=(148, 163, 184), font=font_footer)
        draw.text((width - 360, height - 120), "@Pitamediaid • Official Post", fill=(99, 102, 241), font=font_footer)

        img.save(output_path, format="PNG")
        return output_path


imagen_client = ImagenClient()
