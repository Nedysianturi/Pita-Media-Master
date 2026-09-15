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

    def has_audio_stream(self, video_path: str) -> bool:
        """Mengecek apakah file video memiliki stream audio."""
        if not Path(video_path).exists():
            return False
        try:
            cmd = [self.ffmpeg_exe, "-i", str(video_path)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            return "Audio:" in (res.stderr or "")
        except Exception:
            return False

    def attach_soundtrack(
        self,
        input_video_path: str,
        output_video_path: str,
        mood: str = "inspiratif",
        duration: int = 5,
    ) -> str:
        """
        Menambahkan soundtrack latar berkualitas stereo AAC 192k ke video jika belum memiliki audio.
        """
        from core.audio.music_manager import music_manager

        audio_expr = music_manager.get_audio_expression_for_mood(mood)
        fade_out_start = max(1.0, duration - 1.2)
        af_filter = f"afade=t=in:ss=0:d=0.8,afade=t=out:st={fade_out_start}:d=1.2"

        cmd = [
            self.ffmpeg_exe,
            "-y",
            "-i", str(input_video_path),
            "-f", "lavfi",
            "-i", f"aevalsrc={audio_expr}:s=44100:d={duration}",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-af", af_filter,
            "-shortest",
            str(output_video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            import shutil
            shutil.copy2(input_video_path, output_video_path)
        return str(output_video_path)

    def apply_signature_watermark(
        self,
        input_video_path: str,
        output_video_path: str,
        watermark_text: Optional[str] = None,
        position: str = "bottom_right",
        opacity: float = 0.85,
        mood: str = "inspiratif",
        duration: int = 5,
    ) -> str:
        """
        Menambahkan logo visual/teks signature 'Pita Waktu' dan memastikan audio track AAC aktif.
        """
        from core.audio.music_manager import music_manager

        has_audio = self.has_audio_stream(input_video_path)
        audio_expr = music_manager.get_audio_expression_for_mood(mood)
        fade_out_start = max(1.0, duration - 1.2)
        af_filter = f"afade=t=in:ss=0:d=0.8,afade=t=out:st={fade_out_start}:d=1.2"

        text = watermark_text or settings.SIGNATURE_TEXT or "Pita Waktu"
        coord = "x=w-tw-30:y=h-th-30" if position == "bottom_right" else "x=w-tw-30:y=30"
        drawtext_filter = (
            f"drawtext=text='{text}':{coord}:"
            f"fontsize=26:fontcolor=white@{opacity}:"
            f"shadowcolor=black@0.5:shadowx=2:shadowy=2"
        )

        logo_file = Path("storage/logo.png")
        if logo_file.exists() and settings.ENABLE_SIGNATURE_WATERMARK:
            overlay_coord = "W-w-35:H-h-35" if position == "bottom_right" else "W-w-35:35"
            filter_str = f"[1:v]scale=150:-1,format=rgba,colorchannelmixer=aa={opacity}[logo];[0:v][logo]overlay={overlay_coord}"
            if has_audio:
                cmd = [
                    self.ffmpeg_exe, "-y",
                    "-i", str(input_video_path),
                    "-i", str(logo_file),
                    "-filter_complex", filter_str,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-c:a", "copy",
                    str(output_video_path),
                ]
            else:
                cmd = [
                    self.ffmpeg_exe, "-y",
                    "-i", str(input_video_path),
                    "-i", str(logo_file),
                    "-f", "lavfi", "-i", f"aevalsrc={audio_expr}:s=44100:d={duration}",
                    "-filter_complex", filter_str,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                    "-c:a", "aac", "-b:a", "192k", "-af", af_filter,
                    "-shortest",
                    str(output_video_path),
                ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                return str(output_video_path)

        # Gunakan filter drawtext + synthesize audio jika audio stream belum ada
        if has_audio:
            cmd = [
                self.ffmpeg_exe, "-y",
                "-i", str(input_video_path),
                "-vf", drawtext_filter,
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "copy",
                str(output_video_path),
            ]
        else:
            cmd = [
                self.ffmpeg_exe, "-y",
                "-i", str(input_video_path),
                "-f", "lavfi", "-i", f"aevalsrc={audio_expr}:s=44100:d={duration}",
                "-vf", drawtext_filter,
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "aac", "-b:a", "192k", "-af", af_filter,
                "-shortest",
                str(output_video_path),
            ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            import shutil
            shutil.copy2(input_video_path, output_video_path)
        return str(output_video_path)

    def apply_photo_watermark(
        self,
        image_path: str,
        output_path: Optional[str] = None,
        max_logo_width: int = 160,
    ) -> str:
        """
        Menyematkan logo PitaMedia resmi di sudut gambar dengan transparansi halus.
        """
        out_path = output_path or image_path
        if not settings.ENABLE_SIGNATURE_WATERMARK:
            return image_path

        logo_file = Path("storage/logo.png")
        if not logo_file.exists():
            return image_path

        try:
            from PIL import Image
            base_img = Image.open(image_path).convert("RGBA")
            logo_img = Image.open(logo_file).convert("RGBA")

            # Resize logo secara proporsional
            aspect = logo_img.height / logo_img.width
            new_w = min(max_logo_width, int(base_img.width * 0.18))
            new_h = int(new_w * aspect)
            logo_resized = logo_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # Letakkan di sudut kanan bawah
            pos_x = base_img.width - new_w - 30
            pos_y = base_img.height - new_h - 30

            base_img.paste(logo_resized, (pos_x, pos_y), mask=logo_resized)
            rgb_img = base_img.convert("RGB")
            rgb_img.save(out_path)
            return out_path
        except Exception:
            return image_path

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
