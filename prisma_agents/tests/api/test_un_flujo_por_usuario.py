"""Un solo flujo activo por docente: reserva atómica en DynamoDB, 409 al segundo intento y liberación en todo cierre."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock, patch
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


def test_reservar_el_cupo_es_una_escritura_condicional(cli):
    assert dynamo_store.acquire_user_slot("u1", "s1", ttl_seconds=600) == (True, None)
    kw = cli.put_item.call_args.kwargs
    assert kw["Item"]["session_id"] == {"S": "user#u1"} and kw["Item"]["active_session"] == {"S": "s1"}
    assert "attribute_not_exists(session_id)" in kw["ConditionExpression"] and "active_until < :now" in kw["ConditionExpression"]


def test_si_el_cupo_esta_ocupado_devuelve_la_sesion_activa(cli):
    cli.put_item.side_effect = _err("ConditionalCheckFailedException")
    cli.get_item.return_value = {"Item": {"active_session": {"S": "otra"}}}
    assert dynamo_store.acquire_user_slot("u1", "s2") == (False, "otra")


def test_otros_errores_al_reservar_se_propagan(cli):
    cli.put_item.side_effect = _err("InternalServerError")
    with pytest.raises(ClientError):
        dynamo_store.acquire_user_slot("u1", "s1")


def test_liberar_solo_si_el_cupo_es_de_esa_sesion(cli):
    dynamo_store.release_user_slot("u1", "s1")
    kw = cli.delete_item.call_args.kwargs
    assert kw["Key"] == {"session_id": {"S": "user#u1"}} and "active_session = :sid" in kw["ConditionExpression"]


def test_liberar_ignora_un_cupo_ajeno(cli):
    cli.delete_item.side_effect = _err("ConditionalCheckFailedException")
    dynamo_store.release_user_slot("u1", "s1")            # no lanza


def test_el_registro_de_cupo_no_se_puede_leer_como_sesion(cli):
    cli.get_item.return_value = {"Item": {"session_id": {"S": "user#u1"}, "active_session": {"S": "s1"}}}
    assert dynamo_store.get_session("user#u1") is None


def test_sin_dynamodb_el_cupo_siempre_se_concede(monkeypatch):
    monkeypatch.setattr(dynamo_store, "TABLE", "")
    assert dynamo_store.acquire_user_slot("u1", "s1") == (True, None)
    dynamo_store.release_user_slot("u1", "s1")


# ── /chat/start ─────────────────────────────────────────────────────────────
@pytest.fixture
def web(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: {"sub": "u1"}
    SESSIONS.clear()
    yield
    app.dependency_overrides.pop(get_current_user, None)
    SESSIONS.clear()


ARCHIVOS = {"paci_file": ("p.pdf", b"x"), "material_file": ("m.docx", b"y")}


def test_start_con_cupo_ocupado_responde_409_con_la_sesion_activa(web):
    d = MagicMock()
    d.enabled.return_value = True
    d.acquire_user_slot.return_value = (False, "activa-1")
    with patch.object(chat_router, "dynamo_store", d), patch.object(chat_router.boto3, "client") as s3:
        res = client.post("/chat/start", files=ARCHIVOS)
    assert res.status_code == 409
    assert res.json()["detail"] == {"code": "flow_in_progress", "message": "Ya tienes un flujo en acción", "session_id": "activa-1"}
    s3.assert_not_called()
    d.create_session.assert_not_called()


def test_start_exitoso_reserva_el_cupo_con_el_dueno_y_la_sesion(web):
    d = MagicMock()
    d.enabled.return_value = True
    d.acquire_user_slot.return_value = (True, None)
    with patch.object(chat_router, "dynamo_store", d), patch.object(chat_router, "S3_BUCKET", "b"), patch.object(chat_router.boto3, "client"):
        res = client.post("/chat/start", files=ARCHIVOS)
    assert res.status_code == 201
    args = d.acquire_user_slot.call_args.args
    assert args[0] == "u1" and args[1] == res.json()["session_id"]


def test_si_falla_la_subida_a_s3_se_libera_el_cupo(web):
    d = MagicMock()
    d.enabled.return_value = True
    d.acquire_user_slot.return_value = (True, None)
    s3 = MagicMock()
    s3.put_object.side_effect = RuntimeError("s3 caido")
    with patch.object(chat_router, "dynamo_store", d), patch.object(chat_router, "S3_BUCKET", "b"), patch.object(chat_router.boto3, "client", return_value=s3):
        res = client.post("/chat/start", files=ARCHIVOS)
    assert res.status_code == 500
    d.release_user_slot.assert_called_once()


def test_sin_dynamodb_se_limita_por_las_sesiones_en_memoria(web):
    activa = SessionData(owner_id="u1")                   # phase "running" por defecto
    SESSIONS["viva"] = activa
    res = client.post("/chat/start", files=ARCHIVOS)
    assert res.status_code == 409 and res.json()["detail"]["session_id"] == "viva"


def test_sin_dynamodb_una_sesion_terminada_no_bloquea(web):
    terminada = SessionData(owner_id="u1")
    terminada.phase = "completed"
    SESSIONS["vieja"] = terminada
    with patch.object(chat_router, "run_workflow_for_api", MagicMock(return_value=None)), patch.object(chat_router.asyncio, "create_task", MagicMock()):
        res = client.post("/chat/start", files=ARCHIVOS)
    assert res.status_code == 201


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
