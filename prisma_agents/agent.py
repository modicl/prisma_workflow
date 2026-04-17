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

from agents.analizador_paci import make_analizador_paci_agent
from agents.adaptador import make_adaptador_agent
from agents.generador_rubrica import make_generador_rubrica_agent
from agents.critico import make_critico_agent
from utils.curriculum_catalog import normalize_subject, normalize_grade
from tools.book_repository import get_reference_materials

_genai_client: genai.Client | None = None


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


def _hitl_checkpoint(
    state: dict, attempt: int, max_attempts: int
) -> tuple[bool, str, int]:
    """Pausa interactiva para que el profesor apruebe o rechace los resultados.

    Retorna: (aprobado, razon, agente_a_reiniciar)
    - aprobado=True  → continúa el flujo; razon="" y agente_a_reiniciar=0
    - aprobado=False → razon contiene el feedback del profesor
                       agente_a_reiniciar: 1 o 2, o 0 si se agotaron intentos
    """
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
    respuesta = input("¿Aprueba el análisis y la planificación? ").strip()

    if _classify_response(respuesta):
        return True, "", 0

    # Último intento agotado — cancela sin preguntar agente
    if attempt >= max_attempts:
        print(
            "\n✗ Proceso cancelado: se agotaron los intentos de revisión "
            "sin obtener aprobación del docente.\n"
        )
        return False, respuesta, 0

    # Pide agente a re-ejecutar
    while True:
        eleccion = input(
            "¿El problema está en el análisis del PACI (1) "
            "o en la adaptación del material (2)? "
        ).strip()
        if eleccion in ("1", "2"):
            return False, respuesta, int(eleccion)
        print("  Por favor ingresa 1 o 2.")


MAX_ITERATIONS = 3
MAX_HITL_ITERATIONS = 6          # intentos de revisión del profesor
AGENT_TIMEOUT_SECONDS = 90   # segundos por agente antes de considerar timeout
MAX_RETRIES_ON_TIMEOUT = 2   # reintentos adicionales si el agente hace timeout
RETRY_DELAY_SECONDS = 5      # espera entre reintentos


async def _run_with_timeout(agent, ctx: InvocationContext, label: str) -> AsyncGenerator[Event, None]:
    """
    Ejecuta un agente con timeout y reintentos automáticos.

    Si el agente no responde en AGENT_TIMEOUT_SECONDS, lo reintenta hasta
    MAX_RETRIES_ON_TIMEOUT veces. Como cada agente escribe su resultado en
    session.state al completarse, el reintento retoma desde el punto exacto
    donde falló — los agentes anteriores ya tienen su output guardado.
    """
    for attempt in range(1, MAX_RETRIES_ON_TIMEOUT + 2):  # +2: intento original + reintentos
        timed_out = False
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

        if not timed_out:
            return  # completó exitosamente, salir del loop de reintentos


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

        materiales_texto = ""
        if school_id and subject and grade:
            print(f"\n[BookRepository] Buscando materiales: {subject}/{grade} — colegio {school_id}...\n")
            raw = get_reference_materials(school_id, subject, grade, perfil_paci)
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

            aprobado, razon, agente = _hitl_checkpoint(
                ctx.session.state, attempt=hitl_attempt, max_attempts=MAX_HITL_ITERATIONS
            )

            if aprobado:
                break

            # Intentos agotados — cancela el flujo
            if agente == 0:
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
            print(f"\n[DEBUG] Respuesta cruda del Agente Crítico:\n{evaluacion_raw[:500]}\n")
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


def _parse_critic_json(raw: str) -> dict:
    """Parsea la respuesta JSON del Agente Crítico de forma robusta."""
    cleaned = raw.strip()

    # Intento directo
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Extrae el primer objeto JSON del texto
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: no aceptable con el texto raw como critique
    return {
        "acceptable": False,
        "critique": raw,
        "suggestions": [
            "El Agente Crítico no retornó JSON válido. Revisar la rúbrica manualmente."
        ],
    }


root_agent = PaciWorkflowAgent()
