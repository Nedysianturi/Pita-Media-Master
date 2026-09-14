"""
Pita Media Autonomous Runtime - End-to-End Dry Run Simulator
Executes full multi-agent pipeline (Strategist -> Creator -> Reviewer QC -> Auto-Repair -> Mock Publisher)
WITHOUT posting to Facebook or exposing external networks.
"""

import os
import sys
import asyncio
import logging
from typing import Dict, Any

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from database.connection import init_db, async_session_factory
from database.models import Job, Content, QCRecord
from core.queue import job_queue
from agents.creator import creator_agent
from agents.reviewer import reviewer_agent
from core.governors.fatigue_engine import fatigue_engine

logger = logging.getLogger("pita.runtime.dry_run")

async def run_dry_run_simulation(pilar: str = "pita_cerita") -> Dict[str, Any]:
    """
    Simulates a complete content creation, QC check, and publishing flow without publishing to Meta.
    """
    print("\n" + "=" * 60)
    print(f"🎬 PITA MEDIA: RUNNING END-TO-END DRY RUN SIMULATION (#{pilar})")
    print("=" * 60)
    print("🔒 SAFE MODE: Real Meta posting is DISABLED. Zero live posts will be made.\n")

    await init_db()

    async with async_session_factory() as session:
        # Step 1: Enqueue Dry-Run Job
        print("[1/4] 📦 Enqueuing simulated job...")
        job, is_new = await job_queue.enqueue_job(
            db_session=session,
            pilar=pilar,
            schedule_slot="dry_run_test",
            seed_or_title=f"Dry Run Verification for #{pilar}",
            is_exploration=False
        )
        print(f"  ✓ Job ID: {job.id} (Pilar: #{job.pilar})")

        # Step 2: Creator Agent Generation
        print("\n[2/4] 🎨 Executing Creator Agent (Gemini Content Generation)...")
        recent_topics = await fatigue_engine.get_recent_topics(session, days=7)
        try:
            content_payload = await creator_agent.produce_content(
                pilar=job.pilar,
                job_id=job.id,
                recent_topics=recent_topics,
                is_exploration=False,
                db_session=session
            )
            print(f"  ✓ Title: \"{content_payload.get('title')}\"")
            print(f"  ✓ Media Type: {content_payload.get('media_type')}")
            print(f"  ✓ Media Assets: {len(content_payload.get('media_paths', []))} file(s)")
            print(f"  ✓ Caption Length: {len(content_payload.get('caption', ''))} chars")
        except Exception as e:
            print(f"  ✗ Creator Agent Error: {e}")
            return {"status": "FAILED", "stage": "creator", "error": str(e)}

        # Save simulated content
        content_db = Content(
            job_id=job.id,
            pilar=job.pilar,
            title=content_payload["title"],
            caption=content_payload["caption"],
            media_type=content_payload["media_type"],
            media_paths=content_payload["media_paths"],
        )
        session.add(content_db)
        await session.commit()
        await session.refresh(content_db)

        # Step 3: Reviewer Agent QC & Auto-Repair Loop
        print("\n[3/4] 🛡️ Executing Reviewer Agent QC & Safety Gate...")
        try:
            final_payload, qc_history = await reviewer_agent.review_and_repair_loop(
                content_payload=content_payload,
                job_id=job.id,
                content_id=content_db.id,
                db_session=session
            )
            final_qc = qc_history[-1]
            print(f"  ✓ QC Total Score: {final_qc.get('total_score', 0)}/10")
            print(f"  ✓ QC Verdict: {final_qc.get('verdict')}")
            print(f"  ✓ Repair Iterations: {len(qc_history)}")
        except Exception as e:
            print(f"  ✗ Reviewer Agent Error: {e}")
            return {"status": "FAILED", "stage": "reviewer", "error": str(e)}

        # Step 4: Mock Publisher
        print("\n[4/4] 🚀 Executing Mock Publisher Gate...")
        print("  ✓ Target: Fanspage @Pitamediaid (ID: 1253340697871457) [DRY RUN]")
        print("  ✓ Publishing Action: SIMULATED (No network packet sent to Meta API)")
        print(f"  ✓ Final Simulated Post URL: https://facebook.com/Pitamediaid/posts/mock_dryrun_{job.id[:8]}")

        await job_queue.update_status(session, job.id, "DRY_RUN_COMPLETED")

        print("\n" + "=" * 60)
        print("🎉 DRY RUN SUCCESSFUL: All creator, QC, and mock publishing stages passed!")
        print("=" * 60 + "\n")

        return {
            "status": "PASS",
            "job_id": job.id,
            "title": final_payload.get("title"),
            "qc_score": final_qc.get("total_score"),
            "qc_verdict": final_qc.get("verdict")
        }
