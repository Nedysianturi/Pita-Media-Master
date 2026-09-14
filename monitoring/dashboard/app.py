"""
Pita Media Autonomous Web Control Dashboard (http://pitamedia.localhost)
Decoupled web command center providing 24/7 monitoring, multi-tab navigation,
interactive controls (START, PAUSE, RESUME, STOP, RESTART, EMERGENCY STOP),
and complete visibility into 11 functional subsystems.
"""

import os
import sys
import json
import time
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, Security, status, Request, BackgroundTasks
from fastapi.security import APIKeyHeader
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, desc

from config.settings import settings
from database.connection import async_session_factory
from database.models import Job, Content, Publication, CostRecord, AuditLog, QCRecord, PerformanceMetric
from core.governors.cost_governor import cost_governor
from core.scheduler import content_orchestrator
from monitoring.telegram_bot import telegram_c2
from agents.strategist import strategy_optimizer
from core.runtime.control_bus import control_bus
from core.runtime.self_check import run_startup_self_check
from core.runtime.health_monitor import CredentialHealthMonitor
from core.runtime.maintenance import StorageMaintenance

app = FastAPI(title="Pita Media Command Center", docs_url=None, redoc_url=None)

# Mount static folder
static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

API_KEY_HEADER = APIKeyHeader(name="X-Pita-Secret", auto_error=False)

def verify_dashboard_access(key: str = Security(API_KEY_HEADER), request: Request = None):
    secret = settings.DASHBOARD_SECRET_KEY
    token_param = request.query_params.get("token") if request else None
    
    # Allow localhost / 127.0.0.1 without token by default for local machine security
    client_host = request.client.host if (request and request.client) else ""
    is_localhost = client_host in ["127.0.0.1", "localhost", "::1"]
    
    if is_localhost or (secret and (key == secret or token_param == secret)):
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Akses Ditolak: Masukkan token otorisasi yang valid.",
    )

# --- MEDIA SERVING ---

@app.get("/api/media/{file_path:path}")
async def serve_media(file_path: str):
    """Serve media files securely from storage."""
    raw_target = (settings.raw_media_dir / Path(file_path).name).resolve()
    if raw_target.exists():
        return FileResponse(str(raw_target))

    proc_target = (settings.processed_media_dir / Path(file_path).name).resolve()
    if proc_target.exists():
        return FileResponse(str(proc_target))

    base_storage = settings.storage_dir.resolve()
    target = (base_storage / file_path).resolve()
    if target.exists() and str(target).startswith(str(base_storage)):
        return FileResponse(str(target))

    raise HTTPException(status_code=404, detail="File media tidak ditemukan")

# --- CONTROL API ENDPOINTS (START, PAUSE, RESUME, STOP, RESTART, EMERGENCY STOP) ---

@app.get("/api/control/state", response_class=JSONResponse)
async def get_control_state(_: bool = Depends(verify_dashboard_access)):
    """Returns synchronized system control status."""
    return control_bus.get_state()

@app.post("/api/control/{action}", response_class=JSONResponse)
async def execute_control_action(action: str, background_tasks: BackgroundTasks, _: bool = Depends(verify_dashboard_access)):
    """Executes control action and synchronizes with Worker & Telegram."""
    action = action.upper()
    valid_actions = ["START", "PAUSE", "RESUME", "STOP", "RESTART", "EMERGENCY_STOP"]
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"Invalid action. Choose from {valid_actions}")

    # Synchronize Telegram state
    if action in ["PAUSE", "EMERGENCY_STOP"]:
        telegram_c2.is_paused = True
    elif action in ["RESUME", "START"]:
        telegram_c2.is_paused = False
        telegram_c2.is_emergency_stopped = False

    if action == "EMERGENCY_STOP":
        telegram_c2.is_emergency_stopped = True

    # Update Control Bus State
    new_state = control_bus.send_command(action, source="dashboard")

    # If START or RESTART, trigger background worker if not active
    if action in ["START", "RESTART"]:
        lock_file = Path("storage/pita_media.lock")
        if action == "RESTART" or not lock_file.exists():
            script_path = Path("scripts/start_daemon.bat").resolve()
            if script_path.exists():
                try:
                    subprocess.Popen(
                        ["wscript.exe", str(Path("scripts/pita_daemon.vbs").resolve())],
                        cwd=str(Path(".").resolve()),
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                    )
                except Exception as e:
                    pass

    # If STOP, terminate worker process
    if action == "STOP":
        script_path = Path("scripts/stop_daemon.bat").resolve()
        if script_path.exists():
            try:
                subprocess.run([str(script_path)], cwd=str(Path(".").resolve()), capture_output=True)
            except Exception:
                pass
        control_bus.set_worker_stopped()

    return {"status": "SUCCESS", "action": action, "state": control_bus.get_state()}

# --- STUDIO TRIGGER API ---

@app.post("/api/studio/trigger", response_class=JSONResponse)
async def trigger_content_creation(request: Request, background_tasks: BackgroundTasks, _: bool = Depends(verify_dashboard_access)):
    data = await request.json()
    pilar = data.get("pilar", "pita_transformasi")

    valid_pillars = ["pita_transformasi", "pita_mini", "pita_cerita", "pita_kreasi"]
    if pilar not in valid_pillars:
        raise HTTPException(status_code=400, detail=f"Pilar '{pilar}' tidak valid. Pilihan: {valid_pillars}")

    async def _async_produce():
        job = await content_orchestrator.schedule_next_content_slot(pilar=pilar)
        await content_orchestrator.process_single_job(job.id)

    background_tasks.add_task(_async_produce)
    return {"status": "SUCCESS", "message": f"Job pembuatan konten #{pilar} berhasil di-enqueue dan sedang diproses!"}

