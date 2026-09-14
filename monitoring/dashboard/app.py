"""
Pita Media Autonomous Web Command Center (http://pitamedia.localhost)
Enterprise-grade control suite providing 24/7 monitoring, 18 navigation views,
dynamic credential management (DPAPI vault), multi-provider AI capability routing,
publishing receipts with verification, A/B experiments, storage health guard, and global APP_MODE controls.
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
from database.models import Job, Content, Publication, CostRecord, AuditLog, QCRecord, PerformanceMetric, PublishingReceipt, ABExperiment
from core.governors.cost_governor import cost_governor
from core.scheduler import content_orchestrator
from monitoring.telegram_bot import telegram_c2
from agents.strategist import strategy_optimizer
from core.runtime.control_bus import control_bus
from core.runtime.self_check import run_startup_self_check
from core.runtime.health_monitor import CredentialHealthMonitor
from core.runtime.maintenance import StorageMaintenance
from core.security.credential_manager import credential_manager
from core.security.token_health_manager import token_health_manager
from providers.base_provider import BaseAIProvider, AICapability
from providers.provider_registry import provider_registry, GenericCustomProvider
from providers.ai_router import ai_router
from core.audio.music_manager import music_manager
from core.intelligence.content_analytics import content_intelligence
from core.intelligence.ab_testing import ab_testing_engine
from core.runtime.storage_guard import storage_guard
from core.runtime.config_versioning import config_versioning
from core.runtime.reports import report_generator

app = FastAPI(title="Pita Media Command Center", docs_url=None, redoc_url=None)

# Mount static folder
static_dir = Path(__file__).resolve().parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

API_KEY_HEADER = APIKeyHeader(name="X-Pita-Secret", auto_error=False)

def verify_dashboard_access(key: str = Security(API_KEY_HEADER), request: Request = None):
    secret = settings.DASHBOARD_SECRET_KEY
    token_param = request.query_params.get("token") if request else None
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
    fname = Path(file_path).name
    # 1. Direct path in raw & processed
    raw_target = (settings.raw_media_dir / fname).resolve()
    if raw_target.exists() and raw_target.is_file():
        return FileResponse(str(raw_target))
    proc_target = (settings.processed_media_dir / fname).resolve()
    if proc_target.exists() and proc_target.is_file():
        return FileResponse(str(proc_target))
    # 2. Direct relative path under storage_dir
    base_storage = settings.storage_dir.resolve()
    target = (base_storage / file_path).resolve()
    if target.exists() and target.is_file() and str(target).startswith(str(base_storage)):
        return FileResponse(str(target))
    # 3. Recursive search across all storage subdirectories (e.g. storage/processed/cerita_xxx/)
    for found in settings.storage_dir.rglob(fname):
        if found.is_file():
            return FileResponse(str(found.resolve()))
    raise HTTPException(status_code=404, detail="File media tidak ditemukan")

# --- APP MODE API (DRY RUN vs PRODUCTION) ---
@app.get("/api/app_mode", response_class=JSONResponse)
async def get_app_mode():
    mode = os.environ.get("APP_MODE", getattr(settings, "APP_MODE", "DRY_RUN")).upper()
    return {"app_mode": mode, "is_production": mode == "PRODUCTION"}

@app.post("/api/app_mode/toggle", response_class=JSONResponse)
async def toggle_app_mode(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    target_mode = payload.get("mode", "DRY_RUN").upper()
    if target_mode not in ["DRY_RUN", "PRODUCTION"]:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be DRY_RUN or PRODUCTION.")
    os.environ["APP_MODE"] = target_mode
    config_versioning.record_snapshot("app_mode", {"APP_MODE": target_mode}, changed_by="DASHBOARD", reason="User toggled APP_MODE")
    return {"success": True, "app_mode": target_mode, "message": f"Global system mode changed to {target_mode}"}

# --- CONTROL API ENDPOINTS ---
@app.get("/api/control/state", response_class=JSONResponse)
async def get_control_state(_: bool = Depends(verify_dashboard_access)):
    return control_bus.get_state()

@app.post("/api/control/{action}", response_class=JSONResponse)
async def execute_control_action(action: str, background_tasks: BackgroundTasks, _: bool = Depends(verify_dashboard_access)):
    action = action.upper()
    valid_actions = ["START", "PAUSE", "RESUME", "STOP", "RESTART", "EMERGENCY_STOP"]
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"Invalid action. Choose from {valid_actions}")
    if action in ["PAUSE", "EMERGENCY_STOP"]:
        telegram_c2.is_paused = True
    elif action in ["RESUME", "START"]:
        telegram_c2.is_paused = False

    control_bus.send_command(action, source="DASHBOARD")
    return {"success": True, "action": action, "timestamp": datetime.now(timezone.utc).isoformat()}

# --- STUDIO TRIGGER API ---
@app.post("/api/studio/trigger", response_class=JSONResponse)
async def trigger_content_generation(payload: Dict[str, Any], background_tasks: BackgroundTasks, _: bool = Depends(verify_dashboard_access)):
    pilar = payload.get("pilar", "pita_cerita")
    from core.scheduler import content_orchestrator
    job = await content_orchestrator.schedule_next_content_slot(pilar=pilar)
    background_tasks.add_task(content_orchestrator.process_single_job, job.id)
    return {
        "success": True,
        "job_id": job.id,
        "pilar": pilar,
        "message": f"Kreasi #{pilar} berhasil dijadwalkan (Job {job.id[:8]}). Sistem AI sedang merakit naskah & visual..."
    }

# --- CORE STATS & METRICS ---
@app.get("/api/stats", response_class=JSONResponse)
async def get_dashboard_stats(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as db:
        pending_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == "PENDING"))).scalar() or 0
        running_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == "PROCESSING"))).scalar() or 0
        failed_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == "FAILED"))).scalar() or 0
        completed_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == "COMPLETED"))).scalar() or 0
        total_contents = (await db.execute(select(func.count(Content.id)))).scalar() or 0
        
        recent_pubs_res = await db.execute(
            select(Publication, Content.title, Content.pilar, Content.caption, Content.media_paths)
            .join(Content, Publication.content_id == Content.id)
            .order_by(desc(Publication.published_at))
            .limit(10)
        )
        recent_pubs = []
        for pub, title, pilar, caption, media_paths in recent_pubs_res.all():
            preview_url = f"/api/media/{Path(media_paths[0]).name}" if media_paths and len(media_paths) > 0 else ""
            is_sim = (pub.platform == "mock") or ("mock" in (pub.post_url or "")) or ("dry_run" in (pub.post_url or "")) or ("pita-media.mock" in (pub.post_url or ""))
            recent_pubs.append({
                "id": pub.id,
                "content_id": pub.content_id,
                "title": title or "Tanpa Judul",
                "pilar": pilar or "-",
                "platform": pub.platform,
                "post_url": pub.post_url,
                "status": pub.publish_status,
                "caption": caption or "",
                "verification_hash": pub.verification_hash or "-",
                "published_at": pub.published_at.strftime("%Y-%m-%d %H:%M") if pub.published_at else "-",
                "preview_url": preview_url,
                "is_simulated": is_sim
            })

        spend_metrics = await cost_governor.get_spend_metrics(db)

    providers_list = provider_registry.list_providers()
    active_providers_count = len(providers_list) if providers_list else 1

    app_mode = os.environ.get("APP_MODE", "DRY_RUN").upper()
    ctrl = control_bus.get_state()

    return {
        "status": ctrl.get("status", "STOPPED"),
        "app_mode": app_mode,
        "is_paused": ctrl.get("is_paused", False),
        "active_providers_count": active_providers_count,
        "job_stats": {
            "PENDING": pending_jobs,
            "PROCESSING": running_jobs,
            "FAILED": failed_jobs,
            "COMPLETED": completed_jobs
        },
        "total_contents": total_contents,
        "recent_publications": recent_pubs,
        "cost_metrics": spend_metrics
    }

# --- CREDENTIALS & PROVIDERS APIS ---
@app.get("/api/credentials", response_class=JSONResponse)
async def list_credentials(_: bool = Depends(verify_dashboard_access)):
    return {"credentials": credential_manager.list_all_credentials_masked()}

@app.post("/api/credentials/set", response_class=JSONResponse)
async def set_credential_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    service_name = payload.get("service_name")
    credentials_dict = payload.get("credentials")
    credential_key = payload.get("credential_key")
    secret_value = payload.get("secret_value")

    if not service_name:
        raise HTTPException(status_code=400, detail="service_name is required.")

    if not credentials_dict and credential_key and secret_value is not None:
        credentials_dict = {credential_key: str(secret_value)}

    if not credentials_dict:
        raise HTTPException(status_code=400, detail="credentials object or credential_key + secret_value required.")
    
    success = credential_manager.set_credential(service_name, credentials_dict, updated_by="DASHBOARD")
    test_res = credential_manager.test_connection(service_name)
    return {
        "success": success,
        "service_name": service_name,
        "message": "Kredensial berhasil diperbarui dan dienkripsi ke Windows DPAPI Vault.",
        "test_result": test_res
    }

@app.post("/api/credentials/test", response_class=JSONResponse)
async def test_credential_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    service_name = payload.get("service_name")
    custom_token = payload.get("custom_token")
    if not service_name:
        raise HTTPException(status_code=400, detail="service_name required.")
    res = credential_manager.test_connection(service_name, custom_token=custom_token)
    return res

@app.get("/api/env", response_class=JSONResponse)
async def get_env_endpoint(_: bool = Depends(verify_dashboard_access)):
    env_data = credential_manager.read_env_file()
    # Mask secrets for display unless explicit
    return {"env": env_data}

@app.post("/api/env/save", response_class=JSONResponse)
async def save_env_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    env_updates = payload.get("env") or payload
    if not isinstance(env_updates, dict):
        raise HTTPException(status_code=400, detail="JSON object with environment variables required.")
    
    # Filter out empty or non-string values
    cleaned = {str(k).strip(): str(v).strip() for k, v in env_updates.items() if str(k).strip()}
    success = credential_manager.update_env_file(cleaned)
    return {
        "success": success,
        "message": f"Berhasil menyimpan {len(cleaned)} konfigurasi langsung ke file .env!",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }

@app.get("/api/providers", response_class=JSONResponse)
async def list_providers(_: bool = Depends(verify_dashboard_access)):
    providers = provider_registry.list_providers()
    return {"providers": [p.to_dict() for p in providers]}

@app.post("/api/providers/add", response_class=JSONResponse)
async def add_provider_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    name = payload.get("name", "Custom Provider")
    base_url = payload.get("base_url", "https://api.openai.com/v1")
    api_key = payload.get("api_key", "")
    default_model = payload.get("default_model", "gpt-4o")
    capabilities = payload.get("capabilities", ["TEXT"])

    custom_prov = provider_registry.add_custom_provider(
        provider_name=name,
        base_url=base_url,
        api_key=api_key,
        default_model=default_model,
        capabilities=capabilities
    )
    return {"success": True, "provider": custom_prov.to_dict()}

# --- PUBLISHING RECEIPTS & PLATFORMS ---
@app.get("/api/receipts", response_class=JSONResponse)
async def list_receipts(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as db:
        res = await db.execute(select(PublishingReceipt).order_by(desc(PublishingReceipt.created_at)).limit(50))
        receipts = res.scalars().all()
        return {
            "receipts": [
                {
                    "id": r.id,
                    "content_id": r.content_id,
                    "platform": r.platform,
                    "post_id": r.post_id,
                    "permalink": r.permalink,
                    "status": r.status,
                    "app_mode": r.app_mode,
                    "verified": r.verified,
                    "metrics": r.metrics,
                    "error_message": r.error_message,
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""
                }
                for r in receipts
            ]
        }

# --- A/B EXPERIMENTS ---
@app.get("/api/experiments", response_class=JSONResponse)
async def list_experiments(_: bool = Depends(verify_dashboard_access)):
    return {"experiments": ab_testing_engine.list_experiments()}

@app.post("/api/experiments/{exp_id}/evaluate", response_class=JSONResponse)
async def evaluate_experiment_endpoint(exp_id: str, _: bool = Depends(verify_dashboard_access)):
    res = ab_testing_engine.evaluate_winner(exp_id)
    return res

# --- MUSIC CATALOG ---
@app.get("/api/music/tracks", response_class=JSONResponse)
async def list_music_tracks(mood: Optional[str] = None):
    return {"tracks": music_manager.list_tracks(mood=mood)}

# --- STORAGE & DISK GUARD ---
@app.get("/api/storage/disk", response_class=JSONResponse)
async def get_disk_guard_status():
    return storage_guard.check_disk_usage()

@app.post("/api/storage/cleanup", response_class=JSONResponse)
async def trigger_disk_cleanup(_: bool = Depends(verify_dashboard_access)):
    return storage_guard.cleanup_temporary_files()

# --- REPORTS ---
@app.get("/api/reports/daily", response_class=JSONResponse)
async def get_daily_report():
    return report_generator.generate_daily_report()

@app.get("/api/reports/weekly", response_class=JSONResponse)
async def get_weekly_report():
    return report_generator.generate_weekly_report()

# --- CONFIG VERSIONING ---
@app.get("/api/config/versions", response_class=JSONResponse)
async def list_config_versions(config_name: Optional[str] = None):
    return {"versions": config_versioning.list_versions(config_name=config_name)}

# --- CONTENT & JOBS & QC & LOGS ---
@app.get("/api/jobs", response_class=JSONResponse)
async def list_jobs(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as db:
        res = await db.execute(select(Job).order_by(desc(Job.created_at)).limit(30))
        jobs = res.scalars().all()
        return {"jobs": [
            {
                "id": j.id,
                "pilar": j.pilar,
                "status": j.status,
                "is_exploration": j.is_exploration,
                "created_at": j.created_at.strftime("%Y-%m-%d %H:%M:%S") if j.created_at else "",
                "error_message": j.error_message
            }
            for j in jobs
        ]}

@app.get("/api/content", response_class=JSONResponse)
async def list_content(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as db:
        res = await db.execute(select(Content).order_by(desc(Content.created_at)).limit(20))
        contents = res.scalars().all()
        return {"contents": [
            {
                "id": c.id,
                "title": c.title,
                "pilar": c.pilar,
                "caption": c.caption,
                "media_urls": [f"/api/media/{Path(m).name}" for m in (c.media_paths or [])]
            }
            for c in contents
        ]}

@app.get("/api/qc", response_class=JSONResponse)
async def list_qc(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as db:
        res = await db.execute(
            select(QCRecord, Content.title, Content.pilar)
            .outerjoin(Content, QCRecord.content_id == Content.id)
            .order_by(desc(QCRecord.created_at))
            .limit(20)
        )
        return {"qc_records": [
            {
                "id": q.id,
                "content_title": title or "N/A",
                "pilar": pilar or "N/A",
                "iteration": getattr(q, 'iteration_number', 1),
                "total_score": q.total_score,
                "verdict": q.verdict,
                "feedback_text": q.feedback_text
            }
            for q, title, pilar in res.all()
        ]}

@app.get("/api/logs", response_class=PlainTextResponse)
async def get_logs(_: bool = Depends(verify_dashboard_access)):
    log_file = Path("storage/logs/pita_media.log")
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                return "".join(lines[-150:])
        except Exception as e:
            return f"Error reading log file: {e}"
    return "Log file empty or not initialized."

# --- LEARNING INTELLIGENCE APIS ---
@app.get("/api/learning/overview", response_class=JSONResponse)
async def get_learning_overview(_: bool = Depends(verify_dashboard_access)):
    from core.learning.learning_engine import learning_engine
    return learning_engine.get_learning_dashboard_overview()

@app.post("/api/learning/autonomy", response_class=JSONResponse)
async def set_autonomy_level_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    from core.learning.autonomy_controller import autonomy_controller
    level = payload.get("level", "OBSERVE")
    reason = payload.get("reason", "Manual adjustment from Dashboard")
    res = autonomy_controller.set_autonomy_level(level, changed_by="DASHBOARD_ADMIN", reason=reason)
    return {"success": True, "data": res}

@app.post("/api/learning/strategy/rollback", response_class=JSONResponse)
async def rollback_strategy_endpoint(_: bool = Depends(verify_dashboard_access)):
    from core.learning.strategy_versioning import strategy_versioning
    res = strategy_versioning.rollback_to_last_proven_strategy()
    if res:
        return {"success": True, "message": f"Berhasil rollback ke Strategy v{res.get('version_num')}", "data": res}
    return {"success": False, "message": "Tidak ada strategi versi stabil sebelumnya untuk di-rollback"}

@app.post("/api/learning/pause", response_class=JSONResponse)
async def toggle_learning_pause(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    from core.learning.autonomy_controller import autonomy_controller
    pause = payload.get("pause", True)
    autonomy_controller.pause_learning(pause)
    return {"success": True, "is_paused": autonomy_controller.is_paused}

# --- MAIN DASHBOARD HTML WITH 18 VIEWS ---
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(_: bool = Depends(verify_dashboard_access)):
    app_mode = os.environ.get("APP_MODE", "DRY_RUN").upper()

    html_content = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pita Media Command Center</title>
    <link rel="icon" type="image/png" href="/static/logo.png">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #080C14;
            --bg-surface: #0E1422;
            --bg-card: rgba(19, 27, 44, 0.75);
            --bg-card-hover: rgba(28, 39, 62, 0.85);
            --border: rgba(255, 255, 255, 0.08);
            --border-glow: rgba(96, 165, 250, 0.35);
            --text-main: #F8FAFC;
            --text-muted: #94A3B8;
            --accent-blue: #3B82F6;
            --accent-indigo: #6366F1;
            --accent-purple: #8B5CF6;
            --accent-emerald: #10B981;
            --accent-amber: #F59E0B;
            --accent-rose: #EF4444;
            --accent-cyan: #06B6D4;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }}
        body {{ background: var(--bg-base); color: var(--text-main); display: flex; height: 100vh; overflow: hidden; background-image: radial-gradient(circle at 15% 15%, rgba(59, 130, 246, 0.04) 0%, transparent 40%), radial-gradient(circle at 85% 85%, rgba(139, 92, 246, 0.04) 0%, transparent 40%); }}
        
        /* Custom Scrollbar */
        ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
        ::-webkit-scrollbar-track {{ background: rgba(0,0,0,0.2); }}
        ::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.12); border-radius: 4px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: rgba(255,255,255,0.2); }}

        /* Sidebar */
        .sidebar {{ width: 268px; background: var(--bg-surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 10; }}
        .brand-header {{ padding: 22px 20px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid var(--border); }}
        .brand-logo {{ width: 40px; height: 40px; border-radius: 10px; box-shadow: 0 4px 16px rgba(59, 130, 246, 0.35); }}
        .brand-title {{ font-size: 1.2rem; font-weight: 800; font-family: 'Plus Jakarta Sans', sans-serif; background: linear-gradient(135deg, #60A5FA, #C084FC); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -0.02em; }}
        .brand-badge {{ font-size: 0.68rem; font-weight: 700; background: rgba(59, 130, 246, 0.15); color: #60A5FA; padding: 2px 8px; border-radius: 9999px; border: 1px solid rgba(59, 130, 246, 0.3); display: inline-block; }}
        
        .nav-list {{ list-style: none; overflow-y: auto; flex: 1; padding: 14px 10px; }}
        .nav-group-title {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.08em; color: #64748B; padding: 14px 12px 6px; font-weight: 700; }}
        .nav-item {{ display: flex; align-items: center; gap: 11px; padding: 10px 14px; margin-bottom: 3px; border-radius: 8px; font-size: 0.86rem; font-weight: 500; color: var(--text-muted); cursor: pointer; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); }}
        .nav-item:hover {{ background: rgba(255, 255, 255, 0.04); color: var(--text-main); transform: translateX(2px); }}
        .nav-item.active {{ background: linear-gradient(90deg, rgba(59, 130, 246, 0.16), rgba(59, 130, 246, 0.03)); color: #60A5FA; font-weight: 700; border-left: 3px solid var(--accent-blue); box-shadow: inset 0 0 12px rgba(59, 130, 246, 0.08); }}
        
        /* Main Workspace */
        .main-wrapper {{ flex: 1; display: flex; flex-direction: column; overflow: hidden; }}
        .topbar {{ height: 64px; background: rgba(14, 20, 34, 0.85); backdrop-filter: blur(16px); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 0 28px; z-index: 5; }}
        .mode-banner {{ display: flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 8px; font-size: 0.8rem; font-weight: 700; letter-spacing: 0.02em; transition: 0.2s; }}
        .mode-dry {{ background: rgba(245, 158, 11, 0.12); color: #FBBF24; border: 1px solid rgba(245, 158, 11, 0.35); box-shadow: 0 2px 8px rgba(245, 158, 11, 0.1); }}
        .mode-prod {{ background: rgba(16, 185, 129, 0.12); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.35); box-shadow: 0 2px 8px rgba(16, 185, 129, 0.1); }}
        
        .content-area {{ flex: 1; overflow-y: auto; padding: 28px; }}
        .tab-pane {{ display: none; }}
        .tab-pane.active {{ display: block; animation: fadeIn 0.25s cubic-bezier(0.4, 0, 0.2, 1); }}
        
        /* Dynamic Glassmorphism Cards */
        .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; margin-bottom: 24px; }}
        .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px; margin-bottom: 24px; }}
        .card {{ background: var(--bg-card); backdrop-filter: blur(16px); border: 1px solid var(--border); border-radius: 14px; padding: 22px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25); transition: all 0.25s ease; }}
        .card:hover {{ border-color: rgba(255, 255, 255, 0.12); }}
        
        /* Dynamic KPI Cards */
        .kpi-card {{ background: var(--bg-card); backdrop-filter: blur(16px); border: 1px solid var(--border); border-radius: 14px; padding: 20px 22px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2); transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1); position: relative; overflow: hidden; }}
        .kpi-card:hover {{ transform: translateY(-3px); box-shadow: 0 12px 28px rgba(0, 0, 0, 0.35); border-color: rgba(96, 165, 250, 0.25); }}
        .kpi-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
        .kpi-title {{ font-size: 0.83rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }}
        .kpi-icon-box {{ width: 38px; height: 38px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 1.15rem; }}
        .kpi-value {{ font-size: 2.1rem; font-weight: 800; color: var(--text-main); font-family: 'Plus Jakarta Sans', sans-serif; letter-spacing: -0.02em; line-height: 1.1; margin-bottom: 8px; }}
        .kpi-footer {{ font-size: 0.76rem; color: #64748B; display: flex; align-items: center; gap: 6px; }}

        /* Modern Buttons */
        .btn {{ padding: 8px 18px; border-radius: 8px; font-size: 0.85rem; font-weight: 600; cursor: pointer; border: none; transition: all 0.2s ease; display: inline-flex; align-items: center; gap: 6px; }}
        .btn-primary {{ background: linear-gradient(135deg, #3B82F6, #2563EB); color: white; box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35); }}
        .btn-primary:hover {{ background: linear-gradient(135deg, #60A5FA, #3B82F6); transform: translateY(-1px); box-shadow: 0 6px 18px rgba(37, 99, 235, 0.45); }}
        .btn-danger {{ background: linear-gradient(135deg, #EF4444, #DC2626); color: white; box-shadow: 0 4px 14px rgba(220, 38, 38, 0.3); }}
        .btn-danger:hover {{ background: linear-gradient(135deg, #F87171, #EF4444); }}
        .btn-outline {{ background: rgba(255, 255, 255, 0.03); border: 1px solid var(--border); color: var(--text-main); }}
        .btn-outline:hover {{ background: rgba(255, 255, 255, 0.08); border-color: rgba(255, 255, 255, 0.2); transform: translateY(-1px); }}
        
        /* Dynamic Tables & Badges */
        table {{ width: 100%; border-collapse: separate; border-spacing: 0; margin-top: 10px; font-size: 0.86rem; }}
        th {{ text-align: left; padding: 14px 16px; border-bottom: 1px solid var(--border); color: #64748B; font-weight: 700; text-transform: uppercase; font-size: 0.72rem; letter-spacing: 0.06em; }}
        td {{ padding: 14px 16px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); vertical-align: middle; }}
        .table-row-hover:hover td {{ background: rgba(255, 255, 255, 0.025); }}
        
        /* Media Thumbnail & Fallback Container */
        .media-thumb-container {{ width: 44px; height: 44px; position: relative; border-radius: 10px; overflow: hidden; }}
        .media-thumb-img {{ width: 100%; height: 100%; object-fit: cover; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1); }}
        .media-fallback-badge {{ width: 100%; height: 100%; border-radius: 10px; background: linear-gradient(135deg, rgba(59,130,246,0.15), rgba(139,92,246,0.15)); border: 1px solid rgba(139,92,246,0.25); display: flex; align-items: center; justify-content: center; font-size: 1.25rem; }}
        
        .pilar-pill {{ font-size: 0.75rem; font-weight: 700; padding: 4px 10px; border-radius: 9999px; background: rgba(99, 102, 241, 0.15); color: #818CF8; border: 1px solid rgba(99, 102, 241, 0.3); }}
        .platform-pill {{ font-size: 0.75rem; font-weight: 600; padding: 4px 10px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px; }}
        .status-indicator-badge {{ font-size: 0.75rem; font-weight: 700; padding: 4px 10px; border-radius: 9999px; display: inline-flex; align-items: center; gap: 6px; }}
        
        /* Pulse Animation */
        .pulse-dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; animation: pulseGlow 2s infinite ease-in-out; }}
        @keyframes pulseGlow {{
            0% {{ transform: scale(0.95); opacity: 0.7; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }}
            70% {{ transform: scale(1.1); opacity: 1; box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }}
            100% {{ transform: scale(0.95); opacity: 0.7; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }}
        }}

        /* Modals & Forms */
        .modal-overlay {{ position: fixed; inset: 0; background: rgba(0, 0, 0, 0.78); backdrop-filter: blur(8px); display: none; align-items: center; justify-content: center; z-index: 9999; }}
        .modal-box {{ background: var(--bg-surface); border: 1px solid var(--border); border-radius: 16px; width: 520px; max-width: 92vw; padding: 26px; box-shadow: 0 24px 54px rgba(0,0,0,0.75); animation: fadeIn 0.2s ease-out; }}
        .modal-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid var(--border); padding-bottom: 14px; }}
        .modal-title {{ font-size: 1.15rem; font-weight: 700; color: var(--text-main); font-family: 'Plus Jakarta Sans', sans-serif; }}
        .form-group {{ margin-bottom: 16px; }}
        .form-label {{ display: block; font-size: 0.8rem; font-weight: 600; color: var(--text-muted); margin-bottom: 6px; }}
        .form-control {{ width: 100%; padding: 10px 14px; background: rgba(11, 16, 25, 0.8); border: 1px solid var(--border); border-radius: 8px; color: var(--text-main); font-size: 0.88rem; outline: none; transition: 0.2s; }}
        .form-control:focus {{ border-color: var(--accent-blue); box-shadow: 0 0 0 3px rgba(59,130,246,0.25); }}
        .modal-actions {{ display: flex; justify-content: flex-end; gap: 10px; margin-top: 24px; border-top: 1px solid var(--border); padding-top: 18px; }}

        /* Toast */
        #toast {{ position: fixed; bottom: 24px; right: 24px; background: linear-gradient(135deg, #1E293B, #0F172A); border: 1px solid rgba(255,255,255,0.15); color: white; padding: 14px 22px; border-radius: 10px; display: none; box-shadow: 0 8px 30px rgba(0,0,0,0.55); z-index: 10000; font-weight: 600; font-size: 0.88rem; backdrop-filter: blur(12px); }}
        
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    </style>
</head>
<body>
    <div id="toast">Notifikasi</div>
    <div class="sidebar">
        <div class="brand-header">
            <img src="/static/logo.png" class="brand-logo" alt="Pita Media">
            <div>
                <div class="brand-title">Pita Media</div>
                <div class="brand-badge">Enterprise Master</div>
            </div>
        </div>
        <ul class="nav-list">
            <div class="nav-group-title">Command & Control</div>
            <li class="nav-item active" onclick="switchTab('overview')">📊 Overview</li>
            <li class="nav-item" onclick="switchTab('content')">🎬 Content Studio</li>
            <li class="nav-item" onclick="switchTab('queue')">⏳ Job Queue</li>
            <li class="nav-item" onclick="switchTab('receipts')">📜 Publishing Receipts</li>
            <li class="nav-item" onclick="switchTab('platforms')">🌐 Multi-Platform Hub</li>
            
            <div class="nav-group-title">AI & Infrastructure</div>
            <li class="nav-item" onclick="switchTab('providers')">⚡ AI Providers & Tiers</li>
            <li class="nav-item" onclick="switchTab('credentials')">🔐 Credentials & Vault</li>
            <li class="nav-item" onclick="switchTab('ab_testing')">🧪 A/B Experiments</li>
            <li class="nav-item" onclick="switchTab('music')">🎵 Music & Mood Engine</li>
            
            <div class="nav-group-title">Quality & Strategy</div>
            <li class="nav-item" onclick="switchTab('learning')">🧠 Learning Center</li>
            <li class="nav-item" onclick="switchTab('qc')">🛡️ QC & Quality Gate</li>
            <li class="nav-item" onclick="switchTab('strategy')">📈 Strategy & Heatmap</li>
            <li class="nav-item" onclick="switchTab('costs')">💰 Cost Governor</li>
            <li class="nav-item" onclick="switchTab('storage')">💾 Storage & Disk Guard</li>
            
            <div class="nav-group-title">System & Governance</div>
            <li class="nav-item" onclick="switchTab('health')">❤️ System Health</li>
            <li class="nav-item" onclick="switchTab('reports')">📑 Executive Reports</li>
            <li class="nav-item" onclick="switchTab('logs')">💻 Terminal Logs</li>
            <li class="nav-item" onclick="switchTab('settings')">⚙️ Settings & Versioning</li>
        </ul>
    </div>
    
    <div class="main-wrapper">
        <div class="topbar">
            <div style="display: flex; align-items: center; gap: 16px;">
                <div id="mode-badge" class="mode-banner {'mode-prod' if app_mode == 'PRODUCTION' else 'mode-dry'}">
                    {'🚀 PRODUCTION MODE (LIVE)' if app_mode == 'PRODUCTION' else '🛡️ DRY_RUN MODE (SIMULATED)'}
                </div>
                <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 12px;" onclick="toggleMode()">Switch Mode</button>
            </div>
            <div style="display: flex; align-items: center; gap: 12px;">
                <span id="system-status-pill" style="font-size: 0.82rem; font-weight: 700; color: #34D399; display:flex; align-items:center; gap:6px; background:rgba(16,185,129,0.1); padding:5px 12px; border-radius:9999px; border:1px solid rgba(16,185,129,0.25);">
                    <span class="pulse-dot" style="background:#10B981;"></span> RUNNING
                </span>
                <button class="btn btn-danger" style="font-size: 0.75rem;" onclick="sendControl('EMERGENCY_STOP')">Emergency Stop</button>
                <button class="btn btn-outline" style="font-size: 0.75rem;" onclick="sendControl('PAUSE')">Pause</button>
                <button class="btn btn-primary" style="font-size: 0.75rem;" onclick="sendControl('RESUME')">Resume</button>
            </div>
        </div>
        
        <div class="content-area">
            <!-- 1. OVERVIEW TAB -->
            <div id="tab-overview" class="tab-pane active">
                <div class="grid-4">
                    <div class="kpi-card" style="border-left: 4px solid #10B981;">
                        <div class="kpi-header">
                            <span class="kpi-title">Published Receipts</span>
                            <div class="kpi-icon-box" style="background: rgba(16,185,129,0.15); color: #10B981;">📜</div>
                        </div>
                        <div id="metric-published" class="kpi-value">0</div>
                        <div class="kpi-footer"><span style="color:#10B981; font-weight:700;">● Live Verified</span> &middot; Multi-Platform</div>
                    </div>

                    <div class="kpi-card" style="border-left: 4px solid #06B6D4;">
                        <div class="kpi-header">
                            <span class="kpi-title">Pending Jobs</span>
                            <div class="kpi-icon-box" style="background: rgba(6,182,212,0.15); color: #06B6D4;">⏳</div>
                        </div>
                        <div id="metric-pending" class="kpi-value">0</div>
                        <div class="kpi-footer"><span style="color:#06B6D4; font-weight:700;">● Pipeline Queue</span> &middot; Background Worker</div>
                    </div>

                    <div class="kpi-card" style="border-left: 4px solid #F59E0B;">
                        <div class="kpi-header">
                            <span class="kpi-title">Daily Cost Spent</span>
                            <div class="kpi-icon-box" style="background: rgba(245,158,11,0.15); color: #F59E0B;">💰</div>
                        </div>
                        <div id="metric-cost" class="kpi-value" style="color: #F59E0B;">$0.00</div>
                        <div class="kpi-footer"><span style="color:#F59E0B; font-weight:700;">● Cost Governor</span> &middot; Cap: $10.00/day</div>
                    </div>

                    <div class="kpi-card" style="border-left: 4px solid #8B5CF6;">
                        <div class="kpi-header">
                            <span class="kpi-title">Active AI Providers</span>
                            <div class="kpi-icon-box" style="background: rgba(139,92,246,0.15); color: #8B5CF6;">⚡</div>
                        </div>
                        <div id="metric-providers" class="kpi-value" style="color: #8B5CF6;">-</div>
                        <div class="kpi-footer"><span style="color:#8B5CF6; font-weight:700;">● Multi-Tier</span> &middot; Gemini & Grok</div>
                    </div>
                </div>
                
                <div class="card" style="margin-bottom: 24px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; border-bottom: 1px solid var(--border); padding-bottom: 12px;">
                        <div>
                            <div class="card-title" style="margin-bottom: 2px; font-size: 1.05rem; color: #F8FAFC;">📡 Latest Publishing Feed</div>
                            <p style="font-size: 0.78rem; color: var(--text-muted);">Aktivitas penerbitan konten secara realtime lintas Facebook, Instagram, dan Threads.</p>
                        </div>
                        <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 12px;" onclick="pollStats()">🔄 Refresh Feed</button>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th style="width: 50px;">Media</th>
                                <th>Judul Konten</th>
                                <th style="width: 140px;">Pilar</th>
                                <th style="width: 150px;">Platform</th>
                                <th style="width: 120px;">Status</th>
                                <th style="width: 100px;">Aksi</th>
                            </tr>
                        </thead>
                        <tbody id="overview-pubs-tbody">
                            <tr><td colspan="6" style="color: var(--text-muted); text-align: center; padding: 24px;">Memuat data terbaru...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- 2. CONTENT STUDIO TAB -->
            <div id="tab-content" class="tab-pane">
                <div class="card" style="margin-bottom: 20px;">
                    <div class="card-title">Manual Content Trigger (AI Multi-Agent Studio)</div>
                    <p style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">Picu pembuatan naskah, slide media, dan evaluasi Quality Control secara instan untuk pilar tertentu.</p>
                    <div style="display: flex; flex-wrap: wrap; gap: 12px; margin-top: 14px;">
                        <button class="btn btn-primary" onclick="triggerStudio('pita_waktu')">⏳ Generate Pita Waktu</button>
                        <button class="btn btn-outline" onclick="triggerStudio('pita_cerita')">📖 Generate Pita Cerita (Carousel)</button>
                        <button class="btn btn-outline" onclick="triggerStudio('pita_transformasi')">✨ Generate Pita Transformasi (Reels)</button>
                        <button class="btn btn-outline" onclick="triggerStudio('pita_refleksi')">🪞 Generate Pita Refleksi</button>
                    </div>
                </div>
                <div class="card">
                    <div class="card-title" style="display: flex; justify-content: space-between; align-items: center;">
                        <span>Produced Assets (Media & Naskah)</span>
                        <button class="btn btn-outline" style="font-size:0.75rem; padding:4px 10px;" onclick="fetchContent()">🔄 Refresh Assets</button>
                    </div>
                    <div id="content-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; margin-top: 16px;"></div>
                </div>
            </div>

            <!-- 3. QUEUE TAB -->
            <div id="tab-queue" class="tab-pane">
                <div class="card"><div class="card-title">Autonomous Execution Queue</div><table><thead><tr><th>Job ID</th><th>Pilar</th><th>Status</th><th>Type</th><th>Created</th><th>Error</th></tr></thead><tbody id="queue-tbody"></tbody></table></div>
            </div>

            <!-- 4. PUBLISHING RECEIPTS TAB -->
            <div id="tab-receipts" class="tab-pane">
                <div class="card"><div class="card-title">Audit Publishing Receipts (Transparansi Penuh)</div><table><thead><tr><th>Receipt ID</th><th>Content ID</th><th>Platform</th><th>Status</th><th>Mode</th><th>Verified</th><th>Post Link</th></tr></thead><tbody id="receipts-tbody"></tbody></table></div>
            </div>

            <!-- 5. PLATFORMS TAB -->
            <div id="tab-platforms" class="tab-pane">
                <div class="grid-3" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px;">
                    <div class="card"><div class="card-title">Facebook Fanspage</div><p style="font-size: 0.85rem; color: var(--text-muted);">Handle: <b>@Pitamediaid</b></p><p style="margin-top: 8px;">Status: <b style="color: var(--accent-emerald);">CONNECTED</b></p></div>
                    <div class="card"><div class="card-title">Instagram Business</div><p style="font-size: 0.85rem; color: var(--text-muted);">Reels & Carousels (4:5 / 9:16)</p><p style="margin-top: 8px;">Status: <b style="color: var(--accent-emerald);">CONNECTED</b></p></div>
                    <div class="card"><div class="card-title">Threads API</div><p style="font-size: 0.85rem; color: var(--text-muted);">Short-form Threads (&lt; 500 chars)</p><p style="margin-top: 8px;">Status: <b style="color: var(--accent-emerald);">CONNECTED</b></p></div>
                </div>
            </div>

            <!-- 6. AI PROVIDERS TAB -->
            <div id="tab-providers" class="tab-pane">
                <div class="card" style="margin-bottom: 20px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div class="card-title">Configured AI Providers & Multi-Tier Routing</div>
                        <button class="btn btn-primary" onclick="showAddProviderModal()">+ ADD PROVIDER</button>
                    </div>
                    <table><thead><tr><th>Provider ID</th><th>Name</th><th>Tier</th><th>Capabilities</th><th>Status</th></tr></thead><tbody id="providers-tbody"></tbody></table>
                </div>
            </div>

            <!-- 7. CREDENTIALS TAB (UNIFIED SINGLE PANEL) -->
            <div id="tab-credentials" class="tab-pane">
                <div class="card" style="margin-bottom: 24px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 14px;">
                        <div>
                            <div class="card-title" style="margin-bottom: 4px; font-size: 1.1rem; color: #60A5FA;">🔐 Manajemen Kredensial & API Key (Tersinkronisasi Otomatis)</div>
                            <p style="font-size: 0.82rem; color: var(--text-muted);">Satu tempat untuk mengatur seluruh API Key AI, Token Meta, dan Bot Telegram. Perubahan langsung tersimpan ke file <code>.env</code> dan dienkripsi ke Windows Vault tanpa perlu mengulang input.</p>
                        </div>
                        <div style="display: flex; gap: 10px;">
                            <button class="btn btn-outline" onclick="fetchEnvConfig(true);" style="font-size: 0.85rem;">🔄 Refresh Data</button>
                            <button class="btn btn-primary" onclick="saveEnvConfigDirect()" style="padding: 10px 24px; font-size: 0.88rem; font-weight: 700; background: linear-gradient(135deg, #10B981, #059669); box-shadow: 0 4px 14px rgba(16,185,129,0.3);">💾 Simpan Semua Kredensial (.env)</button>
                        </div>
                    </div>

                    <!-- 3 Logical Columns / Cards -->
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px;">
                        
                        <!-- Panel 1: AI Provider Keys -->
                        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 16px;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;">
                                <div style="font-weight: 700; font-size: 0.9rem; color: #60A5FA;">🤖 AI Providers</div>
                                <span class="brand-badge">Generation & Reasoning</span>
                            </div>

                            <!-- GEMINI_API_KEY -->
                            <div class="form-group">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                    <label class="form-label" style="margin-bottom: 0;">GEMINI_API_KEY (Utama) <span style="color:var(--accent-rose);">*</span></label>
                                    <span id="status-gemini" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Menunggu Uji</span>
                                </div>
                                <div style="position:relative; display: flex; gap: 6px;">
                                    <div style="position:relative; flex: 1;">
                                        <input type="password" id="env-gemini-key" class="form-control" placeholder="AIzaSy..." style="padding-right: 36px; font-size: 0.83rem;">
                                        <button type="button" onclick="toggleInputVisibility('env-gemini-key')" style="position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                    </div>
                                    <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 10px; white-space: nowrap;" onclick="testSingleCred('gemini', 'env-gemini-key', 'status-gemini')">🔍 Test</button>
                                </div>
                                <small style="font-size: 0.72rem; color: var(--text-muted);">Model: Gemini 2.5 Flash, Imagen 3, Veo</small>
                            </div>

                            <!-- GEMINI_API_KEY_2 (Cadangan / Auto-Failover) -->
                            <div class="form-group" style="margin-top: 14px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                    <label class="form-label" style="margin-bottom: 0;">GEMINI_API_KEY_2 (Cadangan / Failover)</label>
                                    <span id="status-gemini-2" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Belum Diatur</span>
                                </div>
                                <div style="position:relative; display: flex; gap: 6px;">
                                    <div style="position:relative; flex: 1;">
                                        <input type="password" id="env-gemini-key-2" class="form-control" placeholder="AIzaSy... (Key Cadangan)" style="padding-right: 36px; font-size: 0.83rem;">
                                        <button type="button" onclick="toggleInputVisibility('env-gemini-key-2')" style="position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                    </div>
                                    <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 10px; white-space: nowrap;" onclick="testSingleCred('gemini_2', 'env-gemini-key-2', 'status-gemini-2')">🔍 Test</button>
                                </div>
                                <small style="font-size: 0.72rem; color: var(--text-muted);">Otomatis aktif jika kuota harian Key Utama habis (429)</small>
                            </div>

                            <!-- XAI_API_KEY -->
                            <div class="form-group" style="margin-top: 14px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                    <label class="form-label" style="margin-bottom: 0;">XAI_API_KEY (Grok / Cadangan)</label>
                                    <span id="status-xai" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Menunggu Uji</span>
                                </div>
                                <div style="position:relative; display: flex; gap: 6px;">
                                    <div style="position:relative; flex: 1;">
                                        <input type="password" id="env-xai-key" class="form-control" placeholder="xai-..." style="padding-right: 36px; font-size: 0.83rem;">
                                        <button type="button" onclick="toggleInputVisibility('env-xai-key')" style="position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                    </div>
                                    <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 10px; white-space: nowrap;" onclick="testSingleCred('xai', 'env-xai-key', 'status-xai')">🔍 Test</button>
                                </div>
                                <small style="font-size: 0.72rem; color: var(--text-muted);">Secondary fallback jika kuota Gemini penuh</small>
                            </div>
                        </div>

                        <!-- Panel 2: Meta / Social Media Platforms -->
                        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 16px;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;">
                                <div style="font-weight: 700; font-size: 0.9rem; color: #10B981;">📱 Meta & Social Platforms</div>
                                <span class="brand-badge" style="border-color: rgba(16,185,129,0.3); color: #10B981;">Publishing</span>
                            </div>

                            <!-- FB_PAGE_ID -->
                            <div class="form-group">
                                <label class="form-label">FB_PAGE_ID (Fanspage ID) <span style="color:var(--accent-rose);">*</span></label>
                                <input type="text" id="env-fb-page-id" class="form-control" placeholder="1253340697871457" style="font-size: 0.83rem;">
                            </div>

                            <!-- FB_PAGE_ACCESS_TOKEN -->
                            <div class="form-group">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                    <label class="form-label" style="margin-bottom: 0;">FB_PAGE_ACCESS_TOKEN <span style="color:var(--accent-rose);">*</span></label>
                                    <span id="status-fb" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Menunggu Uji</span>
                                </div>
                                <div style="position:relative; display: flex; gap: 6px;">
                                    <div style="position:relative; flex: 1;">
                                        <input type="password" id="env-fb-token" class="form-control" placeholder="EAAXHI..." style="padding-right: 36px; font-size: 0.83rem;">
                                        <button type="button" onclick="toggleInputVisibility('env-fb-token')" style="position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                    </div>
                                    <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 10px; white-space: nowrap;" onclick="testSingleCred('facebook', 'env-fb-token', 'status-fb')">🔍 Test</button>
                                </div>
                                <small style="font-size: 0.72rem; color: var(--text-muted);">Fanspage @Pitamediaid</small>
                            </div>

                            <!-- IG_ACCESS_TOKEN -->
                            <div class="form-group">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                    <label class="form-label" style="margin-bottom: 0;">IG_ACCESS_TOKEN (Instagram)</label>
                                    <span id="status-ig" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Menunggu Uji</span>
                                </div>
                                <div style="position:relative; display: flex; gap: 6px;">
                                    <div style="position:relative; flex: 1;">
                                        <input type="password" id="env-ig-token" class="form-control" placeholder="IG token..." style="padding-right: 36px; font-size: 0.83rem;">
                                        <button type="button" onclick="toggleInputVisibility('env-ig-token')" style="position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                    </div>
                                    <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 10px; white-space: nowrap;" onclick="testSingleCred('instagram', 'env-ig-token', 'status-ig')">🔍 Test</button>
                                </div>
                            </div>

                            <!-- THREADS_ACCESS_TOKEN -->
                            <div class="form-group">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                    <label class="form-label" style="margin-bottom: 0;">THREADS_ACCESS_TOKEN (Threads)</label>
                                    <span id="status-threads" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Menunggu Uji</span>
                                </div>
                                <div style="position:relative; display: flex; gap: 6px;">
                                    <div style="position:relative; flex: 1;">
                                        <input type="password" id="env-threads-token" class="form-control" placeholder="Threads token..." style="padding-right: 36px; font-size: 0.83rem;">
                                        <button type="button" onclick="toggleInputVisibility('env-threads-token')" style="position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                    </div>
                                    <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 10px; white-space: nowrap;" onclick="testSingleCred('threads', 'env-threads-token', 'status-threads')">🔍 Test</button>
                                </div>
                            </div>
                        </div>

                        <!-- Panel 3: Telegram C2 Bot & Alerts -->
                        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 16px;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;">
                                <div style="font-weight: 700; font-size: 0.9rem; color: #F59E0B;">📡 Telegram C2 & Alerts</div>
                                <span class="brand-badge" style="border-color: rgba(245,158,11,0.3); color: #F59E0B;">Command Bot</span>
                            </div>

                            <!-- TELEGRAM_BOT_TOKEN -->
                            <div class="form-group">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                                    <label class="form-label" style="margin-bottom: 0;">TELEGRAM_BOT_TOKEN <span style="color:var(--accent-rose);">*</span></label>
                                    <span id="status-telegram" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Menunggu Uji</span>
                                </div>
                                <div style="position:relative; display: flex; gap: 6px;">
                                    <div style="position:relative; flex: 1;">
                                        <input type="password" id="env-telegram-token" class="form-control" placeholder="8059238085:AAFK..." style="padding-right: 36px; font-size: 0.83rem;">
                                        <button type="button" onclick="toggleInputVisibility('env-telegram-token')" style="position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                    </div>
                                    <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 10px; white-space: nowrap;" onclick="testSingleCred('telegram', 'env-telegram-token', 'status-telegram')">🔍 Test</button>
                                </div>
                                <small style="font-size: 0.72rem; color: var(--text-muted);">Bot @pitamediabot dari @BotFather</small>
                            </div>

                            <!-- TELEGRAM_ADMIN_IDS -->
                            <div class="form-group" style="margin-top: 14px;">
                                <label class="form-label">TELEGRAM_ADMIN_IDS (ID Admin Telegram Anda)</label>
                                <input type="text" id="env-telegram-admins" class="form-control" placeholder="308917129" style="font-size: 0.83rem;">
                                <small style="font-size: 0.72rem; color: var(--text-muted);">ID Telegram pemilik untuk otorisasi command</small>
                            </div>

                            <!-- TELEGRAM_ALERT_CHAT_ID -->
                            <div class="form-group" style="margin-top: 14px;">
                                <label class="form-label">TELEGRAM_ALERT_CHAT_ID (Chat ID Notifikasi)</label>
                                <input type="text" id="env-telegram-alert-chat" class="form-control" placeholder="308917129" style="font-size: 0.83rem;">
                                <small style="font-size: 0.72rem; color: var(--text-muted);">Tujuan pesan darurat, QC, dan laporan harian</small>
                            </div>
                        </div>

                    </div>

                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--border);">
                        <div style="font-size: 0.8rem; color: var(--text-muted);">
                            💡 <b>Tips:</b> Klik <b>🔍 Test</b> pada setiap baris untuk memverifikasi keaktifan token secara langsung sebelum menyimpan.
                        </div>
                        <button class="btn btn-primary" onclick="saveEnvConfigDirect()" style="padding: 11px 28px; font-size: 0.92rem; font-weight: 700; background: linear-gradient(135deg, #10B981, #059669); box-shadow: 0 4px 14px rgba(16,185,129,0.3);">💾 Simpan Semua Kredensial (.env)</button>
                    </div>
                </div>
            </div>

            <!-- 8. A/B EXPERIMENTS TAB -->
            <div id="tab-ab_testing" class="tab-pane">
                <div class="card"><div class="card-title">A/B Creative Experiments</div><table><thead><tr><th>Experiment ID</th><th>Type</th><th>Hypothesis</th><th>Status</th><th>Winner</th><th>Action</th></tr></thead><tbody id="experiments-tbody"></tbody></table></div>
            </div>

            <!-- 9. MUSIC & MOOD ENGINE TAB -->
            <div id="tab-music" class="tab-pane">
                <div class="card"><div class="card-title">Copyright-Safe Music Library (8 Emotional Moods)</div><table><thead><tr><th>Track ID</th><th>Title</th><th>Artist</th><th>Mood</th><th>BPM</th><th>License</th></tr></thead><tbody id="music-tbody"></tbody></table></div>
            </div>

            <!-- 10. QC TAB -->
            <div id="tab-qc" class="tab-pane">
                <div class="card"><div class="card-title">Quality Gate Audit Logs</div><table><thead><tr><th>Content</th><th>Pilar</th><th>Iteration</th><th>Score</th><th>Verdict</th><th>Feedback</th></tr></thead><tbody id="qc-tbody"></tbody></table></div>
            </div>

            <!-- 10b. LEARNING CENTER TAB -->
            <div id="tab-learning" class="tab-pane">
                <!-- Maturity & Autonomy Controller Header Grid -->
                <div class="grid-2">
                    <div class="card">
                        <div class="card-title">🧠 Learning Maturity Score</div>
                        <div style="display: flex; align-items: baseline; gap: 12px; margin: 10px 0;">
                            <span id="learn-maturity-score" style="font-size: 2.4rem; font-weight: 800; font-family: 'Plus Jakarta Sans'; color: #60A5FA;">--/100</span>
                            <span id="learn-maturity-stage" class="brand-badge" style="font-size: 0.8rem; padding: 4px 10px;">INSUFFICIENT_DATA</span>
                        </div>
                        <div id="learn-maturity-desc" style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: 14px;">Memuat data kematangan...</div>
                        
                        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; font-size: 0.78rem;">
                            <div>Valid Posts: <b id="learn-bk-posts" style="color:var(--accent-emerald);">-</b> / 20</div>
                            <div>Telemetry: <b id="learn-bk-telemetry" style="color:var(--accent-cyan);">-</b> / 20</div>
                            <div>Experiments: <b id="learn-bk-experiments" style="color:var(--accent-indigo);">-</b> / 15</div>
                            <div>Data Quality: <b id="learn-bk-quality" style="color:var(--accent-emerald);">-</b> / 15</div>
                            <div>Prediction Acc: <b id="learn-bk-pred" style="color:var(--accent-amber);">-</b> / 15</div>
                            <div>Stability: <b id="learn-bk-stability" style="color:var(--accent-blue);">-</b> / 15</div>
                        </div>
                    </div>

                    <div class="card">
                        <div class="card-title">🎮 Autonomy Controller & Governance</div>
                        <div style="display: flex; align-items: center; justify-content: space-between; margin: 10px 0;">
                            <div>
                                <div style="font-size: 0.75rem; color: var(--text-muted);">Current Autonomy Level:</div>
                                <span id="learn-autonomy-badge" style="font-size: 1.1rem; font-weight: 800; color: #10B981; letter-spacing: 0.05em;">OBSERVE</span>
                            </div>
                            <span id="learn-pause-badge" class="brand-badge" style="font-size: 0.75rem;">● LEARNING ACTIVE</span>
                        </div>
                        
                        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-top: 14px;">
                            <button class="btn btn-outline" style="font-size:0.75rem;" onclick="setAutonomyLevel('OBSERVE')">Level 1: OBSERVE</button>
                            <button class="btn btn-outline" style="font-size:0.75rem;" onclick="setAutonomyLevel('RECOMMEND')">Level 2: RECOMMEND</button>
                            <button class="btn btn-outline" style="font-size:0.75rem;" onclick="setAutonomyLevel('ASSISTED_AUTO')">Level 3: ASSISTED AUTO</button>
                            <button class="btn btn-outline" style="font-size:0.75rem;" onclick="setAutonomyLevel('CONTROLLED_AUTO')">Level 4: CONTROLLED AUTO</button>
                        </div>
                        
                        <div style="display: flex; gap: 8px; margin-top: 10px;">
                            <button class="btn btn-outline" style="font-size:0.75rem; flex:1;" onclick="toggleLearningPause()">⏸️ Pause / Resume</button>
                            <button class="btn btn-outline" style="font-size:0.75rem; flex:1; border-color:var(--accent-amber); color:var(--accent-amber);" onclick="rollbackStrategy()">⏪ Rollback Strategy</button>
                        </div>
                    </div>
                </div>

                <!-- Maturity Recommendation Alert Box (if present) -->
                <div id="learn-rec-box" class="card" style="display:none; margin-bottom: 20px; border-color: rgba(59, 130, 246, 0.5); background: rgba(59, 130, 246, 0.05);">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <div style="font-weight:700; color:#60A5FA; font-size:0.9rem;">💡 Rekomendasi Kenaikan Level Otonomi</div>
                            <div id="learn-rec-text" style="font-size:0.83rem; color:var(--text-main); margin-top:4px;">-</div>
                        </div>
                        <div style="display:flex; gap:8px;">
                            <button class="btn btn-primary" style="font-size:0.75rem;" onclick="approveRecommendation()">✅ Setujui</button>
                            <button class="btn btn-outline" style="font-size:0.75rem;" onclick="document.getElementById('learn-rec-box').style.display='none'">Abaikan</button>
                        </div>
                    </div>
                </div>

                <!-- Active Strategy Version & Pilar Distribution -->
                <div class="card" style="margin-bottom: 20px;">
                    <div class="card-title">📈 Strategi Aktif & Bobot Distribusi Pilar</div>
                    <div id="learn-strategy-name" style="font-weight:700; font-size:0.95rem; margin:6px 0;">Strategy v1 - Baseline Balanced Distribution</div>
                    <div id="learn-strategy-pilar-bars" style="display:grid; grid-template-columns:repeat(4, 1fr); gap:12px; margin-top:10px;">
                        <!-- Pillar Bars dynamically populated -->
                    </div>
                </div>

                <!-- Top Distilled Knowledge Items -->
                <div class="card" style="margin-bottom: 20px;">
                    <div class="card-title">📚 Knowledge Base — Sari Pengetahuan Terdistilasi</div>
                    <table>
                        <thead>
                            <tr><th>Kategori</th><th>Judul Pola</th><th>Insight & Aturan</th><th>Sample</th><th>Confidence</th><th>Status</th></tr>
                        </thead>
                        <tbody id="learn-knowledge-tbody">
                            <tr><td colspan="6" style="text-align:center; color:var(--text-muted);">Memuat Knowledge Base...</td></tr>
                        </tbody>
                    </table>
                </div>

                <!-- Recent Automatic Postmortems & Audience Ideas -->
                <div class="grid-2">
                    <div class="card">
                        <div class="card-title">🔍 Evaluasi Postmortem Otomatis</div>
                        <div id="learn-postmortems-list" style="margin-top:10px; font-size:0.82rem;">
                            Memuat postmortem...
                        </div>
                    </div>

                    <div class="card">
                        <div class="card-title">💬 Ide & Feedback Audiens Terkurasi</div>
                        <div id="learn-audience-list" style="margin-top:10px; font-size:0.82rem;">
                            Memuat ide audiens...
                        </div>
                    </div>
                </div>
            </div>

            <!-- 11. STRATEGY TAB -->
            <div id="tab-strategy" class="tab-pane">
                <div class="card"><div class="card-title">Optimal Publishing Heatmap (WIB / UTC+7)</div><div style="margin-top: 12px;"><p>⏰ <b>06:30 - 08:00 WIB:</b> Morning Commute / Mindset Hook</p><p>⏰ <b>12:00 - 13:00 WIB:</b> Lunch Break / Casual Storytelling</p><p>⏰ <b>19:00 - 21:30 WIB:</b> Prime Time Relaxation / Emotional Long-form</p></div></div>
            </div>

            <!-- 12. COSTS TAB -->
            <div id="tab-costs" class="tab-pane">
                <div class="card"><div class="card-title">Cost Governance & API Token Consumption</div><p>Daily Cap: <b>$10.00 USD</b></p><div id="costs-container" style="margin-top: 12px;"></div></div>
            </div>

            <!-- 13. STORAGE GUARD TAB -->
            <div id="tab-storage" class="tab-pane">
                <div class="card"><div class="card-title">Storage Guard & Disk Health</div><div id="disk-info" style="margin: 12px 0;">Loading...</div><button class="btn btn-outline" onclick="cleanDisk()">🧹 Run Temporary Storage Cleanup</button></div>
            </div>

            <!-- 14. HEALTH TAB -->
            <div id="tab-health" class="tab-pane">
                <div class="card"><div class="card-title">System Self-Check & Token Health</div><table><thead><tr><th>Component</th><th>Status</th><th>Message</th></tr></thead><tbody id="health-tbody"></tbody></table></div>
            </div>

            <!-- 15. REPORTS TAB -->
            <div id="tab-reports" class="tab-pane">
                <div class="card" style="margin-bottom: 20px;"><div class="card-title">Executive Reports</div><div style="display: flex; gap: 12px; margin-top: 12px;"><button class="btn btn-primary" onclick="loadReport('daily')">Generate Daily Digest</button><button class="btn btn-outline" onclick="loadReport('weekly')">Generate Weekly Executive Review</button></div></div><div class="card"><pre id="report-output" style="color: var(--text-muted); font-size: 0.85rem;">Pilih laporan di atas...</pre></div>
            </div>

            <!-- 16. LOGS TAB -->
            <div id="tab-logs" class="tab-pane">
                <div class="card"><div class="card-title">Live System Logs</div><pre id="terminal-logs" style="background: #000; color: #10B981; padding: 16px; border-radius: 8px; font-size: 0.8rem; height: 500px; overflow-y: auto;"></pre></div>
            </div>

            <!-- 17. SETTINGS TAB -->
            <div id="tab-settings" class="tab-pane">
                <div class="card"><div class="card-title">Config Snapshot History</div><table><thead><tr><th>Version ID</th><th>Config Name</th><th>Changed By</th><th>Reason</th><th>Timestamp</th></tr></thead><tbody id="versions-tbody"></tbody></table>                </div>
            </div>
        </div>
    </div>


    <!-- MODAL ADD PROVIDER -->
    <div id="modal-add-provider" class="modal-overlay">
        <div class="modal-box">
            <div class="modal-header">
                <div class="modal-title">⚡ Tambah AI Provider Baru</div>
                <button onclick="closeAddProviderModal()" style="background:none; border:none; color:var(--text-muted); font-size:1.3rem; cursor:pointer;">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">Nama Provider</label>
                <input type="text" id="prov-name" class="form-control" placeholder="e.g. DeepSeek AI / Anthropic Claude / Ollama">
            </div>
            <div class="form-group">
                <label class="form-label">Base URL (OpenAI Compatible Endpoint)</label>
                <input type="text" id="prov-url" class="form-control" placeholder="https://api.deepseek.com/v1" value="https://api.openai.com/v1">
            </div>
            <div class="form-group">
                <label class="form-label">API Key</label>
                <input type="password" id="prov-key" class="form-control" placeholder="sk-...">
            </div>
            <div class="form-group">
                <label class="form-label">Default Model</label>
                <input type="text" id="prov-model" class="form-control" placeholder="deepseek-chat" value="gpt-4o">
            </div>
            <div class="form-group">
                <label class="form-label">Prioritas Router Tier</label>
                <select id="prov-tier" class="form-control">
                    <option value="PRIMARY">PRIMARY (Utama)</option>
                    <option value="SECONDARY" selected>SECONDARY (Cadangan)</option>
                    <option value="FALLBACK">FALLBACK (Darurat)</option>
                </select>
            </div>
            <div class="modal-actions">
                <button class="btn btn-outline" onclick="closeAddProviderModal()">Batal</button>
                <button class="btn btn-primary" onclick="saveNewProvider()">💾 Daftarkan Provider</button>
            </div>
        </div>
    </div>

    <!-- MODAL POST PREVIEW (SIMULATED & VERIFIED) -->
    <div id="modal-post-preview" class="modal-overlay">
        <div class="modal-box" style="width: 620px; max-width: 95vw;">
            <div class="modal-header">
                <div class="modal-title" id="prev-modal-title">🔍 Pratinjau Konten Terverifikasi</div>
                <button onclick="closePostPreview()" style="background:none; border:none; color:var(--text-muted); font-size:1.4rem; cursor:pointer;">&times;</button>
            </div>
            
            <div id="prev-sim-alert" style="background: rgba(99, 102, 241, 0.12); border: 1px solid rgba(99, 102, 241, 0.3); border-radius: 10px; padding: 12px 14px; margin-bottom: 16px; font-size: 0.8rem; color: #a5b4fc; display: flex; gap: 10px; align-items: flex-start;">
                <span style="font-size: 1.1rem;">🛡️</span>
                <div>
                    <b>Mode Simulasi (DRY RUN):</b> Postingan ini telah selesai diproses oleh Creator & lulus Quality Control. Konten tersimpan lokal dan <u>belum ditayangkan ke Facebook publik</u> untuk menjaga keamanan akun.
                </div>
            </div>

            <div style="display:flex; gap:16px; margin-bottom:16px; align-items: flex-start;">
                <div id="prev-media-box" style="width: 140px; height: 140px; border-radius: 12px; background: rgba(255,255,255,0.04); border: 1px solid var(--border); overflow: hidden; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
                    <img id="prev-img" src="" style="width: 100%; height: 100%; object-fit: cover; display: none;" onerror="this.style.display='none'; document.getElementById('prev-fallback-icon').style.display='block';">
                    <div id="prev-fallback-icon" style="font-size: 2.2rem;">📘</div>
                </div>
                <div style="flex:1;">
                    <div id="prev-pilar-badge" style="margin-bottom: 6px;"></div>
                    <h3 id="prev-title" style="margin: 0 0 6px 0; font-size: 1.05rem; color: var(--text-main); font-weight: 700; line-height: 1.35;">Judul Konten</h3>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 4px;">Dipublikasikan: <span id="prev-date" style="color:var(--text-main);">-</span></div>
                    <div style="font-size: 0.75rem; color: var(--text-muted);">Status: <span id="prev-status" style="color:var(--accent-emerald); font-weight: 600;">🟢 VERIFIED</span></div>
                </div>
            </div>

            <div class="form-group">
                <label class="form-label" style="font-size: 0.78rem; font-weight: 700;">Naskah & Caption Lengkap:</label>
                <div id="prev-caption" style="background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; font-size: 0.82rem; line-height: 1.5; color: var(--text-main); max-height: 160px; overflow-y: auto; white-space: pre-wrap;">-</div>
            </div>

            <div style="background: rgba(255,255,255,0.02); border: 1px dashed var(--border); border-radius: 8px; padding: 10px 12px; font-size: 0.72rem; color: var(--text-muted); display:flex; justify-content: space-between; align-items: center;">
                <div>Verification Hash: <code id="prev-hash" style="color: var(--accent-blue); font-family: monospace;">-</code></div>
                <div id="prev-plat-name">Platform: Facebook</div>
            </div>

            <div class="modal-actions" style="margin-top: 18px; padding-top: 14px;">
                <button class="btn btn-outline" onclick="closePostPreview()">Tutup</button>
                <button class="btn btn-primary" id="prev-live-btn" onclick="toggleMode()">🚀 Beralih ke Mode PRODUCTION</button>
            </div>
        </div>
    </div>

    <script>
        function showToast(msg) {{
            const t = document.getElementById('toast');
            t.innerText = msg;
            t.style.display = 'block';
            setTimeout(() => t.style.display = 'none', 3000);
        }}

        function switchTab(tabId) {{
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');
            const target = document.getElementById('tab-' + tabId);
            if (target) target.classList.add('active');

            if (tabId === 'overview') pollStats();
            if (tabId === 'learning') fetchLearningData();
            if (tabId === 'content') fetchContent();
            if (tabId === 'queue') fetchQueue();
            if (tabId === 'receipts') fetchReceipts();
            if (tabId === 'providers') fetchProviders();
            if (tabId === 'credentials') fetchEnvConfig();
            if (tabId === 'ab_testing') fetchExperiments();
            if (tabId === 'music') fetchMusic();
            if (tabId === 'qc') fetchQC();
            if (tabId === 'storage') fetchStorage();
            if (tabId === 'health') fetchHealth();
            if (tabId === 'logs') fetchLogs();
            if (tabId === 'settings') fetchVersions();
        }}

        async function pollStats() {{
            try {{
                const res = await fetch('/api/stats');
                const d = await res.json();
                document.getElementById('metric-published').innerText = d.recent_publications ? d.recent_publications.length : 0;
                document.getElementById('metric-pending').innerText = (d.job_stats && d.job_stats.PENDING !== undefined) ? d.job_stats.PENDING : 0;
                document.getElementById('metric-cost').innerText = '$' + (d.cost_metrics ? d.cost_metrics.daily_spent.toFixed(2) : '0.00');
                document.getElementById('metric-providers').innerText = d.active_providers_count || 3;
                
                const getPilarIcon = (pilar) => {{
                    const pil = String(pilar).toLowerCase();
                    if (pil.includes('waktu')) return '⏳';
                    if (pil.includes('cerita')) return '📖';
                    if (pil.includes('transformasi')) return '✨';
                    if (pil.includes('refleksi')) return '🪞';
                    return '🎬';
                }};

                const getPlatformBadge = (plat) => {{
                    const p = String(plat).toLowerCase();
                    if (p.includes('facebook') || p.includes('fb')) return '<span class="platform-pill" style="background:rgba(59,130,246,0.15); color:#60A5FA; border:1px solid rgba(59,130,246,0.3);">📘 Facebook</span>';
                    if (p.includes('instagram') || p.includes('ig')) return '<span class="platform-pill" style="background:rgba(236,72,153,0.15); color:#F472B6; border:1px solid rgba(236,72,153,0.3);">📸 Instagram</span>';
                    if (p.includes('threads')) return '<span class="platform-pill" style="background:rgba(148,163,184,0.15); color:#E2E8F0; border:1px solid rgba(148,163,184,0.3);">🧵 Threads</span>';
                    return '<span class="platform-pill" style="background:rgba(6,182,212,0.15); color:#22D3EE; border:1px solid rgba(6,182,212,0.3);">🛡️ Simulated</span>';
                }};

                window.currentRecentPubs = d.recent_publications || [];

                if (!d.recent_publications || d.recent_publications.length === 0) {{
                    document.getElementById('overview-pubs-tbody').innerHTML = '<tr><td colspan="6" style="text-align:center; padding:32px; color:var(--text-muted);">Belum ada riwayat publikasi. Konten baru otomatis akan muncul di sini.</td></tr>';
                    return;
                }}

                document.getElementById('overview-pubs-tbody').innerHTML = d.recent_publications.map(p => {{
                    const icon = getPilarIcon(p.pilar || '');
                    const platBadge = getPlatformBadge(p.platform || '');
                    const isVerified = p.status === 'VERIFIED' || p.status === 'PUBLISHED' || p.status === 'SUCCESS';
                    const statusColor = isVerified ? '#34D399' : '#FBBF24';
                    const statusBg = isVerified ? 'rgba(16,185,129,0.12)' : 'rgba(245,158,11,0.12)';
                    const statusBorder = isVerified ? 'rgba(16,185,129,0.3)' : 'rgba(245,158,11,0.3)';
                    const isSim = p.is_simulated || (p.post_url && (p.post_url.includes('.mock') || p.post_url.includes('dry_run')));
                    
                    return `
                    <tr class="table-row-hover">
                        <td style="width: 50px;">
                            <div class="media-thumb-container">
                                ${{p.preview_url ? `<img src="${{p.preview_url}}" class="media-thumb-img" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">` : ''}}
                                <div class="media-fallback-badge" style="${{p.preview_url ? 'display:none;' : 'display:flex;'}}">
                                    ${{icon}}
                                </div>
                            </div>
                        </td>
                        <td>
                            <div style="font-weight: 700; color: var(--text-main); font-size: 0.88rem; line-height: 1.35;">${{p.title}}</div>
                            <div style="font-size: 0.73rem; color: var(--text-muted); margin-top: 3px;">Dipublikasikan: ${{p.published_at || 'Baru Saja'}}</div>
                        </td>
                        <td><span class="pilar-pill">#${{p.pilar || 'pita_waktu'}}</span></td>
                        <td>${{platBadge}}</td>
                        <td>
                            <span class="status-indicator-badge" style="color:${{statusColor}}; background:${{statusBg}}; border:1px solid ${{statusBorder}};">
                                <span class="pulse-dot" style="background:${{statusColor}};"></span> ${{p.status}}
                            </span>
                        </td>
                        <td>
                            ${{isSim ? 
                                `<button onclick="openPostPreview('${{p.id}}')" class="btn btn-outline" style="font-size:0.75rem; padding: 5px 12px; border-radius: 6px; border-color: rgba(99,102,241,0.4); color: #818cf8; cursor:pointer;">👁️ Pratinjau Post</button>` : 
                                (p.post_url && p.post_url !== '#' ? `<a href="${{p.post_url}}" target="_blank" class="btn btn-primary" style="font-size:0.75rem; padding: 5px 12px; border-radius: 6px;">↗ Buka Post</a>` : `<span style="color:var(--text-muted); font-size:0.75rem;">-</span>`)
                            }}
                        </td>
                    </tr>
                    `;
                }}).join('');
            }} catch (e) {{ console.error('Poll stats error:', e); }}
        }}

        window.currentRecentPubs = [];

        function openPostPreview(pubId) {{
            const p = window.currentRecentPubs.find(item => item.id === pubId);
            if (!p) return;

            document.getElementById('prev-title').innerText = p.title || 'Tanpa Judul';
            document.getElementById('prev-pilar-badge').innerHTML = `<span class="pilar-pill">#${{p.pilar || 'pita_waktu'}}</span>`;
            document.getElementById('prev-date').innerText = p.published_at || '-';
            document.getElementById('prev-status').innerText = '🟢 ' + (p.status || 'VERIFIED');
            document.getElementById('prev-caption').innerText = p.caption || '(Tidak ada caption tersimpan)';
            document.getElementById('prev-hash').innerText = (p.verification_hash || '-').slice(0, 24) + '...';
            document.getElementById('prev-plat-name').innerText = 'Platform: ' + (p.platform ? p.platform.toUpperCase() : 'FACEBOOK');

            const imgEl = document.getElementById('prev-img');
            const fallbackEl = document.getElementById('prev-fallback-icon');
            if (p.preview_url) {{
                imgEl.src = p.preview_url;
                imgEl.style.display = 'block';
                fallbackEl.style.display = 'none';
            }} else {{
                imgEl.style.display = 'none';
                fallbackEl.style.display = 'block';
                fallbackEl.innerText = p.platform === 'facebook' ? '📘' : (p.platform === 'instagram' ? '📸' : '🧵');
            }}

            const isSim = p.is_simulated || (p.post_url && (p.post_url.includes('.mock') || p.post_url.includes('dry_run')));
            document.getElementById('prev-sim-alert').style.display = isSim ? 'flex' : 'none';

            const liveBtn = document.getElementById('prev-live-btn');
            if (isSim) {{
                liveBtn.innerText = '🚀 Beralih ke Mode PRODUCTION';
                liveBtn.onclick = () => {{ closePostPreview(); toggleMode(); }};
            }} else if (p.post_url && p.post_url !== '#') {{
                liveBtn.innerText = '↗ Buka Post Facebook Asli';
                liveBtn.onclick = () => {{ window.open(p.post_url, '_blank'); }};
            }} else {{
                liveBtn.style.display = 'none';
            }}

            document.getElementById('modal-post-preview').style.display = 'flex';
        }}

        function closePostPreview() {{
            document.getElementById('modal-post-preview').style.display = 'none';
        }}

        async function toggleMode() {{
            const curMode = document.getElementById('mode-badge').innerText.includes('PRODUCTION') ? 'DRY_RUN' : 'PRODUCTION';
            const res = await fetch('/api/app_mode/toggle', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ mode: curMode }})
            }});
            const d = await res.json();
            showToast(d.message);
            setTimeout(() => location.reload(), 1000);
        }}

        async function sendControl(action) {{
            const res = await fetch('/api/control/' + action, {{ method: 'POST' }});
            const d = await res.json();
            showToast('Action ' + action + ' sent.');
        }}

        async function fetchReceipts() {{
            const res = await fetch('/api/receipts');
            const d = await res.json();
            document.getElementById('receipts-tbody').innerHTML = d.receipts.map(r => `
                <tr>
                    <td><code>${{r.id.slice(0,8)}}</code></td>
                    <td><code>${{r.content_id.slice(0,8)}}</code></td>
                    <td>${{r.platform}}</td>
                    <td><b style="color:var(--accent-emerald);">${{r.status}}</b></td>
                    <td><span class="brand-badge">${{r.app_mode}}</span></td>
                    <td>${{r.verified ? '✅ Yes' : '⏳ Pending'}}</td>
                    <td><a href="${{r.permalink}}" target="_blank" style="color:#60A5FA;">Link</a></td>
                </tr>
            `).join('') || '<tr><td colspan="7">No receipts.</td></tr>';
        }}

        async function fetchProviders() {{
            const res = await fetch('/api/providers');
            const d = await res.json();
            document.getElementById('metric-providers').innerText = d.providers.length;
            document.getElementById('providers-tbody').innerHTML = d.providers.map(p => `
                <tr>
                    <td><code>${{p.provider_id}}</code></td>
                    <td><b>${{p.display_name || p.name || p.provider_id.toUpperCase()}}</b></td>
                    <td><span class="brand-badge">${{p.tier || (p.priority === 1 ? 'PRIMARY (Tier 1)' : 'SECONDARY (Tier 2)')}}</span></td>
                    <td>${{(p.capabilities || []).join(', ')}}</td>
                    <td><span style="color:${{p.enabled ? 'var(--accent-emerald)' : 'var(--text-muted)'}};">● ${{p.enabled ? 'TERHUBUNG (ACTIVE)' : 'DISABLED'}}</span></td>
                </tr>
            `).join('');
        }}

        async function testSingleCred(serviceName, inputId, statusSpanId) {{
            const inputEl = document.getElementById(inputId);
            const statusEl = document.getElementById(statusSpanId);
            const tokenVal = inputEl ? inputEl.value.trim() : '';

            if (!tokenVal) {{
                if (statusEl) statusEl.innerHTML = '<span style="color:var(--accent-amber);">● Belum Diisi</span>';
                showToast('⚠️ Nilai token / key belum diisi.');
                return;
            }}

            if (statusEl) statusEl.innerHTML = '<span style="color:var(--accent-blue);">⏳ Menguji...</span>';
            showToast('Menguji koneksi ' + serviceName.toUpperCase() + '...');

            try {{
                const res = await fetch('/api/credentials/test', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        service_name: serviceName,
                        custom_token: tokenVal
                    }})
                }});
                const d = await res.json();
                if (d.status === 'VALID') {{
                    if (statusEl) statusEl.innerHTML = `<span style="color:var(--accent-emerald);">🟢 VALID (${{d.latency_ms || 0}}ms)</span>`;
                    showToast('🟢 ' + serviceName.toUpperCase() + ': ' + (d.message || 'Koneksi Valid'));
                }} else if (d.status === 'RATE_LIMITED') {{
                    if (statusEl) statusEl.innerHTML = `<span style="color:var(--accent-amber);">🟡 RATE LIMITED</span>`;
                    showToast('🟡 ' + serviceName.toUpperCase() + ': ' + (d.message || 'Limit'));
                }} else {{
                    if (statusEl) statusEl.innerHTML = `<span style="color:var(--accent-rose);">🔴 ${{d.status}}</span>`;
                    showToast('🔴 ' + serviceName.toUpperCase() + ' (' + d.status + '): ' + (d.message || 'Gagal'));
                }}
            }} catch (e) {{
                if (statusEl) statusEl.innerHTML = '<span style="color:var(--accent-rose);">🔴 Error</span>';
                showToast('Eror jaringan: ' + e.message);
            }}
        }}

        function showAddProviderModal() {{
            document.getElementById('modal-add-provider').style.display = 'flex';
        }}

        function closeAddProviderModal() {{
            document.getElementById('modal-add-provider').style.display = 'none';
        }}

        async function saveNewProvider() {{
            const name = document.getElementById('prov-name').value.trim();
            const baseUrl = document.getElementById('prov-url').value.trim();
            const apiKey = document.getElementById('prov-key').value.trim();
            const model = document.getElementById('prov-model').value.trim();
            const tier = document.getElementById('prov-tier').value;

            if (!name || !apiKey) {{
                showToast('Nama Provider dan API Key harus diisi.');
                return;
            }}

            showToast('Mendaftarkan provider baru...');
            try {{
                const res = await fetch('/api/providers/add', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        name: name,
                        base_url: baseUrl,
                        api_key: apiKey,
                        default_model: model,
                        tier: tier,
                        capabilities: ["TEXT", "REASONING"]
                    }})
                }});
                const d = await res.json();
                if (d.success) {{
                    showToast('✅ Provider ' + name + ' berhasil didaftarkan.');
                    closeAddProviderModal();
                    fetchProviders();
                }} else {{
                    showToast('Eror mendaftarkan provider.');
                }}
            }} catch (e) {{
                showToast('Eror: ' + e.message);
            }}
        }}

        function toggleInputVisibility(elementId) {{
            const el = document.getElementById(elementId);
            if (el) {{
                el.type = el.type === 'password' ? 'text' : 'password';
            }}
        }}

        async function fetchEnvConfig(showToastMsg = false) {{
            try {{
                const res = await fetch('/api/env');
                const d = await res.json();
                const env = d.env || {{}};
                if (document.getElementById('env-gemini-key')) document.getElementById('env-gemini-key').value = env.GEMINI_API_KEY || '';
                if (document.getElementById('env-gemini-key-2')) document.getElementById('env-gemini-key-2').value = env.GEMINI_API_KEY_2 || '';
                if (document.getElementById('env-xai-key')) document.getElementById('env-xai-key').value = env.XAI_API_KEY || '';
                if (document.getElementById('env-telegram-token')) document.getElementById('env-telegram-token').value = env.TELEGRAM_BOT_TOKEN || '';
                if (document.getElementById('env-telegram-admins')) document.getElementById('env-telegram-admins').value = env.TELEGRAM_ADMIN_IDS || '';
                if (document.getElementById('env-telegram-alert-chat')) document.getElementById('env-telegram-alert-chat').value = env.TELEGRAM_ALERT_CHAT_ID || env.TELEGRAM_ADMIN_IDS || '';
                if (document.getElementById('env-fb-page-id')) document.getElementById('env-fb-page-id').value = env.FB_PAGE_ID || '';
                if (document.getElementById('env-fb-token')) document.getElementById('env-fb-token').value = env.FB_PAGE_ACCESS_TOKEN || '';
                if (document.getElementById('env-ig-token')) document.getElementById('env-ig-token').value = env.IG_ACCESS_TOKEN || '';
                if (document.getElementById('env-threads-token')) document.getElementById('env-threads-token').value = env.THREADS_ACCESS_TOKEN || '';

                const updateBadge = (val, spanId) => {{
                    const el = document.getElementById(spanId);
                    if (!el) return;
                    if (val && val.trim() && !val.startsWith('mock_')) {{
                        el.innerHTML = '<span style="color:var(--accent-emerald);">● Terisi (Siap Diuji)</span>';
                    }} else {{
                        el.innerHTML = '<span style="color:var(--accent-amber);">● Belum Diatur</span>';
                    }}
                }};

                updateBadge(env.GEMINI_API_KEY, 'status-gemini');
                updateBadge(env.GEMINI_API_KEY_2, 'status-gemini-2');
                updateBadge(env.XAI_API_KEY, 'status-xai');
                updateBadge(env.FB_PAGE_ACCESS_TOKEN, 'status-fb');
                updateBadge(env.IG_ACCESS_TOKEN, 'status-ig');
                updateBadge(env.THREADS_ACCESS_TOKEN, 'status-threads');
                updateBadge(env.TELEGRAM_BOT_TOKEN, 'status-telegram');

                if (showToastMsg) {{
                    showToast('✅ Data kredensial dimuat dari file .env');
                }}
            }} catch (e) {{
                console.error('Failed to load .env config:', e);
                if (showToastMsg) showToast('Eror memuat data: ' + e.message);
            }}
        }}

        async function saveEnvConfigDirect() {{
            showToast('Menyimpan perubahan langsung ke file .env...');
            const updates = {{
                GEMINI_API_KEY: document.getElementById('env-gemini-key') ? document.getElementById('env-gemini-key').value.trim() : '',
                GEMINI_API_KEY_2: document.getElementById('env-gemini-key-2') ? document.getElementById('env-gemini-key-2').value.trim() : '',
                XAI_API_KEY: document.getElementById('env-xai-key') ? document.getElementById('env-xai-key').value.trim() : '',
                TELEGRAM_BOT_TOKEN: document.getElementById('env-telegram-token') ? document.getElementById('env-telegram-token').value.trim() : '',
                TELEGRAM_ADMIN_IDS: document.getElementById('env-telegram-admins') ? document.getElementById('env-telegram-admins').value.trim() : '',
                TELEGRAM_ALERT_CHAT_ID: document.getElementById('env-telegram-alert-chat') ? document.getElementById('env-telegram-alert-chat').value.trim() : '',
                FB_PAGE_ID: document.getElementById('env-fb-page-id') ? document.getElementById('env-fb-page-id').value.trim() : '',
                FB_PAGE_ACCESS_TOKEN: document.getElementById('env-fb-token') ? document.getElementById('env-fb-token').value.trim() : '',
                IG_ACCESS_TOKEN: document.getElementById('env-ig-token') ? document.getElementById('env-ig-token').value.trim() : '',
                THREADS_ACCESS_TOKEN: document.getElementById('env-threads-token') ? document.getElementById('env-threads-token').value.trim() : ''
            }};

            try {{
                const res = await fetch('/api/env/save', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(updates)
                }});
                const d = await res.json();
                if (d.success) {{
                    showToast('✅ ' + d.message);
                    fetchEnvConfig(false);
                }} else {{
                    showToast('Eror: ' + (d.detail || 'Gagal menyimpan .env'));
                }}
            }} catch (e) {{
                showToast('Eror jaringan: ' + e.message);
            }}
        }}

        async function fetchExperiments() {{
            const res = await fetch('/api/experiments');
            const d = await res.json();
            document.getElementById('experiments-tbody').innerHTML = d.experiments.map(e => `
                <tr>
                    <td><code>${{e.id.slice(0,8)}}</code></td>
                    <td>${{e.experiment_type}}</td>
                    <td>${{e.hypothesis}}</td>
                    <td>${{e.status}}</td>
                    <td><b>${{e.winner || 'PENDING'}}</b></td>
                    <td><button class="btn btn-outline" style="font-size:0.7rem;" onclick="evalExp('${{e.id}}')">Evaluate</button></td>
                </tr>
            `).join('') || '<tr><td colspan="6">No experiments yet.</td></tr>';
        }}

        async function evalExp(id) {{
            const res = await fetch('/api/experiments/' + id + '/evaluate', {{ method: 'POST' }});
            const d = await res.json();
            showToast('Winner: ' + d.winner);
            fetchExperiments();
        }}

        async function fetchMusic() {{
            const res = await fetch('/api/music/tracks');
            const d = await res.json();
            document.getElementById('music-tbody').innerHTML = d.tracks.map(t => `
                <tr>
                    <td><code>${{t.track_id}}</code></td>
                    <td><b>${{t.title}}</b></td>
                    <td>${{t.artist}}</td>
                    <td><span class="brand-badge">${{t.mood.toUpperCase()}}</span></td>
                    <td>${{t.bpm}}</td>
                    <td>${{t.license_type}}</td>
                </tr>
            `).join('');
        }}

        async function fetchStorage() {{
            const res = await fetch('/api/storage/disk');
            const d = await res.json();
            document.getElementById('disk-info').innerHTML = `
                <p>Status: <b>${{d.status}}</b></p>
                <p>Used: <b>${{d.used_gb}} GB / ${{d.total_gb}} GB (${{d.used_percent}}%)</b></p>
                <p>Free Space: <b>${{d.free_gb}} GB</b></p>
            `;
        }}

        async function cleanDisk() {{
            const res = await fetch('/api/storage/cleanup', {{ method: 'POST' }});
            const d = await res.json();
            showToast('Freed ' + d.freed_mb + ' MB.');
            fetchStorage();
        }}

        async function fetchHealth() {{
            const res = await fetch('/api/health');
            const d = await res.json();
            document.getElementById('health-tbody').innerHTML = d.items.map(h => `
                <tr>
                    <td><b>${{h.name}}</b></td>
                    <td><b style="color:${{h.status === 'PASS' ? 'var(--accent-emerald)' : 'var(--accent-amber)'}}">${{h.status}}</b></td>
                    <td>${{h.message}}</td>
                </tr>
            `).join('');
        }}

        async function fetchLogs() {{
            const res = await fetch('/api/logs');
            const text = await res.text();
            const el = document.getElementById('terminal-logs');
            el.innerText = text;
            el.scrollTop = el.scrollHeight;
        }}

        async function fetchVersions() {{
            const res = await fetch('/api/config/versions');
            const d = await res.json();
            document.getElementById('versions-tbody').innerHTML = d.versions.map(v => `
                <tr>
                    <td><code>${{v.version_id}}</code></td>
                    <td><b>${{v.config_name}}</b></td>
                    <td>${{v.changed_by}}</td>
                    <td>${{v.reason}}</td>
                    <td>${{v.iso_time}}</td>
                </tr>
            `).join('') || '<tr><td colspan="5">No snapshots recorded yet.</td></tr>';
        }}

        async function loadReport(type) {{
            const res = await fetch('/api/reports/' + type);
            const d = await res.json();
            document.getElementById('report-output').innerText = JSON.stringify(d, null, 2);
        }}

        async function fetchContent() {{
            try {{
                const res = await fetch('/api/content');
                const d = await res.json();
                const grid = document.getElementById('content-grid');
                if (!grid) return;
                
                if (!d.contents || d.contents.length === 0) {{
                    grid.innerHTML = '<div style="grid-column: 1/-1; text-align:center; padding: 36px; color: var(--text-muted);">Belum ada aset konten yang diproduksi. Tekan tombol pilar di atas untuk membuat konten sekarang.</div>';
                    return;
                }}

                grid.innerHTML = d.contents.map(c => {{
                    const previewUrl = (c.media_urls && c.media_urls.length > 0) ? c.media_urls[0] : '';
                    const pilarName = c.pilar || 'pita_cerita';
                    const icon = pilarName.includes('cerita') ? '📖' : (pilarName.includes('transformasi') ? '✨' : (pilarName.includes('refleksi') ? '🪞' : '⏳'));

                    return `
                    <div class="card" style="padding: 16px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid var(--border); border-radius: 12px; background: rgba(15, 23, 42, 0.65); transition: border-color 0.2s ease;">
                        <div>
                            <div style="width: 100%; height: 160px; border-radius: 10px; overflow: hidden; background: rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.06); display: flex; align-items: center; justify-content: center; position: relative; margin-bottom: 12px;">
                                ${{previewUrl ? `<img src="${{previewUrl}}" style="width: 100%; height: 100%; object-fit: cover;" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">` : ''}}
                                <div style="display: ${{previewUrl ? 'none' : 'flex'}}; flex-direction: column; align-items: center; justify-content: center; gap: 6px; color: var(--text-muted);">
                                    <span style="font-size: 2.2rem;">${{icon}}</span>
                                    <span style="font-size: 0.72rem; font-weight: 600;">#${{pilarName}}</span>
                                </div>
                                <span class="pilar-pill" style="position: absolute; top: 8px; left: 8px; background: rgba(15,23,42,0.85); backdrop-filter: blur(8px);">#${{pilarName}}</span>
                            </div>
                            <div style="font-weight: 700; font-size: 0.92rem; color: var(--text-main); line-height: 1.35; margin-bottom: 6px;">${{c.title}}</div>
                            <div style="color: var(--text-muted); font-size: 0.76rem; line-height: 1.45; max-height: 48px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;">${{c.caption || '-'}}</div>
                        </div>
                        <div style="margin-top: 14px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.72rem; color: var(--text-muted); font-family: monospace;"><code>${{c.id ? c.id.slice(0, 8) : ''}}</code></span>
                            <span style="font-size: 0.75rem; color: var(--accent-emerald); font-weight:600;">● READY</span>
                        </div>
                    </div>
                    `;
                }}).join('');
            }} catch (e) {{
                console.error('Fetch content error:', e);
            }}
        }}

        async function fetchQueue() {{
            const res = await fetch('/api/jobs');
            const d = await res.json();
            document.getElementById('queue-tbody').innerHTML = d.jobs.map(j => `
                <tr>
                    <td><code>${{j.id.slice(0,8)}}</code></td>
                    <td>${{j.pilar}}</td>
                    <td><b>${{j.status}}</b></td>
                    <td>${{j.is_exploration ? 'Exploration (25%)' : 'Standard'}}</td>
                    <td>${{j.created_at}}</td>
                    <td>${{j.error_message || '-'}}</td>
                </tr>
            `).join('');
        }}

        async function fetchQC() {{
            const res = await fetch('/api/qc');
            const d = await res.json();
            document.getElementById('qc-tbody').innerHTML = d.qc_records.map(q => `
                <tr>
                    <td><b>${{q.content_title}}</b></td>
                    <td>${{q.pilar}}</td>
                    <td>#${{q.iteration}}</td>
                    <td><b style="color:var(--accent-cyan);">${{q.total_score}}/10</b></td>
                    <td><b>${{q.verdict}}</b></td>
                    <td style="color:var(--text-muted); font-size:0.75rem;">${{q.feedback_text}}</td>
                </tr>
            `).join('');
        }}

        // --- LEARNING INTELLIGENCE CLIENT HANDLERS ---
        async function fetchLearningData() {{
            try {{
                const res = await fetch('/api/learning/overview');
                const d = await res.json();
                
                // 1. Maturity Score & Breakdown
                document.getElementById('learn-maturity-score').innerText = d.maturity_score + '/100';
                const stageEl = document.getElementById('learn-maturity-stage');
                stageEl.innerText = d.maturity_stage;
                if (d.maturity_score >= 60) stageEl.style.background = 'rgba(16, 185, 129, 0.2)';
                
                const bk = d.maturity_breakdown || {{}};
                document.getElementById('learn-bk-posts').innerText = (bk.valid_posts_score || 0);
                document.getElementById('learn-bk-telemetry').innerText = (bk.telemetry_coverage_score || 0);
                document.getElementById('learn-bk-experiments').innerText = (bk.experiments_score || 0);
                document.getElementById('learn-bk-quality').innerText = (bk.data_quality_score || 0);
                document.getElementById('learn-bk-pred').innerText = (bk.prediction_accuracy_score || 0);
                document.getElementById('learn-bk-stability').innerText = (bk.stability_score || 0);

                // 2. Autonomy Badge & Pause State
                const badge = document.getElementById('learn-autonomy-badge');
                badge.innerText = d.autonomy_level;
                if (d.autonomy_level === 'OBSERVE') badge.style.color = '#10B981';
                else if (d.autonomy_level === 'RECOMMEND') badge.style.color = '#60A5FA';
                else if (d.autonomy_level === 'ASSISTED_AUTO') badge.style.color = '#F59E0B';
                else if (d.autonomy_level === 'CONTROLLED_AUTO') badge.style.color = '#EF4444';

                const pBadge = document.getElementById('learn-pause-badge');
                if (d.is_paused) {{
                    pBadge.innerText = '⏸️ LEARNING PAUSED';
                    pBadge.style.background = 'rgba(245, 158, 11, 0.2)';
                    pBadge.style.color = 'var(--accent-amber)';
                }} else {{
                    pBadge.innerText = '● LEARNING ACTIVE';
                    pBadge.style.background = 'rgba(16, 185, 129, 0.2)';
                    pBadge.style.color = 'var(--accent-emerald)';
                }}

                // 3. Recommendation Box
                const recBox = document.getElementById('learn-rec-box');
                if (d.maturity_recommendation) {{
                    recBox.style.display = 'block';
                    document.getElementById('learn-rec-text').innerText = d.maturity_recommendation.message;
                    recBox.dataset.targetLevel = d.maturity_recommendation.recommended_level;
                }} else {{
                    recBox.style.display = 'none';
                }}

                // 4. Strategy & Pillar Distribution
                if (d.active_strategy) {{
                    document.getElementById('learn-strategy-name').innerText = d.active_strategy.name + ' (' + d.active_strategy.reason + ')';
                    const dist = d.active_strategy.pilar_distribution || {{}};
                    const pContainer = document.getElementById('learn-strategy-pilar-bars');
                    pContainer.innerHTML = Object.entries(dist).map(([p, w]) => `
                        <div style="background:var(--bg-base); padding:10px; border-radius:8px; border:1px solid var(--border);">
                            <div style="font-size:0.75rem; color:var(--text-muted); text-transform:uppercase;">${{p}}</div>
                            <div style="font-size:1.2rem; font-weight:800; color:#60A5FA; margin:4px 0;">${{Math.round(w*100)}}%</div>
                            <div style="height:4px; background:rgba(255,255,255,0.1); border-radius:2px; overflow:hidden;">
                                <div style="width:${{w*100}}%; height:100%; background:var(--accent-blue);"></div>
                            </div>
                        </div>
                    `).join('');
                }}

                // 5. Knowledge Base Table
                const kbTbody = document.getElementById('learn-knowledge-tbody');
                if (d.top_lessons_learned && d.top_lessons_learned.length > 0) {{
                    kbTbody.innerHTML = d.top_lessons_learned.map(k => `
                        <tr>
                            <td><span class="brand-badge">${{k.category.toUpperCase()}}</span></td>
                            <td><b>${{k.title}}</b></td>
                            <td style="font-size:0.8rem; color:var(--text-main);">${{k.insight_text}}</td>
                            <td>${{k.sample_size}} items</td>
                            <td><b style="color:var(--accent-emerald);">${{Math.round(k.confidence_score*100)}}%</b></td>
                            <td><span class="brand-badge" style="background:rgba(16, 185, 129, 0.15); color:var(--accent-emerald);">${{k.status}}</span></td>
                        </tr>
                    `).join('');
                }} else {{
                    kbTbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-muted);">Belum ada aturan Knowledge Base yang terdistilasi. Sistem sedang mengumpulkan data observasi.</td></tr>';
                }}

                // 6. Postmortems
                const pmList = document.getElementById('learn-postmortems-list');
                if (d.recent_postmortems && d.recent_postmortems.length > 0) {{
                    pmList.innerHTML = d.recent_postmortems.map(pm => `
                        <div style="background:var(--bg-base); padding:10px 14px; border-radius:6px; margin-bottom:8px; border-left:3px solid var(--accent-indigo);">
                            <div style="font-weight:700; color:var(--text-main);">${{pm.trigger_reason}} — #${{pm.pilar}}</div>
                            <div style="color:var(--text-muted); font-size:0.78rem; margin-top:2px;">${{pm.why_worked || pm.why_failed}}</div>
                            <div style="color:#10B981; font-size:0.75rem; margin-top:4px;">💡 <b>Pelajari:</b> ${{pm.what_to_repeat || pm.what_to_avoid}}</div>
                        </div>
                    `).join('');
                }} else {{
                    pmList.innerHTML = '<div style="color:var(--text-muted);">Belum ada konten anomali/viral yang memerlukan evaluasi postmortem.</div>';
                }}

                // 7. Community Ideas
                const audList = document.getElementById('learn-audience-list');
                if (d.community_ideas && d.community_ideas.length > 0) {{
                    audList.innerHTML = d.community_ideas.map(ci => `
                        <div style="background:var(--bg-base); padding:10px 14px; border-radius:6px; margin-bottom:8px; border-left:3px solid var(--accent-emerald);">
                            <div style="font-size:0.8rem; color:var(--text-main); font-weight:600;">${{ci.idea}}</div>
                            <div style="font-size:0.72rem; color:var(--text-muted); margin-top:3px;">Sumber: ${{ci.platform}} (${{ci.created_at}})</div>
                        </div>
                    `).join('');
                }} else {{
                    audList.innerHTML = '<div style="color:var(--text-muted);">Belum ada request ide yang terdistilasi dari komentar audiens.</div>';
                }}

            }} catch (e) {{ console.error('Error fetching learning overview:', e); }}
        }}

        async function setAutonomyLevel(level) {{
            const res = await fetch('/api/learning/autonomy', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ level: level, reason: 'Pengaturan manual melalui Dashboard Control Panel' }})
            }});
            const d = await res.json();
            if (d.success) {{
                showToast('Level Otonomi diubah ke: ' + level);
                fetchLearningData();
            }}
        }}

        async function rollbackStrategy() {{
            if (!confirm('Apakah Anda yakin ingin melakukan rollback ke strategi teruji sebelumnya?')) return;
            const res = await fetch('/api/learning/strategy/rollback', {{ method: 'POST' }});
            const d = await res.json();
            showToast(d.message);
            fetchLearningData();
        }}

        async function toggleLearningPause() {{
            const curBadge = document.getElementById('learn-pause-badge').innerText;
            const isPausedNow = curBadge.includes('PAUSED');
            const res = await fetch('/api/learning/pause', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ pause: !isPausedNow }})
            }});
            const d = await res.json();
            showToast(d.is_paused ? 'Learning Engine DIJEDA.' : 'Learning Engine DIAKTIFKAN.');
            fetchLearningData();
        }}

        async function approveRecommendation() {{
            const recBox = document.getElementById('learn-rec-box');
            const target = recBox.dataset.targetLevel;
            if (target) {{
                await setAutonomyLevel(target);
                recBox.style.display = 'none';
            }}
        }}

        async function triggerStudio(pilar) {{
            showToast('🚀 Menginstruksikan AI Creator untuk membuat konten #' + pilar + '...');
            try {{
                const res = await fetch('/api/studio/trigger', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ pilar: pilar }})
                }});
                const d = await res.json();
                if (d.success) {{
                    showToast('✅ ' + d.message);
                    setTimeout(() => {{
                        fetchContent();
                        fetchQueue();
                        pollStats();
                    }}, 2000);
                }} else {{
                    showToast('Eror: ' + (d.detail || 'Gagal memicu studio'));
                }}
            }} catch (e) {{
                showToast('Eror jaringan: ' + e.message);
            }}
        }}

        pollStats();
        setInterval(pollStats, 5000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
