import asyncio
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

import boto3
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Header, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from api import dynamo_store
from api.auth import get_current_user
from api.schemas import (
    DownloadResponse,
    HitlResponseBody,
    InternalRunResponse,
    OkResponse,
    SessionStateResponse,
    StartChatResponse,
)
from api.session_store import SESSIONS, SessionData, sync_to_dynamo
from api.workflow_runner import run_workflow_for_api

router = APIRouter(prefix="/chat")


def _assert_owner(owner_id: Optional[str], user_sub: str) -> None:
    """Lanza 403 si el usuario no es el propietario de la sesión.

    Si owner_id es None (sesiones pre-fix sin campo), se permite el acceso
    para no romper sesiones existentes.
    """
    if owner_id is not None and owner_id != user_sub:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sesión")


# Local dev upload dir (used only when S3_BUCKET is not configured)
UPLOAD_DIR = Path(tempfile.gettempdir()) / "prisma_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

S3_BUCKET = os.environ.get("S3_BUCKET", "")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc"}


def _safe_ext(filename: str | None, default: str) -> str:
    if not filename:
        return default
    suffix = Path(filename).suffix.lower()
    return suffix if suffix in _ALLOWED_EXTENSIONS else default


# ── Public endpoints ──────────────────────────────────────────────────────────

@router.post(
    "/start",
    tags=["Chat"],
    summary="Registrar sesión y subir documentos",
    description=(
        "Sube el PACI y el material base e inicia el workflow de generación de rúbrica.\n\n"
        "**Modo producción (S3_BUCKET configurado):** los archivos se suben a S3, se crea "
        "la sesión en DynamoDB y la Lambda `prisma-trigger` dispara `/internal/run`.\n\n"
        "**Modo dev local (S3_BUCKET vacío):** los archivos se guardan en disco y el "
        "workflow se lanza como BackgroundTask directamente."
    ),
    response_model=StartChatResponse,
    status_code=201,
    responses={
        400: {"description": "Extensión de archivo no soportada (.doc no está permitido directamente)"},
        500: {"description": "Error al guardar archivos en disco o subir a S3"},
    },
)
async def start_chat(
    paci_file: UploadFile = File(..., description="Documento PACI del estudiante (.pdf o .docx)"),
    material_file: UploadFile = File(..., description="Material educativo base (.pdf o .docx)"),
    prompt: str = Form("", description="Instrucciones adicionales del docente para el flujo"),
    school_id: str = Form("colegio_demo", description="ID del colegio para acceder al repositorio S3 de materiales"),
    _user: dict = Depends(get_current_user),
):
    session_id = str(uuid.uuid4())
    paci_ext = _safe_ext(paci_file.filename, ".pdf")
    material_ext = _safe_ext(material_file.filename, ".docx")

    paci_bytes = await paci_file.read()
    material_bytes = await material_file.read()

    owner_sub = _user["sub"]
    SESSIONS[session_id] = SessionData(owner_id=owner_sub)

    if S3_BUCKET:
        # Event-driven path: upload to S3, Lambda will call /internal/run
        paci_s3_key = f"jobs/{session_id}/paci{paci_ext}"
        material_s3_key = f"jobs/{session_id}/material{material_ext}"
        # Write to DynamoDB BEFORE uploading to S3 — Lambda fires on the first PUT
        # and must find the session record already in DynamoDB.
        dynamo_store.create_session(
            session_id,
            phase="running",
            paci_s3_key=paci_s3_key,
            material_s3_key=material_s3_key,
            prompt=prompt,
            school_id=school_id,
            owner_id=owner_sub,
        )
        try:
            s3 = boto3.client("s3")
            s3.put_object(Bucket=S3_BUCKET, Key=paci_s3_key, Body=paci_bytes)
            s3.put_object(Bucket=S3_BUCKET, Key=material_s3_key, Body=material_bytes)
        except Exception as exc:
            SESSIONS.pop(session_id, None)
            logger.error("S3 upload failed for session %s: %s", session_id, exc)
            raise HTTPException(status_code=500, detail="Error al subir archivos. Intente nuevamente.")
    else:
        # Local dev path: save to disk, launch background task directly
        paci_path = UPLOAD_DIR / f"{session_id}_paci{paci_ext}"
        material_path = UPLOAD_DIR / f"{session_id}_material{material_ext}"
        try:
            paci_path.write_bytes(paci_bytes)
            material_path.write_bytes(material_bytes)
        except Exception:
            paci_path.unlink(missing_ok=True)
            material_path.unlink(missing_ok=True)
            SESSIONS.pop(session_id, None)
            raise HTTPException(status_code=500, detail="Error al guardar los archivos subidos")
        task = asyncio.create_task(run_workflow_for_api(
            session_id=session_id,
            paci_path=str(paci_path),
            material_path=str(material_path),
            prompt=prompt,
            school_id=school_id,
        ))
        SESSIONS[session_id].task = task

    return {"session_id": session_id}


