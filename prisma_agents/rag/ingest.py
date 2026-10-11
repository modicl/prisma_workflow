"""
ingest.py — Ingesta manual y repetible del corpus público del Mineduc hacia la base vectorial.

  cd prisma_agents
  RAG_DATABASE_URL=postgresql://... GOOGLE_API_KEY=... python -m rag.ingest [--solo TEXTO] [--forzar] [--descargas DIR]

Es idempotente: si el sha256 de un documento no cambió, no hace nada; si cambió, reemplaza solo sus fragmentos.
Descarga cada documento una vez, con pausa entre peticiones. No es un crawler: solo baja lo que lista el manifiesto
(el robots.txt de especial.mineduc.cl responde 403 a clientes automáticos).
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from rag import store
from rag.chunking import fragmentar_curriculo, fragmentar_norma, fragmentar_orientacion
from rag.embedder import GeminiEmbedder
from rag.nombres import asignatura_legible, curso_legible, modalidad_de_curso, slug_a_asignatura, slug_a_curso

logger = logging.getLogger(__name__)

USER_AGENT = "PRISMA-RAG-ingesta/1.0 (proyecto academico)"
RAIZ_CURRICULO = "https://www.curriculumnacional.cl"
_ENLACE_CURSO = re.compile(r'href="(/curriculum/(?P<ciclo>[^/"#?]+)/(?P<asig>[^/"#?]+)/(?P<curso>[^/"#?]+))"')


def descargar_http(url: str) -> bytes:
    seguro = urllib.parse.quote(url, safe=":/?&=%#")           # algunos nombres traen «º» o «ó»
    peticion = urllib.request.Request(seguro, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(peticion, timeout=90) as r:
        return r.read()


def texto_de_pdf(datos: bytes) -> list[str]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(datos)
    try:
        return [pdf[i].get_textpage().get_text_range().replace("\r\n", "\n") for i in range(len(pdf))]
    finally:
        pdf.close()


def descubrir_curriculo(descargar, ciclos: list[str]) -> list[dict]:
    entradas, vistos = [], set()
    for ciclo in ciclos:
        html = descargar(f"{RAIZ_CURRICULO}/curriculum/{ciclo}").decode("utf-8", "replace")
        for m in _ENLACE_CURSO.finditer(html):
            ruta, asig_slug, curso_slug = m.group(1), m["asig"], m["curso"]
            curso = slug_a_curso(curso_slug)
            if m["ciclo"] != ciclo or curso is None or asig_slug.endswith("-propuesta") or ruta in vistos:
                continue
            vistos.add(ruta)
            asignatura = slug_a_asignatura(asig_slug)
            entradas.append({
                "url": f"{RAIZ_CURRICULO}{ruta}", "tipo_documento": "curriculo", "formato": "html",
                "titulo": f"{asignatura_legible(asignatura)} {curso_legible(curso)} — Currículum Nacional",
                "anio": None, "vigente": True, "asignatura": asignatura, "curso": curso,
                "modalidad": modalidad_de_curso(curso_slug),
            })
    return entradas


def cargar_manifiesto(ruta, descargar=descargar_http) -> list[dict]:
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    return datos["documentos"] + descubrir_curriculo(descargar, datos.get("curriculo_ciclos", []))


def fragmentar_entrada(entrada: dict, datos: bytes):
    if entrada["formato"] == "html":
        return fragmentar_curriculo(datos.decode("utf-8", "replace"), entrada["asignatura"], entrada["curso"], entrada.get("modalidad"))
    paginas = texto_de_pdf(datos)
    if entrada["tipo_documento"] == "norma":
        return fragmentar_norma("\n".join(paginas), entrada.get("decreto") or entrada["titulo"])
    return fragmentar_orientacion(paginas)


def _guardar(directorio: Path, url: str, datos: bytes) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    partes = urllib.parse.urlparse(url)
    nombre = re.sub(r"[^\w.\-]+", "_", f"{partes.netloc.split('.')[0]}__{Path(partes.path).name or 'index'}")
    (directorio / nombre).write_bytes(datos)


async def ingerir(entradas, conn, embedder, descargar=descargar_http, pausa: float = 1.5,
                  forzar: bool = False, directorio_descargas=None) -> dict:
    resumen = {"nuevos": 0, "actualizados": 0, "sin_cambios": 0, "sin_texto": [], "errores": [], "fragmentos": 0}
    for i, e in enumerate(entradas):
        try:
            if i and pausa:
                await asyncio.sleep(pausa)
            datos = await asyncio.to_thread(descargar, e["url"])
            if directorio_descargas:
                _guardar(Path(directorio_descargas), e["url"], datos)
            sha = hashlib.sha256(datos).hexdigest()
            previo = await store.obtener_documento(conn, e["url"])
            if previo and previo["sha256"] == sha and not forzar:
                resumen["sin_cambios"] += 1
                continue
            fragmentos = fragmentar_entrada(e, datos)
            if not fragmentos:
                resumen["sin_texto"].append(e["url"])
                continue
            vectores = await embedder.embeber_documentos([f'{e["titulo"]} · {f.ruta}\n{f.texto}' for f in fragmentos])
            await store.reemplazar_documento(
                conn,
                {"fuente_url": e["url"], "tipo_documento": e["tipo_documento"], "titulo": e["titulo"],
                 "sha256": sha, "anio": e.get("anio"), "vigente": e.get("vigente", True)},
                [{"orden": n, "ruta": f.ruta, "texto": f.texto, "metadata": f.metadata, "embedding": v,
                  "embedding_model": embedder.nombre, "embedding_version": embedder.version}
                 for n, (f, v) in enumerate(zip(fragmentos, vectores))],
            )
            resumen["actualizados" if previo else "nuevos"] += 1
            resumen["fragmentos"] += len(fragmentos)
        except Exception as exc:
            resumen["errores"].append(f'{e["url"]}: {type(exc).__name__}: {exc}')
    return resumen


async def _correr(args) -> int:  # pragma: no cover
    from rag import schema

    url = os.environ.get("RAG_DATABASE_URL")
    if not url:
        print("Falta RAG_DATABASE_URL", file=sys.stderr)
        return 2
    entradas = cargar_manifiesto(args.manifiesto)
    if args.solo:
        entradas = [e for e in entradas if args.solo.lower() in (e["titulo"] + e["url"]).lower()]
    print(f"{len(entradas)} documentos a revisar")
    conn = await store.conectar(url, timeout=30)
    try:
        await schema.crear_esquema(conn)
        r = await ingerir(entradas, conn, GeminiEmbedder(), forzar=args.forzar, directorio_descargas=args.descargas)
    finally:
        await conn.close()
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in r.items()}, ensure_ascii=False))
    for linea in r["errores"] + [f"sin texto: {u}" for u in r["sin_texto"]]:
        print(" -", linea)
    return 1 if r["errores"] else 0


def main(argv=None):  # pragma: no cover
    p = argparse.ArgumentParser(description="Ingesta del corpus normativo hacia la base vectorial")
    p.add_argument("--manifiesto", default=str(Path(__file__).with_name("corpus.json")))
    p.add_argument("--solo", help="Texto que debe aparecer en el título o la URL")
    p.add_argument("--forzar", action="store_true", help="Reingerir aunque el sha256 no haya cambiado")
    p.add_argument("--descargas", help="Directorio donde guardar los archivos originales")
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(_correr(p.parse_args(argv))))


if __name__ == "__main__":  # pragma: no cover
    main()
