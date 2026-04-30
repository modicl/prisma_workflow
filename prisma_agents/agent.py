"""
PaciWorkflowAgent — Orquestador principal del flujo multi-agente PACI.

Flujo:
  1. AnalizadorPACI   → perfil_paci
  2. Loop HITL (max 6 intentos):
       Adaptador        → planificacion_adaptada
       Checkpoint docente → aprueba o rechaza
       Si rechaza agente 1: re-corre AnalizadorPACI + Adaptador
       Si rechaza agente 2: re-corre solo Adaptador
       Si intentos agotados: status = "hitl_rejected", cancela
  3. Loop (max 3):
       GeneradorRubrica → rubrica
       AgenteCritico    → evaluacion_critica
       Si acceptable: break
       Si no: guarda critica_previa y repite
"""

import asyncio
import json
import re

from google.adk.agents import BaseAgent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from typing import AsyncGenerator
from google import genai
from google.genai import types as genai_types
from google.genai.errors import ServerError

from agents.analizador_paci import make_analizador_paci_agent
from agents.adaptador import make_adaptador_agent
from agents.generador_rubrica import make_generador_rubrica_agent
from agents.critico import make_critico_agent
from utils.curriculum_catalog import normalize_subject, normalize_grade
from tools.book_repository import get_reference_materials_async

_genai_client: genai.Client | None = None

_SSE_MESSAGES: dict[str, str] = {
    "Agente 1":         "Analizando PACI...",
    "Agente 1 (retry)": "Re-analizando PACI con retroalimentación del docente...",
    "Agente 2":         "Adaptando material educativo...",
}


def _get_sse_message(label: str) -> str:
    if label in _SSE_MESSAGES:
        return _SSE_MESSAGES[label]
    if "Agente 3" in label:
        return "Generando rúbrica..."
    if "Agente Crítico" in label:
        return "Evaluando calidad de la rúbrica..."
    return label


def _push_sse_event(state: dict, event_data: dict) -> None:
    """Push an SSE event to the session queue (API mode only; no-op in CLI)."""
    session_id = state.get("api_session_id", "")
    if not session_id:
        return
    try:
        from api.session_store import SESSIONS
        sd = SESSIONS.get(session_id)
        if sd:
            sd.event_queue.put_nowait(event_data)
    except ImportError:
        pass


def _get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client()
    return _genai_client


def _extract_subject_grade(perfil_paci: str) -> tuple[str, str]:
    """Extrae ramo y curso del bloque ---METADATOS--- generado por AnalizadorPACI."""
    block_match = re.search(
        r'---METADATOS---(.*?)---FIN_METADATOS---', perfil_paci, re.DOTALL
    )
    block = block_match.group(1) if block_match else perfil_paci
    ramo_match = re.search(r'RAMO:\s*(.+)', block)
    curso_match = re.search(r'CURSO:\s*(.+)', block)
    subject_raw = ramo_match.group(1).strip() if ramo_match else ""
    grade_raw = curso_match.group(1).strip() if curso_match else ""
    return subject_raw, grade_raw


_CLASSIFY_PROMPT = (
    "Clasifica si el siguiente mensaje de un docente indica APROBACIÓN o RECHAZO "
    "del trabajo presentado. Responde únicamente con \"APROBADO\" o \"RECHAZADO\".\n\n"
    "Mensaje: \"{respuesta}\""
)


def _classify_response(respuesta: str) -> bool:
    """Usa un LLM para determinar si la respuesta del profesor es aprobación o rechazo.

    Retorna True si es aprobación, False si es rechazo o respuesta inesperada.
    """
    prompt = _CLASSIFY_PROMPT.format(respuesta=respuesta)
    response = _get_genai_client().models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
        config=genai_types.GenerateContentConfig(max_output_tokens=5),
    )
    return response.text.strip().upper() == "APROBADO"


