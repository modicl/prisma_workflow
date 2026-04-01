"""
PaciWorkflowAgent — Orquestador principal del flujo multi-agente PACI.

Flujo:
  1. AnalizadorPACI   → perfil_paci
  2. Adaptador        → planificacion_adaptada
  3. Loop (max 3):
       GeneradorRubrica → rubrica
       AgenteCritico    → evaluacion_critica
       Si acceptable: break
       Si no: guarda critica_previa y repite
"""

import json
import re

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from typing import AsyncGenerator

from agents.analizador_paci import analizador_paci_agent
from agents.adaptador import adaptador_agent
from agents.generador_rubrica import generador_rubrica_agent
from agents.critico import critico_agent

MAX_ITERATIONS = 3


class PaciWorkflowAgent(BaseAgent):
    """Coordinador secuencial del flujo PACI con loop de revisión de rúbrica."""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        super().__init__(
            name="PaciWorkflow",
            description=(
                "Coordina el flujo completo: analiza el PACI, adapta el material "
                "educativo y genera una rúbrica revisada críticamente."
            ),
            sub_agents=[
                analizador_paci_agent,
                adaptador_agent,
                generador_rubrica_agent,
                critico_agent,
            ],
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:

        # ── Agente 1: Análisis del PACI ──────────────────────────────────────
        print("\n[Agente 1] Analizando PACI...\n")
        async for event in analizador_paci_agent.run_async(ctx):
            yield event

        # ── Agente 2: Adaptación del material ────────────────────────────────
        print("\n[Agente 2] Adaptando material educativo...\n")
        async for event in adaptador_agent.run_async(ctx):
            yield event

        # ── Loop: Generador de Rúbrica + Agente Crítico ───────────────────────
        for iteration in range(1, MAX_ITERATIONS + 1):
            print(f"\n[Agente 3 — Iteración {iteration}/{MAX_ITERATIONS}] Generando rúbrica...\n")
            async for event in generador_rubrica_agent.run_async(ctx):
                yield event

            print(f"\n[Agente Crítico — Iteración {iteration}/{MAX_ITERATIONS}] Evaluando rúbrica...\n")
            async for event in critico_agent.run_async(ctx):
                yield event

            evaluacion_raw = ctx.session.state.get("evaluacion_critica", "")
            print(f"\n[DEBUG] Respuesta cruda del Agente Crítico:\n{evaluacion_raw[:500]}\n")
            evaluacion = _parse_critic_json(evaluacion_raw)

            if evaluacion.get("acceptable", False):
                print(f"\n✓ Rúbrica aprobada en iteración {iteration}.\n")
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
