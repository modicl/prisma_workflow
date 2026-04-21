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
}
_TRANSFORMS = {
    "messages": json.dumps,
    "hitl_data": json.dumps,
    "error": lambda v: v or "",
    "docx_s3_key": lambda v: v or "",
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
                "paci_s3_key":     {"S": fields.get("paci_s3_key", "")},
                "material_s3_key": {"S": fields.get("material_s3_key", "")},
                "prompt":          {"S": fields.get("prompt", "")},
                "school_id":       {"S": fields.get("school_id", "")},
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
    if not item:
        return None
    return {
        "session_id":      item["session_id"]["S"],
        "phase":           item["phase"]["S"],
        "messages":        json.loads(item.get("messages", {}).get("S", "[]")),
        "hitl_data":       json.loads(item.get("hitl_data", {}).get("S", "null")),
        "error":           item.get("error", {}).get("S") or None,
        "docx_s3_key":     item.get("docx_s3_key", {}).get("S") or None,
        "paci_s3_key":     item.get("paci_s3_key", {}).get("S", ""),
        "material_s3_key": item.get("material_s3_key", {}).get("S", ""),
        "prompt":          item.get("prompt", {}).get("S", ""),
        "school_id":       item.get("school_id", {}).get("S", ""),
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
