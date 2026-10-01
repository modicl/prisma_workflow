"""Consumo de tokens de las llamadas DIRECTAS a Gemini (fuera de ADK) hacia el monitor en vivo.

Antes de esto, la lectura de PDFs/DOCX y la selección de materiales consumían tokens que el monitor
no veía, así que «tokens usados» y «costo promedio» subestimaban el consumo real.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools import book_repository as br
from utils import document_loader as dl
from utils import usage_events as ue


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, event_type, session_id, **fields):
        self.calls.append((event_type, session_id, fields))

    def usage(self):
        return [c[2] for c in self.calls if c[0] == "llm_usage"]


@pytest.fixture
def rec():
    r = Recorder()
    with patch("api.event_publisher.publish", r):
        yield r


@pytest.fixture
def sesion():
    """Simula lo que hace workflow_runner: fija la sesión del flujo en el contexto."""
    token = ue.monitor_session_id.set("sesion-1")
    yield "sesion-1"
    ue.monitor_session_id.reset(token)


def um(prompt=1000, cand=100, thoughts=0, cached=0, tool=0):
    return SimpleNamespace(prompt_token_count=prompt, candidates_token_count=cand, thoughts_token_count=thoughts,
                           cached_content_token_count=cached, tool_use_prompt_token_count=tool)


def gemini_response(text="texto", usage=None, model_version=None):
    r = MagicMock()
    r.text = text
    r.candidates = []
    r.usage_metadata = usage
    r.model_version = model_version
    return r


# ── usage_fields ────────────────────────────────────────────────────────────
def test_usage_fields_mapea_los_cuatro_contadores():
    assert ue.usage_fields(um(1000, 100, 30, 400), "m", "A") == {
        "agent": "A", "model": "m", "input_tokens": 1000, "cached_tokens": 400,
        "output_tokens": 100, "thoughts_tokens": 30}


def test_usage_fields_suma_tokens_de_herramientas_a_la_entrada():
    assert ue.usage_fields(um(100, 0, 0, 0, tool=25), "m", "A")["input_tokens"] == 125


@pytest.mark.parametrize("usage, partial", [(None, False), (um(), True), (um(0, 0, 0), False)])
def test_usage_fields_ignora_lo_que_no_aplica(usage, partial):
    assert ue.usage_fields(usage, "m", "A", partial=partial) is None


def test_usage_fields_tolera_valores_que_no_son_enteros():
    """Los mocks (MagicMock) y respuestas raras no deben colar objetos al evento."""
    raro = SimpleNamespace(prompt_token_count=MagicMock(), candidates_token_count="mucho",
                           thoughts_token_count=None, cached_content_token_count=True)
    assert ue.usage_fields(raro, "m", "A") is None


def test_resolve_model_prefiere_el_de_la_respuesta_y_ignora_lo_que_no_es_texto():
    assert ue.resolve_model("gemini-real", "fallback") == "gemini-real"
    assert ue.resolve_model(None, "fallback") == "fallback"
    assert ue.resolve_model(MagicMock(), "fallback") == "fallback"
    assert ue.resolve_model("", "") == "desconocido"


# ── emit_direct_usage ───────────────────────────────────────────────────────
def test_emite_llm_usage_con_la_sesion_del_contexto(rec, sesion):
    ue.emit_direct_usage(gemini_response(usage=um(50, 5)), agent="Lectura", model="m")
    assert rec.calls == [("llm_usage", "sesion-1", {"agent": "Lectura", "model": "m", "input_tokens": 50,
                                                    "cached_tokens": 0, "output_tokens": 5, "thoughts_tokens": 0})]


def test_sin_sesion_en_el_contexto_no_publica_nada(rec):
    ue.emit_direct_usage(gemini_response(usage=um()), agent="Lectura", model="m")
    assert rec.calls == []


def test_usa_el_modelo_que_devuelve_la_respuesta(rec, sesion):
    ue.emit_direct_usage(gemini_response(usage=um(), model_version="gemini-x-002"), agent="A", model="m")
    assert rec.usage()[0]["model"] == "gemini-x-002"


def test_respuesta_sin_uso_no_publica(rec, sesion):
    ue.emit_direct_usage(gemini_response(usage=None), agent="A", model="m")
    assert rec.calls == []


def test_jamas_lanza_aunque_el_publicador_explote(sesion):
    def roto(*a, **k):
        raise RuntimeError("kafka")

    with patch("api.event_publisher.publish", roto):
        ue.emit_direct_usage(gemini_response(usage=um()), agent="A", model="m")


# ── document_loader ─────────────────────────────────────────────────────────
def _client(response):
    uploaded = MagicMock()
    uploaded.name = "files/mock123"
    c = MagicMock()
    c.files.upload.return_value = uploaded
    c.models.generate_content.return_value = response
    return c


def test_lectura_de_pdf_publica_su_consumo_y_sigue_borrando_el_archivo_de_la_nube(tmp_path, rec, sesion):
    pdf = tmp_path / "paci.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    client = _client(gemini_response("Contenido extraído", um(5000, 700, 0, 1000)))
    with patch("utils.document_loader.genai.Client", return_value=client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        dl._load_pdf_via_gemini(pdf, "PACI")
    assert rec.usage() == [{"agent": "Lectura de PDF (OCR)", "model": dl._MODEL, "input_tokens": 5000,
                            "cached_tokens": 1000, "output_tokens": 700, "thoughts_tokens": 0}]
    client.files.delete.assert_called_once_with(name="files/mock123")   # requerimiento PII intacto


def test_si_la_lectura_de_pdf_falla_no_publica_y_borra_igual(tmp_path, rec, sesion):
    pdf = tmp_path / "paci.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    client = _client(None)
    client.models.generate_content.side_effect = RuntimeError("API error")
    with patch("utils.document_loader.genai.Client", return_value=client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        with pytest.raises(RuntimeError):
            dl._load_pdf_via_gemini(pdf, "PACI")
    assert rec.calls == []
    client.files.delete.assert_called_once()


def test_ocr_de_imagenes_docx_publica_su_consumo(rec, sesion):
    client = _client(gemini_response("texto de la imagen", um(300, 40)))
    with patch("utils.document_loader.genai.Client", return_value=client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        dl._ocr_images_with_gemini([{"mime_type": "image/png", "data": b"x"}], "a.docx")
    assert rec.usage()[0]["agent"] == "OCR de imágenes DOCX" and rec.usage()[0]["input_tokens"] == 300


def test_lectura_de_pdf_sin_sesion_no_publica(tmp_path, rec):
    pdf = tmp_path / "paci.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    client = _client(gemini_response("ok", um()))
    with patch("utils.document_loader.genai.Client", return_value=client), \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        dl._load_pdf_via_gemini(pdf, "PACI")          # modo CLI: no hay sesión
    assert rec.calls == []


# ── book_repository ─────────────────────────────────────────────────────────
def test_seleccion_de_materiales_publica_su_consumo(rec, sesion):
    client = MagicMock()
    client.models.generate_content.return_value = gemini_response('{"selected": ["a.pdf"]}', um(2000, 30))
    with patch("tools.book_repository.genai.Client", return_value=client):
        assert br.select_materials_with_llm({"materials": []}, "perfil") == ["a.pdf"]
    assert rec.usage() == [{"agent": "Selección de materiales", "model": "gemini-3.1-flash-lite",
                            "input_tokens": 2000, "cached_tokens": 0, "output_tokens": 30, "thoughts_tokens": 0}]


def test_transcripcion_de_material_publica_su_consumo_y_limpia(rec, sesion):
    body = MagicMock()
    body.read.return_value = b"%PDF fake"
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": body}
    uploaded = MagicMock()
    uploaded.name = "files/abc"
    client = MagicMock()
    client.files.upload.return_value = uploaded
    client.models.generate_content.return_value = gemini_response("Contenido", um(9000, 1200, 0, 3000))
    with patch("tools.book_repository._get_s3_client", return_value=s3), \
         patch("tools.book_repository.genai.Client", return_value=client), \
         patch("tools.book_repository.os.unlink"), \
         patch.dict(os.environ, {"S3_BUCKET_NAME": "b"}):
        br.transcribe_material_from_s3("s", "matematica", "5basico", "m.pdf")
    assert rec.usage()[0]["agent"] == "Transcripción de material"
    assert rec.usage()[0]["input_tokens"] == 9000 and rec.usage()[0]["cached_tokens"] == 3000
    client.files.delete.assert_called_once_with(name="files/abc")


@pytest.mark.asyncio
async def test_el_executor_hereda_la_sesion_para_atribuir_el_consumo(sesion):
    """get_reference_materials corre en un hilo del executor: la sesión debe viajar con el contexto."""
    def lee_contexto(*args):
        return ue.monitor_session_id.get()

    with patch("tools.book_repository.get_reference_materials", lee_contexto):
        assert await br.get_reference_materials_async("s", "m", "g", "p") == "sesion-1"
