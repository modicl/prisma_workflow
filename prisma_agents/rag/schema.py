"""Esquema de la base `prisma_rag` (pgvector). `crear_esquema` es idempotente."""
from rag.embedder import DIMENSIONES

DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documentos (
    id             serial PRIMARY KEY,
    fuente_url     text NOT NULL UNIQUE,
    tipo_documento text NOT NULL CHECK (tipo_documento IN ('norma', 'orientacion', 'curriculo')),
    titulo         text NOT NULL,
    sha256         text NOT NULL,
    anio           int,
    vigente        boolean NOT NULL DEFAULT true,
    ingestado_en   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fragmentos (
    id                bigserial PRIMARY KEY,
    documento_id      int NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    orden             int NOT NULL,
    ruta              text NOT NULL,
    texto             text NOT NULL,
    texto_tsv         tsvector GENERATED ALWAYS AS (to_tsvector('spanish', texto)) STORED,
    metadata          jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    embedding         vector({DIMENSIONES}) NOT NULL,
    embedding_model   text NOT NULL,
    embedding_version text NOT NULL
);

CREATE INDEX IF NOT EXISTS fragmentos_tsv_idx ON fragmentos USING gin (texto_tsv);
CREATE INDEX IF NOT EXISTS fragmentos_doc_idx ON fragmentos (documento_id);
CREATE INDEX IF NOT EXISTS fragmentos_meta_idx ON fragmentos USING gin (metadata jsonb_path_ops);
"""


async def crear_esquema(conn) -> None:
    await conn.execute(DDL)
