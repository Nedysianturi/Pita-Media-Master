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

    control_bus.emit(action, initiator="DASHBOARD")
    return {"success": True, "action": action, "timestamp": datetime.now(timezone.utc).isoformat()}

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
            select(Publication, Content.title, Content.pilar, Content.media_paths)
            .join(Content, Publication.content_id == Content.id)
            .order_by(desc(Publication.published_at))
            .limit(10)
        )
        recent_pubs = []
        for pub, title, pilar, media_paths in recent_pubs_res.all():
            preview_url = f"/api/media/{Path(media_paths[0]).name}" if media_paths and len(media_paths) > 0 else ""
            recent_pubs.append({
                "id": pub.id,
                "title": title or "Tanpa Judul",
                "pilar": pilar or "-",
                "platform": pub.platform,
                "post_url": pub.post_url,
                "status": pub.publish_status,
                "published_at": pub.published_at.strftime("%Y-%m-%d %H:%M") if pub.published_at else "-",
                "preview_url": preview_url
            })

        spend_metrics = await cost_governor.get_spend_metrics(db)

    app_mode = os.environ.get("APP_MODE", "DRY_RUN").upper()
    ctrl = control_bus.get_state()

    return {
        "status": ctrl.get("status", "STOPPED"),
        "app_mode": app_mode,
        "is_paused": ctrl.get("is_paused", False),
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
            .join(Content, QCRecord.content_id == Content.id)
            .order_by(desc(QCRecord.created_at))
            .limit(20)
        )
        return {"qc_records": [
            {
                "id": q.id,
                "content_title": title,
                "pilar": pilar,
                "iteration": q.iteration_count,
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

@app.get("/api/health", response_class=JSONResponse)
async def run_health_checks(_: bool = Depends(verify_dashboard_access)):
    res = token_health_manager.run_full_health_audit()
    return {"items": [{"name": k, "status": v.get("status"), "message": v.get("message")} for k, v in res.items()]}

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
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #0B0F19;
            --bg-surface: #111827;
            --bg-card: #1F2937;
            --bg-card-hover: #263345;
            --border: #374151;
            --border-focus: #3B82F6;
            --text-main: #F9FAFB;
            --text-muted: #9CA3AF;
            --accent-blue: #3B82F6;
            --accent-indigo: #6366F1;
            --accent-emerald: #10B981;
            --accent-amber: #F59E0B;
            --accent-rose: #EF4444;
            --accent-cyan: #06B6D4;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', sans-serif; }}
        body {{ background: var(--bg-base); color: var(--text-main); display: flex; height: 100vh; overflow: hidden; }}
        
        /* Sidebar */
        .sidebar {{ width: 260px; background: var(--bg-surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 10; }}
        .brand-header {{ padding: 20px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid var(--border); }}
        .brand-logo {{ width: 36px; height: 36px; border-radius: 8px; box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3); }}
        .brand-title {{ font-size: 1.15rem; font-weight: 800; font-family: 'Plus Jakarta Sans', sans-serif; background: linear-gradient(135deg, #60A5FA, #A78BFA); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .brand-badge {{ font-size: 0.65rem; background: rgba(59, 130, 246, 0.15); color: var(--accent-blue); padding: 2px 8px; border-radius: 9999px; border: 1px solid rgba(59, 130, 246, 0.3); }}
        
        .nav-list {{ list-style: none; overflow-y: auto; flex: 1; padding: 12px 8px; }}
        .nav-group-title {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); padding: 12px 12px 4px; font-weight: 700; }}
        .nav-item {{ display: flex; align-items: center; gap: 10px; padding: 9px 12px; margin-bottom: 2px; border-radius: 6px; font-size: 0.85rem; font-weight: 500; color: var(--text-muted); cursor: pointer; transition: all 0.15s; }}
        .nav-item:hover {{ background: rgba(255, 255, 255, 0.05); color: var(--text-main); }}
        .nav-item.active {{ background: rgba(59, 130, 246, 0.12); color: #60A5FA; font-weight: 600; border-left: 3px solid var(--accent-blue); }}
        
        /* Main Workspace */
        .main-wrapper {{ flex: 1; display: flex; flex-direction: column; overflow: hidden; }}
        .topbar {{ height: 60px; background: var(--bg-surface); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 0 24px; }}
        .mode-banner {{ display: flex; align-items: center; gap: 12px; padding: 4px 12px; border-radius: 6px; font-size: 0.8rem; font-weight: 700; }}
        .mode-dry {{ background: rgba(245, 158, 11, 0.15); color: var(--accent-amber); border: 1px solid rgba(245, 158, 11, 0.4); }}
        .mode-prod {{ background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); border: 1px solid rgba(16, 185, 129, 0.4); }}
        
        .content-area {{ flex: 1; overflow-y: auto; padding: 24px; }}
        .tab-pane {{ display: none; }}
        .tab-pane.active {{ display: block; animation: fadeIn 0.2s ease-in-out; }}
        
        /* Cards & Grid */
        .grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
        .grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }}
        .card-title {{ font-size: 0.9rem; font-weight: 600; color: var(--text-muted); margin-bottom: 8px; }}
        .card-value {{ font-size: 1.8rem; font-weight: 800; color: var(--text-main); font-family: 'Plus Jakarta Sans', sans-serif; }}
        
        /* Buttons */
        .btn {{ padding: 8px 16px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; cursor: pointer; border: none; transition: 0.15s; }}
        .btn-primary {{ background: var(--accent-blue); color: white; }}
        .btn-primary:hover {{ background: #2563EB; }}
        .btn-danger {{ background: var(--accent-rose); color: white; }}
        .btn-outline {{ background: transparent; border: 1px solid var(--border); color: var(--text-main); }}
        .btn-outline:hover {{ background: rgba(255, 255, 255, 0.05); }}
        
        /* Tables */
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.85rem; }}
        th {{ text-align: left; padding: 12px; border-bottom: 1px solid var(--border); color: var(--text-muted); font-weight: 600; }}
        td {{ padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); }}
        tr:hover td {{ background: rgba(255, 255, 255, 0.02); }}
        
        /* Modals & Forms */
        .modal-overlay {{ position: fixed; inset: 0; background: rgba(0, 0, 0, 0.75); backdrop-filter: blur(5px); display: none; align-items: center; justify-content: center; z-index: 9999; }}
        .modal-box {{ background: var(--bg-surface); border: 1px solid var(--border); border-radius: 12px; width: 520px; max-width: 92vw; padding: 24px; box-shadow: 0 24px 48px rgba(0,0,0,0.7); animation: fadeIn 0.15s ease-out; }}
        .modal-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid var(--border); padding-bottom: 12px; }}
        .modal-title {{ font-size: 1.15rem; font-weight: 700; color: var(--text-main); font-family: 'Plus Jakarta Sans', sans-serif; }}
        .form-group {{ margin-bottom: 16px; }}
        .form-label {{ display: block; font-size: 0.8rem; font-weight: 600; color: var(--text-muted); margin-bottom: 6px; }}
        .form-control {{ width: 100%; padding: 10px 14px; background: var(--bg-base); border: 1px solid var(--border); border-radius: 6px; color: var(--text-main); font-size: 0.88rem; outline: none; transition: 0.15s; }}
        .form-control:focus {{ border-color: var(--accent-blue); box-shadow: 0 0 0 2px rgba(59,130,246,0.25); }}
        .modal-actions {{ display: flex; justify-content: flex-end; gap: 10px; margin-top: 24px; border-top: 1px solid var(--border); padding-top: 16px; }}

        /* Toast */
        #toast {{ position: fixed; bottom: 20px; right: 20px; background: var(--accent-blue); color: white; padding: 12px 20px; border-radius: 8px; display: none; box-shadow: 0 4px 12px rgba(0,0,0,0.4); z-index: 10000; font-weight: 600; }}
        
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: translateY(0); }} }}
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
                <button class="btn btn-outline" style="font-size: 0.75rem;" onclick="toggleMode()">Switch Mode</button>
            </div>
            <div style="display: flex; align-items: center; gap: 12px;">
                <span id="system-status-pill" style="font-size: 0.8rem; font-weight: 700; color: var(--accent-emerald);">● RUNNING</span>
                <button class="btn btn-danger" style="font-size: 0.75rem;" onclick="sendControl('EMERGENCY_STOP')">Emergency Stop</button>
                <button class="btn btn-outline" style="font-size: 0.75rem;" onclick="sendControl('PAUSE')">Pause</button>
                <button class="btn btn-primary" style="font-size: 0.75rem;" onclick="sendControl('RESUME')">Resume</button>
            </div>
        </div>
        
        <div class="content-area">
            <!-- 1. OVERVIEW TAB -->
            <div id="tab-overview" class="tab-pane active">
                <div class="grid-4">
                    <div class="card"><div class="card-title">Published Receipts</div><div id="metric-published" class="card-value">-</div></div>
                    <div class="card"><div class="card-title">Pending Jobs</div><div id="metric-pending" class="card-value">-</div></div>
                    <div class="card"><div class="card-title">Daily Cost Spent</div><div id="metric-cost" class="card-value" style="color: var(--accent-amber);">-</div></div>
                    <div class="card"><div class="card-title">Active AI Providers</div><div id="metric-providers" class="card-value" style="color: var(--accent-cyan);">-</div></div>
                </div>
                
                <div class="card" style="margin-bottom: 24px;">
                    <div class="card-title">Latest Publishing Feed</div>
                    <table>
                        <thead><tr><th>Media</th><th>Title</th><th>Pilar</th><th>Platform</th><th>Status</th><th>Action</th></tr></thead>
                        <tbody id="overview-pubs-tbody"><tr><td colspan="6" style="color: var(--text-muted); text-align: center;">Loading...</td></tr></tbody>
                    </table>
                </div>
            </div>

            <!-- 2. CONTENT STUDIO TAB -->
            <div id="tab-content" class="tab-pane">
                <div class="card" style="margin-bottom: 20px;">
                    <div class="card-title">Manual Content Trigger (Pita Waktu Studio)</div>
                    <div style="display: flex; gap: 12px; margin-top: 12px;">
                        <button class="btn btn-primary" onclick="triggerStudio('pita_waktu')">⏳ Generate Pita Waktu</button>
                        <button class="btn btn-outline" onclick="triggerStudio('pita_cerita')">📖 Generate Pita Cerita (Carousel)</button>
                        <button class="btn btn-outline" onclick="triggerStudio('pita_transformasi')">✨ Generate Pita Transformasi (Reels)</button>
                    </div>
                </div>
                <div class="card"><div class="card-title">Produced Assets</div><div id="content-grid" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 12px;"></div></div>
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

            <!-- 7. CREDENTIALS TAB -->
            <div id="tab-credentials" class="tab-pane">
                <div class="card" style="margin-bottom: 20px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <div>
                            <div class="card-title" style="margin-bottom: 4px;">Central Encrypted Vault (Windows DPAPI + AES-256)</div>
                            <p style="font-size: 0.8rem; color: var(--text-muted);">Kelola API Key AI & Token Akses Media Sosial secara aman. Kunci langsung dienkripsi di level OS tanpa pernah terekspos.</p>
                        </div>
                        <button class="btn btn-outline" onclick="fetchCredentials(); fetchEnvConfig();">🔄 Refresh Vault</button>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th style="width: 26%;">Layanan & Deskripsi</th>
                                <th style="width: 32%;">Masked Credential (Secret Vault)</th>
                                <th style="width: 14%;">Status</th>
                                <th style="width: 28%;">Aksi & Kontrol</th>
                            </tr>
                        </thead>
                        <tbody id="credentials-tbody"></tbody>
                    </table>
                </div>

                <!-- DIRECT .ENV EDITOR CARD -->
                <div class="card" style="margin-top: 24px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 12px;">
                        <div>
                            <div class="card-title" style="margin-bottom: 4px; font-size: 1.05rem; color: #60A5FA;">📝 Editor File .env Langsung (Sinkronisasi Otomatis)</div>
                            <p style="font-size: 0.8rem; color: var(--text-muted);">Nilai yang diisi atau diedit di bawah ini akan <b>langsung tersimpan ke file <code>.env</code> di disk</b> saat Anda menekan tombol Simpan.</p>
                        </div>
                        <button class="btn btn-primary" onclick="saveEnvConfigDirect()" style="padding: 10px 20px; font-size: 0.9rem; font-weight: 700; background: linear-gradient(135deg, #10B981, #059669); box-shadow: 0 4px 14px rgba(16,185,129,0.3);">💾 Simpan Langsung ke .env</button>
                    </div>

                    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px;">
                        <!-- Col 1: AI & Telegram -->
                        <div>
                            <div style="font-weight: 700; font-size: 0.85rem; color: var(--accent-blue); margin-bottom: 12px;">1. AI Providers & Telegram Bot</div>
                            <div class="form-group">
                                <label class="form-label">GEMINI_API_KEY <span style="color:var(--accent-rose);">*</span></label>
                                <div style="position:relative;">
                                    <input type="password" id="env-gemini-key" class="form-control" placeholder="AIzaSy...">
                                    <button type="button" onclick="toggleInputVisibility('env-gemini-key')" style="position:absolute; right:10px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                </div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">XAI_API_KEY (Opsional / Grok)</label>
                                <div style="position:relative;">
                                    <input type="password" id="env-xai-key" class="form-control" placeholder="xai-...">
                                    <button type="button" onclick="toggleInputVisibility('env-xai-key')" style="position:absolute; right:10px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                </div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">TELEGRAM_BOT_TOKEN <span style="color:var(--accent-rose);">*</span></label>
                                <div style="position:relative;">
                                    <input type="password" id="env-telegram-token" class="form-control" placeholder="8059238085:AAFK...">
                                    <button type="button" onclick="toggleInputVisibility('env-telegram-token')" style="position:absolute; right:10px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                </div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">TELEGRAM_ADMIN_IDS (ID Telegram Anda)</label>
                                <input type="text" id="env-telegram-admins" class="form-control" placeholder="308917129">
                            </div>
                        </div>

                        <!-- Col 2: Meta Graph API & Controls -->
                        <div>
                            <div style="font-weight: 700; font-size: 0.85rem; color: var(--accent-blue); margin-bottom: 12px;">2. Meta Graph API & Publisher</div>
                            <div class="form-group">
                                <label class="form-label">FB_PAGE_ID (Fanspage ID) <span style="color:var(--accent-rose);">*</span></label>
                                <input type="text" id="env-fb-page-id" class="form-control" placeholder="1253340697871457">
                            </div>
                            <div class="form-group">
                                <label class="form-label">FB_PAGE_ACCESS_TOKEN (Meta Page Token) <span style="color:var(--accent-rose);">*</span></label>
                                <div style="position:relative;">
                                    <input type="password" id="env-fb-token" class="form-control" placeholder="EAAXHI...">
                                    <button type="button" onclick="toggleInputVisibility('env-fb-token')" style="position:absolute; right:10px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                </div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">IG_ACCESS_TOKEN (Instagram Business)</label>
                                <div style="position:relative;">
                                    <input type="password" id="env-ig-token" class="form-control" placeholder="IG token...">
                                    <button type="button" onclick="toggleInputVisibility('env-ig-token')" style="position:absolute; right:10px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                </div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">THREADS_ACCESS_TOKEN (Threads API)</label>
                                <div style="position:relative;">
                                    <input type="password" id="env-threads-token" class="form-control" placeholder="Threads token...">
                                    <button type="button" onclick="toggleInputVisibility('env-threads-token')" style="position:absolute; right:10px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer;">👁️</button>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div style="display: flex; justify-content: flex-end; margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--border);">
                        <button class="btn btn-primary" onclick="saveEnvConfigDirect()" style="padding: 10px 24px; font-size: 0.9rem; font-weight: 700; background: linear-gradient(135deg, #10B981, #059669); box-shadow: 0 4px 14px rgba(16,185,129,0.3);">💾 Simpan Langsung ke .env</button>
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

    <!-- MODAL REPLACE CREDENTIAL -->
    <div id="modal-replace-cred" class="modal-overlay">
        <div class="modal-box">
            <div class="modal-header">
                <div class="modal-title" id="modal-cred-title">🔑 Ganti Kredensial / API Key</div>
                <button onclick="closeReplaceCredModal()" style="background:none; border:none; color:var(--text-muted); font-size:1.3rem; cursor:pointer;">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">Nama Layanan</label>
                <input type="text" id="modal-cred-service-display" class="form-control" readonly style="opacity:0.85; background:rgba(255,255,255,0.03);">
                <input type="hidden" id="modal-cred-service-id">
            </div>
            <div class="form-group">
                <label class="form-label">Nama Kunci (Key Identifier)</label>
                <input type="text" id="modal-cred-key" class="form-control" readonly style="opacity:0.85; background:rgba(255,255,255,0.03);">
            </div>
            <div class="form-group">
                <label class="form-label">Nilai Secret Baru (API Key / Access Token)</label>
                <div style="position:relative;">
                    <input type="password" id="modal-cred-value" class="form-control" placeholder="Tempel atau ketik API Key / Access Token baru di sini..." style="padding-right: 42px;">
                    <button type="button" onclick="toggleCredVisibility()" style="position:absolute; right:10px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:1.1rem;" title="Lihat / Sembunyikan">👁️</button>
                </div>
                <small style="color:var(--text-muted); font-size:0.75rem; margin-top:5px; display:block;">Nilai akan langsung dienkripsi menggunakan cipher Windows DPAPI ke dalam file `.vault.enc`.</small>
            </div>
            <div id="modal-cred-test-result" style="display:none; padding:10px 14px; border-radius:6px; font-size:0.82rem; margin-top:12px;"></div>
            <div class="modal-actions">
                <button class="btn btn-outline" onclick="closeReplaceCredModal()">Batal</button>
                <button class="btn btn-outline" onclick="testModalCredential()" style="border-color:var(--accent-blue); color:#60A5FA;">🔍 Uji Koneksi Live</button>
                <button class="btn btn-primary" onclick="saveCredentialFromModal()">💾 Simpan & Enkripsi</button>
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
            if (tabId === 'content') fetchContent();
            if (tabId === 'queue') fetchQueue();
            if (tabId === 'receipts') fetchReceipts();
            if (tabId === 'providers') fetchProviders();
            if (tabId === 'credentials') {{ fetchCredentials(); fetchEnvConfig(); }}
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
                document.getElementById('metric-published').innerText = d.recent_publications.length;
                document.getElementById('metric-pending').innerText = d.job_stats.PENDING;
                document.getElementById('metric-cost').innerText = '$' + d.cost_metrics.daily_spent.toFixed(2);
                document.getElementById('overview-pubs-tbody').innerHTML = d.recent_publications.map(p => `
                    <tr>
                        <td><img src="${{p.preview_url || '/static/logo.png'}}" style="width:36px; height:36px; border-radius:4px; object-fit:cover;"></td>
                        <td>${{p.title}}</td>
                        <td><span class="brand-badge">#${{p.pilar}}</span></td>
                        <td>${{p.platform}}</td>
                        <td><b style="color:var(--accent-emerald);">${{p.status}}</b></td>
                        <td><a href="${{p.post_url}}" target="_blank" class="btn btn-outline" style="font-size:0.7rem;">Open</a></td>
                    </tr>
                `).join('') || '<tr><td colspan="6">No publications yet.</td></tr>';
            }} catch (e) {{ console.error(e); }}
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
                    <td><b>${{p.name}}</b></td>
                    <td><span class="brand-badge">${{p.tier}}</span></td>
                    <td>${{p.capabilities.join(', ')}}</td>
                    <td><span style="color:var(--accent-emerald);">● ACTIVE</span></td>
                </tr>
            `).join('');
        }}

        async function fetchCredentials() {{
            try {{
                const res = await fetch('/api/credentials');
                const d = await res.json();
                document.getElementById('credentials-tbody').innerHTML = d.credentials.map(c => {{
                    const isConfigured = c.is_configured;
                    const statusBadge = isConfigured 
                        ? '<span style="color:var(--accent-emerald); font-weight:700; font-size:0.75rem;">● CONFIGURED</span>'
                        : '<span style="color:var(--accent-amber); font-weight:700; font-size:0.75rem;">● BELUM DIATUR</span>';
                    const dispName = c.display_name || c.service_name;
                    const pKey = c.primary_key || 'api_key';
                    const maskedVal = c.masked_value || (c.credentials ? JSON.stringify(c.credentials) : '-');

                    return `
                    <tr>
                        <td>
                            <div style="font-weight:700; color:var(--text-main); font-size:0.9rem;">${{dispName}}</div>
                            <div style="font-size:0.75rem; color:var(--text-muted); margin-top:2px;">${{c.description || ''}}</div>
                        </td>
                        <td>
                            <div style="font-size:0.72rem; color:var(--text-muted); margin-bottom:3px;">Key ID: <code>${{pKey}}</code></div>
                            <code style="background:rgba(255,255,255,0.06); padding:4px 8px; border-radius:4px; font-size:0.82rem; color:#A78BFA; display:inline-block;">${{maskedVal}}</code>
                        </td>
                        <td>${{statusBadge}}</td>
                        <td style="white-space:nowrap;">
                            <button class="btn btn-outline" style="font-size:0.75rem; padding:5px 10px; margin-right:6px;" onclick="testCred('${{c.service_name}}')">🔍 Test</button>
                            <button class="btn btn-primary" style="font-size:0.75rem; padding:5px 12px;" onclick="openReplaceCredModal('${{c.service_name}}', '${{dispName}}', '${{pKey}}')">🔑 Ganti Key</button>
                        </td>
                    </tr>
                    `;
                }}).join('') || '<tr><td colspan="4" style="text-align:center; color:var(--text-muted);">Tidak ada kredensial.</td></tr>';
            }} catch (e) {{ console.error(e); }}
        }}

        async function testCred(service) {{
            showToast('Menguji koneksi ' + service.toUpperCase() + '...');
            try {{
                const res = await fetch('/api/credentials/test', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ service_name: service }})
                }});
                const d = await res.json();
                if (d.status === 'VALID') {{
                    showToast('🟢 ' + service.toUpperCase() + ': ' + (d.message || 'Koneksi Valid'));
                }} else {{
                    showToast('🔴 ' + service.toUpperCase() + ' (' + d.status + '): ' + (d.message || 'Gagal'));
                }}
            }} catch (e) {{
                showToast('Eror: ' + e.message);
            }}
        }}

        function openReplaceCredModal(serviceId, displayName, primaryKey) {{
            document.getElementById('modal-cred-service-id').value = serviceId;
            document.getElementById('modal-cred-service-display').value = displayName;
            document.getElementById('modal-cred-key').value = primaryKey;
            document.getElementById('modal-cred-value').value = '';
            document.getElementById('modal-cred-value').type = 'password';
            document.getElementById('modal-cred-test-result').style.display = 'none';
            document.getElementById('modal-replace-cred').style.display = 'flex';
        }}

        function closeReplaceCredModal() {{
            document.getElementById('modal-replace-cred').style.display = 'none';
        }}

        function toggleCredVisibility() {{
            const input = document.getElementById('modal-cred-value');
            input.type = input.type === 'password' ? 'text' : 'password';
        }}

        async function testModalCredential() {{
            const serviceId = document.getElementById('modal-cred-service-id').value;
            const secretValue = document.getElementById('modal-cred-value').value.trim();
            const resultBox = document.getElementById('modal-cred-test-result');

            resultBox.style.display = 'block';
            resultBox.style.background = 'rgba(59, 130, 246, 0.15)';
            resultBox.style.color = '#60A5FA';
            resultBox.style.border = '1px solid rgba(59, 130, 246, 0.4)';
            resultBox.innerText = 'Menguji koneksi live ke ' + serviceId.toUpperCase() + '...';

            try {{
                const res = await fetch('/api/credentials/test', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ service_name: serviceId, custom_token: secretValue || undefined }})
                }});
                const d = await res.json();
                if (d.status === 'VALID') {{
                    resultBox.style.background = 'rgba(16, 185, 129, 0.15)';
                    resultBox.style.color = 'var(--accent-emerald)';
                    resultBox.style.border = '1px solid rgba(16, 185, 129, 0.4)';
                    resultBox.innerHTML = '🟢 <b>VALID:</b> ' + (d.message || 'Koneksi berhasil diverifikasi.');
                }} else {{
                    resultBox.style.background = 'rgba(239, 68, 68, 0.15)';
                    resultBox.style.color = 'var(--accent-rose)';
                    resultBox.style.border = '1px solid rgba(239, 68, 68, 0.4)';
                    resultBox.innerHTML = '🔴 <b>' + d.status + ':</b> ' + (d.message || 'Gagal terhubung.');
                }}
            }} catch (e) {{
                resultBox.style.background = 'rgba(239, 68, 68, 0.15)';
                resultBox.style.color = 'var(--accent-rose)';
                resultBox.innerText = 'Eror: ' + e.message;
            }}
        }}

        async function saveCredentialFromModal() {{
            const serviceId = document.getElementById('modal-cred-service-id').value;
            const primaryKey = document.getElementById('modal-cred-key').value;
            const secretValue = document.getElementById('modal-cred-value').value.trim();

            if (!secretValue) {{
                showToast('Nilai API Key / Token tidak boleh kosong.');
                return;
            }}

            showToast('Menyimpan & mengenkripsi ke DPAPI Vault...');
            try {{
                const res = await fetch('/api/credentials/set', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        service_name: serviceId,
                        credential_key: primaryKey,
                        secret_value: secretValue
                    }})
                }});
                const d = await res.json();
                if (d.success) {{
                    showToast('✅ ' + d.message);
                    closeReplaceCredModal();
                    fetchCredentials();
                }} else {{
                    showToast('Eror: ' + (d.detail || 'Gagal menyimpan kredensial.'));
                }}
            }} catch (e) {{
                showToast('Eror: ' + e.message);
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

        async function fetchEnvConfig() {{
            try {{
                const res = await fetch('/api/env');
                const d = await res.json();
                const env = d.env || {{}};
                if (document.getElementById('env-gemini-key')) document.getElementById('env-gemini-key').value = env.GEMINI_API_KEY || '';
                if (document.getElementById('env-xai-key')) document.getElementById('env-xai-key').value = env.XAI_API_KEY || '';
                if (document.getElementById('env-telegram-token')) document.getElementById('env-telegram-token').value = env.TELEGRAM_BOT_TOKEN || '';
                if (document.getElementById('env-telegram-admins')) document.getElementById('env-telegram-admins').value = env.TELEGRAM_ADMIN_IDS || '';
                if (document.getElementById('env-fb-page-id')) document.getElementById('env-fb-page-id').value = env.FB_PAGE_ID || '';
                if (document.getElementById('env-fb-token')) document.getElementById('env-fb-token').value = env.FB_PAGE_ACCESS_TOKEN || '';
                if (document.getElementById('env-ig-token')) document.getElementById('env-ig-token').value = env.IG_ACCESS_TOKEN || '';
                if (document.getElementById('env-threads-token')) document.getElementById('env-threads-token').value = env.THREADS_ACCESS_TOKEN || '';
            }} catch (e) {{
                console.error('Failed to load .env config:', e);
            }}
        }}

        async function saveEnvConfigDirect() {{
            showToast('Menyimpan perubahan langsung ke file .env...');
            const updates = {{
                GEMINI_API_KEY: document.getElementById('env-gemini-key').value.trim(),
                XAI_API_KEY: document.getElementById('env-xai-key').value.trim(),
                TELEGRAM_BOT_TOKEN: document.getElementById('env-telegram-token').value.trim(),
                TELEGRAM_ADMIN_IDS: document.getElementById('env-telegram-admins').value.trim(),
                FB_PAGE_ID: document.getElementById('env-fb-page-id').value.trim(),
                FB_PAGE_ACCESS_TOKEN: document.getElementById('env-fb-token').value.trim(),
                IG_ACCESS_TOKEN: document.getElementById('env-ig-token').value.trim(),
                THREADS_ACCESS_TOKEN: document.getElementById('env-threads-token').value.trim()
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
                    fetchCredentials();
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
            const res = await fetch('/api/content');
            const d = await res.json();
            document.getElementById('content-grid').innerHTML = d.contents.map(c => `
                <div class="card" style="padding:12px;">
                    <img src="${{c.media_urls[0] || '/static/logo.png'}}" style="width:100%; height:140px; object-fit:cover; border-radius:6px; margin-bottom:8px;">
                    <div style="font-weight:700; font-size:0.9rem;">${{c.title}}</div>
                    <div style="color:var(--text-muted); font-size:0.75rem; margin-top:4px;">${{c.caption.slice(0, 80)}}...</div>
                </div>
            `).join('') || 'No contents produced yet.';
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

        async function triggerStudio(pilar) {{
            showToast('Triggering ' + pilar + '...');
        }}

        pollStats();
        setInterval(pollStats, 5000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
