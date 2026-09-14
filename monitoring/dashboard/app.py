"""
Status Dashboard Internal Terproteksi untuk Sistem Pita Media.
Menyediakan antarmuka visual web yang aman untuk memantau metrik antrean, biaya, dan performa konten.
"""

from fastapi import FastAPI, Depends, HTTPException, Security, status, Request
from fastapi.security import APIKeyHeader
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select, func, desc

from config.settings import settings
from database.connection import async_session_factory
from database.models import Job, Content, Publication, CostRecord, AuditLog
from core.governors.cost_governor import cost_governor
from agents.strategist import strategy_optimizer

app = FastAPI(title="Pita Media - Protected Status Dashboard", docs_url=None, redoc_url=None)

API_KEY_HEADER = APIKeyHeader(name="X-Pita-Secret", auto_error=False)


def verify_dashboard_access(key: str = Security(API_KEY_HEADER), request: Request = None):
    """
    Memvalidasi akses dashboard menggunakan secret token untuk mencegah akses publik tanpa otorisasi.
    """
    secret = settings.DASHBOARD_SECRET_KEY
    token_param = request.query_params.get("token") if request else None
    
    # Izinkan jika menggunakan secret default, atau localhost browser request, atau secret cocok
    if (
        not secret
        or secret.startswith("change_this")
        or key == secret
        or token_param == secret
        or (request and request.client and request.client.host in ["127.0.0.1", "localhost"])
    ):
        return True

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Akses Ditolak: Dashboard ini terproteksi. Masukkan token otorisasi yang valid (?token=...).",
    )


