import asyncio
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Awaitable, Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3

from api import dynamo_store, mock_phases, phase_store, sfn_client
from api.session_store import SESSIONS, HITL_CALLBACKS, SessionData, sync_to_dynamo
from run import run_phase, run_workflow
from utils.input_validator import validate_prompt_docente
from utils.document_exporter import export_results_to_docx
from utils.document_loader import load_document
from utils.hitl_feedback import feedback_a1, feedback_a2
from utils.usage_events import monitor_session_id

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get("S3_BUCKET", "")


def _emit(event_type: str, session_id: str, **fields) -> None:
    """Evento para el monitor en vivo (Kafka). El workflow no depende de Kafka: jamás lanza."""
    try:
        from api.event_publisher import publish
        publish(event_type, session_id, **fields)
    except Exception:
        pass


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
        _emit("hitl_required", session_id, attempt=attempt)
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
    flow_started = False       # solo se cierra con flow_finished un flujo que llegó a empezar
    session_token = None       # para dejar de atribuir consumo a esta sesión al terminar
    t0 = time.monotonic()

    try:
        _push_message(session_data, "Documentos recibidos. Iniciando análisis del PACI...")
        sync_to_dynamo(session_id, session_data)

        _emit("flow_started", session_id)
        flow_started = True
        # Las llamadas directas a Gemini (lectura de PDF, materiales) atribuyen su consumo a esta sesión.
        session_token = monitor_session_id.set(session_id)

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
        if flow_started:
            _emit(
                "flow_finished", session_id,
                status="cancelled" if session_data.cancelled else (session_data.workflow_status or "error"),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        if session_token is not None:
            monitor_session_id.reset(session_token)
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

# ══ Fases de Step Functions ══════════════════════════════════════════════════
# Cada fase corre en segundo plano (ver `run_phase_job`), carga su estado de S3, lo guarda al terminar y avisa a Step Functions
# con SendTaskSuccess. Ningún proceso queda esperando al docente: la espera la sostiene Step Functions (waitForTaskToken).

MAX_HITL_ATTEMPTS = 3
PHASE_HEARTBEAT_SECONDS = float(os.environ.get("PHASE_HEARTBEAT_SECONDS", "30"))
_MOCK_PREFIX = "__mock"

_FLOW_STATUS = {"success": "success", "degraded": "degraded", "compliance_blocked": "compliance_blocked"}
_MONITOR_STATUS = {
    "success": "success", "degraded": "degraded", "compliance_blocked": "compliance_blocked",
    "hitl_rejected": "hitl_rejected", "cancelled": "cancelled", "expired": "error", "timeout": "error", "error": "error",
}
_MSG_HITL_RECHAZADO = (
    "Proceso cancelado: el análisis inicial no obtuvo aprobación del docente en el número máximo de intentos."
)
_MSG_EXPIRADA = "El tiempo para revisar el plan expiró. Inicia un nuevo proceso."
_MSG_ERROR = "El proceso terminó con un error. Intenta nuevamente."
_MSG_CANCELADA = "Sesión cancelada por el docente."


class HitlDecisionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _rehydrate(item: dict) -> SessionData:
    """SessionData transitorio con lo que ya mostró el flujo (el estado real vive en DynamoDB)."""
    sd = SessionData(owner_id=item.get("owner_id"))
    sd.messages = list(item.get("messages") or [])
    sd.warnings = list(item.get("warnings") or [])
    return sd


def _results_from_state(state: dict) -> dict:
    """Mismo diccionario que arma run_workflow al terminar, para reutilizar _finalize_result."""
    return {
        "status": state.get("status") or "success",
        "perfil_paci": state.get("perfil_paci", ""),
        "planificacion_adaptada": state.get("planificacion_adaptada", ""),
        "rubrica_final": state.get("rubrica", ""),
        "docx_path": None,
        "validation_reason": state.get("validation_reason", ""),
        "validation_code": state.get("validation_code", ""),
        "warnings": state.get("warnings") or [],
    }


def _cargar_documentos_sync(item: dict) -> dict:
    """Descarga y lee el PACI y el material. Bloqueante (OCR con Gemini): se ejecuta en un hilo para no frenar el heartbeat."""
    paci_path = _download_from_s3(item["paci_s3_key"])
    material_path = _download_from_s3(item["material_s3_key"])
    try:
        return {
            "paci_document": load_document(paci_path, label="PACI del Estudiante"),
            "material_document": load_document(material_path, label="Material Base"),
            "prompt_docente": item.get("prompt", ""),
            "school_id": item.get("school_id", ""),
        }
    finally:
        for ruta in (paci_path, material_path):
            try:
                os.remove(ruta)
            except OSError:
                pass


async def run_phase_a(session_id: str, item: dict, attempt: int, feedback_agent: int) -> dict:
    """Fase A: Agente 1 + gate normativo + materiales + Agente 2 (intento inicial), o la repetición pedida por el docente."""
    from utils.usage_events import monitor_session_id

    sd = _rehydrate(item)
    school_id = item.get("school_id", "") or ""
    mock = school_id.startswith(_MOCK_PREFIX)
    contexto = monitor_session_id.set(session_id)
    try:
        if attempt <= 1:
            _emit("flow_started", session_id)
            dynamo_store.update_session(session_id, started_at=str(time.time()))
            _push_message(sd, "Documentos recibidos. Iniciando análisis del PACI...")
            sync_to_dynamo(session_id, sd)
            if mock:
                state = mock_phases.initial_state(school_id, item.get("prompt", ""))
            else:
                state = await asyncio.to_thread(_cargar_documentos_sync, item)
            state.update(retry_agent=0, hitl_feedback_a1="", hitl_feedback_a2="", critica_previa="", hitl_reason="")
        else:
            state = phase_store.load_state(session_id)
            razon = state.get("hitl_reason", "")
            state["retry_agent"] = feedback_agent
            state["hitl_feedback_a1"] = feedback_a1(razon) if feedback_agent == 1 else ""
            state["hitl_feedback_a2"] = feedback_a2(razon) if feedback_agent == 2 else ""
            _push_message(sd, "Revisando según los comentarios del docente...")
            sync_to_dynamo(session_id, sd)

        if mock:
            nuevo = await mock_phases.run("a", state, school_id)
        else:
            nuevo = await run_phase("a", state, user_id=item.get("owner_id") or session_id, api_session_id=session_id)
        phase_store.save_state(session_id, nuevo)

        estado = nuevo.get("status") or ""
        if estado in ("validation_failed", "timeout"):
            _finalize_result(session_id, sd, _results_from_state(nuevo), [False])
            return {"status": "compliance_blocked" if estado == "validation_failed" else "timeout"}
        return {"status": "ok"}
    finally:
        monitor_session_id.reset(contexto)


async def run_phase_b(session_id: str, item: dict) -> dict:
    """Fase B: Generador de Rúbrica + Crítico, exporta el DOCX y deja la sesión terminada."""
    from utils.usage_events import monitor_session_id

    sd = _rehydrate(item)
    school_id = item.get("school_id", "") or ""
    contexto = monitor_session_id.set(session_id)
    try:
        state = phase_store.load_state(session_id)           # PhaseStateNotFound: la fase falla de forma controlada
        _push_message(sd, "Plan aprobado. Generando la rúbrica...")
        sync_to_dynamo(session_id, sd)
        if school_id.startswith(_MOCK_PREFIX):
            nuevo = await mock_phases.run("b", state, school_id)
        else:
            nuevo = await run_phase("b", state, user_id=item.get("owner_id") or session_id, api_session_id=session_id)

        results = _results_from_state(nuevo)
        if results["rubrica_final"]:
            try:
                base = Path(item.get("material_s3_key") or "material").stem
                results["docx_path"] = str(export_results_to_docx(results, output_filename=f"rubrica_adaptada_{base}.docx"))
            except Exception as exc:                          # igual que run_workflow: sin DOCX, la sesión igual termina
                logger.error("error al exportar el DOCX [%s]: %s", session_id, type(exc).__name__)
        _finalize_result(session_id, sd, results, [False])
        return {"status": _FLOW_STATUS.get(sd.workflow_status or "", "error")}
    finally:
        monitor_session_id.reset(contexto)


async def run_phase_job(phase: str, session_id: str, item: dict, task_token: str, attempt: int, feedback_agent: int) -> None:
    """Ejecuta una fase en segundo plano y completa la tarea de Step Functions (éxito, fallo o abandono)."""
    try:
        trabajo = run_phase_a(session_id, item, attempt, feedback_agent) if phase == "a" else run_phase_b(session_id, item)
        resultado = await sfn_client.with_heartbeat(task_token, trabajo, interval=PHASE_HEARTBEAT_SECONDS)
        await asyncio.to_thread(sfn_client.send_success, task_token, resultado)
    except sfn_client.PhaseAbandoned:
        logger.warning("fase %s de %s abandonada: su token de tarea ya no es válido", phase, session_id)
    except sfn_client.TaskTokenGone:
        logger.warning("fase %s de %s terminó, pero su token de tarea ya no es válido", phase, session_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("fase %s de %s falló: %s", phase, session_id, type(exc).__name__, exc_info=True)
        try:
            # Solo el nombre de la clase: el texto de la excepción podría traer contenido de documentos y Step Functions
            # guarda sus entradas/salidas 90 días en claro.
            await asyncio.to_thread(sfn_client.send_failure, task_token, type(exc).__name__, "")
        except Exception:
            logger.warning("no se pudo informar el fallo de la fase %s de %s", phase, session_id)
    finally:
        dynamo_store.release_phase(session_id, task_token)


def register_wait(session_id: str, item: dict, task_token: str, attempt: int) -> None:
    """Estado `EsperarDocente`: guarda el token y pasa la sesión a awaiting_hitl en UNA sola escritura.

    El front solo puede responder cuando ve `awaiting_hitl`, y para entonces el token ya existe en DynamoDB.
    """
    state = phase_store.load_state(session_id)
    sd = _rehydrate(item)
    hitl_data = {
        "perfil_paci": state.get("perfil_paci", ""),
        "planificacion_adaptada": state.get("planificacion_adaptada", ""),
        "attempt": attempt,
        "max_attempts": MAX_HITL_ATTEMPTS,
    }
    _push_message(sd, f"Revisión requerida — intento {attempt} de {MAX_HITL_ATTEMPTS}. Por favor revise el análisis y la planificación.")
    dynamo_store.update_session(session_id, phase="awaiting_hitl", hitl_data=hitl_data, messages=sd.messages, task_token=task_token)
    _emit("hitl_required", session_id, attempt=attempt)


def emit_flow_finished(session_id: str, item: dict, status: str) -> None:
    inicio = item.get("started_at")
    _emit(
        "flow_finished", session_id,
        status=_MONITOR_STATUS.get(status, "error"),
        duration_ms=int((time.time() - inicio) * 1000) if inicio else 0,
    )


def finalize_session(session_id: str, item: dict, status: str) -> None:
    """Estado `Finalizar`: deja la sesión terminada (si la fase no lo hizo), borra el estado de S3 y cierra el flujo en el monitor."""
    if status == "hitl_rejected":
        dynamo_store.update_session(session_id, phase="error", workflow_status="hitl_rejected", error=_MSG_HITL_RECHAZADO, hitl_data=None, task_token="")
    elif status == "expired":
        dynamo_store.update_session(session_id, phase="error", workflow_status="error", error=_MSG_EXPIRADA, hitl_data=None, task_token="")
    elif status in ("error", "timeout") and item.get("phase") not in ("completed", "error"):
        dynamo_store.update_session(session_id, phase="error", workflow_status="error", error=_MSG_ERROR, hitl_data=None, task_token="")
    phase_store.delete_state(session_id)
    dynamo_store.release_user_slot(item.get("owner_id") or "", session_id)
    emit_flow_finished(session_id, item, status)


def submit_hitl_decision(session_id: str, item: dict, approved: bool, reason: str | None, agent_to_retry: int | None) -> None:
    """Decisión del docente: completa la espera de Step Functions. Lo usa POST /hitl (ya autenticado) y las pruebas E2E."""
    if item.get("phase") != "awaiting_hitl":
        raise HitlDecisionError(409, "La sesión no está esperando revisión HITL")
    token = dynamo_store.consume_token(session_id)          # primero el token: el segundo clic pierde aquí y no pisa nada
    if not token:
        raise HitlDecisionError(409, "La decisión ya fue enviada")
    try:
        if not approved:
            # La razón puede contener datos de un menor: va a S3 (cifrado), NUNCA por Step Functions.
            phase_store.update_state(session_id, hitl_reason=reason or "")
        # `running` ANTES de enviar: si fuera después podría pisar el estado terminal que escribe `Finalizar`.
        dynamo_store.update_session(session_id, phase="running", hitl_data=None)
        sfn_client.send_success(token, {"approved": bool(approved), "agent_to_retry": int(agent_to_retry or 2)})
    except sfn_client.TaskTokenGone:
        dynamo_store.update_session(session_id, phase="error", workflow_status="error", error=_MSG_EXPIRADA, hitl_data=None)
        dynamo_store.release_user_slot(item.get("owner_id") or "", session_id)
        raise HitlDecisionError(409, "La revisión expiró") from None
    except Exception as exc:
        # Nada llegó a Step Functions: se devuelve el token y la sesión vuelve a esperar para que el docente reintente.
        logger.error("no se pudo enviar la decisión de %s: %s", session_id, type(exc).__name__)
        dynamo_store.restore_token(session_id, token)
        dynamo_store.update_session(session_id, phase="awaiting_hitl", hitl_data=item.get("hitl_data"))
        raise HitlDecisionError(503, "No se pudo registrar la decisión. Intenta nuevamente.") from None


def cancel_session_sfn(session_id: str, item: dict) -> None:
    """Cancelación del docente en una sesión orquestada por Step Functions (la ejecución queda ABORTED)."""
    dynamo_store.update_session(session_id, phase="error", workflow_status="cancelled", error=_MSG_CANCELADA, hitl_data=None, task_token="")
    try:
        sfn_client.stop_execution(session_id)
    except Exception as exc:
        logger.warning("no se pudo detener la ejecución de %s: %s", session_id, type(exc).__name__)
    phase_store.delete_state(session_id)
    dynamo_store.release_user_slot(item.get("owner_id") or "", session_id)
    emit_flow_finished(session_id, item, "cancelled")
