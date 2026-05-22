import logging

from fastapi import APIRouter, HTTPException

from api.schemas import ApprovalFeedbackRequest, FeedbackResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["Feedback"])


def register_teacher_approval(trace_id: str, approved: bool, comment: str | None) -> None:
    """Registra el feedback docente como score BOOLEAN en Langfuse para la traza indicada.

    Llama create_score() con data_type BOOLEAN (value 1=aprobado, 0=rechazado) y hace
    flush() para garantizar que el score llegue antes de que retorne el endpoint.
    """
    try:
        from langfuse import get_client
    except ImportError as exc:
        raise RuntimeError("Langfuse no está instalado") from exc

    client = get_client()
    score_kwargs: dict = {
        "trace_id": trace_id,
        "name": "rubric_quality",
        "data_type": "BOOLEAN",
        "value": 1 if approved else 0,
    }
    if comment:
        score_kwargs["comment"] = comment

    client.create_score(**score_kwargs)
    client.flush()


@router.post(
    "/approval",
    response_model=FeedbackResponse,
    summary="Registrar aprobación o rechazo docente de la rúbrica generada",
    description=(
        "Recibe el feedback del docente (👍/👎) y lo persiste como score `teacher_approval` "
        "en Langfuse Cloud sobre la traza indicada por `trace_id`."
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
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("Error al registrar score en Langfuse: %s", exc)
        raise HTTPException(status_code=502, detail="No se pudo registrar el feedback en Langfuse")

    return FeedbackResponse(success=True)
