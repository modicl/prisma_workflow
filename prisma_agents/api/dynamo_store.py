import json
import logging
import os
import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_client = None
TABLE = os.environ.get("DYNAMO_TABLE", "")
TTL_DAYS = 7

logger.info(f"dynamo_store loaded — TABLE={TABLE!r} REGION={os.environ.get('AWS_REGION', 'us-east-1')!r}")

_FIELD_TYPES = {
    "phase": "S",
    "messages": "S",
    "hitl_data": "S",
    "error": "S",
    "docx_s3_key": "S",
    "workflow_status": "S",
    "warnings": "S",
    "task_token": "S",     # token de Step Functions de la espera del docente
    "started_at": "S",     # epoch (texto) del inicio del flujo, para medir duración
}
_TRANSFORMS = {
    "messages": json.dumps,
    "hitl_data": json.dumps,
    "error": lambda v: v or "",
    "docx_s3_key": lambda v: v or "",
    "workflow_status": lambda v: v or "",
    "warnings": json.dumps,
    "task_token": lambda v: v or "",
}


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _client


def enabled() -> bool:
    return bool(TABLE)


def create_session(session_id: str, **fields) -> None:
    if not enabled():
        logger.warning(f"create_session called but DynamoDB is disabled (DYNAMO_TABLE={TABLE!r})")
        return
    logger.info(f"create_session {session_id} — table={TABLE}")
    try:
        _get_client().put_item(
            TableName=TABLE,
            Item={
                "session_id":      {"S": session_id},
                "phase":           {"S": fields.get("phase", "running")},
                "messages":        {"S": "[]"},
                "hitl_data":       {"S": "null"},
                "error":           {"S": ""},
                "docx_s3_key":     {"S": ""},
                "workflow_status": {"S": ""},
                "warnings":        {"S": "[]"},
                "paci_s3_key":     {"S": fields.get("paci_s3_key", "")},
                "material_s3_key": {"S": fields.get("material_s3_key", "")},
                "prompt":          {"S": fields.get("prompt", "")},
                "school_id":       {"S": fields.get("school_id", "")},
                "owner_id":        {"S": fields.get("owner_id", "")},
                "expires_at":      {"N": str(int(time.time()) + TTL_DAYS * 86400)},
            },
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        logger.error("DynamoDB error [%s] en create_session %s: %s", code, session_id, e)
    except Exception as e:
        logger.error("DynamoDB error inesperado en create_session %s: %s", session_id, e)


def get_session(session_id: str) -> Optional[dict]:
    if not enabled():
        return None
    try:
        resp = _get_client().get_item(
            TableName=TABLE,
            Key={"session_id": {"S": session_id}},
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        logger.error("DynamoDB error [%s] en get_session %s: %s", code, session_id, e)
        return None
    except Exception as e:
        logger.error("DynamoDB error inesperado en get_session %s: %s", session_id, e)
        return None
    item = resp.get("Item")
    if not item or "phase" not in item:                 # los registros de cupo (`user#...`) no son sesiones
        return None
    return {
        "session_id":      item["session_id"]["S"],
        "phase":           item["phase"]["S"],
        "messages":        json.loads(item.get("messages", {}).get("S", "[]")),
        "hitl_data":       json.loads(item.get("hitl_data", {}).get("S", "null")),
        "error":           item.get("error", {}).get("S") or None,
        "docx_s3_key":     item.get("docx_s3_key", {}).get("S") or None,
        "workflow_status": item.get("workflow_status", {}).get("S") or None,
        "warnings":        json.loads(item.get("warnings", {}).get("S", "[]")),
        "paci_s3_key":     item.get("paci_s3_key", {}).get("S", ""),
        "material_s3_key": item.get("material_s3_key", {}).get("S", ""),
        "prompt":          item.get("prompt", {}).get("S", ""),
        "school_id":       item.get("school_id", {}).get("S", ""),
        # ms-docs guarda `user_id` (mismo valor: el `sub` de Cognito); las sesiones antiguas guardaban `owner_id`.
        "owner_id":        item.get("owner_id", {}).get("S") or item.get("user_id", {}).get("S") or None,
        "task_token":      item.get("task_token", {}).get("S") or None,
        "started_at":      float(item.get("started_at", {}).get("S") or 0) or None,
    }


def update_session(session_id: str, **fields) -> None:
    if not enabled():
        return
    expr_parts = []
    names: dict = {}
    values: dict = {}
    for i, (key, val) in enumerate(fields.items()):
        if key not in _FIELD_TYPES:
            continue
        transform = _TRANSFORMS.get(key, str)
        names[f"#f{i}"] = key
        values[f":v{i}"] = {_FIELD_TYPES[key]: transform(val)}
        expr_parts.append(f"#f{i} = :v{i}")
    if not expr_parts:
        return
    try:
        _get_client().update_item(
            TableName=TABLE,
            Key={"session_id": {"S": session_id}},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ProvisionedThroughputExceededException":
            logger.warning("DynamoDB throttle en update_session %s, omitiendo update", session_id)
        else:
            logger.error("DynamoDB error [%s] en update_session %s: %s", code, session_id, e)
    except Exception as e:
        logger.error("DynamoDB error inesperado en update_session %s: %s", session_id, e)


def acquire_phase(session_id: str, phase_key: str, ttl_seconds: int = 1800, token: str = "") -> bool:
    """Toma el candado de fase: evita que dos workers corran la misma fase a la vez.

    Escritura condicional con vencimiento (`running_until`) para recuperarse de un worker caído.
    Devuelve True si lo obtuvo. Sin DynamoDB (modo local) siempre True.

    `token` identifica al dueño (el token de tarea de Step Functions). Un reintento de Step Functions trae un token NUEVO y
    puede tomar el candado de un worker caído sin esperar a que venza; un mensaje repetido (mismo token) no.
    """
    if not enabled():
        return True
    now = int(time.time())
    try:
        _get_client().update_item(
            TableName=TABLE,
            Key={"session_id": {"S": session_id}},
            UpdateExpression="SET running_phase = :p, running_until = :u, running_token = :tok",
            ConditionExpression="attribute_not_exists(running_until) OR running_until < :now OR running_token <> :tok",
            ExpressionAttributeValues={
                ":p": {"S": phase_key},
                ":u": {"N": str(now + ttl_seconds)},
                ":now": {"N": str(now)},
                ":tok": {"S": token},
            },
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def release_phase(session_id: str, token: str = "") -> None:
    """Libera el candado. Con `token`, solo si sigue siendo el dueño (un worker zombi no borra el de otro)."""
    if not enabled():
        return
    kwargs = {}
    if token:
        kwargs = {"ConditionExpression": "running_token = :tok", "ExpressionAttributeValues": {":tok": {"S": token}}}
    try:
        _get_client().update_item(
            TableName=TABLE,
            Key={"session_id": {"S": session_id}},
            UpdateExpression="REMOVE running_phase, running_until, running_token",
            **kwargs,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            logger.error("DynamoDB error en release_phase %s: %s", session_id, e)
    except Exception as e:
        logger.error("DynamoDB error inesperado en release_phase %s: %s", session_id, e)


def restore_token(session_id: str, token: str) -> None:
    """Devuelve el token consumido si no se pudo usarlo (SendTaskSuccess falló): el docente puede reintentar."""
    if not enabled():
        return
    try:
        _get_client().update_item(
            TableName=TABLE,
            Key={"session_id": {"S": session_id}},
            UpdateExpression="SET task_token = :t",
            ConditionExpression="attribute_not_exists(task_token) OR task_token = :vacio",
            ExpressionAttributeValues={":t": {"S": token}, ":vacio": {"S": ""}},
        )
    except Exception as e:
        logger.error("no se pudo restaurar el token de %s: %s", session_id, type(e).__name__)


def consume_token(session_id: str) -> Optional[str]:
    """Lee y BORRA el token de tarea en una sola operación atómica (un solo uso).

    Devuelve None si no había token (por ejemplo, un segundo clic del docente).
    """
    if not enabled():
        return None
    try:
        resp = _get_client().update_item(
            TableName=TABLE,
            Key={"session_id": {"S": session_id}},
            UpdateExpression="REMOVE task_token",
            ConditionExpression="attribute_exists(task_token) AND task_token <> :vacio",
            ExpressionAttributeValues={":vacio": {"S": ""}},
            ReturnValues="UPDATED_OLD",
        )
        return resp["Attributes"]["task_token"]["S"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise


# ── Un solo flujo activo por docente ────────────────────────────────────────────────────────────────────────
# El cupo es un registro `user#{owner_id}` en la misma tabla. Lo RESERVA ms-docs al subir (escritura condicional: dos subidas
# simultáneas no pasan las dos) y el workflow solo lo LIBERA cuando el flujo termina.
# Vence solo (active_until) para que un flujo atascado no bloquee al docente; el TTL de DynamoDB limpia el registro después.
USER_SLOT_SECONDS = 600


def _slot_key(owner_id: str) -> dict:
    return {"session_id": {"S": f"user#{owner_id}"}}


def release_user_slot(owner_id: str, session_id: str) -> None:
    """Libera el cupo, pero solo si sigue siendo de esa sesión (no borra el de un flujo posterior)."""
    if not enabled() or not owner_id:
        return
    try:
        _get_client().delete_item(
            TableName=TABLE,
            Key=_slot_key(owner_id),
            ConditionExpression="active_session = :sid",
            ExpressionAttributeValues={":sid": {"S": session_id}},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            logger.error("DynamoDB error en release_user_slot %s: %s", session_id, e)
    except Exception as e:
        logger.error("DynamoDB error inesperado en release_user_slot %s: %s", session_id, e)
