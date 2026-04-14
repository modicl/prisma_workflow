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
    elif suffix in (".docx", ".doc"):
        return _load_docx(p, label)
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
                "Extrae y transcribe el texto completo de este documento, "
                "manteniendo la estructura de secciones y párrafos. "
                "No omitas ningún campo ni tabla.",
            ],
        )
    finally:
        # Eliminar el archivo subido independientemente del resultado.
        # Google retiene archivos 48 h si no se borran explícitamente.
        print(f"  → Eliminando archivo de la nube...")
        client.files.delete(name=uploaded.name)

    # Añadimos una cabecera para que cuando el agente ADK lea este megatexto del estado (state)
    # sepa visualmente de qué archivo proviene.
    prefix = f"[Documento PDF: {label or path.name}]\n\n" if label else f"[{path.name}]\n\n"
    text = prefix + response.text
    print(f"  ✓ {label or path.name} cargado ({len(text):,} caracteres)")
    return text


def _load_docx(path: Path, label: str | None) -> str:
    """
    Rutina específica para la extracción de texto en documentos Microsoft Word (.docx).
    
    Utiliza la librería python-docx (`docx`) para iterar por cada párrafo del documento,
    bypaseando todo el ruido visual del XML y obteniendo solo el texto legible. De esta 
    forma ahorramos llamadas innecesarias (y lentas) a la API multimodales de Google, ya que 
    el texto XML de `.docx` es infinitamente más limpio y fácil de extraer localmente que un PDF.
    
    Args:
        path (Path): Objeto Path apuntando a un doc/docx.
        label (str | None): Etiqueta de cabecera.
    """
    try:
        from docx import Document
    except ImportError:
        # Import local (lazy import) para no crashear el sistema entero si no
        # se está usando la dependencia docx y no está instalada.
        raise ImportError("Instala python-docx: pip install python-docx")

    # Parsear el documento ZIP xml de wrod
    doc = Document(str(path))
    
    # Extraer solamente los párrafos que contienen texto (excluyendo imágenes o espacios en blanco residuales)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    
    # Unir todo el texto con saltos de línea para reconstituir el bloque original
    text = "\n".join(paragraphs)

    # Como siempre, anteponer una etiqueta amigable para la IA
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
