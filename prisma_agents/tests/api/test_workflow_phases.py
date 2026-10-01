"""Fases de Step Functions en el workflow: carga/guardado del estado, finalización, espera y decisión del docente."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock, patch

from api import dynamo_store, mock_phases, phase_store, sfn_client, workflow_runner as wr


class Emisor:
    def __init__(self):
        self.calls = []

    def __call__(self, tipo, sid, **campos):
        self.calls.append((tipo, sid, campos))

    def de(self, tipo):
        return [c[2] for c in self.calls if c[0] == tipo]


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    """Estado en memoria en vez de S3, DynamoDB espiado y publicador de eventos capturado."""
    almacen = {}

    def guardar(sid, st):
        almacen[sid] = {k: v for k, v in st.items() if k in phase_store.STATE_KEYS}

    def cargar(sid):
        if sid not in almacen:
            raise phase_store.PhaseStateNotFound(sid)
        return dict(almacen[sid])

    def actualizar(sid, **delta):
        almacen.setdefault(sid, {}).update(delta)
        return dict(almacen[sid])

    monkeypatch.setattr(phase_store, "save_state", guardar)
    monkeypatch.setattr(phase_store, "load_state", cargar)
    monkeypatch.setattr(phase_store, "update_state", actualizar)
    monkeypatch.setattr(phase_store, "delete_state", lambda sid: almacen.pop(sid, None))
    monkeypatch.setattr(wr, "S3_BUCKET", "")
    monkeypatch.setattr(mock_phases, "STEP_DELAY", 0)
    sync = MagicMock()
    monkeypatch.setattr(wr, "sync_to_dynamo", sync)
    upd = MagicMock()
    monkeypatch.setattr(dynamo_store, "update_session", upd)
    emisor = Emisor()
    with patch("api.event_publisher.publish", emisor):
        yield type("E", (), {"almacen": almacen, "sync": sync, "upd": upd, "emisor": emisor, "tmp": tmp_path})


def item(school="__mock_ok__", **extra):
    base = {"school_id": school, "prompt": "", "owner_id": "u1", "messages": [], "warnings": [],
            "paci_s3_key": "jobs/s1/paci.pdf", "material_s3_key": "jobs/s1/material.docx", "phase": "running"}
    base.update(extra)
    return base


# ── Fase A ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fase_a_inicial_simulada_guarda_el_estado_y_devuelve_ok(entorno):
    res = await wr.run_phase_a("s1", item(), attempt=1, feedback_agent=0)
    assert res == {"status": "ok"}
    assert entorno.almacen["s1"]["perfil_paci"] == "PERFIL SIMULADO"
    assert [c[0] for c in entorno.emisor.calls] == ["flow_started"]
    assert any("started_at" in c.kwargs for c in entorno.upd.call_args_list)


@pytest.mark.asyncio
async def test_fase_a_bloqueada_por_normativa_deja_la_sesion_terminal(entorno):
    res = await wr.run_phase_a("s1", item("__mock_blocked__"), attempt=1, feedback_agent=0)
    assert res == {"status": "compliance_blocked"}
    sd = entorno.sync.call_args.args[1]
    assert sd.phase == "error" and sd.workflow_status == "compliance_blocked"


@pytest.mark.asyncio
async def test_fase_a_timeout_se_informa_como_timeout(entorno):
    res = await wr.run_phase_a("s1", item("__mock_timeout__"), attempt=1, feedback_agent=0)
    assert res == {"status": "timeout"}


@pytest.mark.asyncio
async def test_fase_a_reintento_usa_el_estado_guardado_y_el_feedback_del_agente_elegido(entorno):
    from utils.hitl_feedback import feedback_a1
    entorno.almacen["s1"] = {"perfil_paci": "P", "planificacion_adaptada": "PL", "hitl_reason": "falta X"}
    capturado = {}

    async def falso(fase, estado, **kw):
        capturado.update(estado)
        return {**estado, "planificacion_adaptada": "PL2"}

    with patch.object(wr, "run_phase", falso):
        res = await wr.run_phase_a("s1", item("colegio_real"), attempt=2, feedback_agent=1)
    assert res == {"status": "ok"}
    assert capturado["retry_agent"] == 1
    assert capturado["hitl_feedback_a1"] == feedback_a1("falta X") and capturado["hitl_feedback_a2"] == ""
    assert entorno.almacen["s1"]["planificacion_adaptada"] == "PL2"
    assert entorno.emisor.calls == []                      # flow_started solo en el intento 1


@pytest.mark.asyncio
async def test_fase_a_el_contexto_de_sesion_se_fija_y_se_libera(entorno):
    from utils.usage_events import monitor_session_id
    visto = []

    async def falso(fase, estado, **kw):
        visto.append(monitor_session_id.get())
        return estado

    with patch.object(wr, "run_phase", falso), patch.object(wr, "_cargar_documentos_sync", lambda it: {"paci_document": "x"}):
        await wr.run_phase_a("s1", item("colegio_real"), attempt=1, feedback_agent=0)
    assert visto == ["s1"] and monitor_session_id.get() == ""


# ── Fase B ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fase_b_simulada_exporta_el_docx_y_completa_la_sesion(entorno):
    entorno.almacen["s1"] = {"perfil_paci": "P", "planificacion_adaptada": "PL"}
    docx = entorno.tmp / "r.docx"
    docx.write_bytes(b"x")
    with patch.object(wr, "export_results_to_docx", return_value=str(docx)) as exp:
        res = await wr.run_phase_b("s1", item())
    assert res == {"status": "success"}
    assert exp.call_args.kwargs["output_filename"] == "rubrica_adaptada_material.docx"
    sd = entorno.sync.call_args.args[1]
    assert sd.phase == "completed" and sd.workflow_status == "success"


@pytest.mark.asyncio
async def test_fase_b_degradada(entorno):
    entorno.almacen["s1"] = {"perfil_paci": "P"}
    with patch.object(wr, "export_results_to_docx", return_value=str(entorno.tmp / "r.docx")):
        res = await wr.run_phase_b("s1", item("__mock_degraded__"))
    assert res == {"status": "degraded"}


@pytest.mark.asyncio
async def test_fase_b_sin_estado_en_s3_lanza_phase_state_not_found(entorno):
    with pytest.raises(phase_store.PhaseStateNotFound):
        await wr.run_phase_b("s1", item())


# ── run_phase_job ───────────────────────────────────────────────────────────
@pytest.fixture
def sfn(monkeypatch):
    s = MagicMock()
    monkeypatch.setattr(sfn_client, "send_success", s.send_success)
    monkeypatch.setattr(sfn_client, "send_failure", s.send_failure)
    monkeypatch.setattr(sfn_client, "send_heartbeat", s.send_heartbeat)
    monkeypatch.setattr(wr, "PHASE_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(dynamo_store, "release_phase", s.release)
    return s


@pytest.mark.asyncio
async def test_job_exitoso_envia_success_y_libera_el_candado(entorno, sfn):
    async def fase(*a, **k):
        return {"status": "ok"}

    with patch.object(wr, "run_phase_a", fase):
        await wr.run_phase_job("a", "s1", item(), "tok", 1, 0)
    sfn.send_success.assert_called_once_with("tok", {"status": "ok"})
    sfn.release.assert_called_once_with("s1", "tok")


@pytest.mark.asyncio
async def test_job_con_estado_ausente_envia_failure_controlado_sin_texto(entorno, sfn):
    """Foco de revisión 4: el estado de S3 no existe; la ejecución no se cuelga y no se filtra texto."""
    await wr.run_phase_job("b", "s1", item(), "tok", 1, 0)
    sfn.send_failure.assert_called_once_with("tok", "PhaseStateNotFound", "")
    sfn.release.assert_called_once_with("s1", "tok")


@pytest.mark.asyncio
async def test_job_abandonado_no_envia_nada(entorno, sfn):
    async def lenta(*a, **k):
        await asyncio.sleep(30)

    sfn.send_heartbeat.side_effect = sfn_client.TaskTokenGone("TaskDoesNotExist")
    with patch.object(wr, "run_phase_a", lenta):
        await wr.run_phase_job("a", "s1", item(), "tok", 1, 0)
    sfn.send_success.assert_not_called()
    sfn.send_failure.assert_not_called()
    sfn.release.assert_called_once_with("s1", "tok")


# ── espera del docente y finalización ───────────────────────────────────────
def test_register_wait_guarda_token_y_pasa_a_awaiting_hitl_en_una_sola_escritura(entorno):
    entorno.almacen["s1"] = {"perfil_paci": "P", "planificacion_adaptada": "PL"}
    wr.register_wait("s1", item(), "tok", attempt=2)
    assert entorno.upd.call_count == 1
    kw = entorno.upd.call_args.kwargs
    assert kw["phase"] == "awaiting_hitl" and kw["task_token"] == "tok"
    assert kw["hitl_data"] == {"perfil_paci": "P", "planificacion_adaptada": "PL", "attempt": 2, "max_attempts": 3}
    assert entorno.emisor.de("hitl_required") == [{"attempt": 2}]


@pytest.mark.parametrize("estado, workflow_status", [("hitl_rejected", "hitl_rejected"), ("expired", "error")])
def test_finalize_marca_la_sesion_y_limpia_el_estado(entorno, estado, workflow_status):
    entorno.almacen["s1"] = {"perfil_paci": "P"}
    wr.finalize_session("s1", item(started_at=100.0), estado)
    kw = entorno.upd.call_args.kwargs
    assert kw["phase"] == "error" and kw["workflow_status"] == workflow_status and kw["task_token"] == ""
    assert "s1" not in entorno.almacen
    assert entorno.emisor.de("flow_finished")[0]["status"] == ("hitl_rejected" if estado == "hitl_rejected" else "error")


def test_finalize_de_un_exito_no_pisa_lo_que_dejo_la_fase_b(entorno):
    wr.finalize_session("s1", item(phase="completed", started_at=None), "success")
    entorno.upd.assert_not_called()
    assert entorno.emisor.de("flow_finished")[0]["status"] == "success"


def test_finalize_de_un_error_inesperado_marca_error_si_la_sesion_no_estaba_terminada(entorno):
    wr.finalize_session("s1", item(phase="running"), "error")
    assert entorno.upd.call_args.kwargs["workflow_status"] == "error"


def test_la_duracion_sale_de_started_at(entorno):
    with patch("time.time", return_value=110.0):
        wr.emit_flow_finished("s1", item(started_at=100.0), "success")
    assert entorno.emisor.de("flow_finished")[0]["duration_ms"] == 10000


# ── decisión del docente ────────────────────────────────────────────────────
@pytest.fixture
def hitl(monkeypatch, entorno):
    h = MagicMock()
    h.consume.return_value = "tok"
    monkeypatch.setattr(dynamo_store, "consume_token", h.consume)
    monkeypatch.setattr(sfn_client, "send_success", h.send)
    return h


def test_rechazo_guarda_la_razon_en_s3_y_envia_solo_approved_y_agente(entorno, hitl):
    """Foco de revisión 5: el comentario del docente NO viaja por Step Functions."""
    wr.submit_hitl_decision("s1", item(phase="awaiting_hitl"), False, "El alumno Juan Pérez, RUT 1-9, necesita más apoyo", 1)
    assert entorno.almacen["s1"]["hitl_reason"].startswith("El alumno Juan")
    token, salida = hitl.send.call_args.args
    assert token == "tok" and set(salida) == {"approved", "agent_to_retry"} and salida == {"approved": False, "agent_to_retry": 1}
    assert "Juan" not in json.dumps(salida) and "RUT" not in json.dumps(salida)
    assert entorno.upd.call_args.kwargs["phase"] == "running"


def test_agente_a_reintentar_por_defecto_es_el_2(entorno, hitl):
    wr.submit_hitl_decision("s1", item(phase="awaiting_hitl"), False, "x", None)
    assert hitl.send.call_args.args[1]["agent_to_retry"] == 2


def test_aprobar_no_toca_la_razon(entorno, hitl):
    wr.submit_hitl_decision("s1", item(phase="awaiting_hitl"), True, None, None)
    assert "s1" not in entorno.almacen
    assert hitl.send.call_args.args[1] == {"approved": True, "agent_to_retry": 2}


def test_sesion_que_no_espera_al_docente_da_409(entorno, hitl):
    with pytest.raises(wr.HitlDecisionError) as e:
        wr.submit_hitl_decision("s1", item(phase="running"), True, None, None)
    assert e.value.status_code == 409
    hitl.send.assert_not_called()


def test_doble_clic_no_envia_dos_veces(entorno, hitl):
    """Foco de revisión 2: el token ya fue consumido."""
    hitl.consume.return_value = None
    with pytest.raises(wr.HitlDecisionError) as e:
        wr.submit_hitl_decision("s1", item(phase="awaiting_hitl"), True, None, None)
    assert e.value.status_code == 409
    hitl.send.assert_not_called()


def test_token_vencido_marca_la_sesion_en_error_y_da_409(entorno, hitl):
    hitl.send.side_effect = sfn_client.TaskTokenGone("TaskTimedOut")
    with pytest.raises(wr.HitlDecisionError) as e:
        wr.submit_hitl_decision("s1", item(phase="awaiting_hitl"), True, None, None)
    assert e.value.status_code == 409
    assert entorno.upd.call_args.kwargs["phase"] == "error"


def test_cancelar_marca_cancelada_detiene_la_ejecucion_y_limpia(entorno, monkeypatch):
    parar = MagicMock()
    monkeypatch.setattr(sfn_client, "stop_execution", parar)
    entorno.almacen["s1"] = {"perfil_paci": "P"}
    wr.cancel_session_sfn("s1", item(started_at=None))
    kw = entorno.upd.call_args.kwargs
    assert kw["phase"] == "error" and kw["workflow_status"] == "cancelled" and kw["task_token"] == ""
    parar.assert_called_once_with("s1")
    assert "s1" not in entorno.almacen
    assert entorno.emisor.de("flow_finished")[0]["status"] == "cancelled"
