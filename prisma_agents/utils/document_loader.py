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
    Rutina específica para extraer texto de archivos PDF.
    
    IMPLEMENTACIÓN CLAVE: En lugar de usar librerías clásicas como PyPDF2 o pdfplumber, 
    esta función sube el PDF a la API File API de Gemini de Google. Esto es intencional 
    porque los modelos multimodales (como flash-lite) entienden muchísimo mejor la 
    estructura visual (como tablas, columnas y cuadros de texto complejos) que una librería 
    de parseo a texto normal.
    
    Args:
        path (Path): Objeto Path de la librería pathlib.
        label (str): Etiqueta para prefijar el contexto.
    
    Returns:
        str: La trascripción casi perfecta del PDF.
    """
    import os
    from google import genai

    # Instanciamos el cliente Gemini leyendo GOOGLE_API_KEY desde el ambiente virtual
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

    # Paso 1: Subir el PDF directamente a la nube (Workspace temporal del modelo)
    print(f"  → Subiendo {label or path.name} a Google Files API...")
    with open(path, "rb") as f:
        uploaded = client.files.upload(
            file=f,
            config={"mime_type": "application/pdf", "display_name": path.name},
        )

    # Paso 2: Invocar a gemini-2.5-flash-lite para que "lea" la imagen del PDF.
    # El bloque try/finally garantiza que el archivo se elimine de los servidores de Google
    # incluso si la llamada al modelo falla, evitando retención de PII en la nube.
    print(f"  → Transcribiendo con Gemini...         (puede tardar 20-60s según el PDF)")
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[
                uploaded,
                "Este documento puede ser un PDF escaneado o con formularios. "
                "Usa OCR si es necesario. "
                "Extrae y transcribe TODO el texto visible: párrafos, tablas, cuadros, "
                "encabezados, pies de página y cualquier campo de formulario. "
                "No omitas nada aunque el texto sea pequeño o esté en una imagen.",
            ],
        )
    finally:
        print(f"  → Eliminando archivo de la nube...")
        client.files.delete(name=uploaded.name)

    # Intentar extraer texto de response.text o de los parts del candidato
    extracted = response.text
    if not extracted and response.candidates:
        parts = response.candidates[0].content.parts
        extracted = "".join(p.text for p in parts if hasattr(p, "text") and p.text)

    if not extracted:
        raise ValueError(
            f"Gemini no pudo extraer texto de '{path.name}'. "
            "El PDF puede tener calidad de escaneo muy baja o estar protegido con contraseña. "
            "Intenta exportarlo como .docx desde Word/Acrobat e intenta de nuevo."
        )
    prefix = f"[Documento PDF: {label or path.name}]\n\n" if label else f"[{path.name}]\n\n"
    text = prefix + extracted
    print(f"  ✓ {label or path.name} cargado ({len(text):,} caracteres)")
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
