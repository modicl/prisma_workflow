"""
Cargador de documentos: convierte PDF, DOCX y JSON a texto plano
para inyectar en el session.state de Google ADK.
"""

import base64
import json
from pathlib import Path


def load_document(path: str, label: str | None = None) -> str:
    """
    Carga un documento y retorna su contenido como texto.

    Soporta:
    - .pdf  → extrae texto vía google-genai (Gemini)
    - .docx → extrae texto con python-docx
    - .json → formatea con json.dumps indent=2
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {path}")

    suffix = p.suffix.lower()

    if suffix == ".pdf":
        return _load_pdf(p, label)
    elif suffix in (".docx", ".doc"):
        return _load_docx(p, label)
    elif suffix == ".json":
        return _load_json(p, label)
    else:
        raise ValueError(
            f"Formato no soportado: '{suffix}'. Use .pdf, .docx o .json"
        )


def _load_pdf(path: Path, label: str | None) -> str:
    """
    Sube el PDF a la API de Gemini Files y extrae el texto completo.
    Requiere GOOGLE_API_KEY en el entorno.
    """
    import os
    from google import genai

    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

    with open(path, "rb") as f:
        uploaded = client.files.upload(
            file=f,
            config={"mime_type": "application/pdf", "display_name": path.name},
        )

    # Pedimos a Gemini que extraiga el texto completo del PDF
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=[
            uploaded,
            "Extrae y transcribe el texto completo de este documento, "
            "manteniendo la estructura de secciones y párrafos. "
            "No omitas ningún campo ni tabla.",
        ],
    )

    prefix = f"[Documento PDF: {label or path.name}]\n\n" if label else f"[{path.name}]\n\n"
    return prefix + response.text


def _load_docx(path: Path, label: str | None) -> str:
    """Extrae texto de un archivo DOCX usando python-docx."""
    try:
        from docx import Document
    except ImportError:
        raise ImportError("Instala python-docx: pip install python-docx")

    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs)

    prefix = f"[Documento DOCX: {label or path.name}]\n\n" if label else f"[{path.name}]\n\n"
    return prefix + text


def _load_json(path: Path, label: str | None) -> str:
    """Carga un JSON (formulario PACI online) y lo formatea como texto."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    formatted = json.dumps(data, ensure_ascii=False, indent=2)
    prefix = f"[PACI en formato JSON — {label or path.name}]\n\n"
    return prefix + formatted
