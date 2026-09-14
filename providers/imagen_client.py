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
        self.api_key = api_key or settings.GEMINI_API_KEY
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
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None,
    ) -> str:
        """
        Menghasilkan satu gambar statis dan menyimpannya ke output_path.
        """
        dest_file = Path(output_path)
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.is_configured() or not self.client:
            return self._create_mock_image(str(dest_file), prompt)

        try:
            result = self.client.models.generate_images(
                model=self.model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    output_mime_type="image/png",
                    aspect_ratio=aspect_ratio,
                ),
            )

            if not result.generated_images:
                return self._create_mock_image(str(dest_file), prompt)

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
            return self._create_mock_image(str(dest_file), prompt)

    async def generate_carousel_images(
        self,
        prompts: List[str],
        output_dir: str,
        prefix: str = "slide",
        db_session: Optional[AsyncSession] = None,
        job_id: Optional[str] = None,
    ) -> List[str]:
        """
        Menghasilkan rangkaian 3-5 gambar untuk postingan carousel Pita Cerita.
        """
        generated_paths = []
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        for idx, prompt in enumerate(prompts, start=1):
            file_name = f"{prefix}_{idx:02d}.png"
            dest = out_path / file_name
            saved_path = await self.generate_single_image(
                prompt=prompt,
                output_path=str(dest),
                aspect_ratio="1:1",
                db_session=db_session,
                job_id=job_id,
            )
            generated_paths.append(saved_path)

        return generated_paths

    def _create_mock_image(self, output_path: str, prompt_text: str) -> str:
        """Helper membuat gambar placeholder artistik jika offline/kuota terbatas."""
        img = Image.new("RGB", (1080, 1080), color=(26, 32, 44))
        draw = ImageDraw.Draw(img)

        # Buat border & teks
        draw.rectangle([(20, 20), (1060, 1060)], outline=(66, 153, 225), width=4)
        draw.text((60, 100), "Pita Media - Pita Cerita", fill=(237, 242, 247))
        draw.text((60, 150), f"Prompt: {prompt_text[:80]}...", fill=(160, 174, 192))
        draw.text((60, 980), "Pita Waktu Signature", fill=(203, 213, 225))

        img.save(output_path, format="PNG")
        return output_path


imagen_client = ImagenClient()
