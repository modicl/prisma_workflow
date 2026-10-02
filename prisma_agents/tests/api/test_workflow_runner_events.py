"""Enganches del monitor en api/workflow_runner.py: flow_started, hitl_required y flow_finished."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import patch

from api import workflow_runner
from api.session_store import HITL_CALLBACKS, SESSIONS, SessionData


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, event_type, session_id, **fields):
        self.calls.append((event_type, session_id, fields))

    def types(self):
        return [c[0] for c in self.calls]

    def of(self, event_type):
        return [c[2] for c in self.calls if c[0] == event_type]


@pytest.fixture
def rec():
    r = Recorder()
    with patch("api.event_publisher.publish", r), \
         patch("api.workflow_runner.sync_to_dynamo"), \
         patch("api.workflow_runner.S3_BUCKET", ""):
        yield r
    SESSIONS.clear()
    HITL_CALLBACKS.clear()


def _sesion(sid="sesion-1"):
    sd = SessionData()
    SESSIONS[sid] = sd
    return sd


async def _correr(sid, fake_run, **kw):
    with patch("api.workflow_runner.run_workflow", side_effect=fake_run):
        await workflow_runner.run_workflow_for_api(sid, paci_path="/a", material_path="/b", **kw)


def _devuelve(resultado):
    async def fake(**kwargs):
        return resultado
    return fake


@pytest.mark.parametrize("estado_agente, esperado", [
    ("success", "success"),
    ("fail", "degraded"),
    ("validation_failed", "compliance_blocked"),
    ("compliance_blocked", "compliance_blocked"),
    ("timeout", "error"),
])
@pytest.mark.asyncio
async def test_flujo_normal_publica_started_y_finished_con_el_estado_real(rec, estado_agente, esperado):
    _sesion()
    await _correr("sesion-1", _devuelve({"status": estado_agente}))
    assert rec.types() == ["flow_started", "flow_finished"]
    fin = rec.of("flow_finished")[0]
    assert fin["status"] == esperado
    assert isinstance(fin["duration_ms"], int) and fin["duration_ms"] >= 0
    assert all(c[1] == "sesion-1" for c in rec.calls)


@pytest.mark.asyncio
async def test_rechazo_del_docente_termina_como_hitl_rejected(rec):
    _sesion()

    async def fake(**kwargs):
        # el runner marca hitl_was_rejected a través de la callback registrada
        cb = HITL_CALLBACKS["sesion-1"]
        SESSIONS["sesion-1"].hitl_response_queue.put_nowait({"approved": False, "reason": "no"})
        await cb({"perfil_paci": "", "planificacion_adaptada": ""}, 3, 3)
        return {"status": "hitl_rejected"}

    await _correr("sesion-1", fake)
    assert rec.of("flow_finished")[0]["status"] == "hitl_rejected"


@pytest.mark.asyncio
async def test_excepcion_en_el_workflow_termina_como_error(rec):
    _sesion()

    async def fake(**kwargs):
        raise RuntimeError("Gemini cayó")

    await _correr("sesion-1", fake)
    assert rec.types() == ["flow_started", "flow_finished"]
    assert rec.of("flow_finished")[0]["status"] == "error"


@pytest.mark.asyncio
async def test_cancelacion_publica_cancelled(rec):
    sd = _sesion()

    async def fake(**kwargs):
        sd.cancelled = True
        sd.workflow_status = "cancelled"
        return {"status": "success"}     # el runner ignora el resultado si fue cancelada

    await _correr("sesion-1", fake)
    assert rec.of("flow_finished")[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_si_la_tarea_es_cancelada_igual_se_publica_el_cierre(rec):
    sd = _sesion()
    sd.workflow_status = "cancelled"
    sd.cancelled = True

    async def fake(**kwargs):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _correr("sesion-1", fake)
    assert rec.of("flow_finished")[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_si_falla_antes_de_empezar_no_hay_flow_finished(rec):
    _sesion()
    with patch("api.workflow_runner.validate_prompt_docente", side_effect=ValueError("prompt inválido")):
        await _correr("sesion-1", _devuelve({"status": "success"}), prompt="x")
    assert rec.calls == []


@pytest.mark.asyncio
async def test_hitl_required_se_publica_con_el_numero_de_intento(rec):
    sd = _sesion()
    cb = workflow_runner._make_hitl_callback("sesion-1", sd, [False])
    sd.hitl_response_queue.put_nowait({"approved": True})
    await cb({"perfil_paci": "p", "planificacion_adaptada": "a"}, 2, 3)
    assert rec.of("hitl_required") == [{"attempt": 2}]


@pytest.mark.asyncio
async def test_el_flujo_no_se_rompe_si_el_publicador_lanza():
    _sesion()

    def roto(*a, **k):
        raise RuntimeError("kafka")

    with patch("api.event_publisher.publish", roto), \
         patch("api.workflow_runner.sync_to_dynamo"), \
         patch("api.workflow_runner.S3_BUCKET", ""):
        await _correr("sesion-1", _devuelve({"status": "success"}))
    assert SESSIONS["sesion-1"].workflow_status == "success"
    SESSIONS.clear()


@pytest.mark.asyncio
async def test_el_runner_fija_la_sesion_en_el_contexto_para_las_llamadas_directas(rec):
    """document_loader/book_repository atribuyen su consumo con esta sesión (utils.usage_events)."""
    from utils import usage_events
    _sesion()
    vista = []

    async def fake(**kwargs):
        vista.append(usage_events.monitor_session_id.get())
        return {"status": "success"}

    await _correr("sesion-1", fake)
    assert vista == ["sesion-1"]
