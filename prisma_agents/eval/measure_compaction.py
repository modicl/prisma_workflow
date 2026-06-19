"""
Mide el ahorro del compactador determinista (utils/text_compactor).

Uso:
    # Sobre un archivo de texto (p. ej. una extracción guardada):
    python eval/measure_compaction.py ruta/al/texto_extraido.txt

    # Sobre el sample de demostración incluido:
    python eval/measure_compaction.py --demo

    # Reportar tokens reales de Gemini (consume cuota mínima; requiere GOOGLE_API_KEY):
    python eval/measure_compaction.py ruta.txt --tokens

Reporta caracteres y líneas antes/después. Los caracteres son la métrica
determinista confiable; ~chars/4 es un proxy grueso de tokens. Con --tokens
se cuenta con el tokenizador real de Gemini vía count_tokens.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.text_compactor import compact_text

# Texto que imita salida típica de OCR/extracción: blank lines de sobra,
# números de página sueltos, headers repetidos, espaciado irregular.
_DEMO = """ESTABLECIMIENTO EDUCACIONAL LOS ALERCES


ESTABLECIMIENTO EDUCACIONAL LOS ALERCES

Plan de Adecuaciones Curriculares Individualizadas


Estudiante:    Juan   Pérez


Diagnóstico:     TEA (Trastorno del Espectro Autista)



1



Decreto 83/2015 — adecuaciones de    acceso y    de objetivos.



Página 2 de 12



El estudiante requiere apoyo visual y    tiempos extendidos de evaluación.


"""


def _count_tokens(text: str) -> int | None:
    try:
        from google import genai
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
        resp = client.models.count_tokens(model="gemini-3.1-flash-lite", contents=text)
        return resp.total_tokens
    except Exception as e:
        print(f"  (no se pudieron contar tokens reales: {e})")
        return None


def _pct(before: int, after: int) -> str:
    if before == 0:
        return "0.0%"
    return f"{(1 - after / before) * 100:.1f}%"


def measure(text: str, with_tokens: bool = False) -> None:
    compacted = compact_text(text)

    chars_b, chars_a = len(text), len(compacted)
    lines_b, lines_a = text.count("\n") + 1, compacted.count("\n") + 1

    print(f"  Caracteres : {chars_b:,} → {chars_a:,}   (−{_pct(chars_b, chars_a)})")
    print(f"  Líneas     : {lines_b:,} → {lines_a:,}   (−{_pct(lines_b, lines_a)})")
    print(f"  ~Tokens*   : {chars_b // 4:,} → {chars_a // 4:,}   (proxy chars/4)")

    if with_tokens:
        tb, ta = _count_tokens(text), _count_tokens(compacted)
        if tb is not None and ta is not None:
            print(f"  Tokens     : {tb:,} → {ta:,}   (−{_pct(tb, ta)})  [Gemini real]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mide el ahorro del compactador de texto.")
    ap.add_argument("path", nargs="?", help="Archivo de texto a medir.")
    ap.add_argument("--demo", action="store_true", help="Usar el sample de demostración.")
    ap.add_argument("--tokens", action="store_true", help="Contar tokens reales de Gemini.")
    args = ap.parse_args()

    if args.demo or not args.path:
        print("── Sample de demostración (salida OCR simulada) ──")
        measure(_DEMO, with_tokens=args.tokens)
        return

    text = Path(args.path).read_text(encoding="utf-8")
    print(f"── {args.path} ──")
    measure(text, with_tokens=args.tokens)


if __name__ == "__main__":
    main()
