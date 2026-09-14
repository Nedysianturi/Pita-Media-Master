"""
Modul Audit Logger untuk Sistem Pita Media.
Merekam jejak audit terstruktur (level, komponen, job_id, message, metadata).
"""

from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import AuditLog


class AuditLogger:
    async def log(
        self,
        db_session: AsyncSession,
        level: str,
        component: str,
        message: str,
        job_id: Optional[str] = None,
        content_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        audit_entry = AuditLog(
            level=level.upper(),
            component=component,
            message=message,
            job_id=job_id,
            content_id=content_id,
            details=details,
        )
        db_session.add(audit_entry)
        await db_session.commit()
        return audit_entry


audit_logger = AuditLogger()
