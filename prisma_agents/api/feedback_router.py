import logging
import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException

from api.schemas import ApprovalFeedbackRequest, FeedbackResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["Feedback"])

_LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com")


def register_teacher_approval(trace_id: str, approved: bool, comment: str | None) -> None:
    """Registra el feedback docente como score rubric_quality (BOOLEAN) en Langfuse.

    Usa el endpoint REST /api/public/ingestion directamente para garantizar
    una llamada síncrona sin depender del background queue del SDK.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        raise RuntimeError("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY no configuradas")

    body: dict = {
        "id": uuid.uuid4().hex,
        "traceId": trace_id,
        "name": "rubric_quality",
        "value": 1 if approved else 0,
        "dataType": "BOOLEAN",
    }
    if comment:
        body["comment"] = comment

    payload = {
        "batch": [{
            "id": uuid.uuid4().hex,
            "type": "score-create",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "body": body,
        }]
    }

    with httpx.Client(timeout=10) as client:
        response = client.post(
            f"{_LANGFUSE_HOST}/api/public/ingestion",
            json=payload,
            auth=(public_key, secret_key),
        )

    if response.status_code not in (200, 207):
        raise RuntimeError(f"Langfuse ingestion error {response.status_code}: {response.text}")

    data = response.json()
    errors = data.get("errors", [])
    if errors:
        raise RuntimeError(f"Langfuse score rejected: {errors}")


@router.post(
    "/approval",
    response_model=FeedbackResponse,
    summary="Registrar aprobación o rechazo docente de la rúbrica generada",
    description=(
        "Recibe el feedback del docente (👍/👎) y lo persiste como score `rubric_quality` "
        "en Langfuse Cloud sobre la traza indicada por `trace_id`. "
        "El `author_user_id` se registra como autor nativo del score en Langfuse."
    ),
)
async def post_teacher_approval(body: ApprovalFeedbackRequest) -> FeedbackResponse:
    try:
        register_teacher_approval(
            trace_id=body.trace_id,
            approved=body.approved,
            comment=body.comment,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("Error al registrar score en Langfuse: %s", exc)
        raise HTTPException(status_code=502, detail="No se pudo registrar el feedback en Langfuse")

    return FeedbackResponse(success=True)