async def _hitl_checkpoint(
    state: dict, attempt: int, max_attempts: int
) -> tuple[bool, str, int]:
    """Pausa para que el profesor apruebe o rechace. Modo API si hay callback, sino CLI."""
    api_session_id = state.get("api_session_id", "")
    if api_session_id:
        try:
            from api.session_store import HITL_CALLBACKS
        except ImportError:
            pass
        else:
            callback = HITL_CALLBACKS.get(api_session_id)
            if callback:
                return await callback(state, attempt, max_attempts)
            raise RuntimeError(
                f"HITL callback for session {api_session_id!r} not found. "
                "Cannot fall back to CLI in API mode."
            )

    # CLI fallback
    restantes = max_attempts - attempt
    print("\n" + "═" * 60)
    print(f"  REVISIÓN DEL DOCENTE [{attempt}/{max_attempts}]")
    print("═" * 60)
    print("\n── RESUMEN ANÁLISIS PACI (Agente 1) ────────────────────")
    print(state.get("perfil_paci", "(sin datos)"))
    print("\n── RESUMEN PLANIFICACIÓN ADAPTADA (Agente 2) ───────────")
    print(state.get("planificacion_adaptada", "(sin datos)"))
    if restantes > 0:
        print(f"\n⚠  Quedan {restantes} intento(s) de revisión.")
    else:
        print("\n⚠  Este es el último intento de revisión.")
    print("\n" + "─" * 60)

    loop = asyncio.get_running_loop()
    respuesta = await loop.run_in_executor(
        None, lambda: input("¿Aprueba el análisis y la planificación? ").strip()
    )

    if _classify_response(respuesta):
        return True, "", 0

    if attempt >= max_attempts:
        print(
            "\n✗ Proceso cancelado: se agotaron los intentos de revisión "
            "sin obtener aprobación del docente.\n"
        )
        return False, respuesta, 0

    while True:
        eleccion = await loop.run_in_executor(
            None,
            lambda: input(
                "¿El problema está en el análisis del PACI (1) "
                "o en la adaptación del material (2)? "
            ).strip()
        )
        if eleccion in ("1", "2"):
            return False, respuesta, int(eleccion)
        print("  Por favor ingresa 1 o 2.")


MAX_ITERATIONS = 3
MAX_HITL_ITERATIONS = 3          # intentos de revisión del profesor
AGENT_TIMEOUT_SECONDS = 90   # segundos por agente antes de considerar timeout
MAX_RETRIES_ON_TIMEOUT = 2   # reintentos adicionales si el agente hace timeout
RETRY_DELAY_SECONDS = 5      # espera entre reintentos de timeout
_503_RETRY_DELAYS = [15, 30] # backoff en segundos para errores 503 de Gemini


async def _run_with_timeout(agent, ctx: InvocationContext, label: str) -> AsyncGenerator[Event, None]:
    """
    Ejecuta un agente con timeout y reintentos automáticos.

    Si el agente no responde en AGENT_TIMEOUT_SECONDS, lo reintenta hasta
    MAX_RETRIES_ON_TIMEOUT veces. Como cada agente escribe su resultado en
    session.state al completarse, el reintento retoma desde el punto exacto
    donde falló — los agentes anteriores ya tienen su output guardado.
    """
    _push_sse_event(ctx.session.state, {
        "type": "agent_start",
        "agent": label,
        "message": _get_sse_message(label),
    })
    for attempt in range(1, MAX_RETRIES_ON_TIMEOUT + 2):  # +2: intento original + reintentos
        timed_out = False
        server_error = False
        try:
            async with asyncio.timeout(AGENT_TIMEOUT_SECONDS):
                async for event in agent.run_async(ctx):
                    yield event
        except TimeoutError:
            timed_out = True
            if attempt <= MAX_RETRIES_ON_TIMEOUT:
                print(
                    f"\n⏱ TIMEOUT: {label} superó {AGENT_TIMEOUT_SECONDS}s "
                    f"(intento {attempt}/{MAX_RETRIES_ON_TIMEOUT + 1}). "
                    f"Reintentando en {RETRY_DELAY_SECONDS}s...\n"
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)
            else:
                print(
                    f"\n⏱ TIMEOUT: {label} agotó {MAX_RETRIES_ON_TIMEOUT + 1} intentos "
                    f"sin respuesta. Abortando flujo.\n"
                )
                ctx.session.state["status"] = "timeout"
        except ServerError as exc:
            if exc.code == 503 and attempt <= len(_503_RETRY_DELAYS):
                delay = _503_RETRY_DELAYS[attempt - 1]
                server_error = True
                print(
                    f"\n🔄 503 UNAVAILABLE: {label} (intento {attempt}/{len(_503_RETRY_DELAYS) + 1}). "
                    f"Gemini con alta demanda — reintentando en {delay}s...\n"
                )
                await asyncio.sleep(delay)
            else:
                raise

        if not timed_out and not server_error:
            _push_sse_event(ctx.session.state, {"type": "agent_end", "agent": label})
            return  # completó exitosamente, salir del loop de reintentos

    _push_sse_event(ctx.session.state, {"type": "agent_end", "agent": label})


