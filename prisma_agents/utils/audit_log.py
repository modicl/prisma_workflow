"""
audit_log.py — Registro de auditoría de decisiones HITL del docente.

Persiste en PostgreSQL (BD_LOGS) un historial APPEND-ONLY de "quién aprobó/rechazó
qué y cuándo" en el checkpoint de revisión humana. A diferencia de los logs de
aplicación, este registro es evidencia: la rúbrica tiene efectos legales sobre un
menor bajo los Decretos 67/83/170, y debe poder demostrarse quién la validó.

Garantías de diseño:
- APPEND-ONLY: este módulo solo inserta. Nunca actualiza ni borra filas.
- NO bloqueante para el flujo: cualquier fallo de BD se traga con un warning;
  el workflow del agente continúa. La auditoría no debe poder tumbar el flujo.
- Opcional: si BD_LOGS no está configurado (CLI/dev), es un no-op silencioso.

La tabla `hitl_approvals` se crea con las migraciones del equipo
(ver eval/db_migrations.py o sql/audit_hitl.sql). Este módulo asume que existe.
"""

import asyncio
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

# Timeout defensivo: una escritura de auditoría jamás debe colgar el flujo del agente.
_WRITE_TIMEOUT_SECONDS = 5


def _normalize_url(db_url: str) -> str:
    """Convierte postgresql+asyncpg:// → postgresql:// para asyncpg."""
    return (
        db_url.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres+asyncpg://", "postgres://")
    )


def _plan_fingerprint(plan_text: str) -> str | None:
    """SHA-256 del plan revisado — prueba qué versión exacta se aprobó/rechazó."""
    if not plan_text:
        return None
    return hashlib.sha256(plan_text.encode("utf-8")).hexdigest()


async def record_hitl_decision(
    *,
    session_id: str,
    teacher_id: str | None,
    approved: bool,
    reason: str | None,
    attempt: int,
    max_attempts: int,
    agent_to_retry: int | None,
    plan_reviewed: str | None = None,
) -> None:
    """Registra una decisión HITL en la tabla de auditoría.

    Nunca lanza excepción al llamador: si BD_LOGS no está configurado o la
    escritura falla, registra un warning y retorna. El flujo del agente sigue.

    Args:
        session_id: ID de la sesión PRISMA.
        teacher_id: sub del JWT del docente (owner_id). None en sesiones legacy.
        approved: True si aprobó, False si rechazó.
        reason: Motivo del rechazo o comentario del docente.
        attempt / max_attempts: Posición del checkpoint en el loop HITL.
        agent_to_retry: Agente a reintentar si rechazó (1 o 2), o None.
        plan_reviewed: Texto del plan que se estaba revisando (se guarda solo su hash).
    """
    db_url = os.environ.get("BD_LOGS")
    if not db_url:
        return  # CLI/dev sin persistencia — no-op

    try:
        await asyncio.wait_for(
            _insert(
                db_url=db_url,
                session_id=session_id,
                teacher_id=teacher_id,
                approved=approved,
                reason=reason,
                attempt=attempt,
                max_attempts=max_attempts,
                agent_to_retry=agent_to_retry,
                plan_sha256=_plan_fingerprint(plan_reviewed or ""),
            ),
            timeout=_WRITE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Auditoría HITL: timeout escribiendo decisión de sesión %s (el flujo continúa).",
            session_id,
        )
    except Exception as exc:
        logger.warning(
            "Auditoría HITL: no se pudo registrar la decisión de sesión %s: %s (el flujo continúa).",
            session_id,
            exc,
        )


async def _insert(
    *,
    db_url: str,
    session_id: str,
    teacher_id: str | None,
    approved: bool,
    reason: str | None,
    attempt: int,
    max_attempts: int,
    agent_to_retry: int | None,
    plan_sha256: str | None,
) -> None:
    import asyncpg

    conn = await asyncpg.connect(_normalize_url(db_url))
    try:
        await conn.execute(
            """
            INSERT INTO hitl_approvals
                (session_id, teacher_id, approved, reason,
                 attempt, max_attempts, agent_to_retry, plan_sha256)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            session_id,
            teacher_id,
            approved,
            reason,
            attempt,
            max_attempts,
            agent_to_retry,
            plan_sha256,
        )
    finally:
        await conn.close()
