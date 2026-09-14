"""
Manajemen Koneksi Database Asinkron & Sesi untuk Pita Media.
Mendukung mode SQLite WAL untuk konkurensi tinggi dan fitur backup otomatis.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy import event, text

from config.settings import settings
from database.models import Base


def get_engine() -> AsyncEngine:
    db_url = settings.DATABASE_URL
    # Normalisasi otomatis ke async driver jika menggunakan SQLite
    if db_url.startswith("sqlite:///") and not db_url.startswith("sqlite+aiosqlite:///"):
        db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///")

    connect_args = {}
    if "sqlite" in db_url:
        connect_args["check_same_thread"] = False

    engine = create_async_engine(
        db_url,
        echo=False,
        connect_args=connect_args,
    )

    if settings.SQLITE_WAL_MODE and "sqlite" in db_url:
        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = get_engine()
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Inisialisasi tabel database jika belum ada."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency generator untuk sesi database asinkron."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def backup_database() -> Path:
    """Melakukan snapshot backup database SQLite ke direktori storage/backups/."""
    db_url = settings.DATABASE_URL
    if "sqlite" in db_url:
        path_str = db_url.split("///")[-1]
        src_path = Path(path_str)
        if not src_path.is_absolute():
            src_path = settings.storage_dir / src_path.name

        if src_path.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_file = settings.backup_dir / f"pita_media_backup_{timestamp}.db"
            shutil.copy2(src_path, backup_file)
            return backup_file
    return Path("")
