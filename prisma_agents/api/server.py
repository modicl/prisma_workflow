"""
server.py — API FastAPI para recibir feedback de docentes.

Endpoints:
    POST /feedback          Registra un voto 👍/👎 con session_id
    GET  /feedback/{sid}    Consulta el feedback de una sesión
    GET  /health            Healthcheck

Arranque:
    cd prisma_agents
    uvicorn api.server:app --port 8000 --reload
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

load_dotenv(Path(__file__).parent.parent / ".env")

_raw_db_url = os.environ.get("BD_LOGS", "")
DB_URL = _raw_db_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgres+asyncpg://", "postgres://")


# ── Pool de conexiones ────────────────────────────────────────────────────────

_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    if not DB_URL:
        raise RuntimeError("BD_LOGS no configurado en .env")
    _pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=5)
    # Asegurar que las tablas existen
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS session_feedback (
                id         SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                rating     TEXT NOT NULL CHECK (rating IN ('thumbs_up', 'thumbs_down')),
                reason     TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    yield
    await _pool.close()


app = FastAPI(title="PRISMA Feedback API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restringir en producción
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Modelos ───────────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    session_id: str
    rating: str
    reason: str | None = None

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, v: str) -> str:
        if v not in ("thumbs_up", "thumbs_down"):
            raise ValueError("rating debe ser 'thumbs_up' o 'thumbs_down'")
        return v

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("session_id no puede estar vacío")
        return v.strip()


class FeedbackResponse(BaseModel):
    id: int
    session_id: str
    rating: str
    reason: str | None
    created_at: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/feedback", response_model=FeedbackResponse, status_code=201)
async def create_feedback(body: FeedbackRequest):
    """Registra el voto 👍/👎 de un docente para una sesión."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO session_feedback (session_id, rating, reason)
            VALUES ($1, $2, $3)
            RETURNING id, session_id, rating, reason, created_at
            """,
            body.session_id,
            body.rating,
            body.reason,
        )
    return FeedbackResponse(
        id=row["id"],
        session_id=row["session_id"],
        rating=row["rating"],
        reason=row["reason"],
        created_at=row["created_at"].isoformat(),
    )


@app.get("/feedback/{session_id}", response_model=list[FeedbackResponse])
async def get_feedback(session_id: str):
    """Retorna todos los votos registrados para una sesión."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, session_id, rating, reason, created_at
            FROM session_feedback
            WHERE session_id = $1
            ORDER BY created_at DESC
            """,
            session_id,
        )
    if not rows:
        raise HTTPException(status_code=404, detail="No hay feedback para esta sesión")
    return [
        FeedbackResponse(
            id=r["id"],
            session_id=r["session_id"],
            rating=r["rating"],
            reason=r["reason"],
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]
