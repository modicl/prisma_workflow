"""Fases SIMULADAS para probar Step Functions de punta a punta sin gastar cuota de Gemini.

Se activan con un `school_id` que empieza con `__mock` (igual que api/mock_runner.py para el camino antiguo):
  __mock_ok__        flujo feliz                       __mock_degraded__  la rúbrica termina "degraded"
  __mock_blocked__   el gate normativo bloquea el PACI  __mock_error__     la fase B falla
  __mock_timeout__   un agente agota el tiempo          __mock_dead__      (lo maneja el endpoint: la fase A nunca responde)
"""
import asyncio
import os

STEP_DELAY = float(os.environ.get("MOCK_PHASE_DELAY", "0.5"))


def initial_state(school_id: str, prompt: str) -> dict:
    return {"paci_document": "PACI SIMULADO", "material_document": "MATERIAL SIMULADO", "prompt_docente": prompt, "school_id": school_id}


async def run(phase: str, state: dict, school_id: str) -> dict:
    await asyncio.sleep(STEP_DELAY)
    nuevo = dict(state)
    if phase == "a":
        if school_id == "__mock_blocked__":
            nuevo.update(status="validation_failed", validation_code="paci_vencido", validation_reason="PACI simulado vencido")
        elif school_id == "__mock_timeout__":
            nuevo["status"] = "timeout"
        else:
            nuevo.update(
                perfil_paci="PERFIL SIMULADO",
                planificacion_adaptada=f"PLAN SIMULADO (retry_agent={state.get('retry_agent', 0)})",
                materiales_referencia="",
            )
        return nuevo
    if school_id == "__mock_error__":
        raise RuntimeError("fallo simulado de la fase B")
    nuevo.update(
        rubrica="RUBRICA SIMULADA",
        evaluacion_critica={"acceptable": True},
        status="fail" if school_id == "__mock_degraded__" else "success",
        warnings=[],
    )
    return nuevo