@router.get(
    "/{session_id}/stream",
    tags=["Chat"],
    summary="Stream de eventos SSE de la sesión",
    description=(
        "Abre un stream Server-Sent Events (SSE) para seguir el progreso del workflow en tiempo real.\n\n"
        "**Eventos posibles:**\n"
        "- `message` — mensaje de progreso del agente\n"
        "- `hitl_required` — el workflow pausó y requiere revisión docente; responder via `/hitl`\n"
        "- `completed` — workflow finalizado; el DOCX está disponible en `/download`\n"
        "- `error` — el workflow terminó con error\n"
        "- `ping` — keepalive cada ~25s\n\n"
        "Si el backend fue reiniciado y la sesión existe en DynamoDB, el endpoint emite el evento "
        "terminal correspondiente sin necesidad de reconectar el workflow."
    ),
    responses={
        200: {"description": "Stream SSE activo (Content-Type: text/event-stream)"},
        404: {"description": "Sesión no encontrada"},
        408: {"description": "Timeout esperando a que la sesión sea inicializada en memoria (modo AWS)"},
    },
)
async def stream_session(session_id: str, _user: dict = Depends(get_current_user)):
    sd = SESSIONS.get(session_id)

    # La sesión puede existir en DynamoDB (creada por ms-docs) pero aún no en memoria
    # porque el Lambda todavía no llamó a /internal/run. Esperar hasta 15s.
    if sd is None and dynamo_store.enabled():
        for _ in range(30):
            await asyncio.sleep(0.5)
            sd = SESSIONS.get(session_id)
            if sd is not None:
                break

    # Sesión no está en memoria — puede ser una sesión huérfana (backend reiniciado)
    if sd is None:
        if dynamo_store.enabled():
            item = dynamo_store.get_session(session_id)
            if item:
                _assert_owner(item.get("owner_id"), _user["sub"])
                phase = item.get("phase", "error")
                _sse_headers = {
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "Content-Encoding": "identity",
                }
                if phase in ("completed", "error"):
                    # Sesión ya terminada pero no en memoria: emitir evento terminal inmediato
                    event = {
                        "type": "completed" if phase == "completed" else "error",
                        "workflow_status": item.get("workflow_status") or None,
                        "message": item.get("error") or "",
                    }
                    async def _done_gen(ev=event):
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    return StreamingResponse(_done_gen(), media_type="text/event-stream", headers=_sse_headers)
                else:
                    # Sesión huérfana (running/awaiting_hitl sin workflow vivo)
                    # Marcar como error en DynamoDB para que el frontend no vuelva a intentar
                    _msg = "La sesión fue interrumpida (el servidor fue reiniciado) y no puede retomarse. Por favor inicia una nueva sesión."
                    dynamo_store.update_session(
                        session_id,
                        phase="error",
                        workflow_status="error",
                        error=_msg,
                    )
                    async def _orphan_gen(msg=_msg):
                        yield f"data: {json.dumps({'type': 'error', 'message': msg}, ensure_ascii=False)}\n\n"
                    return StreamingResponse(_orphan_gen(), media_type="text/event-stream", headers=_sse_headers)
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    _assert_owner(sd.owner_id, _user["sub"])

    def _terminal_event(session_data) -> dict:
        if session_data.phase == "completed":
            return {"type": "completed", "workflow_status": session_data.workflow_status}
        return {"type": "error", "message": session_data.error or "El proceso fue interrumpido."}

    async def generator():
        # Si la sesión ya terminó cuando el cliente se conecta, responder de inmediato
        if sd.phase in ("completed", "error"):
            yield f"data: {json.dumps(_terminal_event(sd), ensure_ascii=False)}\n\n"
            return

        while True:
            try:
                event = await asyncio.wait_for(sd.event_queue.get(), timeout=25.0)
            except asyncio.TimeoutError:
                # Keepalive ping + verificación por si el workflow terminó sin pushear
                if sd.phase in ("completed", "error"):
                    yield f"data: {json.dumps(_terminal_event(sd), ensure_ascii=False)}\n\n"
                    return
                yield 'data: {"type": "ping"}\n\n'
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") in ("completed", "error"):
                return

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Encoding": "identity",
        },
    )


