"""Acceso a la base vectorial. Una conexión por operación: el volumen es bajo y evita estado compartido."""
import json

import asyncpg

from rag.embedder import a_literal

_COLUMNAS = "f.id, f.ruta, f.texto, f.metadata, d.titulo, d.tipo_documento, d.anio"
_FILTRO = "d.vigente AND d.tipo_documento = ANY($2::text[]) AND f.metadata @> $3::jsonb"


async def conectar(url: str, timeout: float = 5.0):
    return await asyncpg.connect(url, timeout=timeout)


def _resultado(fila) -> dict:
    d = dict(fila)
    if isinstance(d["metadata"], str):
        d["metadata"] = json.loads(d["metadata"])
    return d


async def obtener_documento(conn, fuente_url: str) -> dict | None:
    fila = await conn.fetchrow("SELECT id, sha256, vigente FROM documentos WHERE fuente_url = $1", fuente_url)
    return dict(fila) if fila else None


async def reemplazar_documento(conn, doc: dict, fragmentos: list[dict]) -> int:
    """Inserta o actualiza el documento y reemplaza TODOS sus fragmentos, en una transacción."""
    async with conn.transaction():
        doc_id = await conn.fetchval(
            """
            INSERT INTO documentos (fuente_url, tipo_documento, titulo, sha256, anio, vigente)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (fuente_url) DO UPDATE SET
                tipo_documento = EXCLUDED.tipo_documento, titulo = EXCLUDED.titulo, sha256 = EXCLUDED.sha256,
                anio = EXCLUDED.anio, vigente = EXCLUDED.vigente, ingestado_en = now()
            RETURNING id
            """,
            doc["fuente_url"], doc["tipo_documento"], doc["titulo"], doc["sha256"], doc.get("anio"), doc.get("vigente", True),
        )
        await conn.execute("DELETE FROM fragmentos WHERE documento_id = $1", doc_id)
        await conn.executemany(
            """
            INSERT INTO fragmentos (documento_id, orden, ruta, texto, metadata, embedding, embedding_model, embedding_version)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::vector, $7, $8)
            """,
            [
                (doc_id, f["orden"], f["ruta"], f["texto"], json.dumps(f["metadata"], ensure_ascii=False),
                 a_literal(f["embedding"]), f["embedding_model"], f["embedding_version"])
                for f in fragmentos
            ],
        )
    return doc_id


async def buscar_vector(conn, vector, tipos, filtro, limite) -> list[dict]:
    filas = await conn.fetch(
        f"""
        SELECT {_COLUMNAS}, f.embedding <=> $1::vector AS distancia
        FROM fragmentos f JOIN documentos d ON d.id = f.documento_id
        WHERE {_FILTRO}
        ORDER BY distancia LIMIT $4
        """,
        a_literal(vector), list(tipos), json.dumps(filtro or {}), limite,
    )
    return [_resultado(f) for f in filas]


async def buscar_texto(conn, palabras, tipos, filtro, limite) -> list[dict]:
    """`palabras` ya viene saneada (solo letras y dígitos): se combinan con OR para no exigir todas a la vez."""
    if not palabras:
        return []
    filas = await conn.fetch(
        f"""
        SELECT {_COLUMNAS}, ts_rank(f.texto_tsv, q) AS rango
        FROM fragmentos f JOIN documentos d ON d.id = f.documento_id, to_tsquery('spanish', $1) q
        WHERE f.texto_tsv @@ q AND {_FILTRO}
        ORDER BY rango DESC LIMIT $4
        """,
        " | ".join(palabras), list(tipos), json.dumps(filtro or {}), limite,
    )
    return [_resultado(f) for f in filas]
