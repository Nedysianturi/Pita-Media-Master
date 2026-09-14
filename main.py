"""
Titik Masuk Utama (Main CLI Entrypoint) Sistem Pita Media.
Penggunaan:
  python main.py self-check          : Uji integritas seluruh 10 subsistem (DB, Meta, Gemini, Telegram, dll)
  python main.py dry-run             : Uji coba end-to-end tanpa posting nyata ke media sosial
  python main.py daemon              : Jalankan Autonomous 24/7 background runtime
  python main.py init-db             : Inisialisasi skema tabel database
  python main.py run                 : Menjalankan orchestrator, scheduler, worker loop & Telegram C2 polling
  python main.py bot                 : Menjalankan listener Telegram Bot C2 saja
  python main.py single --pilar ...  : Menjalankan 1 siklus kreasi pilar langsung (end-to-end)
  python main.py dashboard           : Menjalankan protected web status dashboard
  python main.py backup              : Mengambil backup snapshot database
  python main.py restore --file ...  : Pulihkan database dari snapshot
"""

import sys
import io
import asyncio
import argparse
import uvicorn
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from config.settings import settings
from database.connection import init_db, backup_database
from core.scheduler import content_orchestrator
from core.resilience.crash_recovery import crash_recovery_engine
from database.connection import async_session_factory
from monitoring.telegram_bot import telegram_c2
from core.runtime.self_check import run_startup_self_check, format_self_check_cli
from core.runtime.supervisor import run_daemon
from core.runtime.dry_run import run_dry_run_simulation
from core.runtime.maintenance import StorageMaintenance


async def run_init_db():
    print("[*] Menginisialisasi database SQLite WAL...")
    await init_db()
    print("[+] Database berhasil diinisialisasi.")


async def run_single_cycle(pilar: str):
    await init_db()
    print(f"[*] Menjalankan 1 siklus pembuatan konten mandiri untuk pilar: '{pilar}'...")
    job = await content_orchestrator.schedule_next_content_slot(pilar=pilar)
    print(f"[+] Job {job.id} dijadwalkan. Memulai pemrosesan...")
    success = await content_orchestrator.process_single_job(job.id)
    if success:
        print(f"[+] SUKSES: Job {job.id} berhasil melewati Creator, QC, dan dipublikasikan!")
    else:
        print(f"[-] PERHATIAN: Job {job.id} tidak berhasil terbit. Periksa log/dashboard untuk detail.")


async def worker_loop():
    while True:
        try:
            async with async_session_factory() as session:
                from core.queue import job_queue
                job = await job_queue.acquire_next_job(session)

            if job:
                print(f"[*] Worker memproses Job {job.id} (#{job.pilar})...")
                await content_orchestrator.process_single_job(job.id)
            else:
                await asyncio.sleep(5)
        except (KeyboardInterrupt, asyncio.CancelledError):
            break
        except Exception as e:
            print(f"[-] Error pada worker loop: {e}")
            await asyncio.sleep(5)


