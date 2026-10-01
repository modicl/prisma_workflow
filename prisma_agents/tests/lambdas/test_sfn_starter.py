"""Lambda `starter`: SQS (evento de S3) -> StartExecution. Idempotente, tolera repeticiones y no deja mensajes envenenados sin dueño."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lambda"))

import pytest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError

import sfn_starter as st

SID = "123e4567-e89b-42d3-a456-426614174000"


def mensaje(key, mid="m1"):
    return {"messageId": mid, "body": json.dumps({"Records": [{"s3": {"object": {"key": key}}}]})}


@pytest.fixture
def sfn(monkeypatch):
    c = MagicMock()
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:1:stateMachine:prisma-flujo")
    for v in ("FASE_TIMEOUT_SECONDS", "HEARTBEAT_SECONDS", "HITL_TIMEOUT_SECONDS"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(st, "_client", lambda: c)
    return c


def test_el_paci_arranca_la_ejecucion_nombrada_con_el_session_id(sfn):
    res = st.handler({"Records": [mensaje(f"jobs/{SID}/paci.pdf")]}, None)
    assert res == {"batchItemFailures": []}
    kw = sfn.start_execution.call_args.kwargs
    assert kw["name"] == SID and kw["stateMachineArn"].endswith("stateMachine:prisma-flujo")
    assert json.loads(kw["input"]) == {"session_id": SID, "timeouts": {"fase": 1800, "heartbeat": 120, "hitl": 86400}}


def test_acepta_paci_docx_y_claves_url_encoded(sfn):
    st.handler({"Records": [mensaje(f"jobs%2F{SID}%2Fpaci.docx")]}, None)
    assert sfn.start_execution.call_args.kwargs["name"] == SID


@pytest.mark.parametrize("key", [f"jobs/{SID}/material.docx", f"results/{SID}/rubrica.docx", f"state/{SID}.json",
                                 "jobs/no-es-uuid/paci.pdf", f"jobs/{SID}/otra/paci.pdf", f"jobs/{SID}/paci"])
def test_ignora_todo_lo_que_no_sea_el_paci(sfn, key):
    res = st.handler({"Records": [mensaje(key)]}, None)
    assert res == {"batchItemFailures": []}
    sfn.start_execution.assert_not_called()


def test_ignora_el_evento_de_prueba_de_s3(sfn):
    res = st.handler({"Records": [{"messageId": "m1", "body": json.dumps({"Service": "Amazon S3", "Event": "s3:TestEvent"})}]}, None)
    assert res == {"batchItemFailures": []}
    sfn.start_execution.assert_not_called()


def test_mensaje_repetido_no_es_un_fallo(sfn):
    """Foco de revisión 1: SQS entrega 'al menos una vez'; la ejecución ya existe (abierta o cerrada)."""
    sfn.start_execution.side_effect = ClientError({"Error": {"Code": "ExecutionAlreadyExists", "Message": "x"}}, "StartExecution")
    res = st.handler({"Records": [mensaje(f"jobs/{SID}/paci.pdf")]}, None)
    assert res == {"batchItemFailures": []}


def test_otro_error_de_aws_devuelve_el_mensaje_para_reintento(sfn):
    sfn.start_execution.side_effect = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "StartExecution")
    res = st.handler({"Records": [mensaje(f"jobs/{SID}/paci.pdf", "m9")]}, None)
    assert res == {"batchItemFailures": [{"itemIdentifier": "m9"}]}


def test_cuerpo_que_no_es_json_se_devuelve_para_que_termine_en_la_dlq(sfn):
    res = st.handler({"Records": [{"messageId": "m3", "body": "no soy json"}]}, None)
    assert res == {"batchItemFailures": [{"itemIdentifier": "m3"}]}


def test_un_lote_con_un_fallo_solo_devuelve_ese_mensaje(sfn):
    otra = "223e4567-e89b-42d3-a456-426614174999"
    sfn.start_execution.side_effect = [None, ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "x"}}, "StartExecution")]
    res = st.handler({"Records": [mensaje(f"jobs/{SID}/paci.pdf", "a"), mensaje(f"jobs/{otra}/paci.pdf", "b")]}, None)
    assert res == {"batchItemFailures": [{"itemIdentifier": "b"}]}


def test_los_timeouts_se_leen_del_entorno(sfn, monkeypatch):
    monkeypatch.setenv("FASE_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("HEARTBEAT_SECONDS", "10")
    monkeypatch.setenv("HITL_TIMEOUT_SECONDS", "300")
    st.handler({"Records": [mensaje(f"jobs/{SID}/paci.pdf")]}, None)
    assert json.loads(sfn.start_execution.call_args.kwargs["input"])["timeouts"] == {"fase": 60, "heartbeat": 10, "hitl": 300}


def test_no_registra_el_contenido_del_mensaje(sfn, capsys):
    st.handler({"Records": [mensaje(f"jobs/{SID}/paci.pdf")]}, None)
    assert "paci.pdf" not in capsys.readouterr().out
