import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.session_store import SESSIONS, HITL_CALLBACKS
from run import run_workflow


async def run_workflow_for_api(
    session_id: str,
    paci_path: str,
    material_path: str,
    prompt: str,
    school_id: str,
) -> None:
    session_data = SESSIONS.get(session_id)
    if session_data is None:
        return

    async def hitl_callback(state: dict, attempt: int, max_attempts: int) -> tuple[bool, str, int]:
        session_data.hitl_data = {
            "perfil_paci": state.get("perfil_paci", ""),
            "planificacion_adaptada": state.get("planificacion_adaptada", ""),
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
        session_data.phase = "awaiting_hitl"
        session_data.messages.append({
            "role": "system",
            "content": f"Revisión requerida — intento {attempt} de {max_attempts}. Por favor revise el análisis y la planificación.",
        })

        response = await session_data.hitl_response_queue.get()
        session_data.phase = "running"
        session_data.hitl_data = None

        approved = response.get("approved", False)
        reason = response.get("reason") or ""
        agent_to_retry = int(response.get("agent_to_retry") or 0)
        return approved, reason, agent_to_retry

    HITL_CALLBACKS[session_id] = hitl_callback

    try:
        session_data.messages.append({
            "role": "system",
            "content": "Documentos recibidos. Iniciando análisis del PACI...",
        })
        results = await run_workflow(
            paci_path=paci_path,
            material_path=material_path,
            prompt=prompt,
            user_id=session_id,
            school_id=school_id,
            api_session_id=session_id,
        )
        session_data.result = results
        session_data.docx_path = results.get("docx_path")
        session_data.phase = "completed"
        session_data.messages.append({
            "role": "agent",
            "content": "✅ Proceso completado. La rúbrica adaptada está lista para descargar.",
        })
    except Exception as exc:
        session_data.phase = "error"
        session_data.error = str(exc)
        session_data.messages.append({
            "role": "system",
            "content": f"❌ Error durante el procesamiento: {str(exc)}",
        })
    finally:
        HITL_CALLBACKS.pop(session_id, None)
        if session_data is not None:
            while not session_data.hitl_response_queue.empty():
                session_data.hitl_response_queue.get_nowait()
        for path in [paci_path, material_path]:
            try:
                os.remove(path)
            except OSError:
                pass
