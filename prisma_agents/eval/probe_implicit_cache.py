"""
Sonda: ¿está activa la caché implícita de Gemini para nuestra cuenta/modelo?

Gemini 2.5 trae caché implícita activada por defecto: si el prefijo del prompt
se repite entre llamadas (y supera el mínimo de tokens), la API descuenta esos
tokens y los reporta en usage_metadata.cached_content_token_count.

Esta sonda hace varias llamadas idénticas con un prefijo grande y estable, y
muestra si la 2ª+ llamada reporta tokens cacheados. Es barata (pocas llamadas,
thinking_budget=0, salida mínima) — NO corre el flujo completo de PRISMA.

Uso:
    python eval/probe_implicit_cache.py
    python eval/probe_implicit_cache.py --calls 4
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from google import genai
from google.genai import types as genai_types

# Modelo real de los 4 agentes del flujo (agents/*.py). La caché implícita es
# por modelo, así que medimos sobre el que de verdad consume tokens.
MODEL = "gemini-3.1-flash-lite"

# Prefijo estable y grande (~varios miles de tokens) para superar el mínimo de
# caché implícita. Imita un documento que se reenvía sin cambios entre llamadas.
_STABLE_PREFIX = (
    "Eres un asistente que analiza documentos educativos del sistema escolar "
    "chileno bajo los Decretos 170/2010, 83/2015 y 67/2018. A continuación se "
    "presenta un documento extenso de referencia que debes considerar.\n\n"
    + ("Párrafo de contexto normativo sobre adecuaciones curriculares, diseño "
       "universal de aprendizaje (DUA), evaluación diferenciada y necesidades "
       "educativas especiales permanentes y transitorias. " * 120)
)


def _usage(resp) -> dict:
    u = resp.usage_metadata
    return {
        "prompt": getattr(u, "prompt_token_count", None),
        "cached": getattr(u, "cached_content_token_count", None),
        "candidates": getattr(u, "candidates_token_count", None),
        "total": getattr(u, "total_token_count", None),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=3, help="Número de llamadas idénticas.")
    ap.add_argument("--model", default=MODEL, help=f"Modelo a sondear (default: {MODEL}).")
    args = ap.parse_args()

    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    contents = _STABLE_PREFIX + "\n\nResponde solo con la palabra: LISTO."
    cfg = genai_types.GenerateContentConfig(
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        max_output_tokens=8,
        temperature=0.0,
    )

    print(f"Modelo: {args.model}  |  prefijo estable ≈ {len(_STABLE_PREFIX):,} chars\n")
    print(f"{'#':>2}  {'prompt_tok':>11}  {'cached_tok':>11}  {'cand_tok':>9}  {'total':>7}")
    print("─" * 50)

    any_cache_hit = False
    for i in range(1, args.calls + 1):
        resp = client.models.generate_content(model=args.model, contents=contents, config=cfg)
        u = _usage(resp)
        cached = u["cached"] or 0
        if i >= 2 and cached > 0:
            any_cache_hit = True
        print(f"{i:>2}  {str(u['prompt']):>11}  {str(u['cached']):>11}  "
              f"{str(u['candidates']):>9}  {str(u['total']):>7}")

    print("─" * 50)
    if any_cache_hit:
        print("✓ Caché implícita ACTIVA — la 2ª+ llamada reporta tokens cacheados.")
    else:
        print("✗ Sin aciertos de caché implícita en esta corrida.")
        print("  Posibles causas: prefijo bajo el mínimo de tokens, latencia entre")
        print("  llamadas, o el modelo/cuenta no la aplica. Revisar docs de Gemini.")


if __name__ == "__main__":
    main()
