# prisma_agents/tests/test_document_loader.py
import io
import json
import os
import sys
import zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import utils.document_loader as dl


# ---------------------------------------------------------------------------
# Helpers compartidos
# ---------------------------------------------------------------------------

def _make_gemini_response(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.candidates = []
    return r


def _make_gemini_client(response_text: str = "", side_effect: Exception | None = None) -> MagicMock:
    uploaded = MagicMock()
    uploaded.name = "files/mock123"
    client = MagicMock()
    client.files.upload.return_value = uploaded
    if side_effect is not None:
        client.models.generate_content.side_effect = side_effect
    else:
        client.models.generate_content.return_value = _make_gemini_response(response_text)
    return client


# ---------------------------------------------------------------------------
# load_document — enrutador
# ---------------------------------------------------------------------------

def test_load_document_raises_if_file_missing():
    with pytest.raises(FileNotFoundError):
        dl.load_document("/ruta/que/no/existe.pdf")


def test_load_document_raises_on_unsupported_extension(tmp_path):
    f = tmp_path / "doc.xls"
    f.write_bytes(b"data")
    with pytest.raises(ValueError, match="Formato no soportado"):
        dl.load_document(str(f))


# ---------------------------------------------------------------------------
# _load_pdf — ahora siempre usa Gemini (sin pdfplumber)
# ---------------------------------------------------------------------------

def test_load_pdf_uses_gemini_for_digital_pdf(tmp_path):
    """Un PDF con texto seleccionable debe pasar por Gemini, no pdfplumber."""
    pdf = tmp_path / "digital.pdf"
    pdf.write_bytes(b"%PDF-1.4 texto digital aqui")

    mock_client = _make_gemini_client("Texto extraído del PDF digital.")

    with patch("utils.document_loader.genai.Client", return_value=mock_client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        result = dl.load_document(str(pdf), label="PACI Test")

    assert "Texto extraído del PDF digital." in result
    assert "PACI Test" in result
    # Gemini Files API debe haberse usado
    mock_client.files.upload.assert_called_once()
    # Archivo de Gemini debe eliminarse al finalizar (requerimiento PII)
    mock_client.files.delete.assert_called_once_with(name="files/mock123")


def test_load_pdf_uses_gemini_for_scanned_pdf(tmp_path):
    """Un PDF puramente escaneado (sin texto seleccionable) también pasa por Gemini."""
    pdf = tmp_path / "scanned.pdf"
    pdf.write_bytes(b"%PDF-1.4 binary image data")

    mock_client = _make_gemini_client("Texto OCR del escaneado.")

    with patch("utils.document_loader.genai.Client", return_value=mock_client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        result = dl.load_document(str(pdf), label="Material")

    assert "Texto OCR del escaneado." in result
    assert "Material" in result


def test_load_pdf_deletes_gemini_file_even_on_generate_error(tmp_path):
    """El archivo en Gemini se elimina aunque generate_content falle (requerimiento PII)."""
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    uploaded = MagicMock()
    uploaded.name = "files/will_be_deleted"
    client = MagicMock()
    client.files.upload.return_value = uploaded
    client.models.generate_content.side_effect = RuntimeError("API error")

    with patch("utils.document_loader.genai.Client", return_value=client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with pytest.raises(RuntimeError):
            dl.load_document(str(pdf))

    client.files.delete.assert_called_once_with(name="files/will_be_deleted")


def test_load_pdf_raises_if_gemini_returns_empty(tmp_path):
    """Si Gemini retorna texto vacío, se lanza ValueError con mensaje claro."""
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    client = MagicMock()
    uploaded = MagicMock()
    uploaded.name = "files/empty"
    client.files.upload.return_value = uploaded
    empty_response = MagicMock()
    empty_response.text = ""
    empty_response.candidates = []
    client.models.generate_content.return_value = empty_response

    with patch("utils.document_loader.genai.Client", return_value=client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with pytest.raises(ValueError, match="no pudo extraer texto"):
            dl.load_document(str(pdf))


def test_load_pdf_no_pdfplumber_import(tmp_path):
    """pdfplumber no debe importarse en ningún punto del flujo de PDFs."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    mock_client = _make_gemini_client("Texto de prueba.")

    import sys
    original_modules = dict(sys.modules)
    # Eliminamos pdfplumber del sys.modules para verificar que no se importa
    sys.modules.pop("pdfplumber", None)

    try:
        with patch("utils.document_loader.genai.Client", return_value=mock_client), \
             patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
            dl.load_document(str(pdf))
        # Si pdfplumber fue importado, estará en sys.modules
        assert "pdfplumber" not in sys.modules, "pdfplumber fue importado — no debe usarse"
    finally:
        sys.modules.update(original_modules)


# ---------------------------------------------------------------------------
# _load_docx — ahora usa Gemini Files API
# ---------------------------------------------------------------------------

def test_load_docx_uses_gemini(tmp_path):
    """Un .docx debe procesarse a través de Gemini Files API."""
    # Crear un .docx mínimo válido (ZIP con structure de Word)
    docx = tmp_path / "guia.docx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    docx.write_bytes(buf.getvalue())

    mock_client = _make_gemini_client("Contenido del DOCX extraído.")

    with patch("utils.document_loader.genai.Client", return_value=mock_client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        result = dl.load_document(str(docx), label="Material DOCX")

    assert "Contenido del DOCX extraído." in result
    assert "Material DOCX" in result
    upload_call = mock_client.files.upload.call_args
    assert upload_call.kwargs["config"]["mime_type"] == \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    mock_client.files.delete.assert_called_once_with(name="files/mock123")


def test_load_docx_deletes_gemini_file_even_on_error(tmp_path):
    """El archivo Gemini se elimina aunque generate_content falle."""
    docx = tmp_path / "error.docx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    docx.write_bytes(buf.getvalue())

    uploaded = MagicMock()
    uploaded.name = "files/docx_delete_test"
    client = MagicMock()
    client.files.upload.return_value = uploaded
    client.models.generate_content.side_effect = RuntimeError("fallo")

    with patch("utils.document_loader.genai.Client", return_value=client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with pytest.raises(RuntimeError):
            dl.load_document(str(docx))

    client.files.delete.assert_called_once_with(name="files/docx_delete_test")


# ---------------------------------------------------------------------------
# .doc — sin cambios (sigue siendo ValueError)
# ---------------------------------------------------------------------------

def test_load_doc_raises_value_error(tmp_path):
    doc = tmp_path / "old.doc"
    doc.write_bytes(b"data")
    with pytest.raises(ValueError, match=".doc.*Word 97"):
        dl.load_document(str(doc))


# ---------------------------------------------------------------------------
# _load_json — sin cambios
# ---------------------------------------------------------------------------

def test_load_json_returns_formatted_content(tmp_path):
    data = {"alumno": "Juan", "diagnostico": "TEA"}
    f = tmp_path / "paci.json"
    f.write_text(json.dumps(data), encoding="utf-8")

    result = dl.load_document(str(f), label="PACI JSON")

    assert "alumno" in result
    assert "Juan" in result
    assert "PACI JSON" in result


# ---------------------------------------------------------------------------
# Upload failure handling — Fix crítico PII
# ---------------------------------------------------------------------------

def test_load_pdf_upload_failure_propagates_cleanly(tmp_path):
    """Si files.upload falla, la excepción se propaga sin intentar delete."""
    pdf = tmp_path / "fail_upload.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    client = MagicMock()
    client.files.upload.side_effect = RuntimeError("upload failed — quota exceeded")

    with patch("utils.document_loader.genai.Client", return_value=client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with pytest.raises(RuntimeError, match="upload failed"):
            dl.load_document(str(pdf))

    # delete NO debe llamarse si upload falló (uploaded es None)
    client.files.delete.assert_not_called()
