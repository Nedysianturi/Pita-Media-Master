"""
Synchronous and Helper Database Utilities for Pita Media.
Provides `get_db()` context manager for synchronous database sessions
alongside async database operations, and automatic schema column migration.
"""

from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from config.settings import settings
from database.models import Base

# Synchronous engine
sync_db_url = settings.DATABASE_URL
if sync_db_url.startswith("sqlite+aiosqlite:///"):
    sync_db_url = sync_db_url.replace("sqlite+aiosqlite:///", "sqlite:///")

from sqlalchemy.pool import NullPool

sync_engine = create_engine(
    sync_db_url,
    poolclass=NullPool,
    connect_args={"check_same_thread": False, "timeout": 30.0} if "sqlite" in sync_db_url else {}
)

from sqlalchemy import event
@event.listens_for(sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if "sqlite" in sync_db_url:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
        dbapi_connection.isolation_level = None

# Ensure tables exist
Base.metadata.create_all(bind=sync_engine)

_migrated = False
def _migrate_columns():
    global _migrated
    if _migrated:
        return
    try:
        with sync_engine.begin() as conn:
            # Check publishing_receipts
            try:
                res = conn.execute(text("PRAGMA table_info(publishing_receipts)")).fetchall()
                existing_cols = {row[1] for row in res}
                cols_to_add = [
                    ("post_id", "VARCHAR(150)"),
                    ("created_at", "DATETIME"),
                    ("app_mode", "VARCHAR(20) DEFAULT 'DRY_RUN'"),
                    ("verified", "BOOLEAN DEFAULT 0"),
                    ("metrics", "JSON")
                ]
                for col_name, col_type in cols_to_add:
                    if col_name not in existing_cols:
                        conn.execute(text(f"ALTER TABLE publishing_receipts ADD COLUMN {col_name} {col_type}"))
            except Exception:
                pass

            # Check ab_experiments
            try:
                res = conn.execute(text("PRAGMA table_info(ab_experiments)")).fetchall()
                existing_cols = {row[1] for row in res}
                cols_to_add = [
                    ("experiment_type", "VARCHAR(50)"),
                    ("content_id_a", "VARCHAR(36)"),
                    ("content_id_b", "VARCHAR(36)"),
                    ("metrics_a", "JSON"),
                    ("metrics_b", "JSON")
                ]
                for col_name, col_type in cols_to_add:
                    if col_name not in existing_cols:
                        conn.execute(text(f"ALTER TABLE ab_experiments ADD COLUMN {col_name} {col_type}"))
            except Exception:
                pass

            # Check publications
            try:
                res = conn.execute(text("PRAGMA table_info(publications)")).fetchall()
                existing_cols = {row[1] for row in res}
                if "external_post_id" not in existing_cols:
                    conn.execute(text("ALTER TABLE publications ADD COLUMN external_post_id VARCHAR(150)"))
            except Exception:
                pass

            # Check audit_logs
            try:
                res = conn.execute(text("PRAGMA table_info(audit_logs)")).fetchall()
                existing_cols = {row[1] for row in res}
                if "created_at" not in existing_cols:
                    conn.execute(text("ALTER TABLE audit_logs ADD COLUMN created_at DATETIME"))
            except Exception:
                pass
        _migrated = True
    except Exception:
        pass

_migrate_columns()

SyncSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine, expire_on_commit=False)


@contextmanager
def get_db():
    """Transactional synchronous database session context manager."""
    session: Session = SyncSessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
