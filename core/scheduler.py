"""
Modul Scheduler & Content Calendar Orchestrator untuk Sistem Pita Media.
Mengkoordinasikan seluruh alur otonom:
Ideasi -> Kreasi -> QC -> Auto-Repair -> Publikasi -> Verifikasi -> Pelaporan.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config.settings import settings
from database.connection import async_session_factory
from database.models import Job, Content, Provenance, Publication, PerformanceMetric
from core.queue import job_queue
from core.governors.cost_governor import cost_governor
from core.governors.frequency_governor import frequency_governor
from core.governors.fatigue_engine import fatigue_engine
from core.resilience.circuit_breaker import gemini_circuit_breaker
from agents.creator import creator_agent
from agents.reviewer import reviewer_agent
from agents.publisher import publisher_agent
from agents.strategist import strategy_optimizer
from monitoring.telegram_bot import telegram_c2

logger = logging.getLogger(__name__)


class ContentOrchestrator:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False

    async def schedule_next_content_slot(self, pilar: Optional[str] = None):
        """
        Menjadwalkan slot konten berikutnya berdasarkan bobot pilar Strategist dan kuota eksplorasi.
        """
        async with async_session_factory() as session:
            # 1. Pilih pilar jika tidak ditentukan secara manual
            if not pilar:
                import random
                weights = strategy_optimizer.current_weights
                pillars = list(weights.keys())
                probabilities = list(weights.values())
                selected_pilar = random.choices(pillars, weights=probabilities, k=1)[0]
            else:
                selected_pilar = pilar

            # 2. Tentukan apakah ini slot eksplorasi (20-30%)
            is_exploration = fatigue_engine.decide_exploration_slot()

            # 3. Buat slot timestamp
            slot_time = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H:%M")
            seed_title = f"{selected_pilar}_slot_{slot_time}"

            # 4. Enqueue ke Job Queue
            job, is_new = await job_queue.enqueue_job(
                db_session=session,
                pilar=selected_pilar,
                schedule_slot=slot_time,
                seed_or_title=seed_title,
                is_exploration=is_exploration,
            )

            if is_new:
                logger.info(f"Slot baru dijadwalkan: Job {job.id} (#{selected_pilar}, eksplorasi={is_exploration}).")
            return job

    async def process_single_job(self, job_id: str) -> bool:
        """
        Menjalankan siklus eksekusi lengkap untuk satu job dari awal sampai terbit dan terverifikasi.
        """
        if telegram_c2.is_paused or telegram_c2.is_emergency_stopped:
            logger.info("Pemrosesan dilewati karena sistem dijeda atau emergency stopped.")
            return False

        if not gemini_circuit_breaker.can_execute():
            logger.warning("Circuit breaker OPEN. Menunda pemrosesan job.")
            return False

        async with async_session_factory() as session:
            # 1. Validasi Batas Biaya (Cost Governor)
            allowed, cost_msg = await cost_governor.can_proceed_with_paid_generation(session)
            if not allowed:
                logger.error(f"Pengerjaan dibatalkan oleh Cost Governor: {cost_msg}")
                await telegram_c2.notify_critical_error("CostGovernor", cost_msg, job_id=job_id)
                await job_queue.update_status(session, job_id, "BLOCKED", error_message=cost_msg)
                return False

            # 2. Ambil Riwayat Topik untuk Anti-Fatigue
            recent_topics = await fatigue_engine.get_recent_topics(session, days=30)

            # 3. Creator Agent: Hasilkan Konten & Aset
            try:
                await job_queue.update_status(session, job_id, "IN_CREATOR")
                job_obj = await session.get(Job, job_id)
                if not job_obj:
                    return False

                content_payload = await creator_agent.produce_content(
                    pilar=job_obj.pilar,
                    job_id=job_id,
                    recent_topics=recent_topics,
                    is_exploration=job_obj.is_exploration,
                    db_session=session,
                )

                # Simpan Content Record awal
                content_db = Content(
                    job_id=job_id,
                    pilar=job_obj.pilar,
                    title=content_payload["title"],
                    caption=content_payload["caption"],
                    media_type=content_payload["media_type"],
                    media_paths=content_payload["media_paths"],
                )
                session.add(content_db)
                await session.commit()
                await session.refresh(content_db)

                # Simpan Provenance
                provenance = Provenance(
                    content_id=content_db.id,
                    prompt_version="1.0.0",
                    raw_prompts=content_payload.get("raw_prompts", {}),
                    model_name=content_payload.get("model_name", "gemini"),
                    model_version=content_payload.get("model_version", "1.0"),
                    veo_params=content_payload.get("veo_params"),
                    ffmpeg_params=content_payload.get("ffmpeg_params"),
                )
                session.add(provenance)
                await session.commit()

            except Exception as e:
                gemini_circuit_breaker.record_failure()
                err_msg = f"Creator Agent gagal: {e}"
                logger.error(err_msg)
                await job_queue.update_status(session, job_id, "FAILED", error_message=err_msg)
                await telegram_c2.notify_critical_error("CreatorAgent", str(e), job_id=job_id)
                return False

            # 4. Reviewer Agent: QC & Auto-Repair Loop
            try:
                await job_queue.update_status(session, job_id, "IN_REVIEW")
                final_payload, qc_history = await reviewer_agent.review_and_repair_loop(
                    content_payload=content_payload,
                    job_id=job_id,
                    content_id=content_db.id,
                    db_session=session,
                )

                final_verdict = qc_history[-1]["verdict"]

                if len(qc_history) > 1:
                    # Beritahu event repair
                    await telegram_c2.notify_repair_event(
                        pilar=job_obj.pilar,
                        title=final_payload["title"],
                        iteration=len(qc_history),
                        feedback=qc_history[0]["feedback_text"],
                        job_id=job_id,
                    )

                if final_verdict == "BLOCKED":
                    await job_queue.update_status(session, job_id, "BLOCKED", error_message="Gagal Hard Safety Gate.")
                    await telegram_c2.notify_critical_error("ReviewerSafetyGate", "Konten diblokir oleh Safety Policy.", job_id=job_id)
                    return False
                elif final_verdict == "NEEDS_ATTENTION":
                    await job_queue.update_status(session, job_id, "NEEDS_ATTENTION", error_message="Gagal QC setelah batas maksimal perbaikan.")
                    await telegram_c2.notify_critical_error("ReviewerQC", "Konten membutuhkan perhatian manual.", job_id=job_id)
                    return False

                # Perbarui caption final jika ada perubahan dari self-repair
                content_db.caption = final_payload["caption"]
                await session.commit()
                await job_queue.update_status(session, job_id, "APPROVED")

            except Exception as e:
                err_msg = f"Reviewer Agent gagal: {e}"
                logger.error(err_msg)
                await job_queue.update_status(session, job_id, "FAILED", error_message=err_msg)
                return False

            # 5. Publisher Agent: Publikasi & Verifikasi Pasca-Posting
            try:
                await job_queue.update_status(session, job_id, "IN_PUBLISH")
                pub_result = await publisher_agent.publish_content(
                    content_id=content_db.id,
                    content_payload=final_payload,
                    qc_verdict=final_verdict,
                    platform="mock",
                    db_session=session,
                )

                await job_queue.update_status(session, job_id, "PUBLISHED")
                gemini_circuit_breaker.record_success()

                # Notifikasi ke Telegram
                await telegram_c2.notify_publish_success(
                    pilar=job_obj.pilar,
                    title=final_payload["title"],
                    post_url=pub_result["post_url"],
                    content_id=content_db.id,
                    job_id=job_id,
                )
                return True

            except Exception as e:
                err_msg = f"Publisher Agent gagal: {e}"
                logger.error(err_msg)
                await job_queue.update_status(session, job_id, "FAILED", error_message=err_msg)
                await telegram_c2.notify_critical_error("PublisherAgent", str(e), job_id=job_id)
                return False


content_orchestrator = ContentOrchestrator()
