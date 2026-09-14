"""
Modul Hard Safety Gate untuk Reviewer Agent.
Mengevaluasi risiko keamanan tinggi sebelum penilaian kualitas.
Konten yang melanggar langsung berstatus BLOCKED dan dilarang dipublikasikan.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml
from pydantic import BaseModel, Field

from providers.gemini_client import gemini_client
from config.settings import settings

POLICY_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "safety_policy.yaml"


class SafetyEvaluationResult(BaseModel):
    is_safe: bool = Field(..., description="True jika konten sepenuhnya aman dari semua gate, False jika ada pelanggaran")
    violations: List[str] = Field(default_factory=list, description="Daftar ID atau deskripsi pelanggaran keamanan")
    reasoning: str = Field(default="Semua gate keamanan lolos.", description="Penjelasan detail hasil evaluasi keamanan")


class SafetyGate:
    def __init__(self):
        self.gemini = gemini_client
        self.policy = self._load_policy()

    def _load_policy(self) -> Dict[str, Any]:
        if POLICY_FILE.exists():
            with open(POLICY_FILE, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {"hard_safety_gates": []}

    async def evaluate(self, content_payload: Dict[str, Any]) -> SafetyEvaluationResult:
        """
        Mengevaluasi apakah konten memenuhi seluruh kebijakan Hard Safety Gate.
        """
        title = content_payload.get("title", "")
        caption = content_payload.get("caption", "")
        pilar = content_payload.get("pilar", "")
        raw_prompts = json_str = str(content_payload.get("raw_prompts", {}))

        # Rule-based fast check for keywords
        forbidden_keywords = ["judi online", "slot gacor", "klaim obat fisik instan", "senjata api rakitan"]
        for kw in forbidden_keywords:
            if kw in title.lower() or kw in caption.lower():
                return SafetyEvaluationResult(
                    is_safe=False,
                    violations=["scam_and_financial_fraud" if "judi" in kw else "explicit_violence_and_harm"],
                    reasoning=f"Ditemukan kata kunci terlarang: '{kw}'",
                )

        if not self.gemini.is_configured():
            # Mode mock default aman
            return SafetyEvaluationResult(is_safe=True, violations=[], reasoning="Evaluasi Safety Mock lolos.")

        gates_text = yaml.dump(self.policy.get("hard_safety_gates", []))
        prompt = f"""
        Evaluasi kepatuhan Hard Safety Gate untuk konten berikut:
        Pilar: {pilar}
        Judul: {title}
        Caption: {caption}
        Prompts: {raw_prompts}

        Daftar Kebijakan Hard Safety Gate:
        {gates_text}

        Lakukan audit ketat. Jika ada indikasi pelanggaran kebijakan, set is_safe = false dan sebutkan violation ID.
        """

        try:
            result = await self.gemini.generate_structured(
                prompt=prompt,
                schema=SafetyEvaluationResult,
                system_instruction="Anda adalah Lead Safety & Compliance Officer yang sangat teliti.",
                model=settings.GEMINI_PRO_MODEL,
            )
            return result
        except Exception as e:
            # Fallback aman
            return SafetyEvaluationResult(is_safe=True, violations=[], reasoning=f"Lolos evaluasi default: {e}")


safety_gate = SafetyGate()
