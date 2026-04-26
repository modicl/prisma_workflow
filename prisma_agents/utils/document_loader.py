"""
Cargador de documentos: convierte PDF, DOCX y JSON a texto plano
para inyectar en el session.state de Google ADK.
"""

import base64
import json
from pathlib import Path


def load_document(path: str, label: str | None = None) -> str:
    """
    Función principal (Factory / Enrutador) para cargar documentos.
    
    Toma la ruta física de un archivo y un 'label' (etiqueta) opcional. Dependiendo 
    de la extensión del archivo, delega la extracción del texto a una función 
    específica (_load_pdf, _load_docx o _load_json).
    
    Args:
        path (str): Ruta relativa o absoluta del archivo en el disco.
        label (str | None): Etiqueta descriptiva para añadir como encabezado, ej. 'PACI del Estudiante'.
    
    Returns:
        str: El texto bruto gigante extraído del documento, listo para inyectarse como state de un LLM.
        
    Raises:
        FileNotFoundError: Si la ruta proporcionada no existe.
        ValueError: Si la extensión del archivo no está entre las soportadas (.pdf, .docx, .doc, .json).
    """
    p = Path(path)
    
    # Pre-condición: verificar que el archivo realmente está en el disco
    if not p.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {path}")

    # Extraemos la extensión y la convertimos a minúsculas por si el usuario pasa un archivo .PDF o .DOCX
    suffix = p.suffix.lower()

    # Enrutador simple basado en extensión
    if suffix == ".pdf":
        return _load_pdf(p, label)
    elif suffix == ".docx":
        return _load_docx(p, label)
    elif suffix == ".doc":
        return _load_doc_via_gemini(p, label)
    elif suffix == ".json":
        return _load_json(p, label)
    else:
        # Falla rápido si no soporta el formato para evitar crasheos silenciosos más adelante en el ADK
        raise ValueError(
            f"Formato no soportado: '{suffix}'. Use .pdf, .docx o .json"
        )


def _load_pdf(path: Path, label: str | None) -> str:
    """
    Extrae texto de un PDF con estrategia híbrida:
    1. pdfplumber para PDFs digitales (texto seleccionable) — sin coste de API.
    2. Gemini Files API + OCR solo cuando pdfplumber no extrae suficiente texto
       (PDFs escaneados o basados en imágenes).
    """
    # --- Intento 1: extracción nativa con pdfplumber ---
    extracted = _extract_pdf_native(path)
    if extracted:
        prefix = f"[Documento PDF: {label or path.name}]\n\n" if label else f"[{path.name}]\n\n"
        text = prefix + extracted
        print(f"  ✓ {label or path.name} cargado ({len(text):,} caracteres) [pdfplumber]")
        return text

    # --- Intento 2: Gemini OCR para PDFs escaneados ---
    print(f"  → PDF sin texto extraíble — usando Gemini OCR...")
    return _extract_pdf_gemini(path, label)


def _extract_pdf_native(path: Path) -> str:
    """Extrae texto de un PDF digital con pdfplumber. Retorna '' si el PDF es una imagen."""
    import pdfplumber

    pages_text: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages_text.append(page_text)

    combined = "\n\n".join(pages_text).strip()
    # Umbral: si hay menos de 50 caracteres en todo el doc, probablemente es un escaneado
    return combined if len(combined) >= 50 else ""


