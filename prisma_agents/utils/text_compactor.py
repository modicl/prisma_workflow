"""
Compactador determinista de texto extraído de documentos.

Reduce el conteo de tokens que llega a los agentes LLM eliminando ruido de
formato — sin alterar el contenido. Es lógica pura, sin I/O ni llamadas a
ningún modelo: dado el mismo texto produce siempre la misma salida.

IMPORTANTE (compliance): este módulo NUNCA resume, reescribe ni reordena
contenido. Solo elimina whitespace redundante, marcadores de página del OCR
y líneas idénticas consecutivas. El conjunto de palabras del documento se
preserva íntegro. Ver decisión de diseño en CLAUDE.md (contenido normativo
legal debe transcribirse fielmente).
"""

import re

# Línea que es solo un número (típico número de página suelto del OCR).
_PURE_NUMBER = re.compile(r"\d+")

# Marcadores tipo "Página 3", "Página 3 de 10", "Page 3 / 10", "Pág. 3".
_PAGE_MARKER = re.compile(
    r"(?i)(p[áa]gina|page|p[áa]g\.?)\s*\d+(\s*(de|of|/)\s*\d+)?"
)

# Runs de espacios/tabs horizontales (no toca saltos de línea).
_H_WHITESPACE = re.compile(r"[ \t]+")


def _is_page_marker(line: str) -> bool:
    """True si la línea es un marcador de página suelto (ruido del OCR)."""
    return bool(_PURE_NUMBER.fullmatch(line) or _PAGE_MARKER.fullmatch(line))


def compact_text(text: str) -> str:
    """
    Compacta texto extraído de un documento para reducir tokens.

    Pipeline determinista:
      1. Normaliza fin de línea (CRLF/CR → LF).
      2. Colapsa runs de espacios/tabs a un solo espacio y recorta cada línea.
      3. Elimina líneas que son solo marcadores de página.
      4. Colapsa runs de líneas en blanco a una sola.
      5. Elimina líneas idénticas consecutivas (headers repetidos del OCR).
      6. Recorta whitespace al inicio/fin del documento completo.

    Returns:
        Texto compactado. Cadena vacía si no hay contenido útil.
    """
    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned: list[str] = []
    for raw in normalized.split("\n"):
        line = _H_WHITESPACE.sub(" ", raw).strip()
        if _is_page_marker(line):
            continue
        cleaned.append(line)

    result: list[str] = []
    for line in cleaned:
        if line == "":
            if result and result[-1] == "":
                continue
        elif result and result[-1] == line:
            continue
        result.append(line)

    return "\n".join(result).strip()