@router.get(
    "/{session_id}/state",
    tags=["Chat"],
    summary="Estado actual de la sesión",
    description=(
        "Retorna el estado actual de la sesión. Útil para polling cuando SSE no está disponible.\n\n"
        "En modo AWS, consulta DynamoDB directamente (resistente a reinicios del backend)."
    ),
    response_model=SessionStateResponse,
    responses={404: {"description": "Sesión no encontrada"}},
)
async def get_state(session_id: str, _user: dict = Depends(get_current_user)):
    if dynamo_store.enabled():
        item = dynamo_store.get_session(session_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")
        _assert_owner(item.get("owner_id"), _user["sub"])
        return {
            "phase":           item["phase"],
            "messages":        item["messages"],
            "hitl_data":       item["hitl_data"],
            "error":           item["error"],
            "workflow_status": item.get("workflow_status"),
            "warnings":        item.get("warnings", []),
        }
    # Local dev fallback
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
    _assert_owner(sd.owner_id, _user["sub"])
    return {
        "phase":           sd.phase,
        "messages":        sd.messages,
        "hitl_data":       sd.hitl_data,
        "error":           sd.error,
        "workflow_status": sd.workflow_status,
        "warnings":        sd.warnings,
    }


@router.post(
    "/{session_id}/hitl",
    tags=["HITL"],
    summary="Responder al checkpoint de revisión docente",
    description=(
        "Envía la decisión del docente sobre el plan de adaptación generado por el Agente Adaptador.\n\n"
        "Solo válido cuando `phase == 'awaiting_hitl'`. La respuesta desbloquea el workflow, que "
        "continuará con la generación de la rúbrica si fue aprobado, o reintentará el agente indicado "
        "si fue rechazado. El flujo permite un máximo de 3 iteraciones HITL antes de continuar igual."
    ),
    response_model=OkResponse,
    responses={
        404: {"description": "Sesión no encontrada"},
        409: {"description": "La sesión no está en estado awaiting_hitl"},
    },
)
async def respond_hitl(session_id: str, body: HitlResponseBody, _user: dict = Depends(get_current_user)):
    sd = SESSIONS.get(session_id)
    if sd is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    _assert_owner(sd.owner_id, _user["sub"])
    if sd.phase != "awaiting_hitl":
        raise HTTPException(status_code=409, detail="La sesión no está esperando revisión HITL")
    await sd.hitl_response_queue.put({
        "approved": body.approved,
        "reason": body.reason,
        "agent_to_retry": body.agent_to_retry,
    })
    return {"ok": True}


@router.post(
    "/{session_id}/cancel",
    tags=["Chat"],
    summary="Cancelar la sesión en curso",
    description=(
        "Cancela el workflow activo, marca la sesión como error con `workflow_status: cancelled` "
        "y emite un evento SSE de error. También cancela la tarea asyncio subyacente."
    ),
    response_model=OkResponse,
    responses={
        404: {"description": "Sesión no encontrada"},
        409: {"description": "La sesión ya ha terminado (completed o error)"},
    },
)
async def cancel_session(session_id: str, _user: dict = Depends(get_current_user)):
    sd = SESSIONS.get(session_id)
    if sd is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    _assert_owner(sd.owner_id, _user["sub"])
    if sd.phase in ("completed", "error"):
        raise HTTPException(status_code=409, detail="La sesión ya ha terminado")

    error_msg = "Sesión cancelada por el docente."
    sd.cancelled = True
    sd.phase = "error"
    sd.workflow_status = "cancelled"
    sd.error = error_msg

    sd.event_queue.put_nowait({"type": "error", "message": error_msg, "workflow_status": "cancelled"})
    sync_to_dynamo(session_id, sd)

    # Cancelar la tarea asyncio — interrumpe el agente en el próximo await
    if sd.task and not sd.task.done():
        sd.task.cancel()

    return {"ok": True}


DOWNLOAD_URL_EXPIRES = int(os.environ.get("DOWNLOAD_URL_EXPIRES", "300"))


@router.get(
    "/{session_id}/download",
    tags=["Chat"],
    summary="Descargar el DOCX generado",
    description=(
        "Retorna la URL de descarga del documento DOCX con la rúbrica adaptada.\n\n"
        "**Modo AWS:** retorna un presigned URL de S3 con expiración configurable "
        "(default 300s via `DOWNLOAD_URL_EXPIRES`).\n\n"
        "**Modo local:** retorna el archivo directamente como `FileResponse`.\n\n"
        "Solo disponible cuando `phase == 'completed'`."
    ),
    response_model=DownloadResponse,
    responses={
        404: {"description": "Sesión no encontrada o resultado aún no disponible"},
    },
)
async def download_result(session_id: str, _user: dict = Depends(get_current_user)):
    if dynamo_store.enabled():
        item = dynamo_store.get_session(session_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")
        _assert_owner(item.get("owner_id"), _user["sub"])
        if item.get("phase") != "completed" or not item.get("docx_s3_key"):
            raise HTTPException(status_code=404, detail="Resultado no disponible aún")
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        filename = Path(item["docx_s3_key"]).name
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": item["docx_s3_key"],
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=DOWNLOAD_URL_EXPIRES,
        )
        return {"url": presigned_url, "filename": filename, "expires_in": DOWNLOAD_URL_EXPIRES}

    # Local dev fallback
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
    _assert_owner(sd.owner_id, _user["sub"])
    if sd.phase != "completed" or not sd.docx_path:
        raise HTTPException(status_code=404, detail="Resultado no disponible aún")
    if not Path(sd.docx_path).exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco")
    return FileResponse(
        sd.docx_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=Path(sd.docx_path).name,
    )


# ── Internal endpoint (called by Lambda) ─────────────────────────────────────

@router.post(
    "/internal/run/{session_id}",
    tags=["Internal"],
    summary="Iniciar workflow desde Lambda (trigger S3)",
    description=(
        "Llamado exclusivamente por la Lambda `prisma-trigger` tras recibir el evento PUT de S3. "
        "Verifica el token interno, recupera los metadatos de DynamoDB y lanza el workflow "
        "como tarea asyncio en el backend.\n\n"
        "Requiere el header `X-Internal-Token` con el mismo valor que la variable de entorno "
        "`INTERNAL_TOKEN` del backend y de la Lambda."
    ),
    response_model=InternalRunResponse,
    responses={
        401: {"description": "Token interno inválido o ausente"},
        404: {"description": "Sesión no encontrada en DynamoDB"},
    },
)
async def internal_run(
    session_id: str,
    x_internal_token: Optional[str] = Header(None, description="Token secreto compartido con la Lambda"),
):
    if not INTERNAL_TOKEN or x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="Token interno inválido")

    item = dynamo_store.get_session(session_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada en DynamoDB")

    # Ensure in-memory session exists (created by /start, but guard against edge cases)
    if session_id not in SESSIONS:
        SESSIONS[session_id] = SessionData()

    task = asyncio.create_task(run_workflow_for_api(
        session_id=session_id,
        paci_s3_key=item["paci_s3_key"],
        material_s3_key=item["material_s3_key"],
        prompt=item["prompt"],
        school_id=item["school_id"],
    ))
    SESSIONS[session_id].task = task
    return {"started": True}
