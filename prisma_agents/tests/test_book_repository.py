import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError

# Importamos solo lo que existe ahora; iremos expandiendo el archivo de test.
from tools.book_repository import read_index

SAMPLE_INDEX = {
    "school_id": "test_school",
    "subject": "matematica",
    "grade": "5basico",
    "materials": [
        {
            "filename": "mat1.pdf",
            "title": "Cuadernillo fracciones",
            "description": "Ejercicios de fracciones con contextos cotidianos",
            "priority": 1,
            "pages": 30,
            "tags": ["fracciones", "OA3"],
        },
        {
            "filename": "mat2.pdf",
            "title": "Guía decimales",
            "description": "Operaciones con decimales",
            "priority": 2,
            "pages": 20,
            "tags": ["decimales", "OA5"],
        },
        {
            "filename": "mat3.pdf",
            "title": "Problemas contextualizados",
            "description": "Problemas de la vida diaria",
            "priority": 3,
            "pages": 25,
            "tags": ["problemas"],
        },
        {
            "filename": "mat4.pdf",
            "title": "Actividades TEA",
            "description": "Actividades visuales para estudiantes TEA",
            "priority": 4,
            "pages": 15,
            "tags": ["tea", "visual"],
        },
    ],
}


def test_read_index_success():
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps(SAMPLE_INDEX).encode()))
    }
    with patch("tools.book_repository._get_s3_client", return_value=mock_s3), \
         patch.dict(os.environ, {"S3_BUCKET_NAME": "prisma-schools-repos"}):
        result = read_index("test_school", "matematica", "5basico")
    assert result == SAMPLE_INDEX
    mock_s3.get_object.assert_called_once_with(
        Bucket="prisma-schools-repos",
        Key="schools/test_school/matematica/5basico/index.json",
    )


def test_read_index_not_found_returns_none():
    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject"
    )
    with patch("tools.book_repository._get_s3_client", return_value=mock_s3):
        result = read_index("test_school", "matematica", "5basico")
    assert result is None


def test_read_index_404_returns_none():
    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject"
    )
    with patch("tools.book_repository._get_s3_client", return_value=mock_s3):
        result = read_index("test_school", "matematica", "5basico")
    assert result is None


from tools.book_repository import select_materials_with_llm, get_reference_materials, transcribe_pdf_from_s3


def test_select_materials_returns_llm_choice():
    mock_response = MagicMock()
    mock_response.text = '{"selected": ["mat4.pdf", "mat1.pdf", "mat3.pdf"]}'
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    with patch("tools.book_repository.genai.Client", return_value=mock_client):
        result = select_materials_with_llm(SAMPLE_INDEX, "Perfil TEA matemáticas 5°")
    assert result == ["mat4.pdf", "mat1.pdf", "mat3.pdf"]


def test_select_materials_llm_with_markdown_fence():
    mock_response = MagicMock()
    mock_response.text = '```json\n{"selected": ["mat1.pdf", "mat2.pdf"]}\n```'
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    with patch("tools.book_repository.genai.Client", return_value=mock_client):
        result = select_materials_with_llm(SAMPLE_INDEX, "Perfil")
    assert result == ["mat1.pdf", "mat2.pdf"]


def test_select_materials_invalid_json_returns_empty():
    mock_response = MagicMock()
    mock_response.text = "No puedo determinar los materiales relevantes"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    with patch("tools.book_repository.genai.Client", return_value=mock_client):
        result = select_materials_with_llm(SAMPLE_INDEX, "Perfil")
    assert result == []


def test_get_reference_materials_empty_school_id():
    result = get_reference_materials("", "matematica", "5basico", "perfil")
    assert result == ""


def test_get_reference_materials_empty_subject():
    result = get_reference_materials("school_x", "", "5basico", "perfil")
    assert result == ""


def test_get_reference_materials_no_index():
    with patch("tools.book_repository.read_index", return_value=None):
        result = get_reference_materials("school_x", "matematica", "5basico", "perfil")
    assert result == ""


def test_get_reference_materials_filters_unlisted_filenames():
    """El LLM no puede forzar la descarga de archivos que no están en el índice."""
    with patch("tools.book_repository.read_index", return_value=SAMPLE_INDEX), \
         patch("tools.book_repository.select_materials_with_llm",
               return_value=["../../etc/passwd", "mat1.pdf"]), \
         patch("tools.book_repository.transcribe_pdf_from_s3",
               return_value="contenido mat1") as mock_tx:
        result = get_reference_materials("school_x", "matematica", "5basico", "perfil")
    # Solo mat1.pdf pasa la validación; ../../etc/passwd no está en el índice
    mock_tx.assert_called_once_with("school_x", "matematica", "5basico", "mat1.pdf")
    assert result == "contenido mat1"


def test_get_reference_materials_s3_error_returns_empty():
    with patch("tools.book_repository.read_index", side_effect=Exception("S3 unreachable")):
        result = get_reference_materials("school_x", "matematica", "5basico", "perfil")
    assert result == ""


def test_transcribe_pdf_from_s3_happy_path_and_cleanup():
    """Verifica el happy path y que el finally limpia temp file y archivo Gemini."""
    mock_s3 = MagicMock()
    mock_uploaded_file = MagicMock()
    mock_uploaded_file.name = "files/abc123"

    mock_response = MagicMock()
    mock_response.text = "Contenido del PDF transcrito"

    mock_client = MagicMock()
    mock_client.files.upload.return_value = mock_uploaded_file
    mock_client.models.generate_content.return_value = mock_response

    with patch("tools.book_repository._get_s3_client", return_value=mock_s3), \
         patch("tools.book_repository.genai.Client", return_value=mock_client), \
         patch("tools.book_repository.os.unlink") as mock_unlink, \
         patch.dict(os.environ, {"S3_BUCKET_NAME": "prisma-schools-repos"}):
        result = transcribe_pdf_from_s3("school_x", "matematica", "5basico", "mat1.pdf")

    assert result == "=== mat1.pdf ===\nContenido del PDF transcrito"
    # Verifica cleanup: el archivo Gemini fue eliminado
    mock_client.files.delete.assert_called_once_with(name="files/abc123")
    # Verifica cleanup: el archivo temporal fue eliminado
    mock_unlink.assert_called_once()


def test_get_reference_materials_empty_perfil_paci():
    result = get_reference_materials("school_x", "matematica", "5basico", "")
    assert result == ""
