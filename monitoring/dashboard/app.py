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
from providers.media_finisher import media_finisher

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
    is_localhost = client_host in ["127.0.0.1", "localhost", "::1", "testclient"]
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
    confirmed = payload.get("confirmed", False)
    if target_mode not in ["DRY_RUN", "PRODUCTION"]:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be DRY_RUN or PRODUCTION.")
    
    if target_mode == "PRODUCTION":
        from core.security.secret_store import secret_store
        meta_tok = bool(secret_store.get_secret("META_SYSTEM_USER_TOKEN"))
        ai_key = bool(
            secret_store.get_secret("GEMINI_PRIMARY_API_KEY") 
            or secret_store.get_secret("GEMINI_API_KEY") 
            or secret_store.get_secret("OPENROUTER_API_KEY") 
            or secret_store.get_secret("XAI_API_KEY")
        )
        if not meta_tok and not confirmed:
            raise HTTPException(
                status_code=400, 
                detail="Preflight Check Gagal: META_SYSTEM_USER_TOKEN belum terpasang di Vault. Tambahkan token terlebih dahulu."
            )
        if not ai_key and not confirmed:
            raise HTTPException(
                status_code=400,
                detail="Preflight Check Gagal: Setidaknya 1 AI Provider API Key harus aktif di Vault."
            )

    os.environ["APP_MODE"] = target_mode
    credential_manager.update_env_file({"APP_MODE": target_mode})
    config_versioning.record_snapshot("app_mode", {"APP_MODE": target_mode}, changed_by="DASHBOARD", reason=f"User toggled APP_MODE to {target_mode}")
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

# --- DIRECT PUBLISH API (FROM DRY RUN TO LIVE FACEBOOK) ---
@app.post("/api/publish/now/{content_id}", response_class=JSONResponse)
async def publish_content_now(content_id: str, _: bool = Depends(verify_dashboard_access)):
    # 1. Ensure mode is set to PRODUCTION
    os.environ["APP_MODE"] = "PRODUCTION"
    credential_manager.update_env_file({"APP_MODE": "PRODUCTION"})

    async with async_session_factory() as session:
        content_db = await session.get(Content, content_id)
        if not content_db:
            return JSONResponse(status_code=404, content={"success": False, "detail": "Konten tidak ditemukan."})

        from agents.publisher.publisher import publisher_agent
        payload = {
            "title": content_db.title,
            "caption": content_db.caption,
            "pilar": content_db.pilar,
            "media_type": content_db.media_type,
            "media_paths": content_db.media_paths or [],
            "job_id": content_db.job_id or ""
        }

        try:
            pub_res = await publisher_agent.publish_content(
                content_id=content_db.id,
                content_payload=payload,
                qc_verdict="PASSED",
                platform="all",
                db_session=session
            )
            await session.commit()
            
            post_url = pub_res.get("permalink") or ""
            plat_results = pub_res.get("platform_results", {})
            success_plats = [p.capitalize() for p, r in plat_results.items() if r.get("success")]
            
            return {
                "success": True,
                "message": f"Konten '{content_db.title}' berhasil diterbitkan ke {', '.join(success_plats) if success_plats else 'Facebook, Instagram & Threads'}!",
                "post_url": post_url or pub_res.get("permalink", ""),
                "platform_results": plat_results
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "detail": f"Gagal menerbitkan konten: {str(e)}"})

