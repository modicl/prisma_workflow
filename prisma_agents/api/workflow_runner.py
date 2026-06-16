import os
import sys
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3

from api import dynamo_store
from api.session_store import SESSIONS, HITL_CALLBACKS, sync_to_dynamo
from run import run_workflow
from utils.audit_log import record_hitl_decision
from utils.input_validator import validate_prompt_docente

S3_BUCKET = os.environ.get("S3_BUCKET", "")


def _push_message(session_data: "SessionData", content: str, role: str = "system") -> None:
    """Agrega un mensaje al historial Y lo pushea al stream SSE."""
    msg = {"role": role, "content": content}
    session_data.messages.append(msg)
    session_data.event_queue.put_nowait({"type": "message", **msg})


def _friendly_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "api key" in msg or "api_key" in msg or "invalid_argument" in msg:
        return "Error de configuración del servicio IA. Contacte al administrador."
    if "timeout" in msg or "timed out" in msg or "deadline" in msg:
        return "El servicio de IA no respondió a tiempo. Intente nuevamente."
    if "quota" in msg or "resource_exhausted" in msg:
        return "Se alcanzó el límite de uso del servicio IA. Intente más tarde."
    if "unavailable" in msg or "connection" in msg or "network" in msg:
        return "No se pudo conectar con el servicio IA. Verifique la conexión."
    return "Ocurrió un error inesperado en el servidor. Intente nuevamente."


def _download_from_s3(s3_key: str) -> str:
    """Download an S3 object to a local temp file and return the local path."""
    suffix = Path(s3_key).suffix or ".tmp"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    boto3.client("s3").download_file(S3_BUCKET, s3_key, tmp_path)
    return tmp_path