async def run_orchestrator_and_bot():
    await init_db()
    print("[*] Menjalankan Crash Recovery Scan saat startup...")
    async with async_session_factory() as session:
        recovered = await crash_recovery_engine.recover_orphaned_jobs(session)
        print(f"[+] Pemindaian selesai. {len(recovered)} job gantung dipulihkan.")

    print("[*] Memulai Content Orchestrator, Worker Engine & Telegram C2 Bot Listener...")
    print("Tekan Ctrl+C untuk menghentikan.")

    # Jalankan worker loop dan Telegram polling secara bersamaan
    try:
        await asyncio.gather(
            worker_loop(),
            telegram_c2.start_polling(),
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        telegram_c2.stop_polling()
        print("\n[!] Sistem dihentikan oleh pengguna.")


async def run_bot_only():
    await init_db()
    print("[*] Menjalankan Telegram C2 Bot Listener saja...")
    print("Tekan Ctrl+C untuk menghentikan.")
    try:
        await telegram_c2.start_polling()
    except (KeyboardInterrupt, asyncio.CancelledError):
        telegram_c2.stop_polling()
        print("\n[!] Bot Telegram dihentikan.")


def run_dashboard_server():
    host = settings.DASHBOARD_HOST or "0.0.0.0"
    port = settings.DASHBOARD_PORT or 80
    print(f"[*] Menjalankan Web Command Center di http://pitamedia.localhost (Port {port})")
    try:
        uvicorn.run(
            "monitoring.dashboard.app:app",
            host=host,
            port=port,
            reload=False,
            log_level="info"
        )
    except Exception as e:
        if port == 80:
            print(f"[!] Port 80 tidak dapat dibuka ({e}). Beralih ke fallback Port 8080...")
            uvicorn.run(
                "monitoring.dashboard.app:app",
                host=host,
                port=8080,
                reload=False,
                log_level="info"
            )
        else:
            raise e


def main():
    parser = argparse.ArgumentParser(description="Pita Media - Autonomous Multi-Agent Content Engine")
    subparsers = parser.add_subparsers(dest="command", help="Perintah yang tersedia")

    # Command: self-check
    subparsers.add_parser("self-check", help="Periksa kesehatan 10 subsistem & koneksi API")

    # Command: dry-run
    dry_parser = subparsers.add_parser("dry-run", help="Uji coba siklus konten lengkap tanpa posting ke medsos")
    dry_parser.add_argument(
        "--pilar",
        dest="pilar",
        type=str,
        default="pita_cerita",
        choices=["pita_transformasi", "pita_mini", "pita_cerita", "pita_kreasi"],
        help="Pilar konten untuk dry-run"
    )

    # Command: daemon
    subparsers.add_parser("daemon", help="Jalankan Autonomous 24/7 background runtime")

    # Command: init-db
    subparsers.add_parser("init-db", help="Inisialisasi skema database")

    # Command: run
    subparsers.add_parser("run", help="Jalankan daemon scheduler, worker & Telegram Bot")

    # Command: bot
    subparsers.add_parser("bot", help="Jalankan listener Telegram Bot C2 saja")

    # Command: single
    single_parser = subparsers.add_parser("single", help="Eksekusi 1 siklus konten mandiri")
    single_parser.add_argument(
        "--pilar",
        "--pillar",
        dest="pilar",
        type=str,
        default="pita_cerita",
        choices=["pita_transformasi", "pita_mini", "pita_cerita", "pita_kreasi"],
        help="Pilar konten yang ingin dibuat",
    )

    # Command: dashboard
    subparsers.add_parser("dashboard", help="Jalankan Web Status Dashboard")

    # Command: backup
    subparsers.add_parser("backup", help="Buat backup database ke storage/backups")

    # Command: restore
    restore_parser = subparsers.add_parser("restore", help="Pulihkan database dari file backup")
    restore_parser.add_argument(
        "--file",
        dest="backup_file",
        type=str,
        required=True,
        help="Path ke file database backup (.db)"
    )

    args = parser.parse_args()

    if args.command == "self-check":
        results = run_startup_self_check()
        print(format_self_check_cli(results))
    elif args.command == "dry-run":
        asyncio.run(run_dry_run_simulation(args.pilar))
    elif args.command == "daemon":
        run_daemon()
    elif args.command == "init-db":
        asyncio.run(run_init_db())
    elif args.command == "run":
        asyncio.run(run_orchestrator_and_bot())
    elif args.command == "bot":
        asyncio.run(run_bot_only())
    elif args.command == "single":
        asyncio.run(run_single_cycle(args.pilar))
    elif args.command == "dashboard":
        run_dashboard_server()
    elif args.command == "backup":
        maint = StorageMaintenance()
        bk = maint.backup_database()
        print(f"[+] Backup database berhasil dibuat di: {bk}")
    elif args.command == "restore":
        maint = StorageMaintenance()
        ok = maint.restore_database(args.backup_file)
        if ok:
            print(f"[+] Database berhasil dipulihkan dari: {args.backup_file}")
        else:
            print(f"[-] Gagal memulihkan database dari: {args.backup_file}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
