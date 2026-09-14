"""
Pita Media Autonomous Runtime - Persistent Scheduler & Missed Job Recovery
Manages persistent scheduling, crash recovery for in-flight jobs, frequency-governed
missed-job catchup, and periodic heartbeat notifications.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy import select, update, func
from database.connection import async_session_factory
from database.models import Job, AuditLog, PerformanceMetric
from core.governors.frequency_governor import frequency_governor
from core.scheduler import content_orchestrator
from monitoring.telegram_bot import telegram_c2

logger = logging.getLogger("pita.runtime.persistent_scheduler")

class PersistentScheduler:
    def __init__(self, check_interval_seconds: int = 60):
        self.check_interval_seconds = check_interval_seconds
        self.is_running = False
        self.start_time = datetime.now(timezone.utc)
        self.last_heartbeat_time = datetime.now(timezone.utc)
        self.heartbeat_interval_hours = 4  # Send heartbeat every 4 hours

    async def recover_in_flight_jobs(self):
        """
        Detects jobs interrupted by system shutdown/crash (in IN_CREATOR, IN_REVIEW, IN_PUBLISH)
        and gracefully restores them to PENDING or NEEDS_ATTENTION.
        """
        logger.info("Checking for interrupted in-flight jobs from previous session...")
        async with async_session_factory() as session:
            stmt = select(Job).where(Job.status.in_(["IN_CREATOR", "IN_REVIEW", "IN_PUBLISH"]))
            res = await session.execute(stmt)
            interrupted_jobs = res.scalars().all()

            if not interrupted_jobs:
                logger.info("No interrupted jobs found. Clean startup state.")
                return

            for job in interrupted_jobs:
                prev_status = job.status
                # If interrupted during publish, mark as NEEDS_ATTENTION to avoid accidental duplicate posting
                if prev_status == "IN_PUBLISH":
                    job.status = "NEEDS_ATTENTION"
                    msg = f"Job {job.id} interrupted in IN_PUBLISH state. Set to NEEDS_ATTENTION to prevent duplicate posting."
                else:
                    job.status = "PENDING"
                    msg = f"Job {job.id} recovered from interrupted state '{prev_status}' back to PENDING."

                audit = AuditLog(
                    level="WARNING",
                    component="PersistentScheduler.Recovery",
                    message=msg
                )
                session.add(audit)
                logger.warning(msg)

            await session.commit()

    async def recover_missed_slots(self):
        """
        Detects missed scheduled slots if computer was off/offline.
        Applies Frequency Governor so channels aren't spammed with a burst of posts.
        """
        logger.info("Checking for missed scheduling slots...")
        async with async_session_factory() as session:
            # Check how many posts were published today
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            stmt = select(func.count(Job.id)).where(
                Job.status == "PUBLISHED",
                Job.created_at >= today_start
            )
            res = await session.execute(stmt)
            posts_today = res.scalar() or 0

            max_daily = 4  # Standard daily cap for healthy pacing
            if posts_today >= max_daily:
                logger.info(f"Daily frequency limit reached ({posts_today}/{max_daily}). No catch-up needed.")
                return

            # Check if any PENDING jobs exist
            stmt_pending = select(func.count(Job.id)).where(Job.status == "PENDING")
            res_pending = await session.execute(stmt_pending)
            pending_count = res_pending.scalar() or 0

            if pending_count == 0 and posts_today < max_daily:
                logger.info("No pending jobs found and under daily cap. Scheduling next fresh slot...")
                await content_orchestrator.schedule_next_content_slot()

    async def process_queue_tick(self):
        """Processes one pending job if conditions permit."""
        if telegram_c2.is_paused or telegram_c2.is_emergency_stopped:
            return

        async with async_session_factory() as session:
            # Check if there is a PENDING job
            stmt = select(Job).where(Job.status == "PENDING").order_by(Job.created_at.asc()).limit(1)
            res = await session.execute(stmt)
            job = res.scalar_one_or_none()

            if job:
                # Check frequency governor interval
                allowed, wait_sec = await frequency_governor.is_posting_allowed(session)
                if not allowed:
                    logger.debug(f"Pacing cooldown active. Waiting {wait_sec:.0f}s before next post.")
                    return

                logger.info(f"PersistentScheduler dispatching Job {job.id} (#{job.pilar})...")
                await content_orchestrator.process_single_job(job.id)

    async def send_heartbeat_if_due(self):
        """Sends periodic Telegram status report."""
        now = datetime.now(timezone.utc)
        elapsed = (now - self.last_heartbeat_time).total_seconds()
        if elapsed >= self.heartbeat_interval_hours * 3600:
            self.last_heartbeat_time = now
            await self.send_heartbeat_message()

    async def send_heartbeat_message(self):
        """Builds and sends formatted Telegram heartbeat."""
        uptime = datetime.now(timezone.utc) - self.start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)

        async with async_session_factory() as session:
            # Count status
            stmt_pub = select(func.count(Job.id)).where(Job.status == "PUBLISHED")
            res_pub = await session.execute(stmt_pub)
            published_count = res_pub.scalar() or 0

            stmt_pend = select(func.count(Job.id)).where(Job.status == "PENDING")
            res_pend = await session.execute(stmt_pend)
            pending_count = res_pend.scalar() or 0

            stmt_attn = select(func.count(Job.id)).where(Job.status == "NEEDS_ATTENTION")
            res_attn = await session.execute(stmt_attn)
            attention_count = res_attn.scalar() or 0

        msg = (
            "💓 *PITA MEDIA HEARTBEAT REPORT*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ *Uptime:* {hours}h {minutes}m\n"
            f"📊 *Status:* {'🟢 ACTIVE' if not telegram_c2.is_paused else '🟡 PAUSED'}\n"
            f"✅ *Published Posts:* {published_count}\n"
            f"⏳ *Queue Pending:* {pending_count}\n"
            f"⚠️ *Needs Attention:* {attention_count}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 _Gunakan /status atau /queue untuk info rinci._"
        )
        try:
            await telegram_c2.send_raw_message(msg)
        except Exception as e:
            logger.warning(f"Failed sending Telegram heartbeat: {e}")

    async def run_loop(self):
        """Main persistent scheduler background loop."""
        self.is_running = True
        logger.info("PersistentScheduler background loop started.")

        # 1. Crash recovery on start
        await self.recover_in_flight_jobs()
        # 2. Missed slot recovery
        await self.recover_missed_slots()

        while self.is_running:
            try:
                await self.process_queue_tick()
                await self.send_heartbeat_if_due()
            except Exception as e:
                logger.error(f"Error in PersistentScheduler loop: {e}", exc_info=True)

            await asyncio.sleep(self.check_interval_seconds)

    def stop(self):
        self.is_running = False
        logger.info("PersistentScheduler loop stopped.")

persistent_scheduler = PersistentScheduler()