def _extract_pdf_gemini(path: Path, label: str | None) -> str:
    """Sube el PDF a la Files API de Gemini y usa OCR multimodal para extraer texto."""
    import os
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

    print(f"  → Subiendo {label or path.name} a Google Files API...")
    with open(path, "rb") as f:
        uploaded = client.files.upload(
            file=f,
            config={"mime_type": "application/pdf", "display_name": path.name},
        )

    print(f"  → Transcribiendo con Gemini OCR...     (puede tardar 20-60s según el PDF)")
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[
                uploaded,
                "Este es un PDF escaneado. Usa OCR para extraer todo el texto visible: "
                "párrafos, tablas, cuadros, encabezados, pies de página y campos de formulario. "
                "Transcribe fielmente sin agregar interpretaciones.",
            ],
            config=genai_types.GenerateContentConfig(
                thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
            ),
        )
    finally:
        print(f"  → Eliminando archivo de la nube...")
        client.files.delete(name=uploaded.name)

    extracted = response.text
    if not extracted and response.candidates:
        content = response.candidates[0].content
        if content and content.parts:
            extracted = "".join(
                p.text for p in content.parts
                if hasattr(p, "text") and p.text and not getattr(p, "thought", False)
            )

    if not extracted:
        finish_reason = None
        if response.candidates:
            finish_reason = getattr(response.candidates[0], "finish_reason", None)
        raise ValueError(
            f"Gemini OCR no pudo extraer texto de '{path.name}' "
            f"(finish_reason={finish_reason}). "
            "El PDF puede estar protegido con contraseña o la calidad del escaneo es muy baja."
        )

    prefix = f"[Documento PDF: {label or path.name}]\n\n" if label else f"[{path.name}]\n\n"
    text = prefix + extracted
    print(f"  ✓ {label or path.name} cargado ({len(text):,} caracteres) [Gemini OCR]")
    return text
    return text


def _load_doc_via_gemini(path: Path, label: str | None) -> str:
    """Intenta cargar un .doc; falla con mensaje claro si el formato no es soportado."""
    raise ValueError(
        f"El archivo '{path.name}' está en formato .doc (Word 97-2003), que no es compatible.\n"
        "Por favor guárdalo como .docx (Archivo → Guardar como → Word (.docx)) o como .pdf e intenta de nuevo."
    )


def _extract_docx_text(path: str | Path) -> str:
    """
    Extrae todo el texto de un .docx leyendo directamente los elementos <w:t> del XML.

    A diferencia de doc.paragraphs, este método captura también cuadros de texto
    (w:txbxContent), tablas y encabezados/pies de página que python-docx omite.
    """
    import zipfile
    from xml.etree import ElementTree as ET

    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    texts: list[str] = []

    with zipfile.ZipFile(str(path)) as z:
        # word/document.xml contiene el cuerpo principal; headers/footers/text boxes incluidos
        xml_files = [n for n in z.namelist() if n.startswith("word/") and n.endswith(".xml")]
        for xml_name in xml_files:
            root = ET.fromstring(z.read(xml_name))
            for elem in root.iter(f"{{{W}}}t"):
                if elem.text and elem.text.strip():
                    texts.append(elem.text)

    return "\n".join(texts)


def _load_docx(path: Path, label: str | None) -> str:
    """
    Extrae texto de un .docx incluyendo cuadros de texto, tablas y encabezados.
    Usa parseo XML directo para no depender de doc.paragraphs (que omite text boxes).
    """
    text = _extract_docx_text(path)
    prefix = f"[Documento DOCX: {label or path.name}]\n\n" if label else f"[{path.name}]\n\n"
    return prefix + text


def _load_json(path: Path, label: str | None) -> str:
    """
    Rutina específica para cargar un PACI estructurado en JSON.
    
    Pensado para un escenario en que las escuelas no envíen un papel o PDF, 
    sino un archivo volcado desde un sistema de colegio (formularios online, MINEDUC, etc).
    """
    with open(path, "r", encoding="utf-8") as f:
        # Cargar diccionario de python local
        data = json.load(f)

    # Re-escupir el JSON como una string enorme pre formateada (indent=2 ayuda mucho a Gemini 
    # a procesar visualmente el anidamiento jerárquico).
    formatted = json.dumps(data, ensure_ascii=False, indent=2)
    
    prefix = f"[PACI en formato JSON — {label or path.name}]\n\n"
    return prefix + formatted