def _make_hitl_callback(
    session_id: str,
    session_data: "SessionData",
    hitl_was_rejected: list,
) -> Callable[[dict, int, int], Awaitable[tuple]]:
    """Crea la callback HITL que pausa el flujo y espera la decisión del docente."""

    async def hitl_callback(state: dict, attempt: int, max_attempts: int) -> tuple[bool, str, int]:
        hitl_data = {
            "perfil_paci": state.get("perfil_paci", ""),
            "planificacion_adaptada": state.get("planificacion_adaptada", ""),
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
        session_data.hitl_data = hitl_data
        session_data.phase = "awaiting_hitl"
        _push_message(
            session_data,
            f"Revisión requerida — intento {attempt} de {max_attempts}. Por favor revise el análisis y la planificación.",
        )
        session_data.event_queue.put_nowait({
            "type": "hitl_required",
            "attempt": attempt,
            "max_attempts": max_attempts,
            "hitl_data": hitl_data,
        })
        sync_to_dynamo(session_id, session_data)

        response = await session_data.hitl_response_queue.get()

        # Si la sesión fue cancelada mientras esperábamos, salir sin sobreescribir el estado
        if session_data.cancelled:
            hitl_was_rejected[0] = True
            return False, "Sesión cancelada por el docente.", 0

        session_data.phase = "running"
        session_data.hitl_data = None
        sync_to_dynamo(session_id, session_data)

        approved = response.get("approved", False)
        reason = response.get("reason") or ""
        # El front ya no envía agent_to_retry. Un rechazo no-final siempre reintenta el
        # Agente 2 (Adaptador), que es lo que revisa el checkpoint. El caso de intentos
        # agotados se maneja aparte retornando el sentinel 0.
        agent_to_retry = int(response.get("agent_to_retry") or 2)

        # Auditoría legal: registrar la decisión del docente (append-only, no bloqueante).
        await record_hitl_decision(
            session_id=session_id,
            teacher_id=session_data.owner_id,
            approved=approved,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
            agent_to_retry=agent_to_retry if not approved else None,
            plan_reviewed=state.get("planificacion_adaptada", ""),
        )

        # Si es el último intento y el docente rechaza, señalizamos directamente.
        if not approved and attempt >= max_attempts:
            hitl_was_rejected[0] = True
            return False, reason, 0  # agente=0 → agent.py cancela el flujo

        return approved, reason, agent_to_retry

    return hitl_callback


def _finalize_result(
    session_id: str,
    session_data: "SessionData",
    results: dict,
    hitl_was_rejected: list,
) -> None:
    """Aplica el estado terminal de la sesión según el resultado del workflow."""
    # hitl_was_rejected es la fuente de verdad: no depende de que ADK persista el state.
    raw_status = results.get("status", "success")
    agent_status = "hitl_rejected" if hitl_was_rejected[0] else raw_status
    session_data.result = results

    if agent_status in ("validation_failed", "compliance_blocked"):
        session_data.phase = "error"
        session_data.workflow_status = "compliance_blocked"
        session_data.error = results.get("validation_reason") or (
            "El documento no cumple la normativa requerida y el proceso fue detenido."
        )
        session_data.event_queue.put_nowait({
            "type": "error",
            "message": session_data.error,
            "workflow_status": "compliance_blocked",
            "code": results.get("validation_code", ""),
        })
        _push_message(session_data, f"❌ {session_data.error}", role="error")
        sync_to_dynamo(session_id, session_data)
        return

    if agent_status in ("success", "fail"):
        session_data.docx_path = results.get("docx_path")
        session_data.warnings = results.get("warnings", []) or []
        session_data.phase = "completed"
        wf_status = "success" if agent_status == "success" else "degraded"
        session_data.workflow_status = wf_status
        completion_msg = (
            "✅ Proceso completado. La rúbrica adaptada está lista para descargar."
            if agent_status == "success"
            else "⚠️ Proceso completado. La rúbrica fue generada como mejor esfuerzo y no superó todos los criterios de calidad. Revise el documento antes de usarlo."
        )
        _push_message(session_data, completion_msg, role="agent")
        session_data.event_queue.put_nowait({
            "type": "completed",
            "workflow_status": wf_status,
            "warnings": session_data.warnings,
        })

        # Upload DOCX to S3 and record the key in DynamoDB
        docx_s3_key = ""
        if S3_BUCKET and session_data.docx_path and Path(session_data.docx_path).exists():
            docx_s3_key = f"results/{session_id}/rubrica.docx"
            boto3.client("s3").upload_file(session_data.docx_path, S3_BUCKET, docx_s3_key)

        sync_to_dynamo(session_id, session_data, docx_s3_key=docx_s3_key)

    elif agent_status == "hitl_rejected":
        session_data.phase = "error"
        session_data.workflow_status = "hitl_rejected"
        session_data.error = (
            "Proceso cancelado: se agotaron los intentos de revisión "
            "sin obtener aprobación del docente."
        )
        _push_message(
            session_data,
            "❌ Proceso cancelado: el análisis inicial no obtuvo aprobación del docente en el número máximo de intentos.",
        )
        session_data.event_queue.put_nowait({"type": "error", "message": session_data.error})
        sync_to_dynamo(session_id, session_data)

    else:
        # timeout u otro estado no reconocido
        session_data.phase = "error"
        session_data.workflow_status = "error"
        session_data.error = f"El proceso terminó con estado inesperado: {agent_status}."
        _push_message(session_data, "❌ El proceso agotó el tiempo de espera en un agente. Intente nuevamente.")
        session_data.event_queue.put_nowait({"type": "error", "message": session_data.error})
        sync_to_dynamo(session_id, session_data)


async def run_workflow_for_api(
    session_id: str,
    paci_path: str = "",
    material_path: str = "",
    paci_s3_key: str = "",
    material_s3_key: str = "",
    prompt: str = "",
    school_id: str = "",
) -> None:
    session_data = SESSIONS.get(session_id)
    if session_data is None:
        return

    # Fail-fast: rechazar prompts demasiado cortos antes de descargar documentos de S3
    try:
        validate_prompt_docente(prompt)
    except ValueError as exc:
        session_data.error = str(exc)
        session_data.phase = "error"
        session_data.workflow_status = "error"
        session_data.event_queue.put_nowait({"type": "error", "message": str(exc)})
        _push_message(session_data, str(exc), role="error")
        sync_to_dynamo(session_id, session_data)
        return

    # Resolve local paths — download from S3 if keys provided
    s3_downloaded: list[str] = []
    if paci_s3_key and material_s3_key:
        paci_path = _download_from_s3(paci_s3_key)
        material_path = _download_from_s3(material_s3_key)
        s3_downloaded = [paci_path, material_path]

    hitl_was_rejected = [False]
    HITL_CALLBACKS[session_id] = _make_hitl_callback(session_id, session_data, hitl_was_rejected)

    try:
        # Simulación de flujo para pruebas de UX/UI sin consumir tokens LLM
        if school_id and school_id.startswith("__mock"):
            from api.mock_runner import run_mock_workflow
            await run_mock_workflow(session_id, session_data, school_id)
            return

        _push_message(session_data, "Documentos recibidos. Iniciando análisis del PACI...")
        sync_to_dynamo(session_id, session_data)

        results = await run_workflow(
            paci_path=paci_path,
            material_path=material_path,
            prompt=prompt,
            user_id=session_id,
            school_id=school_id,
            api_session_id=session_id,
        )

        # Si fue cancelada mientras corría un agente, no sobreescribir el estado
        if session_data.cancelled:
            return

        _finalize_result(session_id, session_data, results, hitl_was_rejected)

    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("workflow error [%s]: %s", session_id, exc, exc_info=True)
        session_data.phase = "error"
        session_data.workflow_status = "error"
        session_data.error = _friendly_error(exc)
        _push_message(session_data, "❌ El procesamiento fue interrumpido por un error del servidor.")
        session_data.event_queue.put_nowait({"type": "error", "message": session_data.error})
        sync_to_dynamo(session_id, session_data)

    finally:
        HITL_CALLBACKS.pop(session_id, None)
        while not session_data.hitl_response_queue.empty():
            session_data.hitl_response_queue.get_nowait()

        # Safety: si ningún camino pushó un evento terminal, cerrar el SSE
        if session_data.phase in ("completed", "error") and session_data.event_queue.empty():
            terminal_type = "completed" if session_data.phase == "completed" else "error"
            session_data.event_queue.put_nowait({
                "type": terminal_type,
                "workflow_status": session_data.workflow_status,
                "message": session_data.error or "",
            })

        # Delete S3-downloaded temp files; for local dev path, delete the original uploads
        paths_to_delete = s3_downloaded if s3_downloaded else [paci_path, material_path]
        for path in paths_to_delete:
            try:
                os.remove(path)
            except OSError:
                pass
