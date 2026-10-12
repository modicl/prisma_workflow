"""Recuperación híbrida (vector + texto completo, fusión RRF) con degradación segura.

Las funciones públicas NUNCA lanzan: ante cualquier falla devuelven "" para que el flujo siga sin fragmentos.
"""
import asyncio
import logging
import os
import re

from rag import store
from rag.embedder import GeminiEmbedder
from rag.query import construir_consulta, detectar_nee

logger = logging.getLogger(__name__)

TIEMPO_MAX = 10.0
TIEMPO_CIERRE = 2.0               # tope para cerrar la conexión: un close() colgado no puede bloquear el flujo
TIEMPO_EMBEDDING_MS = 8000       # tope de cada llamada al servicio de embeddings
TOPE_CARACTERES = 12000          # ≈ 3000 tokens
_CANDIDATOS = 20
_ENCABEZADO = "### NORMATIVA Y ORIENTACIONES OFICIALES DEL MINEDUC (referencia):"


def _embedder():
    return GeminiEmbedder(timeout_ms=TIEMPO_EMBEDDING_MS)


async def _cerrar(conn) -> None:
    """Cierre ordenado con tope; si no responde (red caída), se corta la conexión sin esperar."""
    try:
        await asyncio.wait_for(conn.close(), TIEMPO_CIERRE)
    except Exception:
        conn.terminate()


def palabras_clave(consulta: str) -> list[str]:
    """Solo letras y dígitos: así la consulta no puede inyectar sintaxis de tsquery (& | ! ( ) : *)."""
    vistas: list[str] = []
    for p in re.findall(r"\w{4,}", consulta.lower()):
        if p not in vistas:
            vistas.append(p)
    return vistas


def _ruido_eximicion(fila: dict) -> bool:
    """Orientaciones que describen la eximición como práctica (encuestas, usos indeseados) no deben llegar a los agentes:
    en la comparación con/sin RAG del 2026-10-11 el generador repitió «eximiéndolo» en una rúbrica y el crítico la bloqueó (C4).
    Las NORMAS no se filtran: el Art. 5 del D67 («no podrán ser eximidos… deberán implementar las diversificaciones») sí debe llegar."""
    return fila["tipo_documento"] == "orientacion" and re.search(r"exim", fila["texto"], re.I) is not None


def fusionar_rrf(listas: list[list[dict]], k: int = 60) -> list[dict]:
    puntajes: dict[int, float] = {}
    filas: dict[int, dict] = {}
    for lista in listas:
        for posicion, fila in enumerate(lista):
            puntajes[fila["id"]] = puntajes.get(fila["id"], 0.0) + 1.0 / (k + posicion + 1)
            filas.setdefault(fila["id"], fila)
    return [filas[i] for i in sorted(puntajes, key=puntajes.get, reverse=True)]


def ajustar_presupuesto(resultados: list[dict], tope: int = TOPE_CARACTERES) -> list[dict]:
    elegidos, usado = [], 0
    for fila in resultados:
        if elegidos and usado + len(fila["texto"]) > tope:
            break
        elegidos.append(fila)
        usado += len(fila["texto"])
    return elegidos


def formatear(resultados: list[dict], encabezado: str) -> str:
    if not resultados:
        return ""
    cuerpo = "\n\n".join(
        f'[{f["titulo"]} · {f["ruta"]}]\n{f["texto"].replace("</documento_usuario", "< /documento_usuario")}' for f in resultados
    )
    return f'{encabezado}\n<documento_usuario tipo="normativa_referencia">\n{cuerpo}\n</documento_usuario>'


async def _buscar(conn, emb, consulta: str, tipos: list[str], filtro: dict | None, k: int, descartar=None) -> list[dict]:
    vector = await emb.embeber_consulta(consulta)
    por_vector = await store.buscar_vector(conn, vector, tipos, filtro, _CANDIDATOS)
    por_texto = await store.buscar_texto(conn, palabras_clave(consulta), tipos, filtro, _CANDIDATOS)
    fusion = fusionar_rrf([por_vector, por_texto])
    if descartar is not None:
        fusion = [f for f in fusion if not descartar(f)]
    return fusion[:k]


async def _ejecutar(punto: str, asignatura: str, curso: str, diagnostico: str) -> str:
    nee = detectar_nee(diagnostico)
    emb = _embedder()
    conn = await store.conectar(os.environ["RAG_DATABASE_URL"])
    try:
        consulta = construir_consulta(punto, asignatura, curso, nee)
        if punto == "evaluacion":
            # La rúbrica se apoya en el D67: sus artículos primero, y luego orientaciones (sin el ruido sobre eximición).
            resultados = await _buscar(conn, emb, consulta, ["norma"], {"decreto": "D67"}, 3)
            resultados += await _buscar(conn, emb, consulta, ["orientacion"], None, 2, _ruido_eximicion)
        else:
            resultados = await _buscar(conn, emb, consulta, ["norma", "orientacion"], None, 4, _ruido_eximicion)
            if asignatura and curso:
                consulta_oa = construir_consulta("curriculo", asignatura, curso, [])
                resultados += await _buscar(conn, emb, consulta_oa, ["curriculo"], {"asignatura": asignatura, "curso": curso}, 2)
    finally:
        await _cerrar(conn)
    return formatear(ajustar_presupuesto(resultados), _ENCABEZADO)


async def _recuperar(punto: str, asignatura: str, curso: str, diagnostico: str) -> str:
    if not os.environ.get("RAG_DATABASE_URL"):
        return ""
    try:
        return await asyncio.wait_for(_ejecutar(punto, asignatura, curso, diagnostico), TIEMPO_MAX)
    except Exception as exc:                       # incluye TimeoutError: el RAG nunca tumba el flujo
        logger.warning("RAG no disponible (%s): el flujo sigue sin normativa de referencia", type(exc).__name__)
        return ""


async def normativa_para_adaptador(asignatura: str, curso: str, diagnostico: str) -> str:
    return await _recuperar("adaptador", asignatura, curso, diagnostico)


async def normativa_para_evaluacion(asignatura: str, curso: str, diagnostico: str) -> str:
    return await _recuperar("evaluacion", asignatura, curso, diagnostico)