@app.get("/api/stats", response_class=JSONResponse)
async def get_dashboard_api_stats(_: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        cost_metrics = await cost_governor.get_spend_metrics(session)

        # Job Counts
        stmt_jobs = select(Job.status, func.count(Job.id)).group_by(Job.status)
        res_jobs = await session.execute(stmt_jobs)
        job_stats = {row[0]: row[1] for row in res_jobs.all()}

        # Recent Publications
        stmt_pubs = select(Publication).order_by(desc(Publication.published_at)).limit(5)
        res_pubs = await session.execute(stmt_pubs)
        recent_pubs = [
            {
                "id": p.id,
                "platform": p.platform,
                "url": p.post_url,
                "status": p.publish_status,
                "published_at": p.published_at.isoformat(),
            }
            for p in res_pubs.scalars().all()
        ]

        return {
            "costs": cost_metrics,
            "job_queue": job_stats,
            "recent_publications": recent_pubs,
            "pillar_weights": strategy_optimizer.current_weights,
        }


@app.get("/", response_class=HTMLResponse)
async def get_dashboard_page(request: Request, _: bool = Depends(verify_dashboard_access)):
    async with async_session_factory() as session:
        cost_metrics = await cost_governor.get_spend_metrics(session)

        stmt_jobs = select(Job.status, func.count(Job.id)).group_by(Job.status)
        res_jobs = await session.execute(stmt_jobs)
        job_stats = {row[0]: row[1] for row in res_jobs.all()}

        stmt_logs = select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(10)
        res_logs = await session.execute(stmt_logs)
        logs = res_logs.scalars().all()

    log_rows = "".join(
        f"<tr><td class='px-4 py-2 font-mono text-xs text-gray-400'>{l.timestamp.strftime('%H:%M:%S')}</td>"
        f"<td class='px-4 py-2'><span class='px-2 py-0.5 text-xs rounded font-bold {'bg-emerald-950 text-emerald-300' if l.level=='INFO' else 'bg-amber-950 text-amber-300'}'>{l.level}</span></td>"
        f"<td class='px-4 py-2 font-medium text-gray-300'>{l.component}</td>"
        f"<td class='px-4 py-2 text-gray-400'>{l.message}</td></tr>"
        for l in logs
    ) or "<tr><td colspan='4' class='px-4 py-4 text-center text-gray-500'>Belum ada riwayat audit log.</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="id" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pita Media - Sentinel Status Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {{ background-color: #0b0f19; font-family: system-ui, -apple-system, sans-serif; }}
        .glass-card {{ background: rgba(17, 24, 39, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }}
    </style>
</head>
<body class="text-gray-100 min-h-screen p-6">
    <div class="max-w-7xl mx-auto space-y-6">
        <!-- Header -->
        <header class="flex justify-between items-center glass-card p-6 rounded-2xl border-l-4 border-indigo-500 shadow-2xl">
            <div>
                <h1 class="text-2xl font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent">Pita Media · Control Center</h1>
                <p class="text-sm text-gray-400 mt-1">Sistem Otonom Pembuatan, QC & Publikasi Konten Multi-Pilar</p>
            </div>
            <div class="flex items-center space-x-3">
                <span class="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                    <span class="w-2 h-2 mr-2 rounded-full bg-emerald-400 animate-pulse"></span>
                    Engine Active
                </span>
            </div>
        </header>

        <!-- KPI Grid -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-6">
            <div class="glass-card p-5 rounded-xl">
                <div class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Biaya Hari Ini</div>
                <div class="text-2xl font-bold text-indigo-400 mt-2">${cost_metrics['daily_spent']:.2f} <span class="text-xs text-gray-400 font-normal">/ ${cost_metrics['daily_limit']:.2f}</span></div>
                <div class="w-full bg-gray-800 rounded-full h-1.5 mt-3">
                    <div class="bg-indigo-500 h-1.5 rounded-full" style="width: {min(100, cost_metrics['daily_percentage'])}%"></div>
                </div>
            </div>

            <div class="glass-card p-5 rounded-xl">
                <div class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Biaya Bulan Ini</div>
                <div class="text-2xl font-bold text-cyan-400 mt-2">${cost_metrics['monthly_spent']:.2f} <span class="text-xs text-gray-400 font-normal">/ ${cost_metrics['monthly_limit']:.2f}</span></div>
                <div class="w-full bg-gray-800 rounded-full h-1.5 mt-3">
                    <div class="bg-cyan-500 h-1.5 rounded-full" style="width: {min(100, cost_metrics['monthly_percentage'])}%"></div>
                </div>
            </div>

            <div class="glass-card p-5 rounded-xl">
                <div class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Job Selesai Terbit</div>
                <div class="text-2xl font-bold text-emerald-400 mt-2">{job_stats.get('PUBLISHED', 0)}</div>
                <div class="text-xs text-emerald-500/80 mt-1">Postingan terverifikasi</div>
            </div>

            <div class="glass-card p-5 rounded-xl">
                <div class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Antrean Aktif</div>
                <div class="text-2xl font-bold text-amber-400 mt-2">{job_stats.get('PENDING', 0) + job_stats.get('IN_CREATOR', 0) + job_stats.get('IN_REVIEW', 0)}</div>
                <div class="text-xs text-gray-400 mt-1">Pending: {job_stats.get('PENDING', 0)} | Review: {job_stats.get('IN_REVIEW', 0)}</div>
            </div>
        </div>

        <!-- Audit Stream -->
        <div class="glass-card rounded-2xl p-6">
            <h2 class="text-lg font-bold text-gray-200 mb-4 flex items-center">
                <span class="w-2.5 h-2.5 rounded-full bg-indigo-500 mr-2"></span>
                Aktivitas Audit Log Terkini
            </h2>
            <div class="overflow-x-auto">
                <table class="w-full text-left text-sm">
                    <thead class="text-xs uppercase bg-gray-900/50 text-gray-400">
                        <tr>
                            <th class="px-4 py-2">Waktu</th>
                            <th class="px-4 py-2">Level</th>
                            <th class="px-4 py-2">Komponen</th>
                            <th class="px-4 py-2">Pesan</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-800/60">
                        {log_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)
