"""Correcciones de la revisión final de la rama: despacho de /hitl y /cancel, token perdido, candado, heartbeat y plazos."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock, patch

from api import chat_router, dynamo_store, phase_store, sfn_client, workflow_runner as wr
from api.auth import get_current_user
from api.main import app
from api.session_store import SESSIONS, SessionData
from fastapi.testclient import TestClient

client = TestClient(app)
ITEM = {"session_id": "s1", "phase": "awaiting_hitl", "owner_id": "u1", "school_id": "c", "messages": []}


@pytest.fixture
def web(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u1"}
    d = MagicMock()
    d.enabled.return_value = True
    d.get_session.return_value = dict(ITEM)
    w = MagicMock()
    w.HitlDecisionError = wr.HitlDecisionError
    monkeypatch.setattr(chat_router, "dynamo_store", d)
    monkeypatch.setattr(chat_router, "workflow_runner", w)
    SESSIONS.clear()
    yield w
    app.dependency_overrides.pop(get_current_user, None)
    SESSIONS.clear()


# ── C1: una sesión creada por /chat/start está en SESSIONS pero NO tiene tarea propia ──
def test_hitl_de_sesion_de_start_con_step_functions_usa_el_token(web):
    SESSIONS["s1"] = SessionData(owner_id="u1")           # /start la registra siempre; sd.task sigue en None
    res = client.post("/chat/s1/hitl", json={"approved": True})
    assert res.status_code == 200
    web.submit_hitl_decision.assert_called_once()


def test_cancel_de_sesion_de_start_con_step_functions_detiene_la_ejecucion(web):
    SESSIONS["s1"] = SessionData(owner_id="u1")
    assert client.post("/chat/s1/cancel").status_code == 200
    web.cancel_session_sfn.assert_called_once()


def test_sesion_con_tarea_propia_sigue_en_memoria(web):
    sd = SessionData(owner_id="u1")
    sd.phase = "awaiting_hitl"
    sd.task = MagicMock()
    SESSIONS["s1"] = sd
    assert client.post("/chat/s1/hitl", json={"approved": True}).status_code == 200
    web.submit_hitl_decision.assert_not_called()


# ── I1 / I2: decisión del docente ───────────────────────────────────────────
@pytest.fixture
def hitl(monkeypatch):
    h = MagicMock()
    h.consume.return_value = "tok"
    monkeypatch.setattr(dynamo_store, "consume_token", h.consume)
    monkeypatch.setattr(dynamo_store, "restore_token", h.restore)
    monkeypatch.setattr(dynamo_store, "update_session", h.upd)
    monkeypatch.setattr(sfn_client, "send_success", h.send)
    monkeypatch.setattr(phase_store, "update_state", h.estado)
    return h


def test_si_send_success_falla_se_devuelve_el_token_y_se_responde_503(hitl):
    hitl.send.side_effect = RuntimeError("throttling")
    with pytest.raises(wr.HitlDecisionError) as e:
        wr.submit_hitl_decision("s1", dict(ITEM), True, None, None)
    assert e.value.status_code == 503
    hitl.restore.assert_called_once_with("s1", "tok")
    assert hitl.upd.call_args.kwargs["phase"] == "awaiting_hitl"


def test_la_fase_pasa_a_running_antes_de_enviar_el_exito(hitl):
    orden = []
    hitl.upd.side_effect = lambda *a, **k: orden.append(("upd", k.get("phase")))
    hitl.send.side_effect = lambda *a, **k: orden.append(("send", None))
    wr.submit_hitl_decision("s1", dict(ITEM), True, None, None)
    assert orden == [("upd", "running"), ("send", None)]


# ── I3: candado de fase ─────────────────────────────────────────────────────
def test_un_reintento_con_otro_token_toma_el_candado():
    cli = MagicMock()
    with patch.object(dynamo_store, "TABLE", "t"), patch.object(dynamo_store, "_client", cli):
        assert dynamo_store.acquire_phase("s1", "a:1", token="nuevo") is True
    kw = cli.update_item.call_args.kwargs
    assert "running_token <> :tok" in kw["ConditionExpression"] and kw["ExpressionAttributeValues"][":tok"] == {"S": "nuevo"}


def test_release_solo_borra_el_candado_propio():
    cli = MagicMock()
    with patch.object(dynamo_store, "TABLE", "t"), patch.object(dynamo_store, "_client", cli):
        dynamo_store.release_phase("s1", token="mio")
    kw = cli.update_item.call_args.kwargs
    assert "running_token = :tok" in kw["ConditionExpression"]


def test_restore_token_es_condicional():
    cli = MagicMock()
    with patch.object(dynamo_store, "TABLE", "t"), patch.object(dynamo_store, "_client", cli):
        dynamo_store.restore_token("s1", "tok")
    assert "attribute_not_exists(task_token)" in cli.update_item.call_args.kwargs["ConditionExpression"]


# ── I4: heartbeat con error transitorio ─────────────────────────────────────
@pytest.mark.asyncio
async def test_un_error_transitorio_de_heartbeat_no_mata_la_fase():
    llamadas = []

    def latido(tok):
        llamadas.append(1)
        if len(llamadas) == 1:
            raise RuntimeError("throttling")

    async def trabajo():
        await asyncio.sleep(0.3)
        return {"status": "ok"}

    with patch.object(sfn_client, "send_heartbeat", latido):
        assert await sfn_client.with_heartbeat("tok", trabajo(), interval=0.05) == {"status": "ok"}
    assert len(llamadas) >= 2


@pytest.mark.asyncio
async def test_si_with_heartbeat_se_cancela_cancela_el_trabajo():
    terminado = asyncio.Event()

    async def trabajo():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            terminado.set()
            raise

    with patch.object(sfn_client, "send_heartbeat", lambda t: None):
        t = asyncio.ensure_future(sfn_client.with_heartbeat("tok", trabajo(), interval=0.05))
        await asyncio.sleep(0.15)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        await asyncio.sleep(0.05)
    assert terminado.is_set()


# ── I5: infraestructura caída ───────────────────────────────────────────────
ASL = os.path.join(os.path.dirname(__file__), "..", "..", "..", "statemachine", "prisma_flow.asl.json")


def test_finalizar_reintenta_unos_minutos_y_no_termina_sin_aviso():
    f = json.load(open(ASL, encoding="utf-8"))["States"]["Finalizar"]
    r = f["Retry"][0]
    espera = sum(r["IntervalSeconds"] * r["BackoffRate"] ** i for i in range(r["MaxAttempts"]))
    assert espera >= 300
    assert f["Catch"][0]["ErrorEquals"] == ["States.ALL"] and f["Catch"][0]["Next"] == "Falla"


def test_esperar_docente_reintenta_si_falla_la_invocacion():
    e = json.load(open(ASL, encoding="utf-8"))["States"]["EsperarDocente"]
    assert any("States.TaskFailed" in r["ErrorEquals"] for r in e["Retry"])


# ── I6: plazos del invoker ──────────────────────────────────────────────────
def test_los_reintentos_del_invoker_caben_en_el_timeout_de_la_lambda(monkeypatch):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lambda"))
    import sfn_invoker as inv
    for v in ("API_TIMEOUT", "RETRY_ATTEMPTS"):
        monkeypatch.delenv(v, raising=False)
    intentos, http = 3, int(inv.DEFAULT_API_TIMEOUT)
    assert intentos * http + sum(2 ** i for i in range(1, intentos)) < 45
