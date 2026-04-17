import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.session_store import SESSIONS, SessionData
from api.workflow_runner import run_workflow_for_api

router = APIRouter(prefix="/chat")

UPLOAD_DIR = Path("/tmp/prisma_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


class HitlResponseBody(BaseModel):
    approved: bool
    reason: Optional[str] = None
    agent_to_retry: Optional[int] = None


@router.post("/start")
async def start_chat(
    background_tasks: BackgroundTasks,
    paci_file: UploadFile = File(...),
    material_file: UploadFile = File(...),
    prompt: str = Form(""),
    school_id: str = Form("colegio_demo"),
):
    session_id = str(uuid.uuid4())

    paci_ext = Path(paci_file.filename).suffix if paci_file.filename else ".pdf"
    material_ext = Path(material_file.filename).suffix if material_file.filename else ".docx"

    paci_path = UPLOAD_DIR / f"{session_id}_paci{paci_ext}"
    material_path = UPLOAD_DIR / f"{session_id}_material{material_ext}"

    paci_path.write_bytes(await paci_file.read())
    material_path.write_bytes(await material_file.read())

    SESSIONS[session_id] = SessionData()

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
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
    return {
        "phase": sd.phase,
        "messages": sd.messages,
        "hitl_data": sd.hitl_data,
        "error": sd.error,
    }


@router.post("/{session_id}/hitl")
async def respond_hitl(session_id: str, body: HitlResponseBody):
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
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
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
    if sd.phase != "completed" or not sd.docx_path:
        raise HTTPException(status_code=404, detail="Resultado no disponible aún")
    return FileResponse(
        sd.docx_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=Path(sd.docx_path).name,
    )