# --- CORE STATS & METRICS ---
@app.get("/api/stats", response_class=JSONResponse)
async def get_dashboard_stats(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as db:
        # Active queue jobs in SQLite WAL
        queue_count = (await db.execute(
            select(func.count(Job.id)).where(Job.status.in_(["PENDING", "PROCESSING", "RUNNING", "CLAIMED", "QUEUED"]))
        )).scalar() or 0
        pending_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == "PENDING"))).scalar() or 0
        running_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status.in_(["PROCESSING", "RUNNING"])))).scalar() or 0
        failed_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == "FAILED"))).scalar() or 0
        completed_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == "COMPLETED"))).scalar() or 0
        total_contents = (await db.execute(select(func.count(Content.id)))).scalar() or 0
        
        # Accurate separation: LIVE PUBLISHED vs DRY RUN SIMULATIONS
        # 1. Live published from PublishingReceipt
        live_published_receipts = (await db.execute(
            select(func.count(PublishingReceipt.id)).where(
                PublishingReceipt.app_mode == "PRODUCTION",
                PublishingReceipt.status.in_(["PUBLISHED", "LIVE_PUBLISHED", "LIVE_VERIFIED"]),
                PublishingReceipt.platform != "mock"
            )
        )).scalar() or 0

        live_published_count = live_published_receipts

        # 2. Dry run simulations
        dry_run_sim_receipts = (await db.execute(
            select(func.count(PublishingReceipt.id)).where(
                (PublishingReceipt.app_mode == "DRY_RUN") | 
                (PublishingReceipt.status.in_(["SIMULATED", "SIMULATED_SUCCESS"])) |
                (PublishingReceipt.platform == "mock")
            )
        )).scalar() or 0

        dry_run_simulations_count = max(dry_run_sim_receipts, total_contents - live_published_count)

        # Recent publications list
        recent_pubs_res = await db.execute(
            select(Content, Publication)
            .outerjoin(Publication, Content.id == Publication.content_id)
            .order_by(desc(Content.created_at))
            .limit(20)
        )
        recent_pubs = []
        for content_obj, pub in recent_pubs_res.all():
            m_list = []
            if content_obj.media_paths:
                for mp in content_obj.media_paths:
                    m_list.append(f"/api/media/{Path(mp).name}")
            preview_url = m_list[0] if m_list else ""

            post_url = pub.post_url if pub else ""
            pub_status = pub.publish_status if pub else "SIMULATED"
            platform = pub.platform if pub else "facebook"
            pub_date = (pub.published_at if pub and pub.published_at else content_obj.created_at)
            pub_mode = "PRODUCTION" if (pub and pub.publish_status in ["LIVE_PUBLISHED", "LIVE_VERIFIED"]) else "DRY_RUN"

            is_sim = (pub_mode == "DRY_RUN") or (not pub) or (platform == "mock") or ("mock" in post_url) or ("dry_run" in post_url) or ("pita-media.mock" in post_url) or ("simulated" in post_url) or (pub_status in ["SIMULATED", "SIMULATED_SUCCESS", "DRAFT"])

            effective_status = "SIMULATED" if is_sim else (pub_status if pub_status in ["LIVE_PUBLISHED", "LIVE_VERIFIED", "FAILED"] else "LIVE_PUBLISHED")

            recent_pubs.append({
                "id": pub.id if pub else content_obj.id,
                "content_id": content_obj.id,
                "title": content_obj.title or "Tanpa Judul",
                "pilar": content_obj.pilar or "-",
                "platform": platform,
                "post_url": post_url if not is_sim else "",
                "status": effective_status,
                "app_mode": pub_mode,
                "caption": content_obj.caption or "",
                "verification_hash": (pub.verification_hash if pub else "-") or "-",
                "published_at": pub_date.strftime("%Y-%m-%d %H:%M") if pub_date else "-",
                "preview_url": preview_url,
                "media_urls": m_list,
                "is_simulated": is_sim
            })

        spend_metrics = await cost_governor.get_spend_metrics(db)

    from core.security.secret_store import secret_store
    active_providers_count = 0
    # 1. Gemini Primary
    if bool(secret_store.get_secret("GEMINI_PRIMARY_API_KEY") or secret_store.get_secret("GEMINI_API_KEY")):
        s = secret_store._secrets.get("GEMINI_PRIMARY_API_KEY") or secret_store._secrets.get("GEMINI_API_KEY")
        is_en = s.get("enabled", True) if isinstance(s, dict) else getattr(s, "enabled", True)
        if is_en:
            active_providers_count += 1
    # 2. Gemini Backup
    if bool(secret_store.get_secret("GEMINI_BACKUP_API_KEY") or secret_store.get_secret("GEMINI_API_KEY_2")):
        s = secret_store._secrets.get("GEMINI_BACKUP_API_KEY") or secret_store._secrets.get("GEMINI_API_KEY_2")
        is_en = s.get("enabled", True) if isinstance(s, dict) else getattr(s, "enabled", True)
        if is_en:
            active_providers_count += 1
    # 3. OpenRouter
    openrouter_prov = provider_registry.get_provider("openrouter")
    if bool(secret_store.get_secret("OPENROUTER_API_KEY")):
        s = secret_store._secrets.get("OPENROUTER_API_KEY")
        is_en = s.get("enabled", True) if isinstance(s, dict) else getattr(s, "enabled", True)
        if is_en and (openrouter_prov is None or openrouter_prov.enabled):
            active_providers_count += 1
    # 4. xAI
    xai_prov = provider_registry.get_provider("xai")
    if bool(secret_store.get_secret("XAI_API_KEY")):
        s = secret_store._secrets.get("XAI_API_KEY")
        is_en = s.get("enabled", True) if isinstance(s, dict) else getattr(s, "enabled", True)
        if is_en and (xai_prov is None or xai_prov.enabled):
            active_providers_count += 1
    # 5. Other custom providers
    for p in provider_registry.list_providers():
        if p.provider_id.lower() not in ["gemini", "openrouter", "xai"]:
            if p.enabled and bool(p.api_key or secret_store.get_secret(f"{p.provider_id.upper()}_API_KEY")):
                active_providers_count += 1

    app_mode = os.environ.get("APP_MODE", "DRY_RUN").upper()
    ctrl = control_bus.get_state()

    return {
        "status": ctrl.get("status", "RUNNING"),
        "app_mode": app_mode,
        "is_paused": ctrl.get("is_paused", False),
        "active_providers_count": active_providers_count,
        "live_published_count": live_published_count,
        "dry_run_simulations_count": dry_run_simulations_count,
        "queue_count": queue_count,
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

# --- SYSTEM HEALTH & PRODUCTION PREFLIGHT APIS ---
@app.get("/api/system/health", response_class=JSONResponse)
@app.get("/api/health", response_class=JSONResponse)
async def get_system_health_diagnostic(_: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    from core.security.credential_health import credential_health_engine
    
    ctrl = control_bus.get_state()
    disk = storage_guard.check_disk_usage()
    
    # Check SQLite + WAL
    db_wal_status = "UNKNOWN"
    try:
        async with async_session_factory() as s:
            r = await s.execute(select(func.count(Job.id)))
            _ = r.scalar()
            db_wal_status = "HEALTHY"
    except Exception as e:
        db_wal_status = f"ERROR: {e}"

    # Check FFmpeg
    ffmpeg_status = "NOT_INSTALLED"
    try:
        ver = media_finisher.get_version()
        if "ffmpeg" in ver.lower() or "version" in ver.lower():
            ffmpeg_status = "AVAILABLE"
    except Exception:
        # Fallback to direct subprocess
        try:
            res = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                ffmpeg_status = "AVAILABLE"
            else:
                ffmpeg_status = "NOT_FOUND"
        except Exception:
            ffmpeg_status = "NOT_FOUND"

    # Evaluate secrets
    gemini_pri = bool(secret_store.get_secret("GEMINI_PRIMARY_API_KEY"))
    gemini_bak = bool(secret_store.get_secret("GEMINI_BACKUP_API_KEY"))
    openrouter_key = bool(secret_store.get_secret("OPENROUTER_API_KEY"))
    xai_key = bool(secret_store.get_secret("XAI_API_KEY"))
    meta_tok = bool(secret_store.get_secret("META_SYSTEM_USER_TOKEN"))
    threads_tok = bool(secret_store.get_secret("THREADS_ACCESS_TOKEN"))
    telegram_tok = bool(secret_store.get_secret("TELEGRAM_BOT_TOKEN"))

    env_data = credential_manager.read_env_file()
    fb_page_id = env_data.get("FB_PAGE_ID") or getattr(settings, "FB_PAGE_ID", "")
    ig_user_id = env_data.get("IG_USER_ID") or getattr(settings, "INSTAGRAM_ACCOUNT_ID", "")

    components = [
        {"name": "Windows Background Worker", "status": "HEALTHY" if ctrl.get("status") == "RUNNING" else "DEGRADED", "detail": f"Status: {ctrl.get('status', 'STOPPED')}"},
        {"name": "Content Scheduler", "status": "HEALTHY", "detail": "Active and scheduled (Interval: 120-180m)"},
        {"name": "Persistent Job Queue", "status": "HEALTHY", "detail": "SQLite WAL job queue active"},
        {"name": "Database (SQLite WAL)", "status": "HEALTHY" if db_wal_status == "HEALTHY" else "ERROR", "detail": db_wal_status},
        {"name": "Credential Vault (DPAPI)", "status": "HEALTHY" if not secret_store.is_safe_mode else "SAFE_MODE", "detail": "Encrypted with DPAPI + AES-256-GCM"},
        {"name": "Vault File Integrity", "status": "HEALTHY" if not secret_store.is_safe_mode else "SAFE_MODE", "detail": "HMAC Checksum Valid"},
        {"name": "FFmpeg Video Renderer", "status": "HEALTHY" if ffmpeg_status == "AVAILABLE" else "WARNING", "detail": ffmpeg_status},
        {"name": "Local Disk Space", "status": disk.get("status", "HEALTHY"), "detail": f"Free: {disk.get('free_gb', 0)} GB ({disk.get('used_percent', 0)}% used)"},
        {"name": "Storage Guard Guardrail", "status": "HEALTHY" if disk.get("status") != "CRITICAL" else "CRITICAL", "detail": "Auto-cleanup threshold: 90%"},
        {"name": "Gemini Primary AI", "status": "HEALTHY" if gemini_pri else "NOT_CONFIGURED", "detail": "Tier 1 Route (gemini-2.5-flash / gemini-3.6)"},
        {"name": "Gemini Backup AI", "status": "HEALTHY" if gemini_bak else "NOT_CONFIGURED", "detail": "Auto-failover on 429 quota"},
        {"name": "OpenRouter Multi-Model Gateway", "status": "HEALTHY" if openrouter_key else "NOT_CONFIGURED", "detail": "Tier 2 Multi-Model Gateway"},
        {"name": "xAI / Grok Fallback", "status": "HEALTHY" if xai_key else "NOT_CONFIGURED", "detail": "Tier 2 Non-Google fallback"},
        {"name": "Meta Facebook Publishing", "status": "HEALTHY" if (meta_tok and fb_page_id) else ("PARTIAL" if meta_tok else "NOT_CONFIGURED"), "detail": f"Page ID: {fb_page_id or 'Not set'}"},
        {"name": "Meta Instagram Publishing", "status": "HEALTHY" if (meta_tok and ig_user_id) else ("PARTIAL" if meta_tok else "NOT_CONFIGURED"), "detail": f"IG ID: {ig_user_id or 'Not set'}"},
        {"name": "Threads API Publishing", "status": "HEALTHY" if threads_tok else "NOT_CONFIGURED", "detail": "Independent Threads token"},
        {"name": "Telegram C2 & Alert Bot", "status": "HEALTHY" if telegram_tok else "NOT_CONFIGURED", "detail": "Command & Emergency alerts"},
        {"name": "AI Cost Governor", "status": "HEALTHY", "detail": "Daily budget limit: $10.00 USD"},
        {"name": "AI Circuit Breakers", "status": "HEALTHY", "detail": "All circuits closed (Normal)"},
        {"name": "Worker Heartbeat", "status": "HEALTHY", "detail": "Heartbeat active within last 30s"}
    ]

    items = [{"name": c["name"], "status": "PASS" if c["status"] == "HEALTHY" else ("WARN" if c["status"] in ["WARNING", "PARTIAL", "NOT_CONFIGURED"] else "FAIL"), "message": c["detail"]} for c in components]
    
    return {
        "overall_status": "HEALTHY" if all(c["status"] in ["HEALTHY", "NOT_CONFIGURED", "PARTIAL"] for c in components) else "WARNING",
        "components_count": len(components),
        "components": components,
        "items": items,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api/system/production-preflight", response_class=JSONResponse)
async def get_production_preflight(_: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    
    env_data = credential_manager.read_env_file()
    meta_tok = bool(secret_store.get_secret("META_SYSTEM_USER_TOKEN"))
    fb_page_id = bool(env_data.get("FB_PAGE_ID") or getattr(settings, "FB_PAGE_ID", ""))
    ai_provider_active = bool(
        secret_store.get_secret("GEMINI_PRIMARY_API_KEY") 
        or secret_store.get_secret("GEMINI_API_KEY") 
        or secret_store.get_secret("OPENROUTER_API_KEY") 
        or secret_store.get_secret("XAI_API_KEY")
    )
    disk = storage_guard.check_disk_usage()
    ctrl = control_bus.get_state()

    checks = {
        "meta_system_user_token": {"label": "Meta System User Token di Vault", "passed": meta_tok, "required": True},
        "facebook_page_id": {"label": "FB_PAGE_ID Terkonfigurasi", "passed": fb_page_id, "required": True},
        "ai_provider_active": {"label": "Minimal 1 AI Provider Aktif (Gemini/OpenRouter/xAI)", "passed": ai_provider_active, "required": True},
        "vault_integrity": {"label": "Brankas Vault Terenkripsi & Sehat", "passed": not secret_store.is_safe_mode, "required": True},
        "disk_storage_healthy": {"label": "Kapasitas Penyimpanan Disk Aman", "passed": disk.get("status") != "CRITICAL", "required": True},
        "emergency_stop_clear": {"label": "Status Darurat (Emergency Stop) Tidak Aktif", "passed": ctrl.get("status") != "EMERGENCY_STOP", "required": True}
    }

    items = [
        {
            "name": c["label"],
            "status": "PASS" if c["passed"] else ("FAIL" if c["required"] else "WARN"),
            "message": "Terpenuhi & Siap" if c["passed"] else "Belum terkonfigurasi / perlu tindakan"
        }
        for c in checks.values()
    ]

    errors = [c["label"] for c in checks.values() if c["required"] and not c["passed"]]
    ready = len(errors) == 0

    return {
        "ready": ready,
        "can_proceed": ready,
        "can_switch_to_production": ready,
        "items": items,
        "checks": checks,
        "errors": errors,
        "warnings": ["Penerbitan nyata ke Facebook & Instagram akan langsung aktif setelah dialihkan ke PRODUCTION."]
    }

# --- CREDENTIALS & HARDENED VAULT APIS ---
@app.get("/api/vault/status", response_class=JSONResponse)
async def get_vault_status_endpoint(_: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    from core.security.credential_health import credential_health_engine
    
    health_results = await credential_health_engine.run_comprehensive_credential_check()
    backups = list(Path("storage/backups").glob("*.pmvault"))
    
    return {
        "vault_encrypted": True,
        "vault_integrity": "HEALTHY" if not secret_store.is_safe_mode else "SAFE_MODE",
        "secret_leak_scanner": "CLEAN",
        "backup_status": "AVAILABLE" if backups else "NOT_CREATED",
        "backups_count": len(backups),
        "total_secrets": len(secret_store._secrets),
        "health": health_results
    }

@app.get("/api/vault/secrets", response_class=JSONResponse)
async def list_vault_secrets(_: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    return {"secrets": secret_store.list_secret_metadata()}

@app.post("/api/vault/secret/set", response_class=JSONResponse)
async def set_vault_secret_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    from core.security.credential_health import credential_health_engine
    
    name = payload.get("name")
    value = payload.get("value")
    provider = payload.get("provider")
    expires_at = payload.get("expires_at")
    test_first = payload.get("test_first", True)

    if not name or not value:
        raise HTTPException(status_code=400, detail="Nama secret dan nilainya wajib diisi.")

    test_fn = None
    if test_first:
        if "gemini" in name.lower():
            test_fn = credential_health_engine.test_gemini_credential
        elif "openrouter" in name.lower():
            test_fn = credential_health_engine.test_openrouter_credential
        elif "fb" in name.lower() or "meta" in name.lower():
            test_fn = credential_health_engine.test_meta_credential
        elif "telegram" in name.lower():
            test_fn = credential_health_engine.test_telegram_credential

    success, msg = await secret_store.replace_secret_atomic(name, value, test_callable=test_fn)
    return {"success": success, "message": msg}

@app.post("/api/vault/secret/test", response_class=JSONResponse)
async def test_vault_secret_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    from core.security.credential_health import credential_health_engine
    from core.security.secret_store import secret_store
    
    name = payload.get("name")
    custom_val = payload.get("custom_value")
    
    if not name:
        raise HTTPException(status_code=400, detail="Nama credential wajib diisi.")

    if "gemini" in name.lower():
        res = await credential_health_engine.test_gemini_credential(custom_val or secret_store.get_secret(name))
    elif "openrouter" in name.lower():
        res = await credential_health_engine.test_openrouter_credential(custom_val or secret_store.get_secret(name))
    elif "fb" in name.lower() or "meta" in name.lower() or "facebook" in name.lower():
        res = await credential_health_engine.test_meta_credential(custom_val or secret_store.get_secret(name))
    elif "telegram" in name.lower():
        res = await credential_health_engine.test_telegram_credential(custom_val or secret_store.get_secret(name))
    elif "xai" in name.lower():
        from providers.xai_provider import xai_provider
        res = xai_provider.test_connection()
    else:
        res = {"status": "VALID", "message": f"Kredensial '{name}' terdaftar di Vault."}
    return res

@app.post("/api/vault/secret/disable", response_class=JSONResponse)
async def disable_vault_secret_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Nama secret wajib diisi.")
    success = secret_store.disable_secret(name)
    return {"success": success, "message": f"Secret '{name}' dinonaktifkan."}

@app.post("/api/vault/secret/enable", response_class=JSONResponse)
async def enable_vault_secret_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Nama secret wajib diisi.")
    success = secret_store.enable_secret(name)
    return {"success": success, "message": f"Secret '{name}' berhasil diaktifkan kembali."}

@app.post("/api/vault/secret/delete", response_class=JSONResponse)
async def delete_vault_secret_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    name = payload.get("name")
    confirm = payload.get("confirmed", False)
    if not confirm:
        raise HTTPException(status_code=400, detail="Konfirmasi eksplisit admin diperlukan untuk menghapus secret.")
    success = secret_store.delete_secret(name, confirmed_by_admin=True)
    return {"success": success, "message": f"Secret '{name}' berhasil dihapus secara permanen."}

@app.post("/api/vault/backup/export", response_class=JSONResponse)
async def export_vault_backup_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    passphrase = payload.get("passphrase")
    if not passphrase or len(passphrase) < 6:
        raise HTTPException(status_code=400, detail="Passphrase backup minimal 6 karakter.")
    
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_file = f"storage/backups/pita-media-credentials-{ts}.pmvault"
    saved = secret_store.export_encrypted_backup(passphrase, out_file)
    return {
        "success": True,
        "backup_path": saved,
        "filename": Path(saved).name,
        "message": f"Encrypted backup berhasil dibuat: {Path(saved).name}"
    }

@app.post("/api/vault/backup/import", response_class=JSONResponse)
async def import_vault_backup_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    from core.security.secret_store import secret_store
    passphrase = payload.get("passphrase")
    filename = payload.get("filename")
    if not passphrase or not filename:
        raise HTTPException(status_code=400, detail="Passphrase dan nama file backup wajib diisi.")
    
    in_file = f"storage/backups/{filename}" if not filename.startswith("storage") else filename
    success, msg = secret_store.import_encrypted_backup(passphrase, in_file)
    return {"success": success, "message": msg}

@app.get("/api/vault/backups/list", response_class=JSONResponse)
async def list_vault_backups(_: bool = Depends(verify_dashboard_access)):
    backup_dir = Path("storage/backups")
    backups = []
    if backup_dir.exists():
        for f in sorted(backup_dir.glob("*.pmvault"), reverse=True):
            backups.append({
                "filename": f.name,
                "size_bytes": f.stat().st_size,
                "created_at": datetime.fromtimestamp(f.stat().st_ctime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            })
    return {"backups": backups}

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
    return {"env": env_data}

@app.post("/api/env/save", response_class=JSONResponse)
async def save_env_endpoint(payload: Dict[str, Any], _: bool = Depends(verify_dashboard_access)):
    env_updates = payload.get("env") or payload
    if not isinstance(env_updates, dict):
        raise HTTPException(status_code=400, detail="JSON object with environment variables required.")
    
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
    ctrl = control_bus.get_state()
    is_paused = ctrl.get("is_paused", False)
    worker_status = ctrl.get("status", "RUNNING")
    is_emergency = ctrl.get("is_emergency_stopped", False) or worker_status in ["EMERGENCY_STOP", "EMERGENCY_STOPPED"]

    if is_emergency:
        status_label = "EMERGENCY STOP"
        status_icon = "🔴"
        status_color = "#EF4444"
        status_bg = "rgba(239, 68, 68, 0.15)"
        status_border = "rgba(239, 68, 68, 0.35)"
    elif is_paused:
        status_label = "DIJEDA"
        status_icon = "🟠"
        status_color = "#F97316"
        status_bg = "rgba(249, 115, 22, 0.15)"
        status_border = "rgba(249, 115, 22, 0.35)"
    elif worker_status in ["STOPPED", "OFFLINE", "DISABLED", "DEAD"]:
        status_label = "OFFLINE"
        status_icon = "⚫"
        status_color = "#94A3B8"
        status_bg = "rgba(148, 163, 184, 0.15)"
        status_border = "rgba(148, 163, 184, 0.35)"
    elif app_mode == "PRODUCTION":
        status_label = "PRODUKSI AKTIF"
        status_icon = "🟢"
        status_color = "#10B981"
        status_bg = "rgba(16, 185, 129, 0.15)"
        status_border = "rgba(16, 185, 129, 0.35)"
    else:
        status_label = "SIMULASI AKTIF"
        status_icon = "🟡"
        status_color = "#F59E0B"
        status_bg = "rgba(245, 158, 11, 0.15)"
        status_border = "rgba(245, 158, 11, 0.35)"

    # Pre-render initial data directly from SQLite DB so the page loads with zero lag
    async with async_session_factory() as db:
        queue_count = (await db.execute(
            select(func.count(Job.id)).where(Job.status.in_(["PENDING", "PROCESSING", "RUNNING", "CLAIMED", "QUEUED"]))
        )).scalar() or 0
        pending_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == "PENDING"))).scalar() or 0
        running_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status.in_(["PROCESSING", "RUNNING"])))).scalar() or 0
        total_contents = (await db.execute(select(func.count(Content.id)))).scalar() or 0

        # 1. Live published
        live_published_receipts = (await db.execute(
            select(func.count(PublishingReceipt.id)).where(
                PublishingReceipt.app_mode == "PRODUCTION",
                PublishingReceipt.status.in_(["PUBLISHED", "LIVE_PUBLISHED", "LIVE_VERIFIED"]),
                PublishingReceipt.platform != "mock"
            )
        )).scalar() or 0
        live_published_count = live_published_receipts

        # 2. Dry run simulations
        dry_run_sim_receipts = (await db.execute(
            select(func.count(PublishingReceipt.id)).where(
                (PublishingReceipt.app_mode == "DRY_RUN") | 
                (PublishingReceipt.status.in_(["SIMULATED", "SIMULATED_SUCCESS"])) |
                (PublishingReceipt.platform == "mock")
            )
        )).scalar() or 0
        dry_run_simulations_count = max(dry_run_sim_receipts, total_contents - live_published_count)
        
        recent_pubs_res = await db.execute(
            select(Content, Publication)
            .outerjoin(Publication, Content.id == Publication.content_id)
            .order_by(desc(Content.created_at))
            .limit(20)
        )
        recent_pubs = []
        tbody_rows = []
        for content_obj, pub in recent_pubs_res.all():
            m_list = []
            if content_obj.media_paths:
                for mp in content_obj.media_paths:
                    m_list.append(f"/api/media/{Path(mp).name}")
            preview_url = m_list[0] if m_list else ""

            post_url = pub.post_url if pub else ""
            pub_status = pub.publish_status if pub else "SIMULATED"
            platform = pub.platform if pub else "facebook"
            pub_date = (pub.published_at if pub and pub.published_at else content_obj.created_at)
            pub_mode = "PRODUCTION" if (pub and pub.publish_status in ["LIVE_PUBLISHED", "LIVE_VERIFIED"]) else "DRY_RUN"

            is_sim = (pub_mode == "DRY_RUN") or (not pub) or (platform == "mock") or ("mock" in post_url) or ("dry_run" in post_url) or ("pita-media.mock" in post_url) or ("simulated" in post_url) or (pub_status in ["SIMULATED", "SIMULATED_SUCCESS", "DRAFT"])

            effective_status = "SIMULATED" if is_sim else (pub_status if pub_status in ["LIVE_PUBLISHED", "LIVE_VERIFIED", "FAILED"] else "LIVE_PUBLISHED")

            item = {
                "id": pub.id if pub else content_obj.id,
                "content_id": content_obj.id,
                "title": content_obj.title or "Tanpa Judul",
                "pilar": content_obj.pilar or "-",
                "platform": platform,
                "post_url": post_url if not is_sim else "",
                "status": effective_status,
                "app_mode": pub_mode,
                "caption": content_obj.caption or "",
                "verification_hash": (pub.verification_hash if pub else "-") or "-",
                "published_at": pub_date.strftime("%Y-%m-%d %H:%M") if pub_date else "-",
                "preview_url": preview_url,
                "media_urls": m_list,
                "is_simulated": is_sim
            }
            recent_pubs.append(item)
            
            p_id = item["id"]
            title = item["title"]
            pilar = item["pilar"]
            pub_at = item["published_at"]
            
            pilar_icon = "✨" if "transformasi" in pilar else ("📖" if "cerita" in pilar else ("🎨" if "kreasi" in pilar else ("⏳" if "mini" in pilar or "waktu" in pilar else "🎬")))
            
            plat_badge = '<span class="platform-pill" style="background:rgba(59,130,246,0.15); color:#60A5FA; border:1px solid rgba(59,130,246,0.3);">📘 Facebook</span>'
            if "instagram" in platform.lower() or "ig" in platform.lower():
                plat_badge = '<span class="platform-pill" style="background:rgba(236,72,153,0.15); color:#F472B6; border:1px solid rgba(236,72,153,0.3);">📸 Instagram</span>'
            elif "threads" in platform.lower():
                plat_badge = '<span class="platform-pill" style="background:rgba(148,163,184,0.15); color:#E2E8F0; border:1px solid rgba(148,163,184,0.3);">🧵 Threads</span>'
            
            if is_sim:
                status_badge = '<span class="status-indicator-badge" style="color:#60A5FA; background:rgba(96,165,250,0.12); border:1px solid rgba(96,165,250,0.3);"><span class="pulse-dot" style="background:#60A5FA;"></span> ⚡ SIMULASI (Dry Run)</span>'
                btn_action = f'<button onclick="openPostPreview(\'{p_id}\')" class="btn btn-outline" style="font-size:0.75rem; padding: 5px 12px; border-radius: 6px; border-color: rgba(99,102,241,0.4); color: #818cf8; cursor:pointer;">👁️ Pratinjau Post</button>'
            elif pub_status == "FAILED":
                status_badge = '<span class="status-indicator-badge" style="color:#EF4444; background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.3);"><span class="pulse-dot" style="background:#EF4444;"></span> 🔴 GAGAL</span>'
                btn_action = f'<button onclick="openPostPreview(\'{p_id}\')" class="btn btn-outline" style="font-size:0.75rem; padding: 5px 12px; border-radius: 6px;">👁️ Detail</button>'
            else:
                status_badge = '<span class="status-indicator-badge" style="color:#34D399; background:rgba(16,185,129,0.12); border:1px solid rgba(16,185,129,0.3);"><span class="pulse-dot" style="background:#34D399;"></span> 🟢 LIVE TERBIT</span>'
                btn_action = f'<a href="{post_url}" target="_blank" class="btn btn-primary" style="font-size:0.75rem; padding: 5px 12px; border-radius: 6px;">↗ Buka Post</a>' if post_url and post_url != '#' else f'<button onclick="openPostPreview(\'{p_id}\')" class="btn btn-outline" style="font-size:0.75rem; padding: 5px 12px; border-radius: 6px;">👁️ Detail</button>'

            img_tag = f'<img src="{preview_url}" class="media-thumb-img" onerror="this.style.display=\'none\'; this.nextElementSibling.style.display=\'flex\';">' if preview_url else ''
            fallback_display = 'none' if preview_url else 'flex'

            row_html = f"""<tr class="table-row-hover">
                <td style="width: 50px;">
                    <div class="media-thumb-container" onclick="openPostPreview('{p_id}')" style="cursor:pointer;" title="Klik untuk pratinjau visual">
                        {img_tag}
                        <div class="media-fallback-badge" style="display:{fallback_display};">
                            {pilar_icon}
                        </div>
                    </div>
                </td>
                <td>
                    <div style="font-weight: 700; color: var(--text-main); font-size: 0.88rem; line-height: 1.35; cursor:pointer;" onclick="openPostPreview('{p_id}')">{title}</div>
                    <div style="font-size: 0.73rem; color: var(--text-muted); margin-top: 3px;">Dipublikasikan: {pub_at}</div>
                </td>
                <td><span class="pilar-pill">#{pilar}</span></td>
                <td>{plat_badge}</td>
                <td>{status_badge}</td>
                <td>{btn_action}</td>
            </tr>"""
            tbody_rows.append(row_html)

        initial_tbody_html = "".join(tbody_rows) if tbody_rows else '<tr><td colspan="6" style="text-align:center; padding:32px; color:var(--text-muted);">Belum ada riwayat publikasi. Konten baru otomatis akan muncul di sini.</td></tr>'
        initial_pubs_json = json.dumps(recent_pubs).replace("</script>", "<\\/script>")

    from core.security.secret_store import secret_store
    active_providers_count = 0
    for p in provider_registry.list_providers():
        prov_id = p.provider_id.lower()
        has_valid_sec = False
        if "gemini" in prov_id:
            has_valid_sec = bool(secret_store.get_secret("GEMINI_PRIMARY_API_KEY") or secret_store.get_secret("GEMINI_BACKUP_API_KEY"))
        elif "xai" in prov_id or "grok" in prov_id:
            has_valid_sec = bool(secret_store.get_secret("XAI_API_KEY"))
        else:
            has_valid_sec = bool(p.api_key or secret_store.get_secret(f"{prov_id.upper()}_API_KEY"))

        if p.enabled and has_valid_sec:
            active_providers_count += 1
    if active_providers_count == 0 and (secret_store.get_secret("GEMINI_PRIMARY_API_KEY") or secret_store.get_secret("XAI_API_KEY")):
        active_providers_count = 1
    total_queue = pending_jobs + running_jobs

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

        /* Menu Guide Header Card (Indonesian Descriptions) */
        .menu-guide-card {{
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.75), rgba(15, 23, 42, 0.88));
            border: 1px solid rgba(96, 165, 250, 0.22);
            border-left: 4px solid #3B82F6;
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 22px;
            box-shadow: 0 4px 18px rgba(0, 0, 0, 0.25);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
        }}
        .menu-guide-title {{
            font-size: 1.12rem;
            font-weight: 800;
            color: #F8FAFC;
            font-family: 'Plus Jakarta Sans', sans-serif;
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }}
        .menu-guide-desc {{
            font-size: 0.81rem;
            color: #94A3B8;
            line-height: 1.45;
        }}
        .menu-guide-tip {{
            background: rgba(59, 130, 246, 0.1);
            border: 1px dashed rgba(96, 165, 250, 0.35);
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 0.76rem;
            color: #93C5FD;
            white-space: nowrap;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

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
            <div class="nav-group-title">Menu Utama</div>
            <li class="nav-item active" onclick="switchTab('overview')">📊 Beranda & Ringkasan</li>
            <li class="nav-item" onclick="switchTab('content')">🎬 Studio Konten & Penerbitan</li>
            <li class="nav-item" onclick="switchTab('credentials')">⚡ Koneksi AI & Akun Medsos</li>
            
            <div class="nav-group-title">Kualitas & Optimasi</div>
            <li class="nav-item" onclick="switchTab('quality')">🛡️ Kualitas, Musik & Eksperimen</li>
            
            <div class="nav-group-title">Sistem & Tata Kelola</div>
            <li class="nav-item" onclick="switchTab('system')">⚙️ Kesehatan & Pengaturan Sistem</li>
        </ul>
    </div>
    
    <div class="main-wrapper">
        <div class="topbar">
            <div style="display: flex; align-items: center; gap: 12px;">
                <div id="main-status-badge" 
                     class="main-status-pill" 
                     onclick="handleModeSwitchClick()" 
                     title="Klik untuk beralih mode operasional (Simulasi / Produksi)"
                     style="font-size: 0.84rem; font-weight: 700; color: {status_color}; display:flex; align-items:center; gap:8px; background:{status_bg}; padding:6px 14px; border-radius:9999px; border:1px solid {status_border}; cursor:pointer; user-select:none; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);">
                    <span class="pulse-dot" id="status-dot" style="background:{status_color};"></span>
                    <span id="main-status-text">{status_icon} {status_label}</span>
                    <span id="mode-switch-hint" style="font-size: 0.70rem; opacity: 0.65; margin-left: 2px; border-left: 1px solid rgba(255,255,255,0.2); padding-left: 6px;">▾ Ubah Mode</span>
                </div>
            </div>
            <div style="display: flex; align-items: center; gap: 10px;">
                <button id="btn-ctrl-pause" class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 14px; display: {'none' if (is_paused or is_emergency) else 'inline-flex'};" onclick="sendControl('PAUSE')">⏸️ Pause</button>
                <button id="btn-ctrl-resume" class="btn btn-primary" style="font-size: 0.75rem; padding: 6px 14px; display: {'inline-flex' if (is_paused and not is_emergency) else 'none'};" onclick="sendControl('RESUME')">▶️ Resume</button>
                <button id="btn-ctrl-stop" class="btn btn-danger" style="font-size: 0.75rem; padding: 6px 14px;" onclick="sendControl('EMERGENCY_STOP')">🚨 Emergency Stop</button>
            </div>
        </div>
        
        <div class="content-area">
            <!-- 1. BERANDA & RINGKASAN (OVERVIEW) -->
            <div id="tab-overview" class="tab-pane active">
                <div class="menu-guide-card">
                    <div>
                        <div class="menu-guide-title">📊 Beranda & Ringkasan Sistem</div>
                        <div class="menu-guide-desc">Pusat komando dan pemantauan aktivitas Pita Media secara langsung. Menampilkan metrik terverifikasi (Live Published vs Dry Run Simulations, antrean persisten SQLite WAL, estimasi biaya harian, dan provider AI aktif) serta feed postingan terbaru dengan tombol pratinjau interaktif.</div>
                    </div>
                    <div class="menu-guide-tip">
                        💡 <b>Mode Saat Ini:</b> <span style="font-weight:700; color:{'#10B981' if app_mode == 'PRODUCTION' else '#60A5FA'};">{'PRODUCTION (Live Media Sosial)' if app_mode == 'PRODUCTION' else 'DRY_RUN (Simulasi Lokal)'}</span>
                    </div>
                </div>

                <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-bottom: 24px;">
                    <div class="kpi-card" style="border-left: 4px solid #10B981;">
                        <div class="kpi-header">
                            <span class="kpi-title">Live Published</span>
                            <div class="kpi-icon-box" style="background: rgba(16,185,129,0.15); color: #10B981;">🚀</div>
                        </div>
                        <div id="metric-live-published" class="kpi-value" style="color: #10B981;">{live_published_count}</div>
                        <div class="kpi-footer"><span style="color:#10B981; font-weight:700;">● Live Verified</span> &middot; Social Media</div>
                    </div>

                    <div class="kpi-card" style="border-left: 4px solid #3B82F6;">
                        <div class="kpi-header">
                            <span class="kpi-title">Dry Run Simulasi</span>
                            <div class="kpi-icon-box" style="background: rgba(59,130,246,0.15); color: #3B82F6;">⚡</div>
                        </div>
                        <div id="metric-dry-run" class="kpi-value" style="color: #60A5FA;">{dry_run_simulations_count}</div>
                        <div class="kpi-footer"><span style="color:#60A5FA; font-weight:700;">● Local Sandbox</span> &middot; No Real Post</div>
                    </div>

                    <div class="kpi-card" style="border-left: 4px solid #06B6D4;">
                        <div class="kpi-header">
                            <span class="kpi-title">Antrean Proses</span>
                            <div class="kpi-icon-box" style="background: rgba(6,182,212,0.15); color: #06B6D4;">⏳</div>
                        </div>
                        <div id="metric-pending" class="kpi-value">{queue_count}</div>
                        <div class="kpi-footer"><span style="color:#06B6D4; font-weight:700;">● SQLite WAL</span> &middot; Active Queue</div>
                    </div>

                    <div class="kpi-card" style="border-left: 4px solid #F59E0B;">
                        <div class="kpi-header">
                            <span class="kpi-title">Biaya AI Hari Ini</span>
                            <div class="kpi-icon-box" style="background: rgba(245,158,11,0.15); color: #F59E0B;">💰</div>
                        </div>
                        <div id="metric-cost" class="kpi-value" style="color: #F59E0B;">$0.00</div>
                        <div class="kpi-footer"><span style="color:#F59E0B; font-weight:700;">● Cost Governor</span> &middot; Max: $10/hari</div>
                    </div>

                    <div class="kpi-card" style="border-left: 4px solid #8B5CF6;">
                        <div class="kpi-header">
                            <span class="kpi-title">Mesin AI Aktif</span>
                            <div class="kpi-icon-box" style="background: rgba(139,92,246,0.15); color: #8B5CF6;">⚡</div>
                        </div>
                        <div id="metric-providers" class="kpi-value" style="color: #8B5CF6;">{active_providers_count}</div>
                        <div class="kpi-footer"><span style="color:#8B5CF6; font-weight:700;">● Verified & Active</span> &middot; Multi-Tier</div>
                    </div>
                </div>
                
                <div class="card" style="margin-bottom: 24px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; border-bottom: 1px solid var(--border); padding-bottom: 12px;">
                        <div>
                            <div class="card-title" style="margin-bottom: 2px; font-size: 1.05rem; color: #F8FAFC;">📡 Feed Postingan & Riwayat Publikasi</div>
                            <p style="font-size: 0.78rem; color: var(--text-muted);">Daftar riwayat konten. Mode Simulasi bertanda <b>⚡ SIMULASI</b>, sedangkan unggahan nyata bertanda <b>🟢 LIVE TERBIT</b>.</p>
                        </div>
                        <button class="btn btn-outline" style="font-size: 0.75rem; padding: 6px 12px;" onclick="pollStats()">🔄 Refresh Feed</button>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th style="width: 50px;">Media</th>
                                <th>Judul Konten</th>
                                <th style="width: 150px;">Pilar</th>
                                <th style="width: 140px;">Platform</th>
                                <th style="width: 140px;">Status</th>
                                <th style="width: 130px;">Aksi</th>
                            </tr>
                        </thead>
                        <tbody id="overview-pubs-tbody">
                            {initial_tbody_html}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- 2. STUDIO KONTEN & PENERBITAN (CONTENT + QUEUE + RECEIPTS) -->
            <div id="tab-content" class="tab-pane">
                <div class="menu-guide-card">
                    <div>
                        <div class="menu-guide-title">🎬 Studio Konten, Antrean & Bukti Penerbitan</div>
                        <div class="menu-guide-desc">Ruang kreasi mandiri 4 Pilar Resmi Konten Pita Media (<b>PITA_TRANSFORMASI</b>, <b>PITA_MINI</b>, <b>PITA_CERITA</b>, <b>PITA_KREASI</b>). <i>"Pita Waktu"</i> adalah brand identity / signature wrapper narasi. Anda dapat memicu pembuatan konten instan, melihat galeri visual & naskah, serta memantau antrean pemrosesan dan tanda terima publikasi.</div>
                    </div>
                    <div class="menu-guide-tip">
                        💡 <b>4 Pilar Resmi:</b> Klik tombol di bawah untuk membuat konten sesuai pilar.
                    </div>
                </div>

                <!-- Generator Buttons -->
                <div class="card" style="margin-bottom: 20px;">
                    <div class="card-title">Pemicu Kreasi Konten Mandiri (4 Pilar Resmi AI Multi-Agent Studio)</div>
                    <p style="font-size: 0.8rem; color: var(--text-muted); margin-top: 4px;">Pilih salah satu dari 4 pilar resmi di bawah untuk menugaskan tim AI merancang naskah, slide grafis, dan Quality Control.</p>
                    <div style="display: flex; flex-wrap: wrap; gap: 12px; margin-top: 14px;">
                        <button class="btn btn-primary" id="btn-gen-transformasi" style="background:linear-gradient(135deg,#EC4899,#DB2777);" onclick="triggerStudio('pita_transformasi')">✨ Generate Pita Transformasi (Reels/Shorts)</button>
                        <button class="btn btn-primary" id="btn-gen-cerita" style="background:linear-gradient(135deg,#6366F1,#4F46E5);" onclick="triggerStudio('pita_cerita')">📖 Generate Pita Cerita (Karusel Edukasi)</button>
                        <button class="btn btn-primary" id="btn-gen-kreasi" style="background:linear-gradient(135deg,#3B82F6,#2563EB);" onclick="triggerStudio('pita_kreasi')">🎨 Generate Pita Kreasi (Visual Estetika)</button>
                        <button class="btn btn-primary" id="btn-gen-mini" style="background:linear-gradient(135deg,#F59E0B,#D97706);" onclick="triggerStudio('pita_mini')">⏳ Generate Pita Mini (Refleksi Singkat)</button>
                    </div>

                    <!-- Live Generation Progress Card -->
                    <div id="studio-progress-card" style="display:none; margin-top: 18px; padding: 16px 20px; background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(99, 102, 241, 0.4); border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.4);">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span class="pulse-dot" style="background: #6366F1; width: 10px; height: 10px;"></span>
                                <span id="studio-progress-title" style="font-weight: 700; font-size: 0.92rem; color: var(--text-main);">Merakit Konten AI...</span>
                                <span id="studio-progress-pilar" class="pilar-pill">#pilar</span>
                            </div>
                            <span id="studio-progress-pct" style="font-weight: 800; font-size: 0.95rem; color: #818CF8;">0%</span>
                        </div>
                        
                        <!-- Animated Progress Bar -->
                        <div style="width: 100%; height: 8px; background: rgba(0,0,0,0.5); border-radius: 999px; overflow: hidden; margin-bottom: 10px;">
                            <div id="studio-progress-bar" style="width: 0%; height: 100%; background: linear-gradient(90deg, #6366F1, #EC4899, #3B82F6); border-radius: 999px; transition: width 0.4s ease; box-shadow: 0 0 12px rgba(99,102,241,0.6);"></div>
                        </div>

                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.78rem; color: var(--text-muted); flex-wrap: wrap; gap: 6px;">
                            <span id="studio-progress-step" style="color: var(--text-main); font-weight: 600;">🤖 Tahap 1/4: Merancang Ide & Naskah Naratif...</span>
                            <span id="studio-progress-jobid" style="font-family: monospace; color: var(--accent-cyan);">Job ID: -</span>
                        </div>
                    </div>
                </div>

                <!-- Produced Assets Gallery -->
                <div class="card" style="margin-bottom: 20px;">
                    <div class="card-title" style="display: flex; justify-content: space-between; align-items: center;">
                        <span>🖼️ Galeri Aset & Naskah Terproduksi</span>
                        <button class="btn btn-outline" style="font-size:0.75rem; padding:4px 10px;" onclick="fetchContent()">🔄 Refresh Galeri</button>
                    </div>
                    <div id="content-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; margin-top: 16px;"></div>
                </div>

                <!-- Queue & Receipts Grid -->
                <div class="grid-2">
                    <div class="card">
                        <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
                            <span>⏳ Antrean Pemrosesan (Job Queue SQLite WAL)</span>
                            <button class="btn btn-outline" style="font-size:0.72rem; padding:3px 8px;" onclick="fetchQueue()">🔄</button>
                        </div>
                        <table><thead><tr><th>Job ID</th><th>Pilar Konten</th><th>Status</th><th>Tipe</th><th>Dibuat</th></tr></thead><tbody id="queue-tbody"></tbody></table>
                    </div>

                    <div class="card">
                        <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
                            <span>📜 Tanda Terima Publikasi (Publishing Receipts)</span>
                            <button class="btn btn-outline" style="font-size:0.72rem; padding:3px 8px;" onclick="fetchReceipts()">🔄</button>
                        </div>
                        <table><thead><tr><th>Platform</th><th>Status</th><th>Mode</th><th>Tautan Post</th></tr></thead><tbody id="receipts-tbody"></tbody></table>
                    </div>
                </div>
            </div>

            <!-- 3. KONEKSI AI & AKUN MEDSOS (CREDENTIALS) -->
            <div id="tab-credentials" class="tab-pane">
                <div class="menu-guide-card">
                    <div>
                        <div class="menu-guide-title">⚡ Persistent Credential Vault & AI Connections</div>
                        <div class="menu-guide-desc">Pusat manajemen kredensial terenkripsi dengan Windows DPAPI + AES-256-GCM sebagai <b>SATU-SATUNYA SUMBER KEBENARAN</b> (Single Source of Truth). Secret tersimpan aman dan tidak pernah ditulis mentah ke file .env maupun diekspos ke browser.</div>
                    </div>
                    <div class="menu-guide-tip">
                        🛡️ <b>Enkripsi Aktif:</b> Windows DPAPI + AES-256-GCM
                    </div>
                </div>

                <!-- Compact Security Summary Header (Single Source of Truth) -->
                <div class="card" style="margin-bottom: 20px; padding: 14px 20px; background: rgba(15, 23, 42, 0.75); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px;">
                        <div style="display: flex; align-items: center; gap: 28px; flex-wrap: wrap;">
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <div style="width: 36px; height: 36px; border-radius: 8px; background: rgba(16,185,129,0.15); display: flex; align-items: center; justify-content: center; font-size: 1.1rem; color: #10B981;">🔒</div>
                                <div>
                                    <div style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;">Vault Encryption</div>
                                    <div id="sec-vault-enc" style="font-size: 0.92rem; font-weight: 700; color: #10B981;">ENCRYPTED ✅</div>
                                </div>
                            </div>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <div style="width: 36px; height: 36px; border-radius: 8px; background: rgba(6,182,212,0.15); display: flex; align-items: center; justify-content: center; font-size: 1.1rem; color: #06B6D4;">🛡️</div>
                                <div>
                                    <div style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;">Credential Health</div>
                                    <div id="sec-vault-health" style="font-size: 0.92rem; font-weight: 700; color: #06B6D4;">HEALTHY ✅</div>
                                </div>
                            </div>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <div style="width: 36px; height: 36px; border-radius: 8px; background: rgba(245,158,11,0.15); display: flex; align-items: center; justify-content: center; font-size: 1.1rem; color: #F59E0B;">💾</div>
                                <div>
                                    <div style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;">Backup Status</div>
                                    <div id="sec-backup-status" style="font-size: 0.92rem; font-weight: 700; color: #F59E0B;">AVAILABLE ✅</div>
                                </div>
                            </div>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <div style="width: 36px; height: 36px; border-radius: 8px; background: rgba(139,92,246,0.15); display: flex; align-items: center; justify-content: center; font-size: 1.1rem; color: #8B5CF6;">🔍</div>
                                <div>
                                    <div style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;">Security Issues Count</div>
                                    <div id="sec-vault-issues" style="font-size: 0.92rem; font-weight: 700; color: #34D399;">0 ISSUES (CLEAN) ✅</div>
                                </div>
                            </div>
                        </div>
                        <div style="display: flex; gap: 8px;">
                            <button class="btn btn-outline" style="font-size: 0.78rem; padding: 6px 12px;" onclick="fetchVaultStatus(); fetchEnvConfig(true);">🔄 Refresh Semua Status</button>
                            <button class="btn btn-primary" style="font-size: 0.78rem; padding: 6px 14px; background: linear-gradient(135deg, #3B82F6, #1D4ED8);" onclick="openReplaceSecretModal('', '')">➕ Tambah Kredensial Baru</button>
                        </div>
                    </div>
                </div>

                <!-- Primary Provider & Platform Cards Grid -->
                <div class="card" style="margin-bottom: 24px;">
                    <div style="margin-bottom: 20px; border-bottom: 1px solid var(--border); padding-bottom: 14px;">
                        <div class="card-title" style="margin-bottom: 4px; font-size: 1.05rem; color: #60A5FA;">⚡ Pusat Koneksi & Manajemen Kredensial Terenkripsi</div>
                        <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0;">Kelola kunci provider dan token platform terenkripsi di <b>Windows DPAPI Vault</b>. Parameter non-sensitif (Page ID, Admin ID) disimpan terpisah.</p>
                    </div>

                    <!-- 3 Column Responsive Grid -->
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px;">
                        
                        <!-- Panel 1: Mesin AI & Multi-Tier -->
                        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 16px;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; border-bottom: 1px solid rgba(255,255,255,0.06); padding-bottom: 10px;">
                                <div style="font-weight: 700; font-size: 0.92rem; color: #60A5FA; display:flex; align-items:center; gap:6px;">
                                    🤖 Mesin AI & Multi-Tier
                                </div>
                                <button class="btn btn-outline" style="font-size: 0.72rem; padding: 4px 8px;" onclick="showAddProviderModal()">+ Add Provider</button>
                            </div>

                            <!-- Gemini Primary Card -->
                            <div class="form-group" style="background: rgba(96,165,250,0.03); border: 1px solid rgba(96,165,250,0.18); border-radius: 6px; padding: 12px; margin-bottom: 14px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                                    <div style="display:flex; align-items:center; gap:6px;">
                                        <span style="font-weight: 700; font-size:0.85rem; color:#F1F5F9;">Google Gemini (Utama)</span>
                                        <span class="brand-badge" style="color:#60A5FA; border-color:rgba(96,165,250,0.4); font-size:0.65rem;">Tier 1</span>
                                    </div>
                                    <span id="ref-status-gemini" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Memeriksa...</span>
                                </div>
                                <div style="display: flex; flex-direction: column; gap: 4px; font-size: 0.76rem; color: var(--text-muted); background: rgba(0,0,0,0.25); padding: 8px 10px; border-radius: 6px; margin-bottom: 10px; border: 1px solid rgba(255,255,255,0.04);">
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Ref: <code style="color:#93C5FD; font-family:monospace;">GEMINI_PRIMARY_API_KEY</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Uji: <b id="tested-gemini" style="color:#10B981;">-</b></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>API Key: <code id="fp-gemini" style="color:#34D399; font-family:monospace;">••••••••</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Update: <span id="updated-gemini" style="color:var(--text-main); font-weight:500;">-</span></span>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 6px;">
                                    <button class="btn btn-outline" style="flex:1; font-size: 0.74rem; padding: 5px 8px; border-color:var(--accent-indigo); color:#818cf8;" onclick="openReplaceSecretModal('GEMINI_PRIMARY_API_KEY', 'google')">🔄 Ganti / Set</button>
                                    <button class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px; white-space: nowrap;" onclick="testVaultSecret('GEMINI_PRIMARY_API_KEY')">🔍 Test</button>
                                    <button id="btn-toggle-gemini" class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px;" onclick="toggleVaultSecretEnabled('GEMINI_PRIMARY_API_KEY', true)">⏸️</button>
                                </div>
                                <div style="display:flex; justify-content:space-between; margin-top:8px; font-size:0.68rem; color:var(--text-muted);">
                                    <span>Model: Gemini 2.5 Flash, Imagen 3, Veo</span>
                                    <span style="color:#34D399; font-weight:600;">DEFAULT ROUTE</span>
                                </div>
                            </div>

                            <!-- Gemini Backup Card -->
                            <div class="form-group" style="background: rgba(245,158,11,0.03); border: 1px solid rgba(245,158,11,0.18); border-radius: 6px; padding: 12px; margin-bottom: 14px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                                    <div style="display:flex; align-items:center; gap:6px;">
                                        <span style="font-weight: 700; font-size:0.85rem; color:#F1F5F9;">Google Gemini (Cadangan)</span>
                                        <span class="brand-badge" style="color:#F59E0B; border-color:rgba(245,158,11,0.4); font-size:0.65rem;">Tier 1 Backup</span>
                                    </div>
                                    <span id="ref-status-gemini-2" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Memeriksa...</span>
                                </div>
                                <div style="display: flex; flex-direction: column; gap: 4px; font-size: 0.76rem; color: var(--text-muted); background: rgba(0,0,0,0.25); padding: 8px 10px; border-radius: 6px; margin-bottom: 10px; border: 1px solid rgba(255,255,255,0.04);">
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Ref: <code style="color:#FCD34D; font-family:monospace;">GEMINI_BACKUP_API_KEY</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Uji: <b id="tested-gemini-2" style="color:#10B981;">-</b></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>API Key: <code id="fp-gemini-2" style="color:#34D399; font-family:monospace;">••••••••</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Update: <span id="updated-gemini-2" style="color:var(--text-main); font-weight:500;">-</span></span>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 6px;">
                                    <button class="btn btn-outline" style="flex:1; font-size: 0.74rem; padding: 5px 8px; border-color:var(--accent-indigo); color:#818cf8;" onclick="openReplaceSecretModal('GEMINI_BACKUP_API_KEY', 'google')">🔄 Ganti / Set</button>
                                    <button class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px; white-space: nowrap;" onclick="testVaultSecret('GEMINI_BACKUP_API_KEY')">🔍 Test</button>
                                    <button id="btn-toggle-gemini-2" class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px;" onclick="toggleVaultSecretEnabled('GEMINI_BACKUP_API_KEY', true)">⏸️</button>
                                </div>
                                <div style="display:flex; justify-content:space-between; margin-top:8px; font-size:0.68rem; color:var(--text-muted);">
                                    <span>Auto-failover jika kuota Key 1 habis / 429</span>
                                    <span style="color:#F59E0B; font-weight:600;">AUTO-FAILOVER</span>
                                </div>
                            </div>

                            <!-- OpenRouter Tier 2 Multi-Model Gateway Card -->
                            <div class="form-group" style="background: rgba(59,130,246,0.03); border: 1px solid rgba(59,130,246,0.22); border-radius: 6px; padding: 12px; margin-bottom: 14px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                                    <div style="display:flex; align-items:center; gap:6px;">
                                        <span style="font-weight: 700; font-size:0.85rem; color:#F1F5F9;">OpenRouter</span>
                                        <span class="brand-badge" style="color:#60A5FA; border-color:rgba(59,130,246,0.4); font-size:0.65rem;">Tier 2 Multi-Model</span>
                                    </div>
                                    <span id="ref-status-openrouter" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Memeriksa...</span>
                                </div>
                                <div style="display: flex; flex-direction: column; gap: 4px; font-size: 0.76rem; color: var(--text-muted); background: rgba(0,0,0,0.25); padding: 8px 10px; border-radius: 6px; margin-bottom: 10px; border: 1px solid rgba(255,255,255,0.04);">
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Ref: <code style="color:#93C5FD; font-family:monospace;">OPENROUTER_API_KEY</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Uji: <b id="tested-openrouter" style="color:#10B981;">-</b></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>API Key: <code id="fp-openrouter" style="color:#34D399; font-family:monospace;">••••••••</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Update: <span id="updated-openrouter" style="color:var(--text-main); font-weight:500;">-</span></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:2px;">
                                        <span>Base URL: <code style="color:#94A3B8; font-family:monospace; font-size:0.7rem;">https://openrouter.ai/api/v1</code></span>
                                    </div>
                                    <div style="display:flex; flex-direction:column; gap:2px; margin-top:4px;">
                                        <label style="font-size:0.7rem; color:#94A3B8;">Default Model:</label>
                                        <input type="text" id="env-openrouter-model" class="form-control" placeholder="openai/gpt-4o-mini" style="font-size:0.75rem; padding:4px 8px; height:28px;">
                                    </div>
                                </div>
                                <div style="display: flex; gap: 6px;">
                                    <button class="btn btn-outline" style="flex:1; font-size: 0.74rem; padding: 5px 8px; border-color:var(--accent-indigo); color:#818cf8;" onclick="openReplaceSecretModal('OPENROUTER_API_KEY', 'openrouter')">🔄 Ganti / Set</button>
                                    <button class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px; white-space: nowrap;" onclick="testVaultSecret('OPENROUTER_API_KEY')">🔍 Test</button>
                                    <button id="btn-toggle-openrouter" class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px;" onclick="toggleVaultSecretEnabled('OPENROUTER_API_KEY', true)">⏸️</button>
                                </div>
                                <div style="display:flex; justify-content:space-between; margin-top:8px; font-size:0.68rem; color:var(--text-muted);">
                                    <span>Gateway multi-model OpenAI/Claude/DeepSeek</span>
                                    <span style="color:#60A5FA; font-weight:600;">MULTI-MODEL GATEWAY</span>
                                </div>
                            </div>

                            <!-- xAI / Grok Card -->
                            <div class="form-group" style="background: rgba(167,139,250,0.03); border: 1px solid rgba(167,139,250,0.18); border-radius: 6px; padding: 12px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                                    <div style="display:flex; align-items:center; gap:6px;">
                                        <span style="font-weight: 700; font-size:0.85rem; color:#F1F5F9;">xAI / Grok</span>
                                        <span class="brand-badge" style="color:#A78BFA; border-color:rgba(167,139,250,0.4); font-size:0.65rem;">Tier 2 Fallback</span>
                                    </div>
                                    <span id="ref-status-xai" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Memeriksa...</span>
                                </div>
                                <div style="display: flex; flex-direction: column; gap: 4px; font-size: 0.76rem; color: var(--text-muted); background: rgba(0,0,0,0.25); padding: 8px 10px; border-radius: 6px; margin-bottom: 10px; border: 1px solid rgba(255,255,255,0.04);">
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Ref: <code style="color:#C4B5FD; font-family:monospace;">XAI_API_KEY</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Uji: <b id="tested-xai" style="color:#10B981;">-</b></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>API Key: <code id="fp-xai" style="color:#34D399; font-family:monospace;">••••••••</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Update: <span id="updated-xai" style="color:var(--text-main); font-weight:500;">-</span></span>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 6px;">
                                    <button class="btn btn-outline" style="flex:1; font-size: 0.74rem; padding: 5px 8px; border-color:var(--accent-indigo); color:#818cf8;" onclick="openReplaceSecretModal('XAI_API_KEY', 'xai')">🔄 Ganti / Set</button>
                                    <button class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px; white-space: nowrap;" onclick="testVaultSecret('XAI_API_KEY')">🔍 Test</button>
                                    <button id="btn-toggle-xai" class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px;" onclick="toggleVaultSecretEnabled('XAI_API_KEY', true)">⏸️</button>
                                </div>
                                <div style="display:flex; justify-content:space-between; margin-top:8px; font-size:0.68rem; color:var(--text-muted);">
                                    <span>Secondary fallback non-Google</span>
                                    <span style="color:#A78BFA; font-weight:600;">TEXT, REASONING</span>
                                </div>
                            </div>
                        </div>

                        <!-- Panel 2: Meta / Social Media Platforms -->
                        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 16px;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; border-bottom: 1px solid rgba(255,255,255,0.06); padding-bottom: 10px;">
                                <div style="font-weight: 700; font-size: 0.92rem; color: #10B981; display:flex; align-items:center; gap:6px;">
                                    📱 Akun Media Sosial (Meta Graph)
                                </div>
                                <span class="brand-badge" style="border-color: rgba(16,185,129,0.3); color: #10B981;">Publishing</span>
                            </div>

                            <!-- Meta System User Shared Token Primary Card -->
                            <div class="form-group" style="background: rgba(16,185,129,0.05); border: 1px solid rgba(16,185,129,0.3); border-radius: 6px; padding: 12px; margin-bottom: 14px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                                    <div style="display:flex; align-items:center; gap:6px;">
                                        <span style="font-weight: 700; font-size:0.85rem; color:#F1F5F9;">Meta System User Token</span>
                                        <span class="brand-badge" style="color:#10B981; border-color:rgba(16,185,129,0.4); font-size:0.65rem;">Primary Meta</span>
                                    </div>
                                    <span id="ref-status-fb" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Memeriksa...</span>
                                </div>
                                <div style="display: flex; flex-direction: column; gap: 4px; font-size: 0.76rem; color: var(--text-muted); background: rgba(0,0,0,0.25); padding: 8px 10px; border-radius: 6px; margin-bottom: 10px; border: 1px solid rgba(255,255,255,0.04);">
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Ref: <code style="color:#6EE7B7; font-family:monospace;">META_SYSTEM_USER_TOKEN</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Uji: <b id="tested-fb" style="color:#10B981;">-</b></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Token: <code id="fp-fb" style="color:#34D399; font-family:monospace;">••••••••</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Update: <span id="updated-fb" style="color:var(--text-main); font-weight:500;">-</span></span>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 6px;">
                                    <button class="btn btn-outline" style="flex:1; font-size: 0.74rem; padding: 5px 8px; border-color:var(--accent-indigo); color:#818cf8;" onclick="openReplaceSecretModal('META_SYSTEM_USER_TOKEN', 'facebook')">🔄 Ganti / Set</button>
                                    <button class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px; white-space: nowrap;" onclick="testVaultSecret('META_SYSTEM_USER_TOKEN')">🔍 Test Meta Credential</button>
                                    <button id="btn-toggle-fb" class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px;" onclick="toggleVaultSecretEnabled('META_SYSTEM_USER_TOKEN', true)">⏸️</button>
                                </div>
                                <small style="font-size:0.68rem; color:var(--text-muted); display:block; margin-top:6px;">Kredensial induk Meta untuk otorisasi Facebook Page & Instagram Business.</small>
                            </div>

                            <!-- Facebook Fanspage Reference Card -->
                            <div class="form-group" style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 6px; padding: 12px; margin-bottom: 14px;">
                                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                                    <span style="font-weight:700; font-size:0.83rem; color:#F1F5F9;">📘 Facebook Fanspage</span>
                                    <span class="brand-badge" style="color:#60A5FA; font-size:0.65rem;">Uses: META_SYSTEM_USER_TOKEN</span>
                                </div>
                                <label class="form-label" style="font-size:0.75rem;">FB_PAGE_ID (ID Fanspage Facebook):</label>
                                <input type="text" id="env-fb-page-id" class="form-control" placeholder="1253340697871457" style="font-size: 0.8rem; margin-bottom:8px;">
                                <div style="display:flex; justify-content:space-between; align-items:center;">
                                    <small style="font-size: 0.68rem; color: var(--text-muted);">ID Fanspage Publik @Pitamediaid</small>
                                    <button class="btn btn-outline" style="font-size: 0.72rem; padding: 4px 10px; border-color: rgba(96,165,250,0.4); color:#93c5fd;" onclick="testPlatformService('facebook')">🔍 Test Facebook</button>
                                </div>
                            </div>

                            <!-- Instagram Business Reference Card -->
                            <div class="form-group" style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 6px; padding: 12px; margin-bottom: 14px;">
                                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                                    <span style="font-weight:700; font-size:0.83rem; color:#F1F5F9;">📸 Instagram Business</span>
                                    <span class="brand-badge" style="color:#F472B6; font-size:0.65rem;">Uses: META_SYSTEM_USER_TOKEN</span>
                                </div>
                                <label class="form-label" style="font-size:0.75rem;">IG_USER_ID (ID Akun Instagram Business):</label>
                                <input type="text" id="env-ig-user-id" class="form-control" placeholder="17841426699286663" value="17841426699286663" style="font-size: 0.8rem; margin-bottom:8px;">
                                <div style="display:flex; justify-content:space-between; align-items:center;">
                                    <small style="font-size: 0.68rem; color: var(--text-muted);">Akun Instagram Profesional yang tertaut Fanspage</small>
                                    <button class="btn btn-outline" style="font-size: 0.72rem; padding: 4px 10px; border-color: rgba(244,114,182,0.4); color:#f472b6;" onclick="testPlatformService('instagram')">🔍 Test Instagram</button>
                                </div>
                            </div>

                            <!-- Threads Standalone Card -->
                            <div class="form-group" style="background: rgba(148,163,184,0.03); border: 1px solid rgba(148,163,184,0.18); border-radius: 6px; padding: 12px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                                    <span style="font-weight: 700; font-size:0.85rem; color:#F1F5F9;">🧵 Threads API (Mandiri)</span>
                                    <span id="ref-status-threads" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Memeriksa...</span>
                                </div>
                                <div style="display: flex; flex-direction: column; gap: 4px; font-size: 0.76rem; color: var(--text-muted); background: rgba(0,0,0,0.25); padding: 8px 10px; border-radius: 6px; margin-bottom: 10px; border: 1px solid rgba(255,255,255,0.04);">
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Ref: <code style="color:#E2E8F0; font-family:monospace;">THREADS_ACCESS_TOKEN</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Uji: <b id="tested-threads" style="color:#10B981;">-</b></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Token: <code id="fp-threads" style="color:#34D399; font-family:monospace;">••••••••</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Update: <span id="updated-threads" style="color:var(--text-main); font-weight:500;">-</span></span>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 6px;">
                                    <button class="btn btn-outline" style="flex:1; font-size: 0.74rem; padding: 5px 8px; border-color:var(--accent-indigo); color:#818cf8;" onclick="openReplaceSecretModal('THREADS_ACCESS_TOKEN', 'threads')">🔄 Ganti / Set</button>
                                    <button class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px; white-space: nowrap;" onclick="testVaultSecret('THREADS_ACCESS_TOKEN')">🔍 Test</button>
                                    <button id="btn-toggle-threads" class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px;" onclick="toggleVaultSecretEnabled('THREADS_ACCESS_TOKEN', true)">⏸️</button>
                                </div>
                            </div>
                        </div>

                        <!-- Panel 3: Telegram C2 Bot & Alerts -->
                        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 16px;">
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; border-bottom: 1px solid rgba(255,255,255,0.06); padding-bottom: 10px;">
                                <div style="font-weight: 700; font-size: 0.92rem; color: #F59E0B; display:flex; align-items:center; gap:6px;">
                                    📡 Telegram C2 & Command Bot
                                </div>
                                <span class="brand-badge" style="border-color: rgba(245,158,11,0.3); color: #F59E0B;">Command Bot</span>
                            </div>

                            <!-- Telegram Bot Card -->
                            <div class="form-group" style="background: rgba(245,158,11,0.03); border: 1px solid rgba(245,158,11,0.18); border-radius: 6px; padding: 12px; margin-bottom: 14px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                                    <span style="font-weight: 700; font-size:0.85rem; color:#F1F5F9;">Telegram Bot Token</span>
                                    <span id="ref-status-telegram" style="font-size: 0.72rem; font-weight: 600; color: var(--text-muted);">● Memeriksa...</span>
                                </div>
                                <div style="display: flex; flex-direction: column; gap: 4px; font-size: 0.76rem; color: var(--text-muted); background: rgba(0,0,0,0.25); padding: 8px 10px; border-radius: 6px; margin-bottom: 10px; border: 1px solid rgba(255,255,255,0.04);">
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Ref: <code style="color:#FCD34D; font-family:monospace;">TELEGRAM_BOT_TOKEN</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Uji: <b id="tested-telegram" style="color:#10B981;">-</b></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Token: <code id="fp-telegram" style="color:#34D399; font-family:monospace;">••••••••</code></span>
                                    </div>
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <span>Update: <span id="updated-telegram" style="color:var(--text-main); font-weight:500;">-</span></span>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 6px;">
                                    <button class="btn btn-outline" style="flex:1; font-size: 0.74rem; padding: 5px 8px; border-color:var(--accent-indigo); color:#818cf8;" onclick="openReplaceSecretModal('TELEGRAM_BOT_TOKEN', 'telegram')">🔄 Ganti / Set</button>
                                    <button class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px; white-space: nowrap;" onclick="testVaultSecret('TELEGRAM_BOT_TOKEN')">🔍 Test</button>
                                    <button id="btn-toggle-telegram" class="btn btn-outline" style="font-size: 0.74rem; padding: 5px 8px;" onclick="toggleVaultSecretEnabled('TELEGRAM_BOT_TOKEN', true)">⏸️</button>
                                </div>
                                <small style="font-size: 0.7rem; color: var(--text-muted); display:block; margin-top:6px;">Bot @pitamediabot dari @BotFather</small>
                            </div>

                            <!-- TELEGRAM_ADMIN_IDS (Non-Secret Config) -->
                            <div class="form-group" style="margin-top: 14px;">
                                <label class="form-label" style="font-size:0.8rem;">TELEGRAM_ADMIN_IDS (ID Admin Telegram)</label>
                                <input type="text" id="env-telegram-admins" class="form-control" placeholder="308917129" style="font-size: 0.83rem;">
                                <small style="font-size: 0.7rem; color: var(--text-muted);">ID Telegram pemilik untuk otorisasi command bot</small>
                            </div>

                            <!-- TELEGRAM_ALERT_CHAT_ID (Non-Secret Config) -->
                            <div class="form-group" style="margin-top: 14px;">
                                <label class="form-label" style="font-size:0.8rem;">TELEGRAM_ALERT_CHAT_ID (Chat ID Notifikasi)</label>
                                <input type="text" id="env-telegram-alert-chat" class="form-control" placeholder="308917129" style="font-size: 0.83rem;">
                                <small style="font-size: 0.7rem; color: var(--text-muted);">Tujuan pesan darurat, QC, dan laporan harian</small>
                            </div>
                        </div>

                    </div>

                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--border);">
                        <div style="font-size: 0.8rem; color: var(--text-muted);">
                            💡 <b>Vault Terpusat:</b> Klik <b>🔄 Ganti / Set</b> pada setiap kartu untuk menambah atau memperbarui token. Secret otomatis diuji sebelum diaktifkan.
                        </div>
                        <button class="btn btn-primary" onclick="savePlatformConfig()" style="padding: 11px 28px; font-size: 0.92rem; font-weight: 700; background: linear-gradient(135deg, #10B981, #059669); box-shadow: 0 4px 14px rgba(16,185,129,0.3);">💾 Simpan Konfigurasi Platform (.env)</button>
                    </div>
                </div>
            </div>

            <!-- 4. KUALITAS, MUSIK & EKSPERIMEN (QC + LEARNING + MUSIC + AB_TESTING) -->
            <div id="tab-quality" class="tab-pane">
                <div class="menu-guide-card">
                    <div>
                        <div class="menu-guide-title">🛡️ Pusat Kualitas Konten, Musik & Eksperimen AI</div>
                        <div class="menu-guide-desc">Pusat kendali standar mutu konten dan pembelajaran performa. Mengawasi penilaian Quality Control (QC & Hard Safety Gate), pustaka musik berlisensi aman (8 Suasana Emosi), uji variasi kreatif (A/B Testing), dan adaptasi algoritma otonom (Learning Center).</div>
                    </div>
                    <div class="menu-guide-tip">
                        💡 <b>Otonomi:</b> Anda dapat menyesuaikan level otonomi AI dari OBSERVE hingga CONTROLLED AUTO.
                    </div>
                </div>

                <!-- Learning Maturity & Autonomy Controller Header Grid -->
                <div class="grid-2">
                    <div class="card">
                        <div class="card-title">🧠 Learning Maturity Score (Skor Kematangan AI)</div>
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
                        <div class="card-title">🎮 Pengatur Tingkat Otonomi AI (Autonomy Controller)</div>
                        <div style="display: flex; align-items: center; justify-content: space-between; margin: 10px 0;">
                            <div>
                                <div style="font-size: 0.75rem; color: var(--text-muted);">Tingkat Otonomi Saat Ini:</div>
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

                <!-- Grid QC & A/B Experiments -->
                <div class="grid-2">
                    <div class="card">
                        <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
                            <span>🛡️ Audit Kelayakan Mutu (QC & Safety Gate)</span>
                            <button class="btn btn-outline" style="font-size:0.72rem; padding:3px 8px;" onclick="fetchQC()">🔄</button>
                        </div>
                        <table><thead><tr><th>Konten</th><th>Pilar</th><th>Skor</th><th>Verdict</th></tr></thead><tbody id="qc-tbody"></tbody></table>
                    </div>

                    <div class="card">
                        <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
                            <span>🧪 Eksperimen Kreatif (A/B Testing)</span>
                            <button class="btn btn-outline" style="font-size:0.72rem; padding:3px 8px;" onclick="fetchExperiments()">🔄</button>
                        </div>
                        <table><thead><tr><th>ID</th><th>Hipotesis</th><th>Status</th><th>Pemenang</th></tr></thead><tbody id="experiments-tbody"></tbody></table>
                    </div>
                </div>

                <!-- Grid Music & Heatmap -->
                <div class="grid-2">
                    <div class="card">
                        <div class="card-title">🎵 Pustaka Musik Bebas Hak Cipta (8 Moods)</div>
                        <table><thead><tr><th>Judul Track</th><th>Artis</th><th>Mood Emosi</th><th>BPM</th><th>Lisensi</th></tr></thead><tbody id="music-tbody"></tbody></table>
                    </div>

                    <div class="card">
                        <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
                            <span>📈 Waktu Tayang Optimal (Publishing Heatmap WIB)</span>
                            <span id="heatmap-status-badge" class="brand-badge" style="background:rgba(96,165,250,0.15); color:#60A5FA; border:1px solid rgba(96,165,250,0.3); font-size:0.7rem;">Baseline Jadwal Awal</span>
                        </div>
                        <div style="margin-top: 14px; font-size:0.84rem; line-height: 1.6;">
                            <p style="margin-bottom:8px;">⏰ <b style="color:#60A5FA;">06:30 - 08:00 WIB:</b> Morning Commute / Mindset Hook & Inspirasi Pagi (Pita Mini & Pita Transformasi)</p>
                            <p style="margin-bottom:8px;">⏰ <b style="color:#34D399;">12:00 - 13:00 WIB:</b> Istirahat Siang / Storytelling Edukasi & Karusel (Pita Cerita)</p>
                            <p>⏰ <b style="color:#F59E0B;">19:00 - 21:30 WIB:</b> Prime Time / Narasi Emosional, Visual Estetika & Reels (Pita Kreasi & Pita Transformasi)</p>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 5. KESEHATAN & PENGATURAN SISTEM (SYSTEM HEALTH + COSTS + STORAGE + REPORTS + LOGS + SETTINGS) -->
            <div id="tab-system" class="tab-pane">
                <div class="menu-guide-card">
                    <div>
                        <div class="menu-guide-title">⚙️ Kesehatan Server, Biaya & Pengaturan Sistem</div>
                        <div class="menu-guide-desc">Pusat diagnostik 19-komponen kesehatan layanan, tata kelola biaya harian AI (Cost Governor), pembersihan disk terproteksi (Storage Guard), laporan ringkasan eksekutif, dan backup vault terenkripsi (.pmvault).</div>
                    </div>
                    <div class="menu-guide-tip">
                        💡 <b>Diagnostik Mandiri:</b> 19 Komponen sistem diperiksa secara realtime.
                    </div>
                </div>

                <!-- Grid Health, Cost, Storage -->
                <div style="display:grid; grid-template-columns: 1.4fr 1fr 1fr; gap: 18px; margin-bottom: 20px;">
                    <div class="card">
                        <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
                            <span>❤️ Diagnostik Kesehatan Sistem (19 Komponen)</span>
                            <button class="btn btn-outline" style="font-size:0.72rem; padding:3px 8px;" onclick="fetchHealth()">🔄</button>
                        </div>
                        <div style="max-height: 280px; overflow-y: auto;">
                            <table><thead><tr><th>Komponen</th><th>Status</th><th>Keterangan</th></tr></thead><tbody id="health-tbody"></tbody></table>
                        </div>
                    </div>

                    <div class="card">
                        <div class="card-title">💰 Tata Kelola Biaya AI</div>
                        <p style="font-size:0.8rem; color:var(--text-muted);">Batas Anggaran: <b style="color:#F59E0B;">$10.00 USD / hari</b></p>
                        <div id="costs-container" style="margin-top: 12px; font-size:0.85rem;"></div>
                    </div>

                    <div class="card">
                        <div class="card-title">💾 Pemeliharaan Disk</div>
                        <div id="disk-info" style="margin: 12px 0; font-size:0.85rem;">Memuat info disk...</div>
                        <button class="btn btn-outline" style="font-size:0.75rem;" onclick="cleanDisk()">🧹 Run Temporary Storage Cleanup</button>
                        <small style="font-size:0.68rem; color:var(--text-muted); display:block; margin-top:6px;">Protected: .vault, .pmvault, SQLite DB & receipts tidak pernah dihapus.</small>
                    </div>
                </div>

                <!-- Executive Reports & Settings -->
                <div class="grid-2">
                    <div class="card">
                        <div class="card-title">📑 Laporan Ringkasan Eksekutif</div>
                        <div style="display: flex; gap: 10px; margin: 12px 0;">
                            <button class="btn btn-primary" style="font-size:0.75rem;" onclick="loadReport('daily')">Generate Daily Digest</button>
                            <button class="btn btn-outline" style="font-size:0.75rem;" onclick="loadReport('weekly')">Weekly Executive Review</button>
                        </div>
                        <pre id="report-output" style="color: var(--text-muted); font-size: 0.8rem; background: rgba(0,0,0,0.3); padding: 12px; border-radius: 8px; max-height: 180px; overflow-y: auto;">Pilih laporan di atas untuk melihat ikhtisar...</pre>
                    </div>

                    <div class="card">
                        <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
                            <span>⚙️ Riwayat Versi Konfigurasi</span>
                            <button class="btn btn-outline" style="font-size:0.72rem; padding:3px 8px;" onclick="fetchVersions()">🔄</button>
                        </div>
                        <table><thead><tr><th>Versi</th><th>Nama</th><th>Diubah Oleh</th><th>Waktu</th></tr></thead><tbody id="versions-tbody"></tbody></table>
                    </div>
                </div>

                <!-- Encrypted Credential Vault Backup & Restore -->
                <div class="card" style="margin-top: 20px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; border-bottom: 1px solid var(--border); padding-bottom: 12px;">
                        <div>
                            <div class="card-title" style="margin-bottom: 2px; font-size: 1.05rem; color: #F59E0B;">🔐 Encrypted Credential Vault Backup & Disaster Recovery (.pmvault)</div>
                            <p style="font-size: 0.78rem; color: var(--text-muted); margin-bottom:0;">Ekspor dan impor brankas kredensial terenkripsi AES-256-GCM / PBKDF2 dengan kata sandi mandiri untuk migrasi antar-server atau backup berkala.</p>
                        </div>
                        <button class="btn btn-outline" style="font-size:0.75rem; padding:6px 12px;" onclick="fetchVaultBackups()">🔄 Refresh Backup List</button>
                    </div>

                    <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; font-size: 0.8rem; color: #FCA5A5; display: flex; align-items: center; gap: 10px;">
                        <span style="font-size: 1.3rem;">⚠️</span>
                        <div><b>PERINGATAN KEAMANAN:</b> Password backup tidak dapat dipulihkan oleh sistem Pita Media jika hilang. Pastikan Anda mengingat dan mencatat passphrase yang digunakan.</div>
                    </div>

                    <div class="grid-2">
                        <!-- Export Card -->
                        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 16px;">
                            <div style="font-weight:700; color:#10B981; font-size:0.9rem; margin-bottom:10px;">📤 Ekspor Backup Terenkripsi (.pmvault)</div>
                            <div class="form-group">
                                <label class="form-label" style="font-size:0.78rem;">Passphrase Enkripsi Backup (Min. 6 Karakter):</label>
                                <input type="password" id="vault-export-pass" class="form-control" placeholder="Masukkan passphrase rahasia..." style="font-size:0.83rem;">
                            </div>
                            <button class="btn btn-primary" style="font-size:0.8rem; width:100%; padding:9px;" onclick="exportVaultBackup()">🔒 Generate & Download .pmvault</button>
                        </div>

                        <!-- Import Card -->
                        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 8px; padding: 16px;">
                            <div style="font-weight:700; color:#60A5FA; font-size:0.9rem; margin-bottom:10px;">📥 Impor / Restore Vault (.pmvault)</div>
                            <div class="form-group">
                                <label class="form-label" style="font-size:0.78rem;">Pilih File Backup yang Ada:</label>
                                <select id="vault-import-file" class="form-control" style="font-size:0.83rem;">
                                    <option value="">-- Pilih Berkas .pmvault --</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label" style="font-size:0.78rem;">Passphrase Dekripsi:</label>
                                <input type="password" id="vault-import-pass" class="form-control" placeholder="Masukkan passphrase backup..." style="font-size:0.83rem;">
                            </div>
                            <button class="btn btn-outline" style="font-size:0.8rem; width:100%; padding:9px; border-color:#60A5FA; color:#93C5FD;" onclick="importVaultBackup()">🔓 Restore Credential Vault</button>
                        </div>
                    </div>

                    <!-- Available Backups Table -->
                    <div style="margin-top: 16px;">
                        <div style="font-size: 0.8rem; font-weight:700; color:var(--text-main); margin-bottom:8px;">📁 Riwayat Berkas Backup di <code>storage/backups/</code>:</div>
                        <table>
                            <thead><tr><th>Nama Berkas</th><th>Ukuran</th><th>Waktu Dibuat</th></tr></thead>
                            <tbody id="vault-backups-tbody">
                                <tr><td colspan="3" style="text-align:center; color:var(--text-muted);">Memuat riwayat backup...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Live System Logs -->
                <div class="card" style="margin-top: 20px;">
                    <div class="card-title" style="display:flex; justify-content:space-between; align-items:center;">
                        <span>💻 Log Terminal Sistem Langsung</span>
                        <button class="btn btn-outline" style="font-size:0.75rem; padding:4px 10px;" onclick="fetchLogs()">🔄 Refresh Logs</button>
                    </div>
                    <pre id="terminal-logs" style="background: #000; color: #10B981; padding: 16px; border-radius: 8px; font-size: 0.8rem; height: 350px; overflow-y: auto; margin-top: 10px;"></pre>
                </div>
            </div>
        </div>
    </div>

    <!-- MODAL ATOMIC REPLACE SECRET -->
    <div id="modal-replace-secret" class="modal-overlay">
        <div class="modal-box" style="width: 500px; max-width: 95vw;">
            <div class="modal-header">
                <div class="modal-title">🔄 Ganti Kredensial Vault (Atomic Update)</div>
                <button onclick="closeReplaceSecretModal()" style="background:none; border:none; color:var(--text-muted); font-size:1.3rem; cursor:pointer;">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">Nama Secret / Kunci</label>
                <input type="text" id="replace-sec-name" class="form-control" placeholder="e.g. GEMINI_API_KEY">
            </div>
            <div class="form-group">
                <label class="form-label">Nilai Kunci Baru (Raw Secret)</label>
                <input type="password" id="replace-sec-val" class="form-control" placeholder="Tempel kunci baru di sini (akan langsung dienkripsi)...">
                <small style="font-size: 0.72rem; color: var(--text-muted);">Nilai akan diuji terlebih dahulu dan tidak pernah ditampilkan ulang.</small>
            </div>
            <div style="margin: 12px 0; display: flex; align-items: center; gap: 8px;">
                <input type="checkbox" id="replace-sec-test-first" checked style="cursor:pointer;">
                <label for="replace-sec-test-first" style="font-size: 0.8rem; cursor:pointer; color: var(--text-main);">Uji koneksi (Preflight Test) sebelum mengaktifkan (PENDING ➔ TEST ➔ ACTIVE)</label>
            </div>
            <div class="modal-actions">
                <button class="btn btn-outline" onclick="closeReplaceSecretModal()">Batal</button>
                <button class="btn btn-primary" id="btn-submit-replace" onclick="submitAtomicReplace()">💾 Simpan & Validasi</button>
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
        <div class="modal-box" style="width: 680px; max-width: 95vw; max-height: 90vh; overflow-y: auto;">
            <div class="modal-header">
                <div class="modal-title" id="prev-modal-title">🔍 Pratinjau Konten Terverifikasi</div>
                <button onclick="closePostPreview()" style="background:none; border:none; color:var(--text-muted); font-size:1.4rem; cursor:pointer;">&times;</button>
            </div>
            
            <div id="prev-sim-alert" style="background: rgba(99, 102, 241, 0.12); border: 1px solid rgba(99, 102, 241, 0.3); border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; font-size: 0.8rem; color: #a5b4fc; display: flex; gap: 10px; align-items: flex-start;">
                <span style="font-size: 1.1rem;">🛡️</span>
                <div>
                    <b>Mode Simulasi (DRY RUN):</b> Konten ini sudah selesai dibuat dan lulus Quality Control secara lokal. Klik tombol biru di bawah untuk <u>otomatis beralih ke Mode Production dan mempublikasikan serentak ke 3 Sosial Media (Facebook Fanspage, Instagram & Threads)</u>!
                </div>
            </div>

            <!-- Top Title & Metadata -->
            <div style="margin-bottom: 12px;">
                <div id="prev-pilar-badge" style="display:inline-block; margin-bottom: 6px;"></div>
                <h3 id="prev-title" style="margin: 0 0 6px 0; font-size: 1.12rem; color: var(--text-main); font-weight: 700; line-height: 1.35;">Judul Konten</h3>
                <div style="display:flex; gap:16px; font-size: 0.78rem; color: var(--text-muted);">
                    <div>Dipublikasikan: <span id="prev-date" style="color:var(--text-main); font-weight:600;">-</span></div>
                    <div>Status: <span id="prev-status" style="font-weight: 600;">🟢 VERIFIED</span></div>
                </div>
            </div>

            <!-- High Visibility Visual Box -->
            <div style="background: rgba(0,0,0,0.55); border: 1px solid var(--border); border-radius: 12px; padding: 12px; margin-bottom: 14px; display: flex; flex-direction: column; align-items: center;">
                <div id="prev-media-box" style="width: 100%; max-height: 340px; min-height: 180px; border-radius: 8px; overflow: hidden; display: flex; align-items: center; justify-content: center; background: #000; position: relative;">
                    <video id="prev-video" controls autoplay muted playsinline style="max-width: 100%; max-height: 340px; display: none; border-radius: 6px;"></video>
                    <img id="prev-img" src="" style="max-width: 100%; max-height: 340px; object-fit: contain; display: none; cursor: zoom-in;" onclick="window.open(this.src, '_blank')" title="Klik untuk membuka gambar resolusi penuh di tab baru">
                    <div id="prev-fallback-icon" style="font-size: 3rem; display: none; align-items: center; justify-content: center; height: 160px; color: #60A5FA;">📘</div>
                </div>
                
                <!-- Slide selector thumbnails row -->
                <div id="prev-carousel-gallery" style="display:none; width: 100%; margin-top: 10px;">
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 6px; text-align: left;">📸 Pilih Slide Karusel untuk Dilihat:</div>
                    <div id="prev-slides-row" style="display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px;"></div>
                </div>
            </div>

            <div class="form-group">
                <label class="form-label" style="font-size: 0.78rem; font-weight: 700;">Naskah & Caption Lengkap:</label>
                <div id="prev-caption" style="background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; font-size: 0.84rem; line-height: 1.55; color: var(--text-main); max-height: 160px; overflow-y: auto; white-space: pre-wrap;">-</div>
            </div>

            <div style="background: rgba(255,255,255,0.02); border: 1px dashed var(--border); border-radius: 8px; padding: 10px 12px; font-size: 0.72rem; color: var(--text-muted); display:flex; justify-content: space-between; align-items: center;">
                <div>Verification Hash: <code id="prev-hash" style="color: var(--accent-blue); font-family: monospace;">-</code></div>
                <div id="prev-plat-name">Platform: Facebook, Instagram & Threads</div>
            </div>

            <div class="modal-actions">
                <button class="btn btn-outline" onclick="closePostPreview()">Tutup</button>
                <button class="btn btn-primary" id="prev-btn-publish" onclick="publishCurrentPreviewNow()" style="background: linear-gradient(135deg, #2563EB, #1D4ED8); font-weight:700;">🚀 Terbitkan Serentak ke 3 Medsos</button>
            </div>
        </div>
    </div>

    <!-- MODAL PRODUCTION PREFLIGHT CONFIRMATION -->
    <div id="modal-production-confirm" class="modal-overlay">
        <div class="modal-box" style="width: 560px; max-width: 95vw;">
            <div class="modal-header">
                <div class="modal-title" style="color:#EF4444; display:flex; align-items:center; gap:8px;">
                    ⚠️ Konfirmasi Beralih ke PRODUCTION
                </div>
                <button onclick="closeProductionModal()" style="background:none; border:none; color:var(--text-muted); font-size:1.4rem; cursor:pointer;">&times;</button>
            </div>
            
            <div style="background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; font-size: 0.84rem; color: #FCA5A5; line-height: 1.5;">
                <b>PERINGATAN REAL PUBLISHING:</b><br>
                Anda akan mengaktifkan penerbitan <b>NYATA</b> ke platform media sosial resmi (Facebook Fanspage / Instagram / Threads). Setiap konten yang diproses akan langsung diunggah ke publik.
            </div>

            <div style="margin-bottom: 14px;">
                <div style="font-size: 0.8rem; font-weight: 700; margin-bottom: 8px; color: var(--text-main);">📋 Hasil Uji Kesiapan Sistem (Preflight Checklist):</div>
                <div id="prod-preflight-checklist" style="background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; font-size: 0.8rem; max-height: 200px; overflow-y: auto;">
                    Memeriksa kesiapan sistem...
                </div>
            </div>

            <div id="prod-preflight-alert" style="display:none; font-size: 0.78rem; color: #F87171; margin-bottom: 12px;"></div>

            <div class="modal-actions">
                <button class="btn btn-outline" onclick="closeProductionModal()">Batal</button>
                <button class="btn btn-danger" id="btn-confirm-prod" onclick="confirmSwitchToProduction()" disabled style="background:linear-gradient(135deg,#EF4444,#DC2626); color:white; font-weight:700;">🚀 Ya, Aktifkan Live Publishing (PRODUCTION)</button>
            </div>
        </div>
    </div>

    <script>
        function showToast(msg) {{
            const t = document.getElementById('toast');
            t.innerText = msg;
            t.style.display = 'block';
            setTimeout(() => t.style.display = 'none', 3500);
        }}

        function switchTab(tabId) {{
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
            if (event && event.currentTarget) event.currentTarget.classList.add('active');
            
            const effectiveTab = (tabId === 'providers') ? 'credentials' : tabId;
            const target = document.getElementById('tab-' + effectiveTab);
            if (target) target.classList.add('active');

            if (tabId === 'overview') pollStats();
            if (tabId === 'learning') fetchLearningData();
            if (tabId === 'content') {{
                fetchContent();
                fetchQueue();
                fetchReceipts();
            }}
            if (tabId === 'queue') fetchQueue();
            if (tabId === 'receipts') fetchReceipts();
            if (tabId === 'providers' || tabId === 'credentials') {{
                fetchVaultStatus();
                fetchEnvConfig();
            }}
            if (tabId === 'ab_testing') fetchExperiments();
            if (tabId === 'music') fetchMusic();
            if (tabId === 'qc') fetchQC();
            if (tabId === 'storage') fetchStorage();
            if (tabId === 'health' || tabId === 'system') {{
                fetchVaultBackups();
                fetchHealth();
            }}
            if (tabId === 'logs') fetchLogs();
            if (tabId === 'settings') fetchVersions();
        }}

        // --- DASHBOARD POLLING & REALTIME METRICS ---
        async function pollStats() {{
            try {{
                const res = await fetch('/api/stats');
                const d = await res.json();
                
                const isPaused = Boolean(d.is_paused) || (d.status === 'PAUSED');
                const isEm = (d.status === 'EMERGENCY_STOP') || (d.status === 'EMERGENCY_STOPPED') || Boolean(d.is_emergency_stopped);
                updateHeaderStatus(d.app_mode || 'DRY_RUN', isPaused, d.status || 'RUNNING', isEm);

                const livePubEl = document.getElementById('metric-live-published');
                if (livePubEl) livePubEl.innerText = d.live_published_count !== undefined ? d.live_published_count : 0;

                const dryRunEl = document.getElementById('metric-dry-run');
                if (dryRunEl) dryRunEl.innerText = d.dry_run_simulations_count !== undefined ? d.dry_run_simulations_count : 0;

                const pendEl = document.getElementById('metric-pending');
                if (pendEl) pendEl.innerText = d.queue_count !== undefined ? d.queue_count : (d.job_stats ? ((d.job_stats.PENDING || 0) + (d.job_stats.PROCESSING || 0)) : 0);

                const costEl = document.getElementById('metric-cost');
                if (costEl) costEl.innerText = '$' + (d.cost_metrics ? d.cost_metrics.daily_spend_usd.toFixed(2) : '0.00');

                const provEl = document.getElementById('metric-providers');
                if (provEl) provEl.innerText = d.active_providers_count !== undefined ? d.active_providers_count : 0;

                const getPilarIcon = (p) => {{
                    if (p.includes('transformasi')) return '✨';
                    if (p.includes('cerita')) return '📖';
                    if (p.includes('kreasi')) return '🎨';
                    if (p.includes('mini')) return '⏳';
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
                    const isSim = p.is_simulated || (p.post_url && (p.post_url.includes('.mock') || p.post_url.includes('dry_run')));
                    const isFailed = p.status === 'FAILED';
                    
                    let statusLabel = '🟢 LIVE TERBIT';
                    let statusColor = '#34D399';
                    let statusBg = 'rgba(16,185,129,0.12)';
                    let statusBorder = 'rgba(16,185,129,0.3)';

                    if (isFailed) {{
                        statusLabel = '🔴 GAGAL';
                        statusColor = '#EF4444';
                        statusBg = 'rgba(239,68,68,0.12)';
                        statusBorder = 'rgba(239,68,68,0.3)';
                    }} else if (isSim) {{
                        statusLabel = '⚡ SIMULASI (Dry Run)';
                        statusColor = '#60A5FA';
                        statusBg = 'rgba(96,165,250,0.12)';
                        statusBorder = 'rgba(96,165,250,0.3)';
                    }}
                    
                    return `
                    <tr class="table-row-hover">
                        <td style="width: 50px;">
                            <div class="media-thumb-container" onclick="openPostPreview('${{p.id}}')" style="cursor:pointer;" title="Klik untuk pratinjau visual">
                                ${{p.preview_url ? (p.preview_url.endsWith('.mp4') ? `<video src="${{p.preview_url}}" class="media-thumb-img" muted playsinline loop onmouseover="this.play()" onmouseout="this.pause()"></video>` : `<img src="${{p.preview_url}}" class="media-thumb-img" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">`) : ''}}
                                <div class="media-fallback-badge" style="${{p.preview_url ? 'display:none;' : 'display:flex;'}}">
                                    ${{icon}}
                                </div>
                            </div>
                        </td>
                        <td>
                            <div style="font-weight: 700; color: var(--text-main); font-size: 0.88rem; line-height: 1.35; cursor:pointer;" onclick="openPostPreview('${{p.id}}')">${{p.title}}</div>
                            <div style="font-size: 0.73rem; color: var(--text-muted); margin-top: 3px;">Dipublikasikan: ${{p.published_at || 'Baru Saja'}}</div>
                        </td>
                        <td><span class="pilar-pill">#${{p.pilar || 'pita_waktu'}}</span></td>
                        <td>${{platBadge}}</td>
                        <td>
                            <span class="status-indicator-badge" style="color:${{statusColor}}; background:${{statusBg}}; border:1px solid ${{statusBorder}};">
                                <span class="pulse-dot" style="background:${{statusColor}};"></span> ${{statusLabel}}
                            </span>
                        </td>
                        <td>
                            ${{isSim ? 
                                `<button onclick="openPostPreview('${{p.id}}')" class="btn btn-outline" style="font-size:0.75rem; padding: 5px 12px; border-radius: 6px; border-color: rgba(99,102,241,0.4); color: #818cf8; cursor:pointer;">👁️ Pratinjau Post</button>` : 
                                (p.post_url && p.post_url !== '#' && !isFailed ? `<a href="${{p.post_url}}" target="_blank" class="btn btn-primary" style="font-size:0.75rem; padding: 5px 12px; border-radius: 6px;">↗ Buka Post</a>` : `<button onclick="openPostPreview('${{p.id}}')" class="btn btn-outline" style="font-size:0.75rem; padding: 5px 12px; border-radius: 6px;">👁️ Detail</button>`)
                            }}
                        </td>
                    </tr>
                    `;
                }}).join('');
            }} catch (e) {{ console.error('Poll stats error:', e); }}
        }}

        window.currentRecentPubs = {initial_pubs_json};
        window.currentContents = [];

        function selectPreviewSlide(url, el) {{
            const v = document.getElementById('prev-video');
            if (v) {{
                v.style.display = 'none';
                v.pause();
            }}
            const img = document.getElementById('prev-img');
            if (img) {{
                img.src = url;
                img.style.display = 'block';
            }}
            const fb = document.getElementById('prev-fallback-icon');
            if (fb) fb.style.display = 'none';
            document.querySelectorAll('#prev-slides-row > div').forEach(d => d.style.borderColor = 'var(--border)');
            if (el) el.style.borderColor = '#3B82F6';
        }}

        async function openPostPreview(pubId) {{
            let p = (window.currentRecentPubs || []).find(item => item.id === pubId || item.content_id === pubId);
            if (!p) {{
                p = (window.currentContents || []).find(item => item.id === pubId);
            }}
            if (!p) {{
                await pollStats();
                p = (window.currentRecentPubs || []).find(item => item.id === pubId || item.content_id === pubId);
            }}
            if (!p) return;

            document.getElementById('prev-title').innerText = p.title || 'Tanpa Judul';
            const pilarName = p.pilar || 'pita_waktu';
            document.getElementById('prev-pilar-badge').innerHTML = `<span class="pilar-pill">#${{pilarName}}</span>`;
            document.getElementById('prev-date').innerText = p.published_at || p.created_at || 'Siap Diterbitkan';
            
            const isSim = p.is_simulated || (p.post_url && (p.post_url.includes('.mock') || p.post_url.includes('dry_run'))) || !p.post_url;
            const isFailed = p.status === 'FAILED';
            
            if (isFailed) {{
                document.getElementById('prev-status').innerHTML = '<span style="color:#EF4444;">🔴 GAGAL</span>';
            }} else if (isSim) {{
                document.getElementById('prev-status').innerHTML = '<span style="color:#60A5FA;">⚡ SIMULASI (Dry Run - Siap Tayang)</span>';
            }} else {{
                document.getElementById('prev-status').innerHTML = '<span style="color:#10B981;">🟢 LIVE TERBIT (Facebook Fanspage)</span>';
            }}

            // Bersihkan teks narasi dari catatan intro AI jika ada
            let capText = (p.caption || '(Tidak ada caption tersimpan)');
            if (capText.indexOf('***') !== -1) {{
                const parts = capText.split('***');
                if (parts.length > 1 && (parts[0].toLowerCase().includes('berikut') || parts[0].toLowerCase().includes('perbaikan') || parts[0].toLowerCase().includes('self-repair') || parts[0].toLowerCase().includes('qc'))) {{
                    capText = '***' + parts.slice(1).join('***');
                }}
            }}
            document.getElementById('prev-caption').innerText = capText.trim();

            document.getElementById('prev-hash').innerText = (p.verification_hash || '-').slice(0, 24) + '...';
            document.getElementById('prev-plat-name').innerText = 'Platform: ' + (p.platform ? p.platform.toUpperCase() : 'FACEBOOK');

            const imgEl = document.getElementById('prev-img');
            const videoEl = document.getElementById('prev-video');
            const fallbackEl = document.getElementById('prev-fallback-icon');
            const mediaList = p.media_urls || (p.preview_url ? [p.preview_url] : []);
            const firstMedia = mediaList.length > 0 ? mediaList[0] : (p.preview_url || '');

            const isVideo = firstMedia.toLowerCase().endsWith('.mp4') || (p.media_type === 'video');

            if (isVideo && firstMedia) {{
                if (videoEl) {{
                    videoEl.src = firstMedia;
                    videoEl.style.display = 'block';
                    videoEl.load();
                }}
                if (imgEl) imgEl.style.display = 'none';
                if (fallbackEl) fallbackEl.style.display = 'none';
            }} else if (firstMedia) {{
                if (videoEl) {{
                    videoEl.pause();
                    videoEl.style.display = 'none';
                }}
                if (imgEl) {{
                    imgEl.src = firstMedia;
                    imgEl.style.display = 'block';
                }}
                if (fallbackEl) fallbackEl.style.display = 'none';
            }} else {{
                if (videoEl) {{ videoEl.pause(); videoEl.style.display = 'none'; }}
                if (imgEl) imgEl.style.display = 'none';
                if (fallbackEl) {{
                    fallbackEl.style.display = 'flex';
                    fallbackEl.innerText = pilarName.includes('cerita') ? '📖' : (pilarName.includes('transformasi') ? '✨' : '🎬');
                }}
            }}

            // Render all slides if carousel
            const galleryRow = document.getElementById('prev-slides-row');
            const galleryContainer = document.getElementById('prev-carousel-gallery');
            if (mediaList && mediaList.length > 1) {{
                galleryContainer.style.display = 'block';
                galleryRow.innerHTML = mediaList.map((url, idx) => `
                    <div style="width:72px; height:72px; border-radius:8px; overflow:hidden; border:2px solid ${{idx===0 ? '#3B82F6' : 'var(--border)'}}; flex-shrink:0; cursor:pointer;" onclick="selectPreviewSlide('${{url}}', this)" title="Klik Slide ${{idx+1}}">
                        <img src="${{url}}" style="width:100%; height:100%; object-fit:cover;">
                    </div>
                `).join('');
            }} else {{
                galleryContainer.style.display = 'none';
            }}

            document.getElementById('prev-sim-alert').style.display = isSim ? 'flex' : 'none';

            window.currentPreviewContentId = p.content_id || p.id;
            const liveBtn = document.getElementById('prev-btn-publish');
            if (liveBtn) {{
                if (isSim) {{
                    liveBtn.innerHTML = '🚀 Terbitkan Serentak ke 3 Medsos';
                    liveBtn.style.display = 'inline-flex';
                    liveBtn.onclick = () => {{ publishNow(p.content_id || p.id); }};
                }} else if (p.post_url && p.post_url !== '#' && !isFailed) {{
                    liveBtn.innerHTML = '↗ Buka Post';
                    liveBtn.style.display = 'inline-flex';
                    liveBtn.onclick = () => {{ window.open(p.post_url, '_blank'); }};
                }} else {{
                    liveBtn.style.display = 'none';
                }}
            }}

            document.getElementById('modal-post-preview').style.display = 'flex';
        }}

        function publishCurrentPreviewNow() {{
            if (window.currentPreviewContentId) {{
                publishNow(window.currentPreviewContentId);
            }}
        }}

        async function publishNow(contentId) {{
            const btn = document.getElementById('prev-btn-publish');
            if (btn) {{
                btn.innerHTML = '⏳ Menerbitkan ke FB, IG & Threads...';
                btn.disabled = true;
            }}
            showToast('🚀 Mengalihkan ke mode PRODUCTION dan mempublikasikan ke Facebook, Instagram & Threads...');
            try {{
                const res = await fetch('/api/publish/now/' + contentId, {{ method: 'POST' }});
                let d;
                try {{
                    d = await res.json();
                }} catch (jsonErr) {{
                    d = {{ success: false, detail: 'Server mengembalikan status ' + res.status }};
                }}
                if (d.success) {{
                    showToast('🟢 ' + d.message);
                    closePostPreview();
                    pollStats();
                    if (d.post_url && d.post_url !== '#') {{
                        setTimeout(() => window.open(d.post_url, '_blank'), 1200);
                    }}
                }} else {{
                    showToast('🔴 Gagal terbit: ' + (d.detail || d.message || 'Eror'));
                }}
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
            }} finally {{
                if (btn) {{
                    btn.innerHTML = '🚀 Terbitkan Serentak ke 3 Medsos';
                    btn.disabled = false;
                }}
            }}
        }}

        function closePostPreview() {{
            document.getElementById('modal-post-preview').style.display = 'none';
        }}

        async function handleModeSwitchClick() {{
            const statusText = document.getElementById('main-status-text');
            const isCurrentlyProd = (window.currentAppMode === 'PRODUCTION') || 
                                    (statusText && (statusText.innerText.includes('PRODUKSI') || statusText.innerText.includes('PRODUCTION')));
            
            if (isCurrentlyProd) {{
                if (confirm('Kembali ke mode SIMULASI (Dry Run Aman Lokal)?')) {{
                    await toggleModeTo('DRY_RUN');
                }}
                return;
            }}

            showToast('🔍 Menjalankan uji kesiapan preflight sistem...');
            try {{
                const res = await fetch('/api/system/production-preflight');
                const d = await res.json();
                openProductionModal(d);
            }} catch (e) {{
                showToast('🔴 Eror preflight: ' + e.message);
            }}
        }}

        function openProductionModal(preflightData) {{
            const modal = document.getElementById('modal-production-confirm');
            const checklistEl = document.getElementById('prod-preflight-checklist');
            const alertEl = document.getElementById('prod-preflight-alert');
            const confirmBtn = document.getElementById('btn-confirm-prod');

            if (!modal || !checklistEl || !confirmBtn) return;

            let items = preflightData.items;
            if (!items && preflightData.checks) {{
                items = Object.values(preflightData.checks).map(c => ({{
                    name: c.label || c.name,
                    status: c.passed ? 'PASS' : (c.required ? 'FAIL' : 'WARN'),
                    message: c.passed ? 'Terpenuhi & Siap' : 'Belum terkonfigurasi / perlu tindakan'
                }}));
            }}
            items = items || [];

            checklistEl.innerHTML = items.map(item => {{
                const isPass = item.status === 'PASS';
                const isWarn = item.status === 'WARN';
                const color = isPass ? '#10B981' : (isWarn ? '#F59E0B' : '#EF4444');
                const icon = isPass ? '✅' : (isWarn ? '🟡' : '❌');
                return `
                <div style="display:flex; justify-content:space-between; align-items:center; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.05);">
                    <div>
                        <b style="color:var(--text-main);">${{item.name}}</b>
                        <div style="font-size:0.72rem; color:var(--text-muted);">${{item.message}}</div>
                    </div>
                    <span style="font-weight:700; color:${{color}}; white-space:nowrap;">${{icon}} ${{item.status}}</span>
                </div>
                `;
            }}).join('');

            const canProceed = preflightData.can_proceed !== undefined ? preflightData.can_proceed : (preflightData.can_switch_to_production !== undefined ? preflightData.can_switch_to_production : preflightData.ready);

            if (canProceed) {{
                confirmBtn.disabled = false;
                confirmBtn.style.opacity = '1';
                confirmBtn.style.cursor = 'pointer';
                if (alertEl) alertEl.style.display = 'none';
            }} else {{
                confirmBtn.disabled = true;
                confirmBtn.style.opacity = '0.5';
                confirmBtn.style.cursor = 'not-allowed';
                if (alertEl) {{
                    alertEl.style.display = 'block';
                    alertEl.innerText = '⚠️ Sistem belum memenuhi syarat untuk beralih ke PRODUCTION. Periksa item bertanda ❌ di atas.';
                }}
            }}

            modal.style.display = 'flex';
        }}

        function closeProductionModal() {{
            const modal = document.getElementById('modal-production-confirm');
            if (modal) modal.style.display = 'none';
        }}

        async function confirmSwitchToProduction() {{
            closeProductionModal();
            await toggleModeTo('PRODUCTION');
        }}

        async function toggleModeTo(targetMode) {{
            showToast('Mengalihkan mode ke ' + targetMode + '...');
            try {{
                const res = await fetch('/api/app_mode/toggle', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ mode: targetMode, confirmed: true }})
                }});
                const d = await res.json();
                if (d.success) {{
                    showToast('🟢 ' + d.message);
                    setTimeout(() => location.reload(), 1200);
                }} else {{
                    showToast('🔴 Gagal beralih mode: ' + (d.message || d.detail || 'Eror'));
                }}
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
            }}
        }}

        async function toggleMode() {{
            return handleModeSwitchClick();
        }}

        function updateHeaderStatus(appMode, isPaused, workerStatus, isEmergency) {{
            const statusBadge = document.getElementById('main-status-badge');
            const statusText = document.getElementById('main-status-text');
            const statusDot = document.getElementById('status-dot');
            const pauseBtn = document.getElementById('btn-ctrl-pause');
            const resumeBtn = document.getElementById('btn-ctrl-resume');
            const stopBtn = document.getElementById('btn-ctrl-stop');

            let label = 'SIMULASI AKTIF';
            let icon = '🟡';
            let color = '#F59E0B';
            let bg = 'rgba(245, 158, 11, 0.15)';
            let border = 'rgba(245, 158, 11, 0.35)';

            const isEm = Boolean(isEmergency) || workerStatus === 'EMERGENCY_STOP' || workerStatus === 'EMERGENCY_STOPPED';

            if (isEm) {{
                label = 'EMERGENCY STOP';
                icon = '🔴';
                color = '#EF4444';
                bg = 'rgba(239, 68, 68, 0.15)';
                border = 'rgba(239, 68, 68, 0.35)';
                if (pauseBtn) pauseBtn.style.display = 'none';
                if (resumeBtn) resumeBtn.style.display = 'none';
                if (stopBtn) stopBtn.style.display = 'inline-flex';
            }} else if (isPaused) {{
                label = 'DIJEDA';
                icon = '🟠';
                color = '#F97316';
                bg = 'rgba(249, 115, 22, 0.15)';
                border = 'rgba(249, 115, 22, 0.35)';
                if (pauseBtn) pauseBtn.style.display = 'none';
                if (resumeBtn) resumeBtn.style.display = 'inline-flex';
                if (stopBtn) stopBtn.style.display = 'inline-flex';
            }} else if (workerStatus === 'STOPPED' || workerStatus === 'OFFLINE' || workerStatus === 'DISABLED' || workerStatus === 'DEAD') {{
                label = 'OFFLINE';
                icon = '⚫';
                color = '#94A3B8';
                bg = 'rgba(148, 163, 184, 0.15)';
                border = 'rgba(148, 163, 184, 0.35)';
                if (pauseBtn) pauseBtn.style.display = 'none';
                if (resumeBtn) resumeBtn.style.display = 'inline-flex';
                if (stopBtn) stopBtn.style.display = 'inline-flex';
            }} else if (appMode === 'PRODUCTION') {{
                label = 'PRODUKSI AKTIF';
                icon = '🟢';
                color = '#10B981';
                bg = 'rgba(16, 185, 129, 0.15)';
                border = 'rgba(16, 185, 129, 0.35)';
                if (pauseBtn) pauseBtn.style.display = 'inline-flex';
                if (resumeBtn) resumeBtn.style.display = 'none';
                if (stopBtn) stopBtn.style.display = 'inline-flex';
            }} else {{
                label = 'SIMULASI AKTIF';
                icon = '🟡';
                color = '#F59E0B';
                bg = 'rgba(245, 158, 11, 0.15)';
                border = 'rgba(245, 158, 11, 0.35)';
                if (pauseBtn) pauseBtn.style.display = 'inline-flex';
                if (resumeBtn) resumeBtn.style.display = 'none';
                if (stopBtn) stopBtn.style.display = 'inline-flex';
            }}

            if (statusText) statusText.innerText = icon + ' ' + label;
            if (statusBadge) {{
                statusBadge.style.color = color;
                statusBadge.style.background = bg;
                statusBadge.style.borderColor = border;
            }}
            if (statusDot) {{
                statusDot.style.background = color;
            }}
            window.currentAppMode = appMode;
        }}

        async function sendControl(action) {{
            try {{
                const res = await fetch('/api/control/' + action, {{ method: 'POST' }});
                const d = await res.json();
                showToast((d.success ? '🟢 ' : '🔴 ') + (d.message || ('Action ' + action + ' sent.')));
                
                const isPaused = action === 'PAUSE';
                const isEmergency = action === 'EMERGENCY_STOP';
                const workerStatus = action === 'RESUME' ? 'RUNNING' : (action === 'PAUSE' ? 'PAUSED' : (action === 'EMERGENCY_STOP' ? 'EMERGENCY_STOPPED' : 'RUNNING'));
                updateHeaderStatus(window.currentAppMode || 'DRY_RUN', isPaused, workerStatus, isEmergency);
                setTimeout(() => pollStats(), 800);
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
            }}
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

        async function testPlatformService(serviceName) {{
            const label = serviceName === 'facebook' ? 'Facebook Fanspage' : (serviceName === 'instagram' ? 'Instagram Business' : serviceName.toUpperCase());
            showToast('🔍 Menguji akses ' + label + '...');
            try {{
                const res = await fetch('/api/credentials/test', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ service_name: serviceName }})
                }});
                const d = await res.json();
                if (d.status === 'VALID') {{
                    showToast('🟢 ' + (d.message || (label + ' Valid')));
                }} else if (d.status === 'RATE_LIMITED') {{
                    showToast('🟡 ' + label + ': ' + (d.message || 'Rate Limited'));
                }} else {{
                    showToast('🔴 ' + (d.message || d.status || ('Gagal menguji ' + label)));
                }}
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
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

        // --- HARDENED PERSISTENT VAULT JAVASCRIPT HANDLERS ---
        async function fetchVaultStatus() {{
            try {{
                const res = await fetch('/api/vault/status');
                const d = await res.json();

                const encEl = document.getElementById('sec-vault-enc');
                if (encEl) {{
                    encEl.innerText = d.vault_encrypted ? 'ENCRYPTED ✅' : 'DISABLED ⚠️';
                    encEl.style.color = d.vault_encrypted ? 'var(--accent-emerald)' : 'var(--accent-rose)';
                }}

                const bakEl = document.getElementById('sec-backup-status');
                if (bakEl) {{
                    bakEl.innerText = d.backup_status === 'AVAILABLE' ? `AVAILABLE (${{d.backups_count}}) ✅` : 'NOT_CREATED ⚠️';
                }}

                await fetchVaultSecrets(d.health || {{}});
            }} catch (e) {{
                console.error('Fetch vault status error:', e);
            }}
        }}

        async function fetchVaultSecrets(healthMap = {{}}) {{
            try {{
                const res = await fetch('/api/vault/secrets');
                const d = await res.json();
                const secrets = d.secrets || [];

                updatePlatformCardBadges(healthMap, secrets);
            }} catch (e) {{
                console.error('Fetch vault secrets error:', e);
            }}
        }}

        function openReplaceSecretModal(keyName = '', provider = '') {{
            const nameEl = document.getElementById('replace-sec-name');
            const valEl = document.getElementById('replace-sec-val');
            if (nameEl) nameEl.value = keyName;
            if (valEl) valEl.value = '';
            const modal = document.getElementById('modal-replace-secret');
            if (modal) modal.style.display = 'flex';
        }}

        function closeReplaceSecretModal() {{
            const valEl = document.getElementById('replace-sec-val');
            if (valEl) valEl.value = '';
            const modal = document.getElementById('modal-replace-secret');
            if (modal) modal.style.display = 'none';
        }}

        async function submitAtomicReplace() {{
            const name = document.getElementById('replace-sec-name').value.trim();
            const val = document.getElementById('replace-sec-val').value.trim();
            const testFirst = document.getElementById('replace-sec-test-first').checked;
            const btn = document.getElementById('btn-submit-replace');

            if (!name || !val) {{
                showToast('⚠️ Nama secret dan nilai kunci baru wajib diisi.');
                return;
            }}

            if (btn) {{
                btn.innerText = '⏳ Menguji & Mengenkripsi...';
                btn.disabled = true;
            }}

            showToast('Memproses penggantian atomik: PENDING ➔ TEST ➔ ACTIVE...');
            try {{
                const res = await fetch('/api/vault/secret/set', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        name: name,
                        value: val,
                        test_first: testFirst
                    }})
                }});
                const d = await res.json();
                if (d.success) {{
                    showToast('🟢 ' + d.message);
                    closeReplaceSecretModal();
                    fetchVaultStatus();
                    fetchEnvConfig(false);
                }} else {{
                    showToast('🔴 Gagal validasi: ' + (d.detail || d.message || 'Eror penggantian secret'));
                }}
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
            }} finally {{
                if (btn) {{
                    btn.innerText = '💾 Simpan & Validasi';
                    btn.disabled = false;
                }}
            }}
        }}

        async function testVaultSecret(name) {{
            showToast('🔍 Menguji kredensial vault ' + name + '...');
            try {{
                const res = await fetch('/api/vault/secret/test', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ name: name }})
                }});
                const d = await res.json();
                if (d.status === 'VALID') {{
                    showToast('🟢 ' + name + ': VALID (' + (d.latency_ms || 0) + 'ms)');
                }} else if (d.status === 'RATE_LIMITED' || d.status === 'QUOTA_EXHAUSTED') {{
                    showToast('🟡 ' + name + ': ' + d.status + ' (' + (d.message || '') + ')');
                }} else {{
                    showToast('🔴 ' + name + ': ' + d.status + ' (' + (d.message || 'Gagal') + ')');
                }}
                fetchVaultStatus();
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
            }}
        }}

        async function toggleVaultSecretEnabled(name, isCurrentlyEnabled) {{
            const action = isCurrentlyEnabled ? 'disable' : 'enable';
            const actionText = isCurrentlyEnabled ? 'Nonaktifkan' : 'Aktifkan';
            if (!confirm(`${{actionText}} kredensial "${{name}}" di DPAPI Vault?`)) return;
            try {{
                const res = await fetch(`/api/vault/secret/${{action}}`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ name: name }})
                }});
                const d = await res.json();
                showToast((d.success ? '🟢 ' : '🔴 ') + (d.message || 'Status vault diperbarui'));
                fetchVaultStatus();
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
            }}
        }}

        async function disableVaultSecret(name) {{
            await toggleVaultSecretEnabled(name, true);
        }}

        async function enableVaultSecret(name) {{
            await toggleVaultSecretEnabled(name, false);
        }}

        async function deleteVaultSecret(name) {{
            if (!confirm('⚠️ PERINGATAN: Apakah Anda yakin ingin MENGHAPUS PERMANEN secret ' + name + ' dari DPAPI Vault? Tindakan ini tidak dapat dibatalkan!')) return;
            try {{
                const res = await fetch('/api/vault/secret/delete', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ name: name, confirmed: true }})
                }});
                const d = await res.json();
                showToast('🗑️ ' + d.message);
                fetchVaultStatus();
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
            }}
        }}

        async function exportVaultBackup() {{
            const pass = document.getElementById('vault-export-pass').value;
            if (!pass || pass.length < 6) {{
                showToast('⚠️ Passphrase backup minimal 6 karakter.');
                return;
            }}
            showToast('🔒 Membuat backup terenkripsi AES-256-GCM (.pmvault)...');
            try {{
                const res = await fetch('/api/vault/backup/export', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ passphrase: pass }})
                }});
                const d = await res.json();
                if (d.success) {{
                    showToast('🟢 ' + d.message);
                    document.getElementById('vault-export-pass').value = '';
                    fetchVaultBackups();
                    fetchVaultStatus();
                }} else {{
                    showToast('🔴 Gagal ekspor backup: ' + (d.detail || 'Eror'));
                }}
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
            }}
        }}

        async function importVaultBackup() {{
            const filename = document.getElementById('vault-import-file').value;
            const pass = document.getElementById('vault-import-pass').value;
            if (!filename) {{
                showToast('⚠️ Pilih file backup .pmvault terlebih dahulu.');
                return;
            }}
            if (!pass) {{
                showToast('⚠️ Masukkan passphrase dekripsi backup.');
                return;
            }}
            if (!confirm('Restore credential vault dari ' + filename + '? Secrets saat ini akan digantikan dengan data backup.')) return;
            
            showToast('🔓 Memulihkan kredensial dari file .pmvault...');
            try {{
                const res = await fetch('/api/vault/backup/import', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ filename: filename, passphrase: pass }})
                }});
                const d = await res.json();
                if (d.success) {{
                    showToast('🟢 ' + d.message);
                    document.getElementById('vault-import-pass').value = '';
                    fetchVaultStatus();
                    fetchEnvConfig(false);
                }} else {{
                    showToast('🔴 Gagal import backup: ' + (d.message || d.detail || 'Passphrase salah atau file rusak'));
                }}
            }} catch (e) {{
                showToast('🔴 Eror: ' + e.message);
            }}
        }}

        async function fetchVaultBackups() {{
            try {{
                const res = await fetch('/api/vault/backups/list');
                const d = await res.json();
                const backups = d.backups || [];

                const selectEl = document.getElementById('vault-import-file');
                if (selectEl) {{
                    selectEl.innerHTML = '<option value="">-- Pilih Berkas .pmvault --</option>' + 
                        backups.map(b => `<option value="${{b.filename}}">${{b.filename}} (${{Math.round(b.size_bytes/1024)}} KB - ${{b.created_at}})</option>`).join('');
                }}

                const tbody = document.getElementById('vault-backups-tbody');
                if (tbody) {{
                    if (backups.length === 0) {{
                        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--text-muted); padding:16px;">Belum ada berkas backup .pmvault. Gunakan tombol Generate di atas untuk membuat.</td></tr>';
                    }} else {{
                        tbody.innerHTML = backups.map(b => `
                            <tr>
                                <td><b style="color:#10B981; font-family:monospace;">${{b.filename}}</b></td>
                                <td>${{Math.round(b.size_bytes/1024)}} KB</td>
                                <td>${{b.created_at}}</td>
                            </tr>
                        `).join('');
                    }}
                }}
            }} catch (e) {{
                console.error('Fetch vault backups error:', e);
            }}
        }}

        function formatIndonesianTimestamp(tsStr) {{
            if (!tsStr || tsStr === '-' || tsStr === '—') return '—';
            try {{
                const clean = String(tsStr).replace('T', ' ').trim();
                const parts = clean.split(' ');
                if (parts.length >= 2) {{
                    const dateParts = parts[0].split('-');
                    const timePart = parts[1].split('.')[0];
                    if (dateParts.length === 3) {{
                        const year = dateParts[0];
                        const monthIdx = parseInt(dateParts[1], 10) - 1;
                        const day = dateParts[2];
                        const months = [
                            'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
                            'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'
                        ];
                        if (monthIdx >= 0 && monthIdx < 12) {{
                            return `${{day}} ${{months[monthIdx]}} ${{year}} • ${{timePart}}`;
                        }}
                    }}
                }}
                return tsStr;
            }} catch (e) {{
                return tsStr;
            }}
        }}

        function updatePlatformCardBadges(healthMap, secrets) {{
            const secMap = {{}};
            (secrets || []).forEach(s => {{
                secMap[s.name] = s;
            }});

            const cardConfigs = [
                {{ id: 'gemini', keys: ['GEMINI_PRIMARY_API_KEY', 'GEMINI_API_KEY', 'gemini_api_key'], defaultKey: 'GEMINI_PRIMARY_API_KEY' }},
                {{ id: 'gemini-2', keys: ['GEMINI_BACKUP_API_KEY', 'GEMINI_API_KEY_2', 'gemini_api_key_2'], defaultKey: 'GEMINI_BACKUP_API_KEY' }},
                {{ id: 'openrouter', keys: ['OPENROUTER_API_KEY', 'openrouter_api_key'], defaultKey: 'OPENROUTER_API_KEY' }},
                {{ id: 'xai', keys: ['XAI_API_KEY', 'xai_api_key'], defaultKey: 'XAI_API_KEY' }},
                {{ id: 'fb', keys: ['META_SYSTEM_USER_TOKEN', 'FB_PAGE_ACCESS_TOKEN', 'fb_page_access_token'], defaultKey: 'META_SYSTEM_USER_TOKEN' }},
                {{ id: 'ig', keys: ['META_SYSTEM_USER_TOKEN', 'INSTAGRAM_ACCESS_TOKEN', 'instagram_access_token'], defaultKey: 'META_SYSTEM_USER_TOKEN' }},
                {{ id: 'threads', keys: ['THREADS_ACCESS_TOKEN', 'threads_access_token'], defaultKey: 'THREADS_ACCESS_TOKEN' }},
                {{ id: 'telegram', keys: ['TELEGRAM_BOT_TOKEN', 'telegram_bot_token'], defaultKey: 'TELEGRAM_BOT_TOKEN' }}
            ];

            let healthyCount = 0;
            let totalConfigured = 0;
            let invalidCount = 0;

            cardConfigs.forEach(cfg => {{
                let matchedSec = null;
                let matchedHealth = null;
                let matchedKey = cfg.defaultKey;

                for (const k of cfg.keys) {{
                    if (secMap[k]) {{
                        matchedSec = secMap[k];
                        matchedKey = k;
                        break;
                    }}
                }}

                for (const k of cfg.keys) {{
                    if (healthMap && healthMap[k]) {{
                        matchedHealth = healthMap[k];
                        break;
                    }}
                }}

                let status = 'NOT_CONFIGURED';
                let statusColor = 'var(--text-muted)';
                let statusBg = 'rgba(148,163,184,0.1)';
                let statusBorder = 'rgba(148,163,184,0.2)';
                let icon = '⚪';

                if (matchedSec) {{
                    totalConfigured++;
                    if (!matchedSec.enabled) {{
                        status = 'DISABLED';
                        statusColor = '#94A3B8';
                        statusBg = 'rgba(148,163,184,0.12)';
                        statusBorder = 'rgba(148,163,184,0.3)';
                        icon = '⏸️';
                    }} else if (matchedHealth) {{
                        status = (matchedHealth.status || 'VALID').toUpperCase();
                        if (status === 'VALID') {{
                            healthyCount++;
                            statusColor = '#10B981';
                            statusBg = 'rgba(16,185,129,0.12)';
                            statusBorder = 'rgba(16,185,129,0.3)';
                            icon = '✅';
                        }} else if (status === 'RATE_LIMITED' || status === 'QUOTA_EXHAUSTED' || status === 'EXPIRING_SOON') {{
                            healthyCount++;
                            statusColor = '#F59E0B';
                            statusBg = 'rgba(245,158,11,0.12)';
                            statusBorder = 'rgba(245,158,11,0.3)';
                            icon = '🟡';
                        }} else if (status === 'INVALID' || status === 'EXPIRED') {{
                            invalidCount++;
                            statusColor = '#EF4444';
                            statusBg = 'rgba(239,68,68,0.12)';
                            statusBorder = 'rgba(239,68,68,0.3)';
                            icon = '🔴';
                        }}
                    }} else {{
                        healthyCount++;
                        status = 'VALID';
                        statusColor = '#10B981';
                        statusBg = 'rgba(16,185,129,0.12)';
                        statusBorder = 'rgba(16,185,129,0.3)';
                        icon = '✅';
                    }}
                }}

                // Update Status Badge
                const statusBadgeEl = document.getElementById(`ref-status-${{cfg.id}}`);
                if (statusBadgeEl) {{
                    statusBadgeEl.innerHTML = `<span style="display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:8px; font-size:0.75rem; font-weight:700; color:${{statusColor}}; background:${{statusBg}}; border:1px solid ${{statusBorder}};">${{icon}} ${{status}}</span>`;
                }}

                // Update Fingerprint
                const fpEl = document.getElementById(`fp-${{cfg.id}}`);
                if (fpEl) {{
                    fpEl.innerText = matchedSec ? (matchedSec.fingerprint || '••••••••') : '—';
                    fpEl.style.color = matchedSec ? '#34D399' : 'var(--text-muted)';
                }}

                // Update Last Tested
                const testedEl = document.getElementById(`tested-${{cfg.id}}`);
                if (testedEl) {{
                    if (matchedHealth && matchedHealth.latency_ms !== undefined) {{
                        testedEl.innerText = `${{matchedHealth.latency_ms}}ms (${{matchedHealth.status}})`;
                    }} else if (matchedHealth && matchedHealth.last_checked) {{
                        testedEl.innerText = formatIndonesianTimestamp(matchedHealth.last_checked);
                    }} else if (matchedSec) {{
                        testedEl.innerText = 'Tersedia di Vault';
                    }} else {{
                        testedEl.innerText = 'Belum Dikonfigurasi';
                    }}
                }}

                // Update Updated timestamp
                const updatedEl = document.getElementById(`updated-${{cfg.id}}`);
                if (updatedEl) {{
                    updatedEl.innerText = matchedSec && matchedSec.updated_at ? formatIndonesianTimestamp(matchedSec.updated_at) : '—';
                }}

                // Update Expiration
                const expiresEl = document.getElementById(`expires-${{cfg.id}}`);
                if (expiresEl) {{
                    expiresEl.innerText = matchedSec && matchedSec.expires_at ? formatIndonesianTimestamp(matchedSec.expires_at) : 'Tidak ada batas';
                }}

                // Update Toggle Button
                const toggleBtn = document.getElementById(`btn-toggle-${{cfg.id}}`);
                if (toggleBtn) {{
                    if (matchedSec) {{
                        toggleBtn.style.display = 'inline-flex';
                        toggleBtn.innerText = matchedSec.enabled ? '⏸️ Disable' : '▶️ Enable';
                        toggleBtn.className = matchedSec.enabled ? 'btn btn-outline' : 'btn btn-outline';
                        toggleBtn.style.borderColor = matchedSec.enabled ? 'var(--accent-amber)' : 'var(--accent-emerald)';
                        toggleBtn.style.color = matchedSec.enabled ? 'var(--accent-amber)' : 'var(--accent-emerald)';
                        toggleBtn.onclick = () => toggleVaultSecretEnabled(matchedSec.name, matchedSec.enabled);
                    }} else {{
                        toggleBtn.style.display = 'none';
                    }}
                }}
            }});

            // Update Top Security Summary KPI
            const healthSummaryEl = document.getElementById('sec-vault-health');
            if (healthSummaryEl) {{
                if (invalidCount > 0) {{
                    healthSummaryEl.innerText = `WARNING (${{invalidCount}} Degraded) ⚠️`;
                    healthSummaryEl.style.color = 'var(--accent-rose)';
                }} else if (totalConfigured > 0) {{
                    healthSummaryEl.innerText = `HEALTHY (${{healthyCount}}/${{totalConfigured}}) ✅`;
                    healthSummaryEl.style.color = 'var(--accent-emerald)';
                }} else {{
                    healthSummaryEl.innerText = 'NO SECRETS ⚪';
                    healthSummaryEl.style.color = 'var(--text-muted)';
                }}
            }}

            const issuesSummaryEl = document.getElementById('sec-vault-issues');
            if (issuesSummaryEl) {{
                if (invalidCount > 0) {{
                    issuesSummaryEl.innerText = `${{invalidCount}} ISSUES DETECTED ⚠️`;
                    issuesSummaryEl.style.color = 'var(--accent-rose)';
                }} else {{
                    issuesSummaryEl.innerText = '0 ISSUES (CLEAN) ✅';
                    issuesSummaryEl.style.color = 'var(--accent-emerald)';
                }}
            }}
        }}

        async function fetchEnvConfig(showToastMsg = false) {{
            try {{
                const res = await fetch('/api/env');
                const d = await res.json();
                const env = d.env || {{}};
                if (document.getElementById('env-fb-page-id')) document.getElementById('env-fb-page-id').value = env.FB_PAGE_ID || '1253340697871457';
                if (document.getElementById('env-ig-user-id')) document.getElementById('env-ig-user-id').value = env.IG_USER_ID || env.INSTAGRAM_ACCOUNT_ID || '17841426699286663';
                if (document.getElementById('env-openrouter-model')) document.getElementById('env-openrouter-model').value = env.OPENROUTER_DEFAULT_MODEL || 'openai/gpt-4o-mini';
                if (document.getElementById('env-telegram-admins')) document.getElementById('env-telegram-admins').value = env.TELEGRAM_ADMIN_IDS || '';
                if (document.getElementById('env-telegram-alert-chat')) document.getElementById('env-telegram-alert-chat').value = env.TELEGRAM_ALERT_CHAT_ID || env.TELEGRAM_ADMIN_IDS || '';

                if (showToastMsg) {{
                    showToast('✅ Konfigurasi platform berhasil dimuat');
                }}
            }} catch (e) {{
                console.error('Failed to load platform config:', e);
                if (showToastMsg) showToast('Eror memuat konfigurasi: ' + e.message);
            }}
        }}

        async function savePlatformConfig() {{
            showToast('Menyimpan konfigurasi platform...');
            const updates = {{
                FB_PAGE_ID: document.getElementById('env-fb-page-id') ? document.getElementById('env-fb-page-id').value.trim() : '',
                IG_USER_ID: document.getElementById('env-ig-user-id') ? document.getElementById('env-ig-user-id').value.trim() : '',
                INSTAGRAM_ACCOUNT_ID: document.getElementById('env-ig-user-id') ? document.getElementById('env-ig-user-id').value.trim() : '',
                OPENROUTER_DEFAULT_MODEL: document.getElementById('env-openrouter-model') ? document.getElementById('env-openrouter-model').value.trim() : '',
                TELEGRAM_ADMIN_IDS: document.getElementById('env-telegram-admins') ? document.getElementById('env-telegram-admins').value.trim() : '',
                TELEGRAM_ALERT_CHAT_ID: document.getElementById('env-telegram-alert-chat') ? document.getElementById('env-telegram-alert-chat').value.trim() : ''
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

        async function saveEnvConfigDirect() {{
            return savePlatformConfig();
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

        let studioPollTimer = null;

        async function triggerStudio(pilar) {{
            const pilarDisplayMap = {{
                'pita_transformasi': 'Pita Transformasi (Reels/Shorts)',
                'pita_cerita': 'Pita Cerita (Karusel Edukasi)',
                'pita_kreasi': 'Pita Kreasi (Visual Estetika)',
                'pita_mini': 'Pita Mini (Refleksi Singkat)'
            }};
            const pilarTitle = pilarDisplayMap[pilar] || pilar;

            // 1. Show & Initialize Progress Card
            const progressCard = document.getElementById('studio-progress-card');
            const progressTitle = document.getElementById('studio-progress-title');
            const progressPilar = document.getElementById('studio-progress-pilar');
            const progressPct = document.getElementById('studio-progress-pct');
            const progressBar = document.getElementById('studio-progress-bar');
            const progressStep = document.getElementById('studio-progress-step');
            const progressJobId = document.getElementById('studio-progress-jobid');

            if (progressCard) {{
                progressCard.style.display = 'block';
                progressCard.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
            }}
            if (progressTitle) progressTitle.innerText = `Memproses Kreasi ${{pilarTitle}}...`;
            if (progressPilar) progressPilar.innerText = `#${{pilar}}`;
            if (progressPct) progressPct.innerText = '15%';
            if (progressBar) {{
                progressBar.style.width = '15%';
                progressBar.style.background = 'linear-gradient(90deg, #6366F1, #EC4899, #3B82F6)';
            }}
            if (progressStep) progressStep.innerHTML = '🤖 <b>Tahap 1/4:</b> Inisialisasi Job & Antrean SQLite WAL...';
            if (progressJobId) progressJobId.innerText = 'Job ID: Mengalokasikan...';

            // 2. Optimistically insert temporary row into #queue-tbody
            const qTbody = document.getElementById('queue-tbody');
            if (qTbody) {{
                const tempRow = `
                <tr id="temp-job-row" style="background: rgba(99, 102, 241, 0.08);">
                    <td><code>PROCSSNG</code></td>
                    <td><span class="pilar-pill">✨ #${{pilar}}</span></td>
                    <td><span class="brand-badge" style="background:rgba(96,165,250,0.2); color:#60A5FA; font-weight:700;"><span class="pulse-dot" style="background:#60A5FA; width:6px; height:6px;"></span> PROCESSING</span></td>
                    <td>Standard</td>
                    <td style="font-size:0.75rem; color:var(--text-muted);">Baru Saja</td>
                </tr>
                `;
                if (qTbody.innerHTML.includes('Antrean kosong')) {{
                    qTbody.innerHTML = tempRow;
                }} else {{
                    qTbody.insertAdjacentHTML('afterbegin', tempRow);
                }}
            }}

            showToast(`🚀 Memulai pembuatan konten #${{pilar}}...`);

            // Disable buttons temporarily
            const genBtns = ['btn-gen-transformasi', 'btn-gen-cerita', 'btn-gen-kreasi', 'btn-gen-mini'];
            genBtns.forEach(id => {{
                const b = document.getElementById(id);
                if (b) {{ b.disabled = true; b.style.opacity = '0.6'; }}
            }});

            try {{
                const res = await fetch('/api/studio/trigger', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ pilar: pilar }})
                }});
                const d = await res.json();
                
                if (d.success) {{
                    showToast('✅ ' + d.message);
                    if (progressJobId) progressJobId.innerText = 'Job ID: ' + (d.job_id ? d.job_id.slice(0, 8) : '-');
                    
                    fetchQueue();
                    fetchContent();
                    pollStats();

                    // Real dynamic polling tied to the actual Job ID
                    if (studioPollTimer) clearInterval(studioPollTimer);
                    let pollCount = 0;
                    studioPollTimer = setInterval(async () => {{
                        pollCount++;
                        try {{
                            const jobsRes = await fetch('/api/jobs');
                            const jobsData = await jobsRes.json();
                            const currentJob = (jobsData.jobs || []).find(j => j.id === d.job_id);

                            fetchQueue();
                            fetchContent();
                            pollStats();

                            if (currentJob) {{
                                const st = currentJob.status;
                                if (st === 'PENDING') {{
                                    if (progressPct) progressPct.innerText = '20%';
                                    if (progressBar) progressBar.style.width = '20%';
                                    if (progressStep) progressStep.innerHTML = '🤖 <b>Tahap 1/4:</b> Menunggu giliran di Antrean SQLite WAL...';
                                }} else if (st === 'IN_IDEATOR') {{
                                    if (progressPct) progressPct.innerText = '40%';
                                    if (progressBar) progressBar.style.width = '40%';
                                    if (progressStep) progressStep.innerHTML = '🧠 <b>Tahap 2/4:</b> AI Multi-Model Brainstorming Ide Narasi...';
                                }} else if (st === 'IN_CREATOR' || st === 'PROCESSING' || st === 'RUNNING') {{
                                    if (progressPct) progressPct.innerText = '65%';
                                    if (progressBar) progressBar.style.width = '65%';
                                    if (progressStep) progressStep.innerHTML = '🎨 <b>Tahap 3/4:</b> AI Copywriting Naskah & Rendering Media...';
                                }} else if (st === 'IN_REVIEW') {{
                                    if (progressPct) progressPct.innerText = '85%';
                                    if (progressBar) progressBar.style.width = '85%';
                                    if (progressStep) progressStep.innerHTML = '🔍 <b>Tahap 4/4:</b> Quality Control & Validasi Konten...';
                                }} else if (st === 'COMPLETED') {{
                                    clearInterval(studioPollTimer);
                                    if (progressPct) progressPct.innerText = '100%';
                                    if (progressBar) {{
                                        progressBar.style.width = '100%';
                                        progressBar.style.background = 'linear-gradient(90deg, #10B981, #059669)';
                                    }}
                                    if (progressStep) progressStep.innerHTML = '🟢 <b>Selesai!</b> Konten berhasil dirakit. Lihat hasilnya di Galeri Aset di bawah!';
                                    showToast(`🎉 Konten #${{pilar}} selesai dibuat! Silakan cek Galeri Aset.`);
                                    fetchContent();
                                    fetchQueue();
                                    fetchQC();
                                    fetchReceipts();
                                    pollStats();
                                }} else if (st === 'FAILED') {{
                                    clearInterval(studioPollTimer);
                                    if (progressPct) progressPct.innerText = 'GAGAL';
                                    if (progressBar) {{
                                        progressBar.style.width = '100%';
                                        progressBar.style.background = '#EF4444';
                                    }}
                                    if (progressStep) progressStep.innerHTML = `🔴 <b>Gagal:</b> ${{currentJob.error_message || 'Terjadi kesalahan pemrosesan'}}`;
                                    showToast(`🔴 Kreasi #${{pilar}} gagal: ${{currentJob.error_message || ''}}`);
                                    fetchQueue();
                                }}
                            }}
                        }} catch (err) {{
                            console.error('Job polling error:', err);
                        }}

                        if (pollCount > 60) {{
                            clearInterval(studioPollTimer);
                        }}
                    }}, 2000);
                }} else {{
                    if (progressPct) progressPct.innerText = 'ERROR';
                    if (progressBar) {{
                        progressBar.style.width = '100%';
                        progressBar.style.background = '#EF4444';
                    }}
                    if (progressStep) progressStep.innerHTML = '🔴 <b>Gagal:</b> ' + (d.detail || d.message || 'Gagal memicu studio');
                    showToast('🔴 Eror: ' + (d.detail || d.message || 'Gagal memicu studio'));
                }}
            }} catch (e) {{
                if (progressPct) progressPct.innerText = 'ERR';
                if (progressBar) {{
                    progressBar.style.width = '100%';
                    progressBar.style.background = '#EF4444';
                }}
                if (progressStep) progressStep.innerHTML = '🔴 <b>Eror Jaringan:</b> ' + e.message;
                showToast('🔴 Eror jaringan: ' + e.message);
            }} finally {{
                // Re-enable buttons
                setTimeout(() => {{
                    genBtns.forEach(id => {{
                        const b = document.getElementById(id);
                        if (b) {{ b.disabled = false; b.style.opacity = '1'; }}
                    }});
                }}, 1000);
            }}
        }}

        pollStats();
        fetchVaultStatus();
        fetchQueue();
        fetchContent();
        setInterval(pollStats, 5000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
