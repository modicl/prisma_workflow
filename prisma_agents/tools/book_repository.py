"""
book_repository.py — Acceso de solo lectura al repositorio S3 de materiales por colegio.

Flujo:
  1. read_index()               → lee index.json de S3
  2. select_materials_with_llm() → Gemini elige los 3 PDFs más relevantes
  3. transcribe_pdf_from_s3()   → descarga + transcribe cada PDF via Gemini Files API
  4. get_reference_materials()  → orquesta todo; retorna "" si cualquier paso falla
"""

import json
import os
import re
import tempfile

import boto3
from botocore.exceptions import ClientError
from google import genai


def _get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


def _get_bucket() -> str:
    return os.getenv("S3_BUCKET_NAME", "prisma-schools-repos")


def read_index(school_id: str, subject: str, grade: str) -> dict | None:
    """Lee index.json desde S3. Retorna None si no existe o hay error."""
    s3 = _get_s3_client()
    key = f"schools/{school_id}/{subject}/{grade}/index.json"
    bucket = _get_bucket()
    print(f"[BookRepository] S3 lookup → bucket={bucket!r}  key={key!r}")
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            return None
        raise


def select_materials_with_llm(index: dict, perfil_paci: str) -> list[str]:
    """Usa Gemini para seleccionar hasta 3 filenames del índice según el perfil del estudiante."""
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    materials_info = json.dumps(index.get("materials", []), ensure_ascii=False, indent=2)
    prompt = (
        "Dado el siguiente índice de materiales de referencia y el perfil del estudiante, "
        "selecciona los 3 archivos más útiles para generar una rúbrica adaptada. "
        "Si hay menos de 3 materiales disponibles, selecciona todos los disponibles. "
        'Responde SOLO con un JSON válido: {"selected": ["filename1.pdf", "filename2.pdf", "filename3.pdf"]}\n\n'
        f"ÍNDICE DE MATERIALES:\n{materials_info}\n\n"
        f"PERFIL DEL ESTUDIANTE:\n{perfil_paci}"
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=[prompt],
    )
    text = response.text.strip()
    match = re.search(r'\{.*?"selected".*?\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group()).get("selected", [])
        except json.JSONDecodeError:
            return []
    return []


_MIME_TYPES = {
    ".pdf":  "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc":  "application/msword",
}


def _mime_type_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_TYPES.get(ext, "application/octet-stream")


def transcribe_material_from_s3(school_id: str, subject: str, grade: str, filename: str) -> str:
    """Descarga un material de S3 y lo transcribe. PDF → Gemini Files API; DOCX → python-docx local."""
    s3 = _get_s3_client()
    key = f"schools/{school_id}/{subject}/{grade}/{filename}"
    suffix = os.path.splitext(filename)[1].lower() or ".bin"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with open(tmp_path, "wb") as f:
            print(f"[BookRepository] descargando → bucket={_get_bucket()!r}  key={key!r}")
            response_s3 = s3.get_object(Bucket=_get_bucket(), Key=key)
            f.write(response_s3["Body"].read())

        if suffix == ".docx":
            from docx import Document
            doc = Document(tmp_path)
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return f"=== {filename} ===\n{text}"

        # PDF y otros formatos soportados → Gemini Files API
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
        uploaded = None
        try:
            with open(tmp_path, "rb") as f:
                uploaded = client.files.upload(
                    file=f,
                    config={"mime_type": _mime_type_for(filename), "display_name": filename},
                )
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=[
                    uploaded,
                    "Extrae y transcribe el texto completo de este material educativo. "
                    "Preserva la estructura, títulos, ejercicios y ejemplos.",
                ],
            )
            return f"=== {filename} ===\n{response.text}"
        finally:
            if uploaded:
                try:
                    client.files.delete(name=uploaded.name)
                except Exception:
                    pass
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# Alias de compatibilidad
transcribe_pdf_from_s3 = transcribe_material_from_s3


def get_reference_materials(
    school_id: str, subject: str, grade: str, perfil_paci: str
) -> str:
    """
    Orquesta la carga de materiales de referencia para el colegio/ramo/curso.

    Retorna el texto transcrito de hasta 3 PDFs seleccionados por el LLM.
    Retorna "" si no hay materiales, school_id vacío, o cualquier error.
    """
    if not school_id or not subject or not grade or not perfil_paci:
        return ""
    try:
        index = read_index(school_id, subject, grade)
        if not index or not index.get("materials"):
            return ""

        selected = select_materials_with_llm(index, perfil_paci)
        if not selected:
            return ""

        # Seguridad: validar que los filenames elegidos por el LLM estén en el índice
        valid_filenames = {m["filename"] for m in index["materials"]}
        selected = [f for f in selected if f in valid_filenames][:3]

        texts = [
            transcribe_material_from_s3(school_id, subject, grade, fn)
            for fn in selected
        ]
        return "\n\n".join(texts)
    except Exception as e:
        print(f"[book_repository] Error obteniendo materiales de referencia: {e}")
        return ""
