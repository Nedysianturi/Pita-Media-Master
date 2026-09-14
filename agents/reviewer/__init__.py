"""
Reviewer Agent Package: Mengatur Hard Safety Gate, QC Rubric Engine, dan Auto-Repair Loop.
"""

from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from agents.reviewer.safety_gate import safety_gate, SafetyGate
from agents.reviewer.qc_engine import qc_engine, QCEngine
from agents.reviewer.self_repair import self_repair_engine, SelfRepairEngine
from database.models import QCRecord


class ReviewerAgent:
    def __init__(self):
        self.safety_gate = safety_gate
        self.qc_engine = qc_engine
        self.repair_engine = self_repair_engine

    async def review_and_repair_loop(
        self,
        content_payload: Dict[str, Any],
        job_id: str,
        content_id: Optional[str] = None,
        db_session: Optional[AsyncSession] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """
        Menjalankan loop evaluasi kualitas dan perbaikan otomatis:
        - Mengevaluasi Hard Safety Gate
        - Mengevaluasi Rubrik Kualitas
        - Jika REPAIR_REQUIRED: lakukan self-repair dan ulangi hingga batas maksimum
        - Mengembalikan (final_content_payload, list_of_qc_records)
        """
        current_payload = dict(content_payload)
        qc_history = []
        iteration = 1
        max_attempts = 3

        while iteration <= max_attempts:
            # 1. Evaluasi QC
            eval_res = await self.qc_engine.evaluate_content(
                content_payload=current_payload,
                iteration_number=iteration,
            )
            verdict = eval_res["verdict"]

            # 2. Simpan record QC ke database jika ada session
            if db_session and content_id:
                qc_db = QCRecord(
                    content_id=content_id,
                    iteration_number=iteration,
                    verdict=verdict,
                    total_score=eval_res["total_score"],
                    scores_breakdown=eval_res["scores_breakdown"],
                    feedback_text=eval_res["feedback_text"],
                    repair_plan=eval_res.get("repair_plan"),
                    diff_before_after=eval_res.get("diff_before_after"),
                    safety_gate_passed=eval_res["safety_gate_passed"],
                    safety_violations=eval_res["safety_violations"],
                )
                db_session.add(qc_db)
                await db_session.commit()

            qc_history.append(eval_res)

            # 3. Handle Verdict
            if verdict == "PASSED":
                break
            elif verdict == "BLOCKED":
                # Pelanggaran keras, langsung hentikan
                break
            elif verdict == "REPAIR_REQUIRED" and iteration < max_attempts:
                # Jalankan perbaikan otomatis
                repaired_payload, diff = await self.repair_engine.execute_repair(
                    content_payload=current_payload,
                    qc_eval=eval_res,
                    job_id=job_id,
                    db_session=db_session,
                )
                eval_res["diff_before_after"] = diff
                current_payload = repaired_payload
                iteration += 1
            else:
                # Gagal mencapai threshold setelah max repair
                eval_res["verdict"] = "NEEDS_ATTENTION"
                break

        return current_payload, qc_history


reviewer_agent = ReviewerAgent()

__all__ = [
    "reviewer_agent",
    "ReviewerAgent",
    "safety_gate",
    "qc_engine",
    "self_repair_engine",
]
