"""Cliente de Step Functions: callbacks de la tarea, StopExecution y heartbeat que cancela la fase si el token se pierde."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

from api import sfn_client as sc


def _err(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


@pytest.fixture
def cli(monkeypatch):
    c = MagicMock()
    monkeypatch.setenv("SFN_STATE_MACHINE_ARN", "arn:aws:states:us-east-1:111111111111:stateMachine:prisma-flujo")
    monkeypatch.setattr(sc, "_client", lambda: c)
    return c


def test_enabled_depende_de_la_variable(monkeypatch):
    monkeypatch.delenv("SFN_STATE_MACHINE_ARN", raising=False)
    assert sc.enabled() is False
    monkeypatch.setenv("SFN_STATE_MACHINE_ARN", "arn:x")
    assert sc.enabled() is True


def test_send_success_serializa_la_salida(cli):
    sc.send_success("tok", {"status": "ok"})
    cli.send_task_success.assert_called_once_with(taskToken="tok", output=json.dumps({"status": "ok"}))


@pytest.mark.parametrize("codigo", ["TaskDoesNotExist", "TaskTimedOut", "InvalidToken"])
def test_token_ya_no_valido_se_traduce_a_task_token_gone(cli, codigo):
    cli.send_task_success.side_effect = _err(codigo)
    with pytest.raises(sc.TaskTokenGone):
        sc.send_success("tok", {})


def test_otros_errores_se_propagan(cli):
    cli.send_task_success.side_effect = _err("ThrottlingException")
    with pytest.raises(ClientError):
        sc.send_success("tok", {})


def test_send_failure_recorta_error_y_causa(cli):
    sc.send_failure("tok", "E" * 500, "C" * 40000)
    kw = cli.send_task_failure.call_args.kwargs
    assert len(kw["error"]) == 256 and len(kw["cause"]) == 32768


def test_execution_arn_se_deriva_del_arn_de_la_maquina(cli):
    assert sc.execution_arn("sid-1") == "arn:aws:states:us-east-1:111111111111:execution:prisma-flujo:sid-1"


def test_stop_execution_llama_a_la_api_con_el_arn_derivado(cli):
    sc.stop_execution("sid-1")
    assert cli.stop_execution.call_args.kwargs["executionArn"].endswith(":execution:prisma-flujo:sid-1")


def test_stop_execution_ignora_una_ejecucion_inexistente(cli):
    cli.stop_execution.side_effect = _err("ExecutionDoesNotExist")
    sc.stop_execution("sid-1")                    # no lanza


# ── with_heartbeat ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_with_heartbeat_devuelve_el_resultado_y_envia_latidos():
    latidos = []

    async def trabajo():
        await asyncio.sleep(0.25)
        return {"status": "ok"}

    with patch.object(sc, "send_heartbeat", lambda tok: latidos.append(tok)):
        resultado = await sc.with_heartbeat("tok", trabajo(), interval=0.05)
    assert resultado == {"status": "ok"} and len(latidos) >= 2 and set(latidos) == {"tok"}


@pytest.mark.asyncio
async def test_with_heartbeat_cancela_la_fase_si_el_token_se_pierde():
    """Foco de revisión 3: Step Functions reintentó o canceló; seguir gastando Gemini no sirve de nada."""
    terminado = asyncio.Event()

    async def trabajo():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            terminado.set()
            raise

    def latido_perdido(tok):
        raise sc.TaskTokenGone("TaskDoesNotExist")

    with patch.object(sc, "send_heartbeat", latido_perdido):
        with pytest.raises(sc.PhaseAbandoned):
            await sc.with_heartbeat("tok", trabajo(), interval=0.05)
    assert terminado.is_set()                     # el trabajo fue cancelado


@pytest.mark.asyncio
async def test_with_heartbeat_propaga_los_errores_del_trabajo():
    async def trabajo():
        raise ValueError("boom")

    with patch.object(sc, "send_heartbeat", lambda tok: None):
        with pytest.raises(ValueError):
            await sc.with_heartbeat("tok", trabajo(), interval=0.05)
