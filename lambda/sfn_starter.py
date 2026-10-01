"""
PRISMA — Lambda `starter`: SQS (evento de S3) -> Step Functions StartExecution.

Cadena:  S3 PUT jobs/{id}/paci.* -> SQS -> esta Lambda -> máquina de estados `prisma-flujo`.

* Va FUERA de la VPC: necesita llegar a la API pública de Step Functions (las subredes por defecto no dan internet a una Lambda en VPC).
* Idempotente: la ejecución se nombra con el session_id. SQS entrega "al menos una vez": si ya existe (abierta o cerrada),
  `ExecutionAlreadyExists` NO es un fallo.
* Solo reacciona al PACI: el material también dispara un evento de S3 y se ignora.
* Sin PII: por Step Functions viajan solo el session_id y los tiempos.
* ReportBatchItemFailures: solo vuelven a la cola los mensajes que fallaron.
* No registra el contenido de los mensajes.

Variables: STATE_MACHINE_ARN (requerida), FASE_TIMEOUT_SECONDS (1800), HEARTBEAT_SECONDS (120), HITL_TIMEOUT_SECONDS (86400)
"""
import json
import os
import re
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

_PACI = re.compile(
    r"^jobs/(?P<sid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/paci\.[A-Za-z0-9]+$"
)
_sfn = None


def _client():
    global _sfn
    if _sfn is None:
        _sfn = boto3.client("stepfunctions")
    return _sfn


def _timeouts() -> dict:
    return {
        "fase": int(os.environ.get("FASE_TIMEOUT_SECONDS", "1800")),
        "heartbeat": int(os.environ.get("HEARTBEAT_SECONDS", "120")),
        "hitl": int(os.environ.get("HITL_TIMEOUT_SECONDS", "86400")),
    }


def _session_ids(cuerpo: dict):
    for rec in cuerpo.get("Records", []):
        clave = unquote_plus(rec.get("s3", {}).get("object", {}).get("key", ""))
        m = _PACI.match(clave)
        if m:
            yield m.group("sid")


def _iniciar(session_id: str) -> None:
    try:
        _client().start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=session_id,
            input=json.dumps({"session_id": session_id, "timeouts": _timeouts()}),
        )
        print(f"ejecucion iniciada: {session_id}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ExecutionAlreadyExists":
            print(f"ejecucion ya existia (mensaje repetido): {session_id}")
            return
        raise


def handler(event: dict, context) -> dict:
    fallidos = []
    for registro in event.get("Records", []):
        try:
            for session_id in _session_ids(json.loads(registro["body"])):
                _iniciar(session_id)
        except Exception as exc:                                # noqa: BLE001 — cualquier fallo devuelve el mensaje a la cola
            print(f"fallo {type(exc).__name__} en el mensaje {registro.get('messageId')}")
            fallidos.append({"itemIdentifier": registro["messageId"]})
    return {"batchItemFailures": fallidos}
