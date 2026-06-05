import logging
import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from api.schemas import ApprovalFeedbackRequest, FeedbackResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["Feedback"])

_LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com")


def _get_langfuse_credentials() -> tuple[str, str]:
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not pk or not sk:
        raise RuntimeError("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY no configuradas")
    return pk, sk


def _resolve_trace_id(session_id: str, pk: str, sk: str) -> str:
    """Obtiene el trace_id de Langfuse a partir del session_id de PRISMA."""
    resp = httpx.get(
        f"{_LANGFUSE_HOST}/api/public/traces",
        params={"sessionId": session_id, "limit": 1},
        auth=(pk, sk),
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Error consultando trazas Langfuse: {resp.status_code}")

    traces = resp.json().get("data", [])
    if not traces:
        raise RuntimeError(f"No se encontró ninguna traza para session_id={session_id!r}")

    return traces[0]["id"]


def register_teacher_approval(session_id: str, approved: bool, comment: str | None) -> None:
    """Registra el feedback docente como score rubric_quality (BOOLEAN) en Langfuse.

    Resuelve el trace_id a partir del session_id de PRISMA (que coincide con el
    session_id registrado en Langfuse por propagate_attributes en run.py).
    """
    pk, sk = _get_langfuse_credentials()
    trace_id = _resolve_trace_id(session_id, pk, sk)

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

    resp = httpx.post(
        f"{_LANGFUSE_HOST}/api/public/ingestion",
        json=payload,
        auth=(pk, sk),
        timeout=10,
    )

    if resp.status_code not in (200, 207):
        raise RuntimeError(f"Langfuse ingestion error {resp.status_code}: {resp.text}")

    errors = resp.json().get("errors", [])
    if errors:
        raise RuntimeError(f"Langfuse score rejected: {errors}")


@router.post(
    "/approval",
    response_model=FeedbackResponse,
    summary="Registrar aprobación o rechazo docente de la rúbrica generada",
    description=(
        "Recibe el feedback del docente (👍/👎) y lo persiste como score `rubric_quality` "
        "en Langfuse Cloud. Resuelve el `trace_id` internamente a partir del `session_id` "
        "de PRISMA, que ya está registrado como `session_id` en la traza de Langfuse."
    ),
)
async def post_teacher_approval(
    body: ApprovalFeedbackRequest,
    _user: dict = Depends(get_current_user),
) -> FeedbackResponse:
    try:
        register_teacher_approval(
            session_id=body.session_id,
            approved=body.approved,
            comment=body.comment,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("Error al registrar score en Langfuse: %s", exc)
        raise HTTPException(status_code=502, detail="No se pudo registrar el feedback en Langfuse")

    return FeedbackResponse(success=True)
