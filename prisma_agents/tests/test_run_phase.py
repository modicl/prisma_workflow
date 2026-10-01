"""run_phase: corre UNA fase con el Runner real de ADK (agentes simulados) y devuelve el estado persistido."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.adk.events import Event, EventActions

import run

SIN_BLOQUEO = SimpleNamespace(blocked=False, code="", reason="")
BASE = {"paci_document": "PACI", "material_document": "MAT", "prompt_docente": "", "school_id": ""}


async def agentes_falsos(agente, ctx, label):
    delta = {}
    if label.startswith("Agente 1"):
        delta = {"perfil_paci": "PERFIL"}
    elif label == "Agente 2":
        delta = {"planificacion_adaptada": "PLAN"}
    if delta:
        yield Event(author="falso", actions=EventActions(state_delta=delta))


@pytest.mark.asyncio
async def test_fase_a_devuelve_las_salidas_de_los_agentes_y_los_materiales():
    with patch("agent._run_with_timeout", agentes_falsos), patch("agent.evaluate_paci_compliance", return_value=SIN_BLOQUEO):
        estado = await run.run_phase("a", {**BASE, "retry_agent": 0}, user_id="u1", api_session_id="s1")
    assert estado["perfil_paci"] == "PERFIL" and estado["planificacion_adaptada"] == "PLAN"
    assert estado["materiales_referencia"] == ""


@pytest.mark.asyncio
async def test_un_timeout_escrito_directo_llega_al_estado_final():
    async def con_timeout(agente, ctx, label):
        ctx.session.state["status"] = "timeout"           # así lo escribe _run_with_timeout
        for _ in ():
            yield _

    with patch("agent._run_with_timeout", con_timeout):
        estado = await run.run_phase("a", {**BASE, "retry_agent": 0}, user_id="u1", api_session_id="s1")
    assert estado["status"] == "timeout"


@pytest.mark.asyncio
async def test_cada_fase_usa_una_sesion_adk_nueva_y_el_estado_de_entrada_se_conserva():
    with patch("agent._run_with_timeout", agentes_falsos), patch("agent.evaluate_paci_compliance", return_value=SIN_BLOQUEO):
        e1 = await run.run_phase("a", {**BASE, "retry_agent": 0}, user_id="u1", api_session_id="s1")
        e2 = await run.run_phase("a", {**e1, "retry_agent": 2}, user_id="u1", api_session_id="s1")   # mismo id de sesión API
    assert e2["perfil_paci"] == "PERFIL" and e2["paci_document"] == "PACI"


def test_fase_desconocida_falla():
    with pytest.raises(KeyError):
        asyncio.run(run.run_phase("z", {}, user_id="u", api_session_id="s"))
