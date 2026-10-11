"""El RAG normativo se inyecta en la Fase A (Agente 2) y en la Fase B (Generador) sin cambiar el resto del flujo."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, patch

import pytest

import agent as ag
from tests.test_agent_phases import Camino, ctx, correr, delta, parches  # noqa: F401  (parches es una fixture)


@pytest.mark.asyncio
async def test_fase_a_inyecta_la_normativa_del_adaptador_y_la_hace_viajar(parches):
    camino, c = Camino(), ctx(retry_agent=0)
    rag = AsyncMock(return_value="NORMATIVA A")
    with patch("agent._run_with_timeout", camino), patch("agent.normativa_para_adaptador", new=rag):
        eventos = await correr(ag.PaciWorkflowAgent(phase="a"), c)
    rag.assert_awaited_once()
    assert delta(eventos)["normativa_adaptador"] == "NORMATIVA A"
    assert c.session.state["normativa_adaptador"] == "NORMATIVA A"


@pytest.mark.asyncio
async def test_fase_a_envia_al_buscador_solo_asignatura_curso_y_diagnostico(parches):
    camino = Camino()
    c = ctx(retry_agent=0, prompt_docente="matematica 5basico")
    rag = AsyncMock(return_value="")
    with patch("agent._run_with_timeout", camino), patch("agent.normativa_para_adaptador", new=rag), \
         patch("agent.normalize_subject", return_value="matematica"), patch("agent.normalize_grade", return_value="5basico"):
        await correr(ag.PaciWorkflowAgent(phase="a"), c)
    asignatura, curso, diagnostico = rag.await_args.args
    assert (asignatura, curso) == ("matematica", "5basico")
    assert diagnostico == ""                                   # el Camino de prueba no escribe DIAGNOSTICO


@pytest.mark.asyncio
async def test_fase_a_reintento_conserva_la_normativa_sin_volver_a_buscar(parches):
    camino, c = Camino(), ctx(retry_agent=2, normativa_adaptador="PREVIA")
    rag = AsyncMock(return_value="OTRA")
    with patch("agent._run_with_timeout", camino), patch("agent.normativa_para_adaptador", new=rag):
        eventos = await correr(ag.PaciWorkflowAgent(phase="a"), c)
    rag.assert_not_awaited()
    assert delta(eventos)["normativa_adaptador"] == "PREVIA"


@pytest.mark.asyncio
async def test_fase_b_busca_la_normativa_de_evaluacion_una_vez(parches):
    camino, c = Camino(), ctx(perfil_paci="P", planificacion_adaptada="PL")
    rag = AsyncMock(return_value="NORMATIVA B")
    with patch("agent._run_with_timeout", camino), patch("agent.normativa_para_evaluacion", new=rag):
        eventos = await correr(ag.PaciWorkflowAgent(phase="b"), c)
    rag.assert_awaited_once()
    assert delta(eventos)["normativa_evaluacion"] == "NORMATIVA B"


@pytest.mark.asyncio
async def test_estado_de_una_sesion_en_vuelo_sin_las_claves_nuevas_no_rompe(parches):
    """Un estado guardado en S3 antes del despliegue no trae las claves: deben existir (vacías) antes de correr los agentes."""
    vistos = {}

    class Espia(Camino):
        async def __call__(self, agente, ctx_, label):
            vistos[label] = (ctx_.session.state.get("normativa_adaptador"), ctx_.session.state.get("normativa_evaluacion"))
            async for e in super().__call__(agente, ctx_, label):
                yield e

    c = ctx(perfil_paci="P", planificacion_adaptada="PL")
    with patch("agent._run_with_timeout", Espia()):
        await correr(ag.PaciWorkflowAgent(phase="b"), c)
    assert vistos["Agente 3 (it.1)"][0] == "" and vistos["Agente 3 (it.1)"][1] is not None


@pytest.mark.asyncio
async def test_fase_all_tambien_inyecta_ambas(parches):
    camino, c = Camino(), ctx()
    with patch("agent._run_with_timeout", camino), patch("agent._hitl_checkpoint", new=AsyncMock(return_value=(True, "", 0))), \
         patch("agent.normativa_para_adaptador", new=AsyncMock(return_value="A")), \
         patch("agent.normativa_para_evaluacion", new=AsyncMock(return_value="B")):
        await correr(ag.PaciWorkflowAgent(), c)
    assert c.session.state["normativa_adaptador"] == "A" and c.session.state["normativa_evaluacion"] == "B"


def test_las_claves_del_rag_viajan_entre_fases():
    from api import phase_store
    assert {"normativa_adaptador", "normativa_evaluacion"} <= set(phase_store.STATE_KEYS)


def test_los_prompts_usan_los_placeholders_del_rag():
    from agents.adaptador import make_adaptador_agent
    from agents.generador_rubrica import make_generador_rubrica_agent
    assert "{normativa_adaptador}" in make_adaptador_agent().instruction
    assert "{normativa_evaluacion}" in make_generador_rubrica_agent().instruction