# --- 11 VIEWS DATA APIS ---

@app.get("/api/stats", response_class=JSONResponse)
async def get_dashboard_api_stats(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        cost_metrics = await cost_governor.get_spend_metrics(session)

        # Job Counts
        stmt_jobs = select(Job.status, func.count(Job.id)).group_by(Job.status)
        res_jobs = await session.execute(stmt_jobs)
        job_stats = {row[0]: row[1] for row in res_jobs.all()}

        # Active Jobs
        stmt_active = select(Job).where(Job.status.in_(["PENDING", "IN_CREATOR", "IN_REVIEW", "IN_PUBLISH"])).order_by(desc(Job.created_at)).limit(5)
        res_active = await session.execute(stmt_active)
        active_jobs = [
            {"id": j.id, "pilar": j.pilar, "status": j.status, "created_at": j.created_at.strftime("%H:%M:%S")}
            for j in res_active.scalars().all()
        ]

        # Recent Publications
        stmt_pubs = (
            select(Publication, Content)
            .join(Content, Publication.content_id == Content.id)
            .where(Publication.publish_status.in_(["SUCCESS", "VERIFIED"]))
            .order_by(desc(Publication.published_at))
            .limit(6)
        )
        res_pubs = await session.execute(stmt_pubs)
        recent_pubs = []
        for pub, cont in res_pubs.all():
            mpaths = cont.media_paths or []
            preview_url = f"/api/media/{Path(mpaths[0]).name}" if mpaths else None
            recent_pubs.append({
                "id": pub.id,
                "title": cont.title,
                "pilar": cont.pilar,
                "platform": pub.platform,
                "post_url": pub.post_url,
                "preview_url": preview_url,
                "media_type": cont.media_type,
                "published_at": pub.published_at.strftime("%Y-%m-%d %H:%M UTC") if pub.published_at else "Baru Saja",
            })

        # Average QC Score
        stmt_qc = select(func.avg(QCRecord.total_score))
        res_qc = await session.execute(stmt_qc)
        avg_qc = res_qc.scalar_one() or 0.0

        # Pillar Strategy Weights
        pillar_weights = strategy_optimizer.current_weights

        # Subsystems Health
        health_monitor = CredentialHealthMonitor()
        health_summary = health_monitor.run_health_cycle()

    ctrl_state = control_bus.get_state()

    return {
        "status": ctrl_state.get("status", "RUNNING"),
        "control_state": ctrl_state,
        "is_paused": ctrl_state.get("is_paused", False),
        "is_emergency_stopped": ctrl_state.get("is_emergency_stopped", False),
        "cost_metrics": cost_metrics,
        "job_stats": job_stats,
        "active_jobs": active_jobs,
        "recent_publications": recent_pubs,
        "avg_qc_score": round(float(avg_qc), 2),
        "pillar_weights": pillar_weights,
        "health_summary": health_summary,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

@app.get("/api/jobs", response_class=JSONResponse)
async def get_jobs_list(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        stmt = select(Job).order_by(desc(Job.created_at)).limit(50)
        res = await session.execute(stmt)
        jobs = [
            {
                "id": j.id,
                "pilar": j.pilar,
                "status": j.status,
                "is_exploration": j.is_exploration,
                "error_message": j.error_message,
                "created_at": j.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "updated_at": j.updated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            }
            for j in res.scalars().all()
        ]
        return {"jobs": jobs, "total": len(jobs)}

@app.get("/api/queue", response_class=JSONResponse)
async def get_queue_breakdown(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        stmt = select(Job).where(Job.status.in_(["PENDING", "IN_CREATOR", "IN_REVIEW", "IN_PUBLISH", "WAITING"])).order_by(Job.created_at.asc())
        res = await session.execute(stmt)
        queue_items = [
            {
                "id": j.id,
                "pilar": j.pilar,
                "status": j.status,
                "is_exploration": j.is_exploration,
                "created_at": j.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            }
            for j in res.scalars().all()
        ]
        return {"queue": queue_items, "count": len(queue_items)}

@app.get("/api/content", response_class=JSONResponse)
async def get_content_history(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        stmt = select(Content).order_by(desc(Content.created_at)).limit(30)
        res = await session.execute(stmt)
        contents = []
        for c in res.scalars().all():
            mpaths = c.media_paths or []
            media_urls = [f"/api/media/{Path(p).name}" for p in mpaths]
            contents.append({
                "id": c.id,
                "job_id": c.job_id,
                "pilar": c.pilar,
                "title": c.title,
                "caption": c.caption,
                "media_type": c.media_type,
                "media_urls": media_urls,
                "created_at": c.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            })
        return {"contents": contents}

@app.get("/api/publications", response_class=JSONResponse)
async def get_publishing_history(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        stmt = select(Publication, Content).join(Content, Publication.content_id == Content.id).order_by(desc(Publication.published_at)).limit(30)
        res = await session.execute(stmt)
        pubs = []
        for p, c in res.all():
            mpaths = c.media_paths or []
            pubs.append({
                "id": p.id,
                "title": c.title,
                "pilar": c.pilar,
                "platform": p.platform,
                "post_url": p.post_url,
                "post_id": p.external_post_id,
                "status": p.publish_status,
                "media_type": c.media_type,
                "preview_url": f"/api/media/{Path(mpaths[0]).name}" if mpaths else None,
                "published_at": p.published_at.strftime("%Y-%m-%d %H:%M:%S UTC") if p.published_at else None
            })
        return {"publications": pubs}

@app.get("/api/qc", response_class=JSONResponse)
async def get_qc_analytics(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        stmt = select(QCRecord, Content).join(Content, QCRecord.content_id == Content.id).order_by(desc(QCRecord.created_at)).limit(25)
        res = await session.execute(stmt)
        records = []
        for q, c in res.all():
            records.append({
                "id": q.id,
                "content_title": c.title,
                "pilar": c.pilar,
                "iteration": q.iteration_number,
                "verdict": q.verdict,
                "total_score": q.total_score,
                "scores_breakdown": q.scores_breakdown,
                "feedback_text": q.feedback_text,
                "safety_passed": q.safety_gate_passed,
                "created_at": q.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            })
        return {"qc_records": records}

@app.get("/api/performance", response_class=JSONResponse)
async def get_performance_analytics(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        stmt = select(PerformanceMetric, Content).join(Content, PerformanceMetric.content_id == Content.id).order_by(desc(PerformanceMetric.captured_at)).limit(20)
        res = await session.execute(stmt)
        metrics = []
        for m, c in res.all():
            metrics.append({
                "content_title": c.title,
                "pilar": c.pilar,
                "views": m.views_count,
                "likes": m.likes_count,
                "shares": m.shares_count,
                "comments": m.comments_count,
                "roi_score": m.calculated_roi_score,
                "captured_at": m.captured_at.strftime("%Y-%m-%d %H:%M UTC")
            })
        return {"metrics": metrics}

@app.get("/api/costs", response_class=JSONResponse)
async def get_costs_breakdown(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        cost_metrics = await cost_governor.get_spend_metrics(session)
        stmt_records = select(CostRecord).order_by(desc(CostRecord.created_at)).limit(30)
        res_rec = await session.execute(stmt_records)
        records = [
            {
                "id": r.id,
                "service": r.service,
                "token_count": r.token_count,
                "cost_usd": r.estimated_cost_usd,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            }
            for r in res_rec.scalars().all()
        ]
        return {"metrics": cost_metrics, "records": records}

@app.get("/api/health", response_class=JSONResponse)
async def get_health_subsystems(_: bool = Depends(verify_dashboard_access)):
    results = run_startup_self_check()
    return results

@app.get("/api/logs", response_class=PlainTextResponse)
async def get_system_logs(lines: int = 150, _: bool = Depends(verify_dashboard_access)):
    log_file = Path("storage/logs/pita_media.log")
    if not log_file.exists():
        return "Log file storage/logs/pita_media.log belum terbentuk."
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            return "".join(all_lines[-lines:])
    except Exception as e:
        return f"Error reading logs: {e}"

@app.get("/api/settings", response_class=JSONResponse)
async def get_system_settings(_: bool = Depends(verify_dashboard_access)):
    """Returns safe masked system settings."""
    def mask(val: str) -> str:
        if not val or val.startswith("your_"):
            return "(Not configured)"
        return val[:4] + "••••••••" + val[-4:] if len(val) > 10 else "••••••••"

    return {
        "gemini": {
            "api_key": mask(settings.GEMINI_API_KEY),
            "text_model": settings.GEMINI_TEXT_MODEL,
            "pro_model": settings.GEMINI_PRO_MODEL,
            "image_model": settings.GEMINI_IMAGE_MODEL,
            "min_interval_seconds": settings.GEMINI_MIN_REQUEST_INTERVAL_SECONDS
        },
        "meta": {
            "page_id": settings.FB_PAGE_ID,
            "token": mask(settings.FB_PAGE_ACCESS_TOKEN),
            "api_version": settings.FB_API_VERSION
        },
        "telegram": {
            "token": mask(settings.TELEGRAM_BOT_TOKEN),
            "admin_ids": list(settings.admin_ids),
            "alert_chat": settings.TELEGRAM_ALERT_CHAT_ID
        },
        "thresholds": {
            "qc_pass": settings.QC_TOTAL_PASS_THRESHOLD,
            "daily_cost_limit": settings.DAILY_COST_LIMIT_USD,
            "monthly_cost_limit": settings.MONTHLY_COST_LIMIT_USD,
            "min_posting_interval_mins": settings.MIN_POSTING_INTERVAL_MINUTES
        }
    }

@app.post("/api/backup/create", response_class=JSONResponse)
async def create_backup_api(_: bool = Depends(verify_dashboard_access)):
    maint = StorageMaintenance()
    bk = maint.backup_database()
    return {"status": "SUCCESS", "backup_path": bk}

# --- MAIN DASHBOARD HTML INTERFACE (11 TABS) ---

@app.get("/", response_class=HTMLResponse)
async def render_dashboard_html(_: bool = Depends(verify_dashboard_access)):
    html_content = """<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pita Media — Command Center</title>
    <link rel="icon" type="image/png" href="/static/logo.png">
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-base: #0a0d14;
            --bg-card: rgba(18, 24, 38, 0.75);
            --bg-card-hover: rgba(26, 35, 56, 0.85);
            --border-glow: rgba(56, 189, 248, 0.2);
            --border-subtle: rgba(255, 255, 255, 0.08);
            --accent-cyan: #38bdf8;
            --accent-purple: #a855f7;
            --accent-amber: #f59e0b;
            --accent-emerald: #10b981;
            --accent-rose: #f43f5e;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Plus Jakarta Sans', sans-serif; }
        body { background-color: var(--bg-base); color: var(--text-main); min-height: 100vh; overflow-x: hidden; }

        /* Top Bar */
        .topbar {
            position: sticky; top: 0; z-index: 100;
            background: rgba(10, 13, 20, 0.85); backdrop-filter: blur(16px);
            border-bottom: 1px solid var(--border-subtle);
            padding: 12px 28px; display: flex; align-items: center; justify-content: space-between;
        }
        .brand { display: flex; align-items: center; gap: 14px; }
        .brand-logo { width: 38px; height: 38px; border-radius: 10px; object-fit: cover; box-shadow: 0 0 16px rgba(56, 189, 248, 0.3); }
        .brand-title { font-size: 1.25rem; font-weight: 800; letter-spacing: -0.5px; background: linear-gradient(135deg, #fff 0%, #38bdf8 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .brand-badge { font-size: 0.7rem; background: rgba(56, 189, 248, 0.15); color: var(--accent-cyan); border: 1px solid rgba(56, 189, 248, 0.3); padding: 2px 8px; border-radius: 20px; font-weight: 600; }

        /* Navigation Bar (11 Tabs) */
        .nav-bar {
            background: rgba(15, 20, 32, 0.95); border-bottom: 1px solid var(--border-subtle);
            padding: 6px 28px; display: flex; gap: 6px; overflow-x: auto; scrollbar-width: none;
        }
        .nav-tab {
            padding: 8px 14px; border-radius: 8px; font-size: 0.82rem; font-weight: 600; color: var(--text-muted);
            cursor: pointer; transition: all 0.2s; white-space: nowrap; border: 1px solid transparent; display: flex; align-items: center; gap: 6px;
        }
        .nav-tab:hover { color: var(--text-main); background: rgba(255, 255, 255, 0.05); }
        .nav-tab.active { color: #fff; background: rgba(56, 189, 248, 0.15); border-color: rgba(56, 189, 248, 0.4); }

        /* Main Container */
        .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
        .tab-content { display: none; }
        .tab-content.active { display: block; animation: fadeIn 0.25s ease-out; }

        @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }

        /* Control Action Bar */
        .control-panel {
            background: var(--bg-card); border: 1px solid var(--border-subtle);
            border-radius: 16px; padding: 18px 24px; margin-bottom: 24px;
            display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px;
        }
        .status-badge-box { display: flex; align-items: center; gap: 12px; }
        .status-pill {
            padding: 6px 14px; border-radius: 30px; font-size: 0.8rem; font-weight: 700;
            display: flex; align-items: center; gap: 8px; text-transform: uppercase; letter-spacing: 0.5px;
        }
        .status-running { background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); border: 1px solid rgba(16, 185, 129, 0.3); }
        .status-paused { background: rgba(245, 158, 11, 0.15); color: var(--accent-amber); border: 1px solid rgba(245, 158, 11, 0.3); }
        .status-stopped { background: rgba(244, 63, 94, 0.15); color: var(--accent-rose); border: 1px solid rgba(244, 63, 94, 0.3); }
        
        .btn-group { display: flex; gap: 8px; flex-wrap: wrap; }
        .btn {
            padding: 8px 16px; border-radius: 8px; font-size: 0.82rem; font-weight: 600; cursor: pointer;
            border: 1px solid var(--border-subtle); background: rgba(255, 255, 255, 0.06); color: var(--text-main);
            transition: all 0.2s; display: inline-flex; align-items: center; gap: 6px;
        }
        .btn:hover { background: rgba(255, 255, 255, 0.12); border-color: rgba(255, 255, 255, 0.2); }
        .btn-start { background: rgba(16, 185, 129, 0.2); border-color: rgba(16, 185, 129, 0.4); color: #34d399; }
        .btn-start:hover { background: rgba(16, 185, 129, 0.3); }
        .btn-pause { background: rgba(245, 158, 11, 0.2); border-color: rgba(245, 158, 11, 0.4); color: #fbbf24; }
        .btn-stop { background: rgba(244, 63, 94, 0.2); border-color: rgba(244, 63, 94, 0.4); color: #fb7185; }
        .btn-emergency { background: #e11d48; color: #fff; font-weight: 800; border-color: #f43f5e; box-shadow: 0 0 12px rgba(225, 29, 72, 0.4); }

        /* Metric Grid */
        .grid-4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .metric-card {
            background: var(--bg-card); border: 1px solid var(--border-subtle);
            border-radius: 14px; padding: 20px; transition: all 0.2s;
        }
        .metric-card:hover { transform: translateY(-2px); border-color: var(--border-glow); }
        .metric-label { font-size: 0.78rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; margin-bottom: 8px; }
        .metric-value { font-size: 1.8rem; font-weight: 800; letter-spacing: -0.5px; }

        /* Studio Triggers */
        .studio-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .studio-card {
            background: var(--bg-card); border: 1px solid var(--border-subtle);
            border-radius: 14px; padding: 20px; transition: all 0.2s; cursor: pointer;
        }
        .studio-card:hover { border-color: var(--accent-cyan); background: var(--bg-card-hover); }
        .studio-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
        .studio-badge { font-size: 0.7rem; font-weight: 700; padding: 3px 8px; border-radius: 20px; }

        /* Data Tables */
        .table-box {
            background: var(--bg-card); border: 1px solid var(--border-subtle);
            border-radius: 14px; overflow: hidden; margin-bottom: 24px;
        }
        .table-header { padding: 16px 20px; border-bottom: 1px solid var(--border-subtle); display: flex; align-items: center; justify-content: space-between; }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th { padding: 12px 20px; font-size: 0.75rem; font-weight: 700; color: var(--text-muted); text-transform: uppercase; border-bottom: 1px solid var(--border-subtle); background: rgba(0,0,0,0.15); }
        td { padding: 14px 20px; font-size: 0.85rem; border-bottom: 1px solid var(--border-subtle); }
        tr:hover td { background: rgba(255, 255, 255, 0.02); }

        /* Terminal Logs */
        .terminal {
            background: #05070a; border: 1px solid var(--border-subtle); border-radius: 14px;
            padding: 16px; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;
            color: #38bdf8; height: 600px; overflow-y: auto; line-height: 1.5; white-space: pre-wrap;
        }

        /* Gallery */
        .gallery-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }
        .gallery-card {
            background: var(--bg-card); border: 1px solid var(--border-subtle);
            border-radius: 14px; overflow: hidden; transition: all 0.2s;
        }
        .gallery-card:hover { transform: translateY(-3px); border-color: var(--border-glow); }
        .gallery-media { width: 100%; height: 200px; object-fit: cover; background: #05070a; }
        .gallery-body { padding: 16px; }
        .gallery-title { font-size: 0.95rem; font-weight: 700; margin-bottom: 8px; line-height: 1.4; }
        .gallery-caption { font-size: 0.8rem; color: var(--text-muted); line-height: 1.5; max-height: 80px; overflow: hidden; text-overflow: ellipsis; }

        /* Toast Notifications */
        #toast {
            position: fixed; bottom: 24px; right: 24px; z-index: 1000;
            background: rgba(18, 24, 38, 0.95); backdrop-filter: blur(12px);
            border: 1px solid var(--accent-cyan); color: #fff; padding: 14px 20px;
            border-radius: 10px; font-size: 0.85rem; font-weight: 600; box-shadow: 0 10px 25px rgba(0,0,0,0.5);
            display: none; animation: slideUp 0.3s ease-out;
        }
        @keyframes slideUp { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
    </style>
</head>
<body>

    <!-- Topbar -->
    <div class="topbar">
        <div class="brand">
            <img src="/static/logo.png" alt="Pita Media" class="brand-logo">
            <div>
                <span class="brand-title">PitaMedia.tv</span>
                <span class="brand-badge">Autonomous OS</span>
            </div>
        </div>
        <div style="display: flex; align-items: center; gap: 16px;">
            <div id="clock" style="font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; color: var(--text-muted);"></div>
            <a href="https://facebook.com/Pitamediaid" target="_blank" class="btn" style="font-size: 0.75rem;">
                🌐 Fanspage @Pitamediaid
            </a>
        </div>
    </div>

    <!-- 11 Subsystem Navigation Tabs -->
    <div class="nav-bar">
        <div class="nav-tab active" onclick="switchTab('dashboard')">📊 Dashboard</div>
        <div class="nav-tab" onclick="switchTab('jobs')">⚙️ Jobs</div>
        <div class="nav-tab" onclick="switchTab('queue')">⏳ Queue</div>
        <div class="nav-tab" onclick="switchTab('content')">🎨 Content History</div>
        <div class="nav-tab" onclick="switchTab('publishing')">🚀 Publishing</div>
        <div class="nav-tab" onclick="switchTab('qc')">🛡️ QC & Auto-Repair</div>
        <div class="nav-tab" onclick="switchTab('performance')">📈 Performance</div>
        <div class="nav-tab" onclick="switchTab('costs')">💰 Costs</div>
        <div class="nav-tab" onclick="switchTab('health')">🏥 System Health</div>
        <div class="nav-tab" onclick="switchTab('logs')">📜 Logs</div>
        <div class="nav-tab" onclick="switchTab('settings')">⚙️ Settings</div>
    </div>

    <!-- Main Content Container -->
    <div class="container">

        <!-- Global Interactive Control Bar -->
        <div class="control-panel">
            <div class="status-badge-box">
                <div id="status-pill" class="status-pill status-running">
                    <span id="status-dot">●</span>
                    <span id="status-text">RUNNING</span>
                </div>
                <div style="font-size: 0.8rem; color: var(--text-muted);">
                    Uptime: <span id="uptime-val" style="color: #fff; font-weight: 600;">--</span>
                </div>
            </div>
            <div class="btn-group">
                <button class="btn btn-start" onclick="sendControl('start')">▶ START</button>
                <button class="btn btn-pause" onclick="sendControl('pause')">⏸ PAUSE</button>
                <button class="btn" onclick="sendControl('resume')">▶ RESUME</button>
                <button class="btn btn-stop" onclick="sendControl('stop')">⏹ STOP</button>
                <button class="btn" onclick="sendControl('restart')">🔄 RESTART</button>
                <button class="btn btn-emergency" onclick="sendControl('emergency_stop')">🚨 EMERGENCY STOP</button>
            </div>
        </div>

        <!-- TAB 1: DASHBOARD OVERVIEW -->
        <div id="tab-dashboard" class="tab-content active">
            <!-- Metrics -->
            <div class="grid-4">
                <div class="metric-card">
                    <div class="metric-label">Published Posts</div>
                    <div class="metric-value" id="metric-published" style="color: var(--accent-emerald);">0</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Avg QC Score</div>
                    <div class="metric-value" id="metric-qc" style="color: var(--accent-cyan);">0.0/10</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Daily Spend</div>
                    <div class="metric-value" id="metric-cost" style="color: var(--accent-amber);">$0.00</div>
                </div>
                <div class="metric-card">
                    <div class="metric-label">Queue Pending</div>
                    <div class="metric-value" id="metric-queue" style="color: var(--accent-purple);">0</div>
                </div>
            </div>

            <!-- Content Studio Trigger Cards -->
            <h3 style="font-size: 1rem; margin-bottom: 14px; font-weight: 700;">🎬 Interactive 4 Pillars Studio (Trigger Creation)</h3>
            <div class="studio-grid">
                <div class="studio-card" onclick="triggerStudio('pita_transformasi')">
                    <div class="studio-header">
                        <span style="font-weight: 700;">Pita Transformasi</span>
                        <span class="studio-badge" style="background: rgba(56, 189, 248, 0.2); color: var(--accent-cyan);">Timelapse Video</span>
                    </div>
                    <p style="font-size: 0.8rem; color: var(--text-muted);">Kreasi karya seni bertahap dari kanvas kosong hingga mahakarya.</p>
                </div>
                <div class="studio-card" onclick="triggerStudio('pita_mini')">
                    <div class="studio-header">
                        <span style="font-weight: 700;">Pita Mini</span>
                        <span class="studio-badge" style="background: rgba(168, 85, 247, 0.2); color: var(--accent-purple);">Micro-World</span>
                    </div>
                    <p style="font-size: 0.8rem; color: var(--text-muted);">Eksplorasi miniatur diorama dan detail presisi tinggi.</p>
                </div>
                <div class="studio-card" onclick="triggerStudio('pita_cerita')">
                    <div class="studio-header">
                        <span style="font-weight: 700;">Pita Cerita</span>
                        <span class="studio-badge" style="background: rgba(245, 158, 11, 0.2); color: var(--accent-amber);">3-5 Carousel</span>
                    </div>
                    <p style="font-size: 0.8rem; color: var(--text-muted);">Visual statis artistik dengan 150-300 kata narasi mendalam.</p>
                </div>
                <div class="studio-card" onclick="triggerStudio('pita_kreasi')">
                    <div class="studio-header">
                        <span style="font-weight: 700;">Pita Kreasi</span>
                        <span class="studio-badge" style="background: rgba(16, 185, 129, 0.2); color: var(--accent-emerald);">Satisfying Craft</span>
                    </div>
                    <p style="font-size: 0.8rem; color: var(--text-muted);">Proses pembuatan objek estetis yang memuaskan dan menenangkan.</p>
                </div>
            </div>

            <!-- Recent Activity Table -->
            <div class="table-box">
                <div class="table-header">
                    <h3 style="font-size: 0.95rem; font-weight: 700;">🚀 Latest Publications (@Pitamediaid)</h3>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Preview</th>
                            <th>Judul Konten</th>
                            <th>Pilar</th>
                            <th>Platform</th>
                            <th>Waktu Terbit</th>
                            <th>Aksi</th>
                        </tr>
                    </thead>
                    <tbody id="recent-pubs-tbody">
                        <tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Memuat aktivitas...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- TAB 2: JOBS -->
        <div id="tab-jobs" class="tab-content">
            <div class="table-box">
                <div class="table-header">
                    <h3 style="font-size: 0.95rem; font-weight: 700;">⚙️ Job Processing History</h3>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Job ID</th>
                            <th>Pilar</th>
                            <th>Status</th>
                            <th>Eksplorasi</th>
                            <th>Waktu Dibuat</th>
                            <th>Keterangan</th>
                        </tr>
                    </thead>
                    <tbody id="jobs-tbody"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 3: QUEUE -->
        <div id="tab-queue" class="tab-content">
            <div class="table-box">
                <div class="table-header">
                    <h3 style="font-size: 0.95rem; font-weight: 700;">⏳ Active Queue Breakdown</h3>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Job ID</th>
                            <th>Pilar</th>
                            <th>Status Antrean</th>
                            <th>Waktu Enqueue</th>
                        </tr>
                    </thead>
                    <tbody id="queue-tbody"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 4: CONTENT HISTORY -->
        <div id="tab-content" class="tab-content">
            <h3 style="font-size: 1rem; margin-bottom: 16px; font-weight: 700;">🎨 Generated Content Gallery</h3>
            <div id="content-gallery" class="gallery-grid"></div>
        </div>

        <!-- TAB 5: PUBLISHING HISTORY -->
        <div id="tab-publishing" class="tab-content">
            <div class="table-box">
                <div class="table-header">
                    <h3 style="font-size: 0.95rem; font-weight: 700;">🚀 Meta Fanspage Publishing Log</h3>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Judul</th>
                            <th>Pilar</th>
                            <th>Status Meta</th>
                            <th>Post ID</th>
                            <th>Waktu Terbit</th>
                            <th>Tautan</th>
                        </tr>
                    </thead>
                    <tbody id="pubs-tbody"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 6: QC & AUTO-REPAIR -->
        <div id="tab-qc" class="tab-content">
            <div class="table-box">
                <div class="table-header">
                    <h3 style="font-size: 0.95rem; font-weight: 700;">🛡️ Quality Control & Auto-Repair History</h3>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Judul Konten</th>
                            <th>Pilar</th>
                            <th>Iterasi</th>
                            <th>Skor Total</th>
                            <th>Verdict</th>
                            <th>Feedback Evaluasi</th>
                        </tr>
                    </thead>
                    <tbody id="qc-tbody"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 7: PERFORMANCE -->
        <div id="tab-performance" class="tab-content">
            <div class="table-box">
                <div class="table-header">
                    <h3 style="font-size: 0.95rem; font-weight: 700;">📈 Content Engagement & Performance Metrics</h3>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Judul</th>
                            <th>Pilar</th>
                            <th>Views</th>
                            <th>Likes</th>
                            <th>Shares</th>
                            <th>Comments</th>
                            <th>ROI Score</th>
                        </tr>
                    </thead>
                    <tbody id="perf-tbody"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 8: COSTS -->
        <div id="tab-costs" class="tab-content">
            <div class="table-box">
                <div class="table-header">
                    <h3 style="font-size: 0.95rem; font-weight: 700;">💰 Token & API Spend Ledger</h3>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>ID Transaksi</th>
                            <th>Layanan API</th>
                            <th>Token Count</th>
                            <th>Estimasi Biaya ($)</th>
                            <th>Waktu</th>
                        </tr>
                    </thead>
                    <tbody id="costs-tbody"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 9: SYSTEM HEALTH -->
        <div id="tab-health" class="tab-content">
            <div class="table-box">
                <div class="table-header">
                    <h3 style="font-size: 0.95rem; font-weight: 700;">🏥 10 Subsystems Health Diagnostics</h3>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Status</th>
                            <th>Nama Subsistem</th>
                            <th>Diagnostik & Pesan</th>
                        </tr>
                    </thead>
                    <tbody id="health-tbody"></tbody>
                </table>
            </div>
        </div>

        <!-- TAB 10: LOGS -->
        <div id="tab-logs" class="tab-content">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="font-size: 0.95rem; font-weight: 700;">📜 Live Streaming Logs (storage/logs/pita_media.log)</h3>
                <button class="btn" onclick="fetchLogs()">🔄 Refresh Log</button>
            </div>
            <div id="terminal-logs" class="terminal">Memuat log...</div>
        </div>

        <!-- TAB 11: SETTINGS -->
        <div id="tab-settings" class="tab-content">
            <div class="table-box" style="padding: 24px;">
                <h3 style="font-size: 1rem; margin-bottom: 16px; font-weight: 700;">⚙️ System Configuration & Credentials (Masked)</h3>
                <div id="settings-view" style="font-size: 0.85rem; line-height: 1.8; color: var(--text-muted);">
                    Memuat konfigurasi...
                </div>
                <div style="margin-top: 24px;">
                    <button class="btn" onclick="createBackup()">💾 Buat Backup Database Sekarang</button>
                </div>
            </div>
        </div>

    </div>

    <!-- Toast Component -->
    <div id="toast"></div>

    <script>
        function showToast(msg) {
            const t = document.getElementById('toast');
            t.innerText = msg;
            t.style.display = 'block';
            setTimeout(() => { t.style.display = 'none'; }, 3500);
        }

        function switchTab(tabId) {
            document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');
            document.getElementById('tab-' + tabId).classList.add('active');

            if (tabId === 'jobs') fetchJobs();
            if (tabId === 'queue') fetchQueue();
            if (tabId === 'content') fetchContent();
            if (tabId === 'publishing') fetchPublishing();
            if (tabId === 'qc') fetchQC();
            if (tabId === 'performance') fetchPerformance();
            if (tabId === 'costs') fetchCosts();
            if (tabId === 'health') fetchHealth();
            if (tabId === 'logs') fetchLogs();
            if (tabId === 'settings') fetchSettings();
        }

        async function sendControl(action) {
            try {
                const res = await fetch('/api/control/' + action, { method: 'POST' });
                const data = await res.json();
                showToast('Perintah ' + action.toUpperCase() + ' berhasil dijalankan!');
                pollStats();
            } catch (e) {
                showToast('Gagal mengirim kontrol: ' + e);
            }
        }

        async function triggerStudio(pilar) {
            try {
                const res = await fetch('/api/studio/trigger', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pilar: pilar })
                });
                const data = await res.json();
                showToast('🎬 ' + data.message);
                pollStats();
            } catch (e) {
                showToast('Gagal trigger studio: ' + e);
            }
        }

        async function pollStats() {
            try {
                const res = await fetch('/api/stats');
                const d = await res.json();

                // Status pill
                const pill = document.getElementById('status-pill');
                const text = document.getElementById('status-text');
                pill.className = 'status-pill status-' + (d.status.toLowerCase() === 'running' ? 'running' : (d.status.toLowerCase() === 'paused' ? 'paused' : 'stopped'));
                text.innerText = d.status;

                // Metrics
                document.getElementById('metric-published').innerText = d.recent_publications.length;
                document.getElementById('metric-qc').innerText = d.avg_qc_score + '/10';
                document.getElementById('metric-cost').innerText = '$' + d.cost_metrics.daily_spent.toFixed(2);
                document.getElementById('metric-queue').innerText = (d.job_stats['PENDING'] || 0);

                // Publications
                const pubBody = document.getElementById('recent-pubs-tbody');
                if (d.recent_publications.length === 0) {
                    pubBody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Belum ada publikasi.</td></tr>';
                } else {
                    pubBody.innerHTML = d.recent_publications.map(p => `
                        <tr>
                            <td><img src="${p.preview_url || '/static/logo.png'}" style="width: 44px; height: 44px; object-fit: cover; border-radius: 6px;"></td>
                            <td style="font-weight: 600;">${p.title}</td>
                            <td><span class="brand-badge">#${p.pilar}</span></td>
                            <td>${p.platform}</td>
                            <td style="color: var(--text-muted);">${p.published_at}</td>
                            <td><a href="${p.post_url}" target="_blank" class="btn" style="font-size: 0.7rem;">Buka Post</a></td>
                        </tr>
                    `).join('');
                }
            } catch (e) {
                console.warn('Poll error:', e);
            }
        }

        async function fetchJobs() {
            const res = await fetch('/api/jobs');
            const d = await res.json();
            document.getElementById('jobs-tbody').innerHTML = d.jobs.map(j => `
                <tr>
                    <td><code>${j.id.slice(0, 8)}</code></td>
                    <td>#${j.pilar}</td>
                    <td><b>${j.status}</b></td>
                    <td>${j.is_exploration ? 'Eksperimen (25%)' : 'Standar'}</td>
                    <td style="color: var(--text-muted);">${j.created_at}</td>
                    <td style="color: var(--accent-rose); font-size: 0.75rem;">${j.error_message || '-'}</td>
                </tr>
            `).join('');
        }

        async function fetchQueue() {
            const res = await fetch('/api/queue');
            const d = await res.json();
            document.getElementById('queue-tbody').innerHTML = d.queue.map(q => `
                <tr>
                    <td><code>${q.id.slice(0, 8)}</code></td>
                    <td>#${q.pilar}</td>
                    <td><span class="status-pill status-running" style="display:inline-flex;">${q.status}</span></td>
                    <td style="color: var(--text-muted);">${q.created_at}</td>
                </tr>
            `).join('');
        }

        async function fetchContent() {
            const res = await fetch('/api/content');
            const d = await res.json();
            document.getElementById('content-gallery').innerHTML = d.contents.map(c => `
                <div class="gallery-card">
                    <img src="${c.media_urls[0] || '/static/logo.png'}" class="gallery-media">
                    <div class="gallery-body">
                        <span class="brand-badge" style="margin-bottom: 8px; display: inline-block;">#${c.pilar}</span>
                        <div class="gallery-title">${c.title}</div>
                        <div class="gallery-caption">${c.caption}</div>
                    </div>
                </div>
            `).join('');
        }

        async function fetchPublishing() {
            const res = await fetch('/api/publications');
            const d = await res.json();
            document.getElementById('pubs-tbody').innerHTML = d.publications.map(p => `
                <tr>
                    <td style="font-weight: 600;">${p.title}</td>
                    <td>#${p.pilar}</td>
                    <td><span style="color: var(--accent-emerald); font-weight: 700;">${p.status}</span></td>
                    <td><code>${p.post_id || '-'}</code></td>
                    <td style="color: var(--text-muted);">${p.published_at}</td>
                    <td><a href="${p.post_url}" target="_blank" class="btn" style="font-size: 0.7rem;">Lihat di FB</a></td>
                </tr>
            `).join('');
        }

        async function fetchQC() {
            const res = await fetch('/api/qc');
            const d = await res.json();
            document.getElementById('qc-tbody').innerHTML = d.qc_records.map(q => `
                <tr>
                    <td style="font-weight: 600;">${q.content_title}</td>
                    <td>#${q.pilar}</td>
                    <td>Iterasi #${q.iteration}</td>
                    <td><b style="color: var(--accent-cyan);">${q.total_score}/10</b></td>
                    <td><b>${q.verdict}</b></td>
                    <td style="font-size: 0.8rem; color: var(--text-muted);">${q.feedback_text}</td>
                </tr>
            `).join('');
        }

        async function fetchPerformance() {
            const res = await fetch('/api/performance');
            const d = await res.json();
            document.getElementById('perf-tbody').innerHTML = d.metrics.map(m => `
                <tr>
                    <td style="font-weight: 600;">${m.content_title}</td>
                    <td>#${m.pilar}</td>
                    <td>${m.views}</td>
                    <td>${m.likes}</td>
                    <td>${m.shares}</td>
                    <td>${m.comments}</td>
                    <td><b style="color: var(--accent-emerald);">${m.roi_score}x</b></td>
                </tr>
            `).join('');
        }

        async function fetchCosts() {
            const res = await fetch('/api/costs');
            const d = await res.json();
            document.getElementById('costs-tbody').innerHTML = d.records.map(c => `
                <tr>
                    <td><code>${c.id}</code></td>
                    <td>${c.service}</td>
                    <td>${c.token_count}</td>
                    <td style="color: var(--accent-amber); font-weight: 700;">$${c.cost_usd.toFixed(4)}</td>
                    <td style="color: var(--text-muted);">${c.created_at}</td>
                </tr>
            `).join('');
        }

        async function fetchHealth() {
            const res = await fetch('/api/health');
            const d = await res.json();
            document.getElementById('health-tbody').innerHTML = d.items.map(h => `
                <tr>
                    <td><span class="status-pill status-${h.status === 'PASS' ? 'running' : 'paused'}" style="display:inline-flex;">${h.status}</span></td>
                    <td style="font-weight: 600;">${h.name}</td>
                    <td style="color: var(--text-muted);">${h.message}</td>
                </tr>
            `).join('');
        }

        async function fetchLogs() {
            const res = await fetch('/api/logs');
            const text = await res.text();
            const logBox = document.getElementById('terminal-logs');
            logBox.innerText = text;
            logBox.scrollTop = logBox.scrollHeight;
        }

        async function fetchSettings() {
            const res = await fetch('/api/settings');
            const d = await res.json();
            document.getElementById('settings-view').innerHTML = `
                <div style="background: rgba(0,0,0,0.3); padding: 16px; border-radius: 10px;">
                    <p><b>Gemini API Key:</b> <code>${d.gemini.api_key}</code></p>
                    <p><b>Gemini Text Model:</b> <code>${d.gemini.text_model}</code></p>
                    <p><b>Meta Page ID:</b> <code>${d.meta.page_id}</code> (@Pitamediaid)</p>
                    <p><b>Meta Token:</b> <code>${d.meta.token}</code></p>
                    <p><b>Telegram Bot Token:</b> <code>${d.telegram.token}</code> (@pitamediabot)</p>
                    <p><b>Daily Cost Cap:</b> $${d.thresholds.daily_cost_limit} USD</p>
                    <p><b>QC Passing Score:</b> ${d.thresholds.qc_pass} / 10</p>
                </div>
            `;
        }

        async function createBackup() {
            try {
                const res = await fetch('/api/backup/create', { method: 'POST' });
                const d = await res.json();
                showToast('Backup berhasil dibuat: ' + d.backup_path);
            } catch (e) {
                showToast('Gagal membuat backup: ' + e);
            }
        }

        // Clock & Initial Polling
        setInterval(() => {
            document.getElementById('clock').innerText = new Date().toUTCString().slice(17, 25) + ' UTC';
        }, 1000);

        pollStats();
        setInterval(pollStats, 4000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
