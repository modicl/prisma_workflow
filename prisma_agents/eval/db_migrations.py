"""
db_migrations.py — Migraciones SQL para el sistema de evaluación.

Crea las tablas necesarias si no existen:
  - session_feedback : votos 👍/👎 de docentes desde la web
  - eval_results     : registro de sesiones evaluadas y sus scores
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

MIGRATIONS = [
    # Feedback de docentes (👍/👎 desde la web)
    """
    CREATE TABLE IF NOT EXISTS session_feedback (
        id            SERIAL PRIMARY KEY,
        session_id    TEXT NOT NULL,
        rating        TEXT NOT NULL CHECK (rating IN ('thumbs_up', 'thumbs_down')),
        reason        TEXT,
        created_at    TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # Índice para búsquedas por session_id
    """
    CREATE INDEX IF NOT EXISTS idx_session_feedback_session_id
        ON session_feedback (session_id)
    """,
    # Resultados de evaluaciones (qué sesiones ya se evaluaron)
    """
    CREATE TABLE IF NOT EXISTS eval_results (
        id              SERIAL PRIMARY KEY,
        session_id      TEXT NOT NULL UNIQUE,
        evaluated_at    TIMESTAMPTZ DEFAULT NOW(),
        nee_type        TEXT,
        golden_match    TEXT,
        end_to_end      FLOAT,
        pass            BOOLEAN,
        report_json     JSONB,
        triggered_by    TEXT
    )
    """,
    # Índice para filtrar sesiones no evaluadas eficientemente
    """
    CREATE INDEX IF NOT EXISTS idx_eval_results_session_id
        ON eval_results (session_id)
    """,
]


def _normalize_url(db_url: str) -> str:
    """Convierte postgresql+asyncpg:// → postgresql:// para asyncpg."""
    return db_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgres+asyncpg://", "postgres://")


async def run_migrations(db_url: str) -> None:
    conn = await asyncpg.connect(_normalize_url(db_url))
    try:
        for sql in MIGRATIONS:
            await conn.execute(sql.strip())
        print("  ✓ Migraciones aplicadas correctamente.")
    finally:
        await conn.close()


async def get_unevaluated_sessions(db_url: str, sample_pct: int) -> list[str]:
    """Retorna session_ids no evaluados, muestreando sample_pct%."""
    conn = await asyncpg.connect(_normalize_url(db_url))
    try:
        rows = await conn.fetch(
            """
            SELECT s.session_id
            FROM (
                SELECT DISTINCT state->>'session_id' AS session_id
                FROM sessions
                WHERE state->>'status' IS NOT NULL
            ) s
            WHERE s.session_id NOT IN (
                SELECT session_id FROM eval_results
            )
            ORDER BY RANDOM()
            LIMIT GREATEST(1, (
                SELECT COUNT(*) * $1 / 100
                FROM sessions
                WHERE state->>'status' IS NOT NULL
            ))
            """,
            sample_pct,
        )
        return [r["session_id"] for r in rows if r["session_id"]]
    finally:
        await conn.close()


async def get_edge_case_sessions(db_url: str) -> list[tuple[str, str]]:
    """Retorna (session_id, reason) de sesiones con 👎 aún no evaluadas."""
    conn = await asyncpg.connect(_normalize_url(db_url))
    try:
        rows = await conn.fetch(
            """
            SELECT sf.session_id, sf.reason
            FROM session_feedback sf
            LEFT JOIN eval_results er ON sf.session_id = er.session_id
            WHERE sf.rating = 'thumbs_down'
              AND er.id IS NULL
            ORDER BY sf.created_at ASC
            """
        )
        return [(r["session_id"], r["reason"] or "") for r in rows]
    finally:
        await conn.close()


async def save_eval_result(db_url: str, report: dict, triggered_by: str) -> None:
    """Guarda el resultado de una evaluación en eval_results."""
    import json
    conn = await asyncpg.connect(_normalize_url(db_url))
    try:
        await conn.execute(
            """
            INSERT INTO eval_results
                (session_id, nee_type, golden_match, end_to_end, pass, report_json, triggered_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (session_id) DO UPDATE SET
                evaluated_at = NOW(),
                nee_type     = EXCLUDED.nee_type,
                golden_match = EXCLUDED.golden_match,
                end_to_end   = EXCLUDED.end_to_end,
                pass         = EXCLUDED.pass,
                report_json  = EXCLUDED.report_json,
                triggered_by = EXCLUDED.triggered_by
            """,
            report.get("run_id", ""),
            report.get("case_id"),
            report.get("golden_match"),
            report.get("end_to_end"),
            report.get("pass"),
            json.dumps(report),
            triggered_by,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    db_url = os.environ.get("BD_LOGS")
    if not db_url:
        print("Error: BD_LOGS no configurado en .env")
        sys.exit(1)
    asyncio.run(run_migrations(db_url))