class PaciWorkflowAgent(BaseAgent):
    """Coordinador secuencial del flujo PACI con loop de revisión de rúbrica."""

    model_config = {"arbitrary_types_allowed": True}

    analizador_paci_agent: LlmAgent | None = None
    adaptador_agent: LlmAgent | None = None
    generador_rubrica_agent: LlmAgent | None = None
    critico_agent: LlmAgent | None = None

    def __init__(self):
        _analizador = make_analizador_paci_agent()
        _adaptador = make_adaptador_agent()
        _generador = make_generador_rubrica_agent()
        _critico = make_critico_agent()
        super().__init__(
            name="PaciWorkflow",
            description=(
                "Coordina el flujo completo: analiza el PACI, adapta el material "
                "educativo y genera una rúbrica revisada críticamente."
            ),
            sub_agents=[_analizador, _adaptador, _generador, _critico],
            analizador_paci_agent=_analizador,
            adaptador_agent=_adaptador,
            generador_rubrica_agent=_generador,
            critico_agent=_critico,
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:

        # ── Agente 1: Análisis inicial del PACI ──────────────────────────────
        print("\n[Agente 1] Analizando PACI...\n")
        async for event in _run_with_timeout(self.analizador_paci_agent, ctx, "Agente 1"):
            yield event
        if ctx.session.state.get("status") == "timeout":
            return

        # ── Book Repository: materiales de referencia del establecimiento ────
        perfil_paci = ctx.session.state.get("perfil_paci", "")
        prompt_docente = ctx.session.state.get("prompt_docente", "")
        subject_raw, grade_raw = _extract_subject_grade(perfil_paci)
        school_id = ctx.session.state.get("school_id", "")
        subject = normalize_subject(subject_raw) if subject_raw else None
        grade = normalize_grade(grade_raw) if grade_raw else None

        # Fallback: si el PACI no dio ramo o curso, intentar desde el prompt del docente
        if not subject and prompt_docente:
            subject = normalize_subject(prompt_docente)
        if not grade and prompt_docente:
            grade = normalize_grade(prompt_docente)

        # Fallback: si sigue sin ramo o curso (ej. PACI dice "Todas las asignaturas"),
        # inferir desde el material_document sin costo extra de tokens
        if not subject or not grade:
            material_doc = ctx.session.state.get("material_document", "")
            if not subject:
                subject = normalize_subject(material_doc)
            if not grade:
                grade = normalize_grade(material_doc)

        materiales_texto = ""
        if school_id and subject and grade:
            print(f"\n[BookRepository] Buscando materiales: {subject}/{grade} — colegio {school_id}...\n")
            raw = await get_reference_materials_async(school_id, subject, grade, perfil_paci)
            if raw:
                # Envuelve con encabezado y etiqueta de seguridad para que el LLM identifique la sección
                materiales_texto = (
                    "### MATERIALES DE REFERENCIA DEL ESTABLECIMIENTO:\n"
                    "<documento_usuario tipo=\"materiales_referencia\">\n"
                    f"{raw}\n"
                    "</documento_usuario>"
                )
                print("  ✓ Materiales de referencia cargados.\n")
            else:
                print("  ℹ Sin materiales disponibles para este ramo/curso.\n")
        else:
            print("\n[BookRepository] Sin school_id o ramo/curso no reconocido — omitiendo materiales.\n")

        ctx.session.state["materiales_referencia"] = materiales_texto

        # ── Loop HITL: Agente 2 + aprobación del profesor ────────────────────
        for hitl_attempt in range(1, MAX_HITL_ITERATIONS + 1):
            print("\n[Agente 2] Adaptando material educativo...\n")
            async for event in _run_with_timeout(self.adaptador_agent, ctx, "Agente 2"):
                yield event
            if ctx.session.state.get("status") == "timeout":
                return

            aprobado, razon, agente = await _hitl_checkpoint(
                ctx.session.state, attempt=hitl_attempt, max_attempts=MAX_HITL_ITERATIONS
            )

            if aprobado:
                break

            # Intentos agotados — cancela el flujo
            # agente == 0: camino CLI (última iteración retorna 0 explícitamente)
            # hitl_attempt == MAX_HITL_ITERATIONS: camino API (agent_to_retry siempre 1 o 2)
            if agente == 0 or hitl_attempt == MAX_HITL_ITERATIONS:
                ctx.session.state["status"] = "hitl_rejected"
                return

            # Inyectar feedback según el agente elegido por el profesor
            if agente == 1:
                ctx.session.state["hitl_feedback_a1"] = (
                    f"\nRETROALIMENTACIÓN DEL DOCENTE — Debes revisar tu análisis "
                    f"considerando el siguiente problema señalado:\n"
                    f"\"{razon}\"\n"
                    f"Ajusta tu respuesta para abordar específicamente este punto."
                )
                ctx.session.state["hitl_feedback_a2"] = ""
                print("\n[Agente 1] Re-analizando PACI con feedback del docente...\n")
                async for event in _run_with_timeout(self.analizador_paci_agent, ctx, "Agente 1 (retry)"):
                    yield event
                if ctx.session.state.get("status") == "timeout":
                    return
            else:  # agente == 2
                ctx.session.state["hitl_feedback_a1"] = ""   # limpia feedback previo de a1
                ctx.session.state["hitl_feedback_a2"] = (
                    f"\nRETROALIMENTACIÓN DEL DOCENTE — Debes revisar la adaptación "
                    f"considerando el siguiente problema señalado:\n"
                    f"\"{razon}\"\n"
                    f"Ajusta tu respuesta para abordar específicamente este punto."
                )

        # ── Loop: Generador de Rúbrica + Agente Crítico ───────────────────────
        for iteration in range(1, MAX_ITERATIONS + 1):
            print(f"\n[Agente 3 — Iteración {iteration}/{MAX_ITERATIONS}] Generando rúbrica...\n")
            async for event in _run_with_timeout(self.generador_rubrica_agent, ctx, f"Agente 3 (it.{iteration})"):
                yield event
            if ctx.session.state.get("status") == "timeout":
                return

            print(f"\n[Agente Crítico — Iteración {iteration}/{MAX_ITERATIONS}] Evaluando rúbrica...\n")
            async for event in _run_with_timeout(self.critico_agent, ctx, f"Agente Crítico (it.{iteration})"):
                yield event
            if ctx.session.state.get("status") == "timeout":
                return

            evaluacion_raw = ctx.session.state.get("evaluacion_critica", "")
            print(f"\n[DEBUG] Respuesta cruda del Agente Crítico:\n{str(evaluacion_raw)[:500]}\n")
            evaluacion = _parse_critic_json(evaluacion_raw)

            if evaluacion.get("acceptable", False):
                print(f"\n✓ Rúbrica aprobada en iteración {iteration}.\n")
                ctx.session.state["status"] = "success"
                break

            if iteration < MAX_ITERATIONS:
                critique = evaluacion.get("critique", "Sin descripción.")
                suggestions = evaluacion.get("suggestions", [])
                suggestions_text = "\n".join(f"- {s}" for s in suggestions)
                ctx.session.state["critica_previa"] = (
                    f"RETROALIMENTACIÓN EVALUADOR (iteración {iteration}):\n"
                    f"{critique}\n\n"
                    f"SUGERENCIAS A INCORPORAR:\n"
                    f"{suggestions_text}"
                )
                print(f"\n✗ Rúbrica rechazada. Reintentando con retroalimentación...\n")
            else:
                print(f"\n⚠ Máximo de iteraciones alcanzado. Se entrega la última versión generada.\n")
                ctx.session.state["status"] = "fail"


def _parse_critic_json(raw) -> dict:
    """Parsea la respuesta del Agente Crítico. ADK puede entregar dict o JSON string según output_schema."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw.strip())
    except (json.JSONDecodeError, AttributeError):
        return {
            "acceptable": False,
            "critique": str(raw),
            "suggestions": ["El Agente Crítico no retornó JSON válido. Revisar la rúbrica manualmente."],
        }


root_agent = PaciWorkflowAgent()
