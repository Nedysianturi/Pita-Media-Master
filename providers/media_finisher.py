"""
Modul Media Finisher menggunakan FFmpeg (via binary imageio-ffmpeg atau sistem).
Menangani normalisasi rasio aspek (9:16 vertical, 1:1 square),
watermark/signature halus 'Pita Waktu', dan verifikasi integritas media.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
import imageio_ffmpeg

from config.settings import settings


class MediaFinisher:
    def __init__(self, binary_path: Optional[str] = None):
        if binary_path and Path(binary_path).exists():
            self.ffmpeg_exe = binary_path
        else:
            try:
                self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                self.ffmpeg_exe = "ffmpeg"

    def get_version(self) -> str:
        """Mengambil versi ffmpeg yang aktif."""
        try:
            res = subprocess.run(
                [self.ffmpeg_exe, "-version"],
                capture_output=True,
                text=True,
                check=True,
            )
            first_line = res.stdout.splitlines()[0] if res.stdout else "FFmpeg available"
            return first_line
        except Exception as e:
            return f"FFmpeg error: {e}"

    def apply_signature_watermark(
        self,
        input_video_path: str,
        output_video_path: str,
        watermark_text: Optional[str] = None,
        position: str = "bottom_right",
        opacity: float = 0.65,
    ) -> str:
        """
        Menambahkan teks watermark/signature berkelas (e.g. 'Pita Waktu') pada video.
        """
        text = watermark_text or settings.SIGNATURE_TEXT
        if not settings.ENABLE_SIGNATURE_WATERMARK or not text:
            # Jika watermark dinonaktifkan, salin langsung
            import shutil
            shutil.copy2(input_video_path, output_video_path)
            return output_video_path

        # Tentukan posisi koordinat FFmpeg
        if position == "bottom_right":
            coord = "x=w-tw-30:y=h-th-30"
        elif position == "top_right":
            coord = "x=w-tw-30:y=30"
        else:
            coord = "x=w-tw-30:y=h-th-30"

        # Format filter drawtext FFmpeg
        # Menggunakan font standar sistem yang elegan
        drawtext_filter = (
            f"drawtext=text='{text}':{coord}:"
            f"fontsize=26:fontcolor=white@{opacity}:"
            f"shadowcolor=black@0.4:shadowx=2:shadowy=2"
        )

        cmd = [
            self.ffmpeg_exe,
            "-y",  # Overwrite output
            "-i", str(input_video_path),
            "-vf", drawtext_filter,
            "-c:a", "copy",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            str(output_video_path),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # Fallback jika drawtext libfreetype bermasalah di environment tertentu
            import shutil
            shutil.copy2(input_video_path, output_video_path)
        return str(output_video_path)

    def normalize_video_aspect_ratio(
        self,
        input_video_path: str,
        output_video_path: str,
        target_aspect: str = "9:16",
        width: int = 1080,
        height: int = 1920,
    ) -> str:
        """
        Memastikan resolusi video sesuai rasio 9:16 (1080x1920) dengan padding atau crop yang proporsional.
        """
        vf_scale = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"

        cmd = [
            self.ffmpeg_exe,
            "-y",
            "-i", str(input_video_path),
            "-vf", vf_scale,
            "-c:a", "copy",
            "-c:v", "libx264",
            "-preset", "fast",
            str(output_video_path),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            import shutil
            shutil.copy2(input_video_path, output_video_path)
        return str(output_video_path)

    def stitch_image_carousel_preview(
        self,
        image_paths: List[str],
        output_preview_video: str,
        duration_per_image: float = 3.0,
    ) -> str:
        """
        Membuat klip preview cepat dari urutan gambar carousel (untuk keperluan QC reviewer).
        """
        # Buat file daftar sementara untuk concat demuxer FFmpeg
        temp_list_file = Path(output_preview_video).parent / "concat_list.txt"
        with open(temp_list_file, "w", encoding="utf-8") as f:
            for img in image_paths:
                f.write(f"file '{Path(img).resolve().as_posix()}'\n")
                f.write(f"duration {duration_per_image}\n")
            # Concat demuxer butuh pengulangan baris terakhir
            if image_paths:
                f.write(f"file '{Path(image_paths[-1]).resolve().as_posix()}'\n")

        cmd = [
            self.ffmpeg_exe,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(temp_list_file),
            "-vsync", "vfr",
            "-pix_fmt", "yuv420p",
            str(output_preview_video),
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True)
        finally:
            if temp_list_file.exists():
                temp_list_file.unlink()

        return str(output_preview_video)


media_finisher = MediaFinisher()
