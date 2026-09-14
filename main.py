"""
Titik Masuk Utama (Main CLI Entrypoint) Sistem Pita Media.
Penggunaan:
  python main.py init-db             : Inisialisasi skema tabel database
  python main.py run                 : Menjalankan orchestrator, scheduler, worker loop & Telegram C2 polling
  python main.py bot                 : Menjalankan listener Telegram Bot C2 saja
  python main.py single --pilar ...  : Menjalankan 1 siklus kreasi pilar langsung (end-to-end)
  python main.py dashboard           : Menjalankan protected web status dashboard
  python main.py backup              : Mengambil backup snapshot database
"""

import sys
import asyncio
import argparse
import uvicorn
from pathlib import Path

from config.settings import settings
from database.connection import init_db, backup_database
from core.scheduler import content_orchestrator
from core.resilience.crash_recovery import crash_recovery_engine
from database.connection import async_session_factory
from monitoring.telegram_bot import telegram_c2


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
    print(f"[*] Menjalankan Status Dashboard di http://{settings.DASHBOARD_HOST}:{settings.DASHBOARD_PORT}")
    uvicorn.run(
        "monitoring.dashboard.app:app",
        host=settings.DASHBOARD_HOST,
        port=settings.DASHBOARD_PORT,
        reload=False,
    )


def main():
    parser = argparse.ArgumentParser(description="Pita Media - Autonomous Multi-Agent Content Engine")
    subparsers = parser.add_subparsers(dest="command", help="Perintah yang tersedia")

    # Command: init-db
    subparsers.add_parser("init-db", help="Inisialisasi database")

    # Command: run
    subparsers.add_parser("run", help="Jalankan daemon scheduler, worker & Telegram Bot")

    # Command: bot
    subparsers.add_parser("bot", help="Jalankan listener Telegram Bot C2 saja")

    # Command: single
    single_parser = subparsers.add_parser("single", help="Eksekusi 1 siklus konten mandiri")
    single_parser.add_argument(
        "--pilar",
        type=str,
        default="pita_cerita",
        choices=["pita_transformasi", "pita_mini", "pita_cerita", "pita_kreasi"],
        help="Pilar konten yang ingin dibuat",
    )

    # Command: dashboard
    subparsers.add_parser("dashboard", help="Jalankan Web Status Dashboard terproteksi")

    # Command: backup
    subparsers.add_parser("backup", help="Buat backup database ke storage/backups")

    args = parser.parse_args()

    if args.command == "init-db":
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
        bk = backup_database()
        print(f"[+] Backup database berhasil dibuat di: {bk}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
