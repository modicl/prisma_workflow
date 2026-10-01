"""Endpoints internos de fase y el doble camino de /hitl y /cancel (memoria vs Step Functions)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from api import chat_router, dynamo_store
from api.auth import get_current_user
from api.main import app
from api.session_store import SESSIONS

client = TestClient(app)
INTERNO = {"X-Internal-Token": "tok-interno"}
BODY = {"task_token": "tok", "attempt": 2, "feedback_agent": 1}
ITEM = {"session_id": "s1", "phase": "awaiting_hitl", "owner_id": "u1", "school_id": "colegio", "messages": []}


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setattr(chat_router, "INTERNAL_TOKEN", "tok-interno")
    previo = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u1"}
    SESSIONS.clear()
    yield
    if previo is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = previo


@pytest.fixture
def db(monkeypatch):
    d = MagicMock()
    d.get_session.return_value = dict(ITEM)
    d.acquire_phase.return_value = True
    monkeypatch.setattr(chat_router, "dynamo_store", d)
    return d


@pytest.fixture
def wr(monkeypatch):
    w = MagicMock()
    w.run_phase_job = AsyncMock()
    w.HitlDecisionError = chat_router.workflow_runner.HitlDecisionError
    monkeypatch.setattr(chat_router, "workflow_runner", w)
    return w


# Nota: `run_phase_job` es un AsyncMock; la llamada queda registrada en el momento de crear la corrutina (dentro del endpoint),
# así que no hace falta esperar a que la tarea en segundo plano corra. No se parchea asyncio: el cliente de pruebas lo usa por dentro.

# ── autenticación interna ───────────────────────────────────────────────────
@pytest.mark.parametrize("ruta", ["a", "b", "wait", "finalize"])
def test_sin_token_interno_es_401(db, wr, ruta):
    body = {"status": "success"} if ruta == "finalize" else BODY
    assert client.post(f"/chat/internal/phase/{ruta}/s1", json=body).status_code == 401
    assert client.post(f"/chat/internal/phase/{ruta}/s1", json=body, headers={"X-Internal-Token": "otro"}).status_code == 401


def test_sesion_inexistente_es_404(db, wr):
    db.get_session.return_value = None
    assert client.post("/chat/internal/phase/a/s1", json=BODY, headers=INTERNO).status_code == 404


def test_fase_desconocida_es_422(db, wr):
    assert client.post("/chat/internal/phase/z/s1", json=BODY, headers=INTERNO).status_code == 422


# ── fases a / b ─────────────────────────────────────────────────────────────
def test_fase_a_toma_el_candado_y_lanza_el_trabajo(db, wr):
    res = client.post("/chat/internal/phase/a/s1", json=BODY, headers=INTERNO)
    assert res.status_code == 202 and res.json() == {"started": True}
    assert db.acquire_phase.call_args.args[:2] == ("s1", "a:2")
    wr.run_phase_job.assert_called_once()
    assert wr.run_phase_job.call_args.args == ("a", "s1", db.get_session.return_value, "tok", 2, 1)


def test_fase_b_usa_su_propia_clave_de_candado(db, wr):
    client.post("/chat/internal/phase/b/s1", json={"task_token": "tok"}, headers=INTERNO)
    assert db.acquire_phase.call_args.args[:2] == ("s1", "b:1")


def test_si_otra_ejecucion_tiene_el_candado_no_se_lanza_nada(db, wr):
    db.acquire_phase.return_value = False
    res = client.post("/chat/internal/phase/a/s1", json=BODY, headers=INTERNO)
    assert res.status_code == 202 and res.json() == {"started": False}
    wr.run_phase_job.assert_not_called()


def test_el_colegio_mock_dead_simula_un_worker_que_no_responde(db, wr):
    db.get_session.return_value = {**ITEM, "school_id": "__mock_dead__"}
    res = client.post("/chat/internal/phase/a/s1", json=BODY, headers=INTERNO)
    assert res.json() == {"started": True}
    db.acquire_phase.assert_not_called()
    wr.run_phase_job.assert_not_called()


# ── wait / finalize (no deben ser capturados por /phase/{phase}) ────────────
def test_wait_registra_el_token(db, wr):
    res = client.post("/chat/internal/phase/wait/s1", json=BODY, headers=INTERNO)
    assert res.status_code == 200 and res.json() == {"ok": True}
    wr.register_wait.assert_called_once_with("s1", db.get_session.return_value, "tok", 2)


def test_finalize_cierra_la_sesion(db, wr):
    res = client.post("/chat/internal/phase/finalize/s1", json={"status": "expired"}, headers=INTERNO)
    assert res.status_code == 200
    wr.finalize_session.assert_called_once_with("s1", db.get_session.return_value, "expired")


# ── /hitl ───────────────────────────────────────────────────────────────────
def test_hitl_de_una_sesion_de_step_functions(db, wr):
    db.enabled.return_value = True
    res = client.post("/chat/s1/hitl", json={"approved": False, "reason": "falta X", "agent_to_retry": 1})
    assert res.status_code == 200 and res.json() == {"ok": True}
    wr.submit_hitl_decision.assert_called_once_with("s1", db.get_session.return_value, False, "falta X", 1)


def test_hitl_de_otro_docente_es_403(db, wr):
    db.enabled.return_value = True
    db.get_session.return_value = {**ITEM, "owner_id": "otro"}
    assert client.post("/chat/s1/hitl", json={"approved": True}).status_code == 403
    wr.submit_hitl_decision.assert_not_called()


def test_hitl_doble_clic_se_traduce_a_409(db, wr):
    db.enabled.return_value = True
    wr.submit_hitl_decision.side_effect = chat_router.workflow_runner.HitlDecisionError(409, "La decisión ya fue enviada")
    res = client.post("/chat/s1/hitl", json={"approved": True})
    assert res.status_code == 409 and "ya fue enviada" in res.json()["detail"]


def test_hitl_de_una_sesion_en_memoria_sigue_usando_la_cola(db, wr):
    """Compatibilidad: el camino antiguo no cambia."""
    from api.session_store import SessionData
    db.enabled.return_value = True
    SESSIONS["s1"] = SessionData(owner_id="u1")
    SESSIONS["s1"].phase = "awaiting_hitl"
    SESSIONS["s1"].task = MagicMock()          # el camino antiguo siempre tiene una tarea asyncio propia
    res = client.post("/chat/s1/hitl", json={"approved": True})
    assert res.status_code == 200 and SESSIONS["s1"].hitl_response_queue.qsize() == 1
    wr.submit_hitl_decision.assert_not_called()


def test_hitl_de_sesion_desconocida_es_404(db, wr):
    db.enabled.return_value = True
    db.get_session.return_value = None
    assert client.post("/chat/s1/hitl", json={"approved": True}).status_code == 404


# ── /cancel ─────────────────────────────────────────────────────────────────
def test_cancel_de_una_sesion_de_step_functions(db, wr):
    db.enabled.return_value = True
    res = client.post("/chat/s1/cancel")
    assert res.status_code == 200
    wr.cancel_session_sfn.assert_called_once_with("s1", db.get_session.return_value)


def test_cancel_de_una_sesion_ya_terminada_es_409(db, wr):
    db.enabled.return_value = True
    db.get_session.return_value = {**ITEM, "phase": "completed"}
    assert client.post("/chat/s1/cancel").status_code == 409
    wr.cancel_session_sfn.assert_not_called()


def test_cancel_de_otro_docente_es_403(db, wr):
    db.enabled.return_value = True
    db.get_session.return_value = {**ITEM, "owner_id": "otro"}
    assert client.post("/chat/s1/cancel").status_code == 403
