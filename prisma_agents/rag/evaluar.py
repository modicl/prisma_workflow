"""
evaluar.py — Mide recall@k de la recuperación contra un set de oro.

  cd prisma_agents && RAG_DATABASE_URL=... GOOGLE_API_KEY=... python -m rag.evaluar [--k 5]

Un acierto = alguno de los k primeros resultados cumple a la vez: el título coincide con `doc` (regex) y el texto con
`esperado` (regex). Mide la misma ruta híbrida que usa el flujo (sin el filtro de currículo).
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path


async def recall_en_k(items, buscar, k: int = 5) -> dict:
    aciertos, fallos = 0, []
    for item in items:
        resultados = await buscar(item["pregunta"], k)
        if any(re.search(item["doc"], r["titulo"], re.I) and re.search(item["esperado"], r["texto"], re.I) for r in resultados[:k]):
            aciertos += 1
        else:
            fallos.append(item["pregunta"])
    total = len(items)
    return {"total": total, "aciertos": aciertos, "recall": aciertos / total if total else 0.0, "fallos": fallos}


async def _correr(args) -> int:  # pragma: no cover
    from rag import retriever, store

    url = os.environ.get("RAG_DATABASE_URL")
    if not url:
        print("Falta RAG_DATABASE_URL", file=sys.stderr)
        return 2
    items = json.loads(Path(args.set).read_text(encoding="utf-8"))
    emb = retriever._embedder()
    conn = await store.conectar(url, timeout=30)
    try:
        async def buscar(pregunta, k):
            return await retriever._buscar(conn, emb, pregunta, ["norma", "orientacion", "curriculo"], None, k)

        r = await recall_en_k(items, buscar, args.k)
    finally:
        await conn.close()
    print(f"recall@{args.k} = {r['recall']:.0%} ({r['aciertos']}/{r['total']})")
    for f in r["fallos"]:
        print(" - sin acierto:", f)
    return 0 if r["recall"] >= 0.8 else 1


def main(argv=None):  # pragma: no cover
    p = argparse.ArgumentParser()
    p.add_argument("--set", default=str(Path(__file__).with_name("eval_set.json")))
    p.add_argument("--k", type=int, default=5)
    sys.exit(asyncio.run(_correr(p.parse_args(argv))))


if __name__ == "__main__":  # pragma: no cover
    main()
