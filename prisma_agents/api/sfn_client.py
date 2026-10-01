"""
Cliente de Step Functions para el workflow: completa las tareas con `waitForTaskToken`, envía heartbeats y cancela ejecuciones.

`boto3` respeta AWS_ENDPOINT_URL, así que en local apunta a LocalStack sin tocar código.

Datos personales: por Step Functions solo viajan ids, números y estados (su historial guarda entradas y salidas 90 días en claro).
Por eso `send_failure` NO recibe texto de excepciones (podrían incluir contenido de documentos): solo el nombre de la clase.
"""
import asyncio
import json
import logging
import os
from typing import Awaitable

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_TOKEN_INVALIDO = {"TaskDoesNotExist", "TaskTimedOut", "InvalidToken"}


class TaskTokenGone(Exception):
    """El token de tarea ya no es válido (la tarea venció, fue reintentada o la ejecución se detuvo)."""


class PhaseAbandoned(Exception):
    """La fase se canceló porque su token dejó de ser válido."""


def _client():
    return boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def enabled() -> bool:
    return bool(os.environ.get("SFN_STATE_MACHINE_ARN"))


def _traducir(e: ClientError) -> Exception:
    return TaskTokenGone(e.response["Error"]["Code"]) if e.response["Error"]["Code"] in _TOKEN_INVALIDO else e


def send_success(token: str, output: dict) -> None:
    try:
        _client().send_task_success(taskToken=token, output=json.dumps(output))
    except ClientError as e:
        raise _traducir(e) from e


def send_failure(token: str, error: str, cause: str = "") -> None:
    try:
        _client().send_task_failure(taskToken=token, error=error[:256], cause=cause[:32768])
    except ClientError as e:
        raise _traducir(e) from e


def send_heartbeat(token: str) -> None:
    try:
        _client().send_task_heartbeat(taskToken=token)
    except ClientError as e:
        raise _traducir(e) from e


def execution_arn(session_id: str) -> str:
    """El ARN de la ejecución se deriva del de la máquina: la ejecución se nombra con el session_id."""
    return os.environ["SFN_STATE_MACHINE_ARN"].replace(":stateMachine:", ":execution:") + f":{session_id}"


def stop_execution(session_id: str) -> None:
    try:
        _client().stop_execution(executionArn=execution_arn(session_id), cause="Cancelada por el docente")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ExecutionDoesNotExist":
            raise


async def with_heartbeat(token: str, work: Awaitable, interval: float = 30.0):
    """Corre `work` enviando un heartbeat cada `interval` segundos.

    Si el heartbeat descubre que el token ya no es válido, CANCELA el trabajo y lanza PhaseAbandoned: nadie está
    esperando ese resultado y seguir sería gastar llamadas a Gemini.
    """
    tarea = asyncio.ensure_future(work)
    try:
        while True:
            hecho, _ = await asyncio.wait({tarea}, timeout=interval)
            if hecho:
                return tarea.result()
            try:
                await asyncio.to_thread(send_heartbeat, token)
            except TaskTokenGone as e:
                tarea.cancel()
                await asyncio.gather(tarea, return_exceptions=True)
                raise PhaseAbandoned("el token de tarea ya no es válido") from e
    except asyncio.CancelledError:
        tarea.cancel()
        raise
