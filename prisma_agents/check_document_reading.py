"""
Verifica que document_loader puede extraer texto de dos documentos.

Uso:
    python check_document_reading.py <doc1> <doc2>
    python check_document_reading.py  # usa los documentos de prueba por defecto
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from utils.document_loader import load_document

DEFAULTS = [
    Path(__file__).parent.parent / "docs_test" / "paci_test.pdf",
    Path(__file__).parent.parent / "docs_test" / "material_base_test.pdf",
]

PREVIEW_CHARS = 1000


def check(path: str) -> None:
    p = Path(path)
    print(f"\n{'─' * 60}")
    print(f"Archivo : {p.name}  ({p.suffix.upper()[1:]})")
    print(f"{'─' * 60}")

    try:
        text = load_document(str(p), label=p.stem)
        char_count = len(text)
        word_count = len(text.split())
        print(f"✓ Extraído — {char_count:,} caracteres / {word_count:,} palabras")
        print(f"\nPreview (primeros {PREVIEW_CHARS} caracteres):")
        print(f"  {text[:PREVIEW_CHARS].replace(chr(10), chr(10) + '  ')!r}")
    except FileNotFoundError:
        print(f"✗ Archivo no encontrado: {path}")
    except ValueError as exc:
        print(f"✗ Error de formato: {exc}")
    except Exception as exc:
        print(f"✗ Error inesperado: {exc}")


def main() -> None:
    paths = sys.argv[1:] if len(sys.argv) > 1 else [str(p) for p in DEFAULTS]

    if not paths:
        print("No se especificaron documentos y no se encontraron los archivos de prueba por defecto.")
        sys.exit(1)

    print(f"Verificando lectura de {len(paths)} documento(s)...\n")
    for path in paths:
        check(path)

    print(f"\n{'─' * 60}")
    print("Listo.")


if __name__ == "__main__":
    main()
