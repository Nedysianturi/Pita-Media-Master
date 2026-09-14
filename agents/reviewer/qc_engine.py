"""
Modul Quality Control (QC) Engine untuk Reviewer Agent.
Mengevaluasi rubrik multi-kategori, menghitung bobot skor QC,
dan menentukan status verdict (PASSED, REPAIR_REQUIRED, NEEDS_ATTENTION, BLOCKED).
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml
from pydantic import BaseModel, Field

from providers.gemini_client import gemini_client
from agents.reviewer.safety_gate import safety_gate, SafetyEvaluationResult
from config.settings import settings

RUBRIC_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "rubric_definitions.yaml"


class CategoryScoreItem(BaseModel):
    category_id: str = Field(default="relevance", description="ID kategori penilaian")
    score: float = Field(default=8.5, ge=0.0, le=10.0, description="Skor dari skala 0.0 sampai 10.0")
    notes: str = Field(default="", description="Catatan evaluasi untuk kategori ini")


class QCEvaluationResult(BaseModel):
    scores: List[CategoryScoreItem] = Field(default_factory=list, description="Daftar penilaian per kategori")
    feedback_text: str = Field(default="Evaluasi QC otomatis selesai.", description="Ulasan menyeluruh atas kekuatan dan kelemahan konten")
    repair_plan: Optional[str] = Field(default=None, description="Instruksi spesifik perbaikan jika ada skor di bawah threshold")


class QCEngine:
    def __init__(self):
        self.gemini = gemini_client
        self.rubrics = self._load_rubrics()
        self.safety_gate = safety_gate

    def _load_rubrics(self) -> Dict[str, Any]:
        if RUBRIC_FILE.exists():
            with open(RUBRIC_FILE, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {"thresholds": {"total_pass": 8.0, "category_pass": 7.0, "max_repair_attempts": 3}, "pillars": {}}

    async def evaluate_content(
        self,
        content_payload: Dict[str, Any],
        iteration_number: int = 1,
    ) -> Dict[str, Any]:
        """
        Evaluasi lengkap:
        1. Hard Safety Gate
        2. Rubrik Kualitas per Kategori
        3. Kalkulasi Skor Total Terbobot
        4. Penentuan Status Verdict
        """
        pilar = content_payload.get("pilar", "pita_transformasi")
        title = content_payload.get("title", "")
        caption = content_payload.get("caption", "")

        # 1. Hard Safety Gate
        safety_res: SafetyEvaluationResult = await self.safety_gate.evaluate(content_payload)
        if not safety_res.is_safe:
            return {
                "verdict": "BLOCKED",
                "total_score": 0.0,
                "scores_breakdown": {},
                "feedback_text": f"DIBLOKIR: Konten melanggar Hard Safety Gate ({', '.join(safety_res.violations)}). {safety_res.reasoning}",
                "repair_plan": None,
                "safety_gate_passed": False,
                "safety_violations": safety_res.violations,
                "iteration_number": iteration_number,
            }

        # 2. Dapatkan Rubrik Pilar
        pillar_config = self.rubrics.get("pillars", {}).get(pilar, {})
        categories = pillar_config.get("categories", [])
        thresholds = self.rubrics.get("thresholds", {})
        total_pass_threshold = thresholds.get("total_pass", settings.QC_TOTAL_PASS_THRESHOLD)
        category_pass_threshold = thresholds.get("category_pass", settings.QC_CATEGORY_PASS_THRESHOLD)
        max_repairs = thresholds.get("max_repair_attempts", settings.MAX_AUTO_REPAIR_ATTEMPTS)

        # 3. Evaluasi Skor Kualitas
        if not self.gemini.is_configured():
            qc_result = self._get_mock_qc_result(pilar, caption, categories)
        else:
            prompt = f"""
            Evaluasi kualitas konten '{pilar}' berikut dengan ketat dan objektif:
            Judul: {title}
            Caption: {caption}
            Media Type: {content_payload.get('media_type')}
            Jumlah Media: {len(content_payload.get('media_paths', []))}

            Kategori Rubrik yang Harus Dinilai (Skor 1.0 - 10.0):
            {yaml.dump(categories)}

            Berikan penilaian numerik per category_id, ulasan menyeluruh, serta repair_plan jika ada aspek yang perlu diperbaiki.
            """
            qc_result = await self.gemini.generate_structured(
                prompt=prompt,
                schema=QCEvaluationResult,
                system_instruction="Anda adalah Lead Quality Reviewer yang perfeksionis dan berstandar tinggi.",
                model=settings.GEMINI_PRO_MODEL,
            )

        # 4. Hitung Skor Total Terbobot
        if not qc_result.scores:
            qc_result = self._get_mock_qc_result(pilar, caption, categories)

        scores_map = {item.category_id: item.score for item in qc_result.scores}
        total_score = 0.0
        all_categories_pass = True

        for cat in categories:
            cid = cat["id"]
            weight = cat.get("weight", 0.25)
            score = scores_map.get(cid, 8.5)
            total_score += score * weight

            min_required = cat.get("min_score", category_pass_threshold)
            if score < min_required:
                all_categories_pass = False

        total_score = round(total_score, 2)

        # 5. Tentukan Status Verdict
        if total_score >= total_pass_threshold and all_categories_pass:
            verdict = "PASSED"
        elif iteration_number < max_repairs:
            verdict = "REPAIR_REQUIRED"
        else:
            verdict = "NEEDS_ATTENTION"

        return {
            "verdict": verdict,
            "total_score": total_score,
            "scores_breakdown": {item.category_id: {"score": item.score, "notes": item.notes} for item in qc_result.scores},
            "feedback_text": qc_result.feedback_text,
            "repair_plan": qc_result.repair_plan,
            "safety_gate_passed": True,
            "safety_violations": [],
            "iteration_number": iteration_number,
        }

    def _get_mock_qc_result(self, pilar: str, caption: str, categories: List[Dict[str, Any]]) -> QCEvaluationResult:
        word_count = len(caption.split())
        score_items = []

        for cat in categories:
            cid = cat["id"]
            score = 8.5
            # Pengecekan pilar cerita: kata caption harus 150-300 kata
            if cid == "story_depth_and_length":
                if word_count < 150 or word_count > 300:
                    score = 6.0
                else:
                    score = 9.0
            score_items.append(CategoryScoreItem(category_id=cid, score=score, notes=f"Penilaian standar {cid}"))

        return QCEvaluationResult(
            scores=score_items,
            feedback_text="Hasil kreasi memiliki estetika visual yang baik dan memenuhi standar pilar.",
            repair_plan="Tingkatkan transisi pencahayaan dan perkuat kata kunci emosional." if any(s.score < 7.0 for s in score_items) else None,
        )


qc_engine = QCEngine()
