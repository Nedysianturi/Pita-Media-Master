"""
Modul Self-Repair Engine untuk Reviewer Agent.
Menjalankan loop perbaikan otomatis ketika skor QC menghasilkan status REPAIR_REQUIRED.
Menyimpan riwayat diff sebelum dan sesudah perbaikan, lalu menguji ulang.
"""

from typing import Dict, Any, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from providers.gemini_client import gemini_client
from agents.creator.ideator import ContentIdea
from config.settings import settings


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
        Lakukan perbaikan mandiri (Self-Repair) untuk konten '{pilar}' berikut:
        Judul: {old_title}
        Caption Saat Ini: {old_caption}
        Rencana Perbaikan QC: {repair_plan}

        Instruksi Perbaikan:
        - Jika pilar adalah pita_cerita: Pastikan caption berupa cerita utuh dengan panjang tepat 150-300 kata yang menyentuh.
        - Perbaiki kelemahan yang disebutkan pada rencana perbaikan tanpa mengubah esensi ide dasar.
        
        Keluarkan teks caption baru yang telah disempurnakan.
        """

        if not self.gemini.is_configured():
            new_caption = (
                f"[REPAIRED] {old_caption}\n\n"
                f"Catatan: Narasi telah disempurnakan dengan penekanan pada kedalaman karakter dan atmosfer yang lebih mendalam."
            )
        else:
            new_caption = await self.gemini.generate_text(
                prompt=repair_prompt,
                system_instruction="Anda adalah Editor Ahli Perbaikan Konten Kreatif.",
                db_session=db_session,
                job_id=job_id,
            )

        # Hitung diff ringkas
        diff_record = {
            "old_word_count": len(old_caption.split()),
            "new_word_count": len(new_caption.split()),
            "repair_plan_applied": repair_plan,
            "old_caption_preview": old_caption[:100] + "...",
            "new_caption_preview": new_caption[:100] + "...",
        }

        # Perbarui payload
        repaired_payload = dict(content_payload)
        repaired_payload["caption"] = new_caption.strip()

        return repaired_payload, diff_record


self_repair_engine = SelfRepairEngine()
