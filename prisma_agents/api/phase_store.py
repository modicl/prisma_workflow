"""
Estado del flujo ENTRE FASES, en S3 (`state/{session_id}.json`).

Por qué S3 y no DynamoDB: el ítem de DynamoDB tiene tope de 400 KB y aquí viajan textos completos de documentos
y de materiales de referencia.

Reglas (ver la especificación):
  * Va bajo `state/`, NUNCA bajo `jobs/`: ese prefijo dispara la notificación de S3 y se armaría un bucle.
  * Solo se persiste la lista explícita STATE_KEYS (nada de `api_session_id` ni claves internas).
  * Contiene datos de menores (diagnóstico): cifrado en reposo y expiración de 7 días (regla de ciclo de vida del bucket).
    Además se borra al finalizar el flujo.
"""
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

STATE_PREFIX = "state/"

STATE_KEYS = (
    # entradas
    "paci_document", "material_document", "prompt_docente", "school_id",
    # salidas de los agentes
    "perfil_paci", "planificacion_adaptada", "rubrica", "evaluacion_critica",
    # control entre fases
    "materiales_referencia", "hitl_feedback_a1", "hitl_feedback_a2", "hitl_reason", "critica_previa",
    "status", "warnings", "validation_code", "validation_reason",
)


class PhaseStateNotFound(Exception):
    """El estado de la sesión no existe en S3 (expiró o se borró)."""


def state_key(session_id: str) -> str:
    return f"{STATE_PREFIX}{session_id}.json"


def _bucket() -> str:
    bucket = os.environ.get("S3_BUCKET", "")
    if not bucket:
        raise RuntimeError("S3_BUCKET no está configurado: no se puede guardar el estado de las fases")
    return bucket


def _client():
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def save_state(session_id: str, state: dict) -> None:
    permitido = {k: v for k, v in state.items() if k in STATE_KEYS}
    _client().put_object(
        Bucket=_bucket(),
        Key=state_key(session_id),
        Body=json.dumps(permitido, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )


def load_state(session_id: str) -> dict:
    try:
        resp = _client().get_object(Bucket=_bucket(), Key=state_key(session_id))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            raise PhaseStateNotFound(session_id) from e
        raise
    return json.loads(resp["Body"].read().decode("utf-8"))


def update_state(session_id: str, **delta) -> dict:
    try:
        actual = load_state(session_id)
    except PhaseStateNotFound:
        actual = {}
    actual.update(delta)
    save_state(session_id, actual)
    return {k: v for k, v in actual.items() if k in STATE_KEYS}


def delete_state(session_id: str) -> None:
    try:
        _client().delete_object(Bucket=_bucket(), Key=state_key(session_id))
    except Exception as e:                       # limpieza best-effort: la regla de ciclo de vida lo borra igual
        logger.warning("no se pudo borrar el estado de %s: %s", session_id, type(e).__name__)
