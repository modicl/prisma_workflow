"""Un solo flujo activo por docente: ms-docs reserva el cupo; el workflow lo libera en todo cierre y no expone los registros de cupo."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from api import chat_router, dynamo_store, phase_store, sfn_client, workflow_runner as wr
from api.auth import get_current_user
from api.main import app
from api.session_store import SESSIONS, SessionData, sync_to_dynamo

client = TestClient(app)


def _err(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


# ── dynamo_store ────────────────────────────────────────────────────────────
@pytest.fixture
def cli(monkeypatch):
    c = MagicMock()
    monkeypatch.setattr(dynamo_store, "TABLE", "t")
    monkeypatch.setattr(dynamo_store, "_client", c)
    return c


def test_liberar_solo_si_el_cupo_es_de_esa_sesion(cli):
    dynamo_store.release_user_slot("u1", "s1")
    kw = cli.delete_item.call_args.kwargs
    assert kw["Key"] == {"session_id": {"S": "user#u1"}} and "active_session = :sid" in kw["ConditionExpression"]


def test_sin_dynamodb_liberar_el_cupo_es_inocuo(monkeypatch):
    monkeypatch.setattr(dynamo_store, "TABLE", "")
    dynamo_store.release_user_slot("u1", "s1")


def test_liberar_ignora_un_cupo_ajeno(cli):
    cli.delete_item.side_effect = _err("ConditionalCheckFailedException")
    dynamo_store.release_user_slot("u1", "s1")            # no lanza


def test_el_registro_de_cupo_no_se_puede_leer_como_sesion(cli):
    cli.get_item.return_value = {"Item": {"session_id": {"S": "user#u1"}, "active_session": {"S": "s1"}}}
    assert dynamo_store.get_session("user#u1") is None


# ── liberación en todo cierre ───────────────────────────────────────────────
@pytest.fixture
def libera(monkeypatch):
    m = MagicMock()
    monkeypatch.setattr(dynamo_store, "release_user_slot", m)
    monkeypatch.setattr(dynamo_store, "update_session", MagicMock())
    monkeypatch.setattr(phase_store, "delete_state", MagicMock())
    monkeypatch.setattr(sfn_client, "stop_execution", MagicMock())
    return m


@pytest.mark.parametrize("fase", ["completed", "error"])
def test_sync_to_dynamo_libera_el_cupo_en_un_estado_terminal(libera, fase):
    sd = SessionData(owner_id="u1")
    sd.phase = fase
    sync_to_dynamo("s1", sd)
    libera.assert_called_once_with("u1", "s1")


def test_sync_to_dynamo_no_libera_mientras_corre(libera):
    sd = SessionData(owner_id="u1")
    sync_to_dynamo("s1", sd)
    libera.assert_not_called()


def test_finalizar_libera_el_cupo(libera):
    wr.finalize_session("s1", {"owner_id": "u1", "phase": "completed"}, "success")
    libera.assert_called_once_with("u1", "s1")


def test_cancelar_libera_el_cupo(libera):
    wr.cancel_session_sfn("s1", {"owner_id": "u1"})
    libera.assert_called_once_with("u1", "s1")


def test_las_sesiones_de_ms_docs_exponen_user_id_como_owner_id(cli):
    """ms-docs guarda `user_id`; sin esto el cupo no se libera y la comprobación de dueño de /hitl era permisiva."""
    cli.get_item.return_value = {"Item": {"session_id": {"S": "s1"}, "phase": {"S": "running"}, "user_id": {"S": "u1"}}}
    assert dynamo_store.get_session("s1")["owner_id"] == "u1"
