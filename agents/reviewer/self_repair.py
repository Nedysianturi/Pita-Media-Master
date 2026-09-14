"""
Modul Self-Repair Engine untuk Reviewer Agent.
Menjalankan loop perbaikan otomatis ketika skor QC menghasilkan status REPAIR_REQUIRED.
Menyimpan riwayat diff sebelum dan sesudah perbaikan, lalu menguji ulang.
"""

import re
from typing import Dict, Any, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from providers.gemini_client import gemini_client
from agents.creator.ideator import ContentIdea
from config.settings import settings


def clean_ai_caption_fluff(text: str) -> str:
    """
    Membersihkan teks pembuka/pengantar percakapan AI seperti:
    - 'Berikut adalah hasil perbaikan...'
    - 'Tentu, ini naskah yang telah diperbaiki...'
    - 'Catatan QC:...'
    sehingga hanya menyisakan teks konten / narasi murni untuk media sosial.
    """
    if not text:
        return ""
    
    # Hapus baris pengantar AI di awal
    patterns = [
        r"^(?:berikut|tentu|ini|hasil|catatan|naskah|teks)[^\n]*?(?:perbaikan|self-repair|qc|sesuai|instruksi|disempurnakan)[^\n]*?:?\s*\n+",
        r"^\*?\*?(?:berikut adalah|ini adalah|hasil perbaikan|revisi)[^\n]*?\*?\*?:?\s*\n+",
        r"^\[REPAIRED\]\s*",
        r"^\"|\"$"
    ]
    
    cleaned = text.strip()
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()
    
    # Hapus catatan di bagian akhir jika ada catatan editor
    cleaned = re.sub(r"\n\s*(?:catatan|note|perubahan yang dilakukan|diff):.*$", "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip()
    
    return cleaned


class SelfRepairEngine:
    def __init__(self):
        self.gemini = gemini_client

    async def execute_repair(
        self,
        content_payload: Dict[str, Any],
        qc_eval: Dict[str, Any],
        job_id: str,
        db_session: Optional[AsyncSession] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Menjalankan perbaikan terarah:
        1. Analisis kelemahan dari repair_plan & scores_breakdown
        2. Perbaiki caption / deskripsi visual
        3. Rekam perubahan diff_before_after
        4. Kembalikan payload yang diperbaiki
        """
        old_caption = content_payload.get("caption", "")
        old_title = content_payload.get("title", "")
        pilar = content_payload.get("pilar", "")
        repair_plan = qc_eval.get("repair_plan", "Tingkatkan kualitas narasi dan estetika visual.")

        # Lakukan perbaikan teks & prompt via Gemini
        repair_prompt = f"""
        Lakukan perbaikan mandiri untuk narasi konten '{pilar}' berikut:
        Judul: {old_title}
        Caption Saat Ini: {old_caption}
        Rencana Perbaikan QC: {repair_plan}

        ATURAN MUTLAK PERBAIKAN:
        1. Kembalikan HANYA teks caption final murni yang siap dipublikasikan ke media sosial.
        2. DILARANG KERAS menyertakan kalimat pengantar, salam pembuka, kata 'Berikut adalah hasil perbaikan', atau catatan editor AI apapun.
        3. Jika pilar pita_cerita: Pastikan caption berupa cerita utuh dengan panjang 150-300 kata yang menyentuh.
        4. Perbaiki kelemahan yang disebutkan pada rencana perbaikan tanpa mengubah esensi ide dasar.
        """

        if not self.gemini.is_configured():
            new_caption = old_caption
        else:
            new_caption = await self.gemini.generate_text(
                prompt=repair_prompt,
                system_instruction=(
                    "Anda adalah Editor Ahli Konten Media Sosial Pita Media. "
                    "Output Anda HANYA berupa teks caption final media sosial tanpa pengantar atau komentar apapun."
                ),
                db_session=db_session,
                job_id=job_id,
            )

        # Bersihkan fluff/meta percakapan jika ada
        cleaned_caption = clean_ai_caption_fluff(new_caption)
        if not cleaned_caption:
            cleaned_caption = old_caption

        # Hitung diff ringkas
        diff_record = {
            "old_word_count": len(old_caption.split()),
            "new_word_count": len(cleaned_caption.split()),
            "repair_plan_applied": repair_plan,
            "old_caption_preview": old_caption[:100] + "...",
            "new_caption_preview": cleaned_caption[:100] + "...",
        }

        # Perbarui payload
        repaired_payload = dict(content_payload)
        repaired_payload["caption"] = cleaned_caption.strip()

        return repaired_payload, diff_record


self_repair_engine = SelfRepairEngine()

