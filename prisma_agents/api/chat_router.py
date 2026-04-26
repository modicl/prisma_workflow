import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import boto3
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Header, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from api import dynamo_store
from api.session_store import SESSIONS, SessionData, sync_to_dynamo
from api.workflow_runner import run_workflow_for_api

router = APIRouter(prefix="/chat")

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


class HitlResponseBody(BaseModel):
    approved: bool
    reason: Optional[str] = None
    agent_to_retry: Optional[int] = None


class StartChatResponse(BaseModel):
    session_id: str


# ── Public endpoints ──────────────────────────────────────────────────────────

@router.post("/start", response_model=StartChatResponse, status_code=201)
async def start_chat(
    background_tasks: BackgroundTasks,
    paci_file: UploadFile = File(...),
    material_file: UploadFile = File(...),
    prompt: str = Form(""),
    school_id: str = Form("colegio_demo"),
):
    session_id = str(uuid.uuid4())
    paci_ext = _safe_ext(paci_file.filename, ".pdf")
    material_ext = _safe_ext(material_file.filename, ".docx")

    paci_bytes = await paci_file.read()
    material_bytes = await material_file.read()

    SESSIONS[session_id] = SessionData()

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
        )
        try:
            s3 = boto3.client("s3")
            s3.put_object(Bucket=S3_BUCKET, Key=paci_s3_key, Body=paci_bytes)
            s3.put_object(Bucket=S3_BUCKET, Key=material_s3_key, Body=material_bytes)
        except Exception as exc:
            SESSIONS.pop(session_id, None)
            raise HTTPException(status_code=500, detail=f"Error al subir archivos a S3: {exc}")
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
        background_tasks.add_task(
            run_workflow_for_api,
            session_id=session_id,
            paci_path=str(paci_path),
            material_path=str(material_path),
            prompt=prompt,
            school_id=school_id,
        )

    return {"session_id": session_id}


@router.get("/{session_id}/state")
async def get_state(session_id: str):
    if dynamo_store.enabled():
        item = dynamo_store.get_session(session_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")
        return {
            "phase":           item["phase"],
            "messages":        item["messages"],
            "hitl_data":       item["hitl_data"],
            "error":           item["error"],
            "workflow_status": item.get("workflow_status"),
        }
    # Local dev fallback
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
    return {
        "phase":           sd.phase,
        "messages":        sd.messages,
        "hitl_data":       sd.hitl_data,
        "error":           sd.error,
        "workflow_status": sd.workflow_status,
    }


@router.post("/{session_id}/hitl")
async def respond_hitl(session_id: str, body: HitlResponseBody):
    sd = SESSIONS.get(session_id)
    if sd is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if sd.phase != "awaiting_hitl":
        raise HTTPException(status_code=409, detail="La sesión no está esperando revisión HITL")
    await sd.hitl_response_queue.put({
        "approved": body.approved,
        "reason": body.reason,
        "agent_to_retry": body.agent_to_retry,
    })
    return {"ok": True}


@router.get("/{session_id}/download")
async def download_result(session_id: str):
    if dynamo_store.enabled():
        item = dynamo_store.get_session(session_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")
        if item.get("phase") != "completed" or not item.get("docx_s3_key"):
            raise HTTPException(status_code=404, detail="Resultado no disponible aún")
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        s3_obj = s3.get_object(Bucket=S3_BUCKET, Key=item["docx_s3_key"])
        filename = Path(item["docx_s3_key"]).name
        return StreamingResponse(
            s3_obj["Body"].iter_chunks(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Local dev fallback
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
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

@router.post("/internal/run/{session_id}")
async def internal_run(
    session_id: str,
    background_tasks: BackgroundTasks,
    x_internal_token: Optional[str] = Header(None),
):
    if INTERNAL_TOKEN and x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="Token interno inválido")

    item = dynamo_store.get_session(session_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada en DynamoDB")

    # Ensure in-memory session exists (created by /start, but guard against edge cases)
    if session_id not in SESSIONS:
        SESSIONS[session_id] = SessionData()

    background_tasks.add_task(
        run_workflow_for_api,
        session_id=session_id,
        paci_s3_key=item["paci_s3_key"],
        material_s3_key=item["material_s3_key"],
        prompt=item["prompt"],
        school_id=item["school_id"],
    )
    return {"started": True}
