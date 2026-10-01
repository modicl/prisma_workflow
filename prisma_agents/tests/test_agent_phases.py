"""Fases del orquestador: `phase="all"` conserva el comportamiento de siempre; `a` y `b` lo parten sin cambiar la lógica."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import agent as ag
from utils.hitl_feedback import feedback_a2


class Camino:
    """Reemplaza a _run_with_timeout: registra qué agentes corren y simula sus salidas (sin Gemini)."""

    def __init__(self, status_en=None):
        self.labels = []
        self.status_en = status_en or {}

    async def __call__(self, agente, ctx, label):
        self.labels.append(label)
        if label in self.status_en:
            ctx.session.state["status"] = self.status_en[label]
        if label.startswith("Agente 1"):
            ctx.session.state["perfil_paci"] = "PERFIL"
        elif label == "Agente 2":
            ctx.session.state["planificacion_adaptada"] = "PLAN"
        elif label.startswith("Agente 3"):
            ctx.session.state["rubrica"] = "RUBRICA"
        elif label.startswith("Agente Crítico"):
            ctx.session.state["evaluacion_critica"] = {"acceptable": True}
        for _ in ():
            yield _


def ctx(**estado):
    base = {"school_id": "", "prompt_docente": "", "paci_document": "x", "material_document": "y"}
    base.update(estado)
    return SimpleNamespace(session=SimpleNamespace(state=base))


async def correr(agente, c):
    return [e async for e in agente._run_async_impl(c)]


def delta(eventos):
    return eventos[-1].actions.state_delta


SIN_BLOQUEO = SimpleNamespace(blocked=False, code="", reason="")
ACEPTA = SimpleNamespace(action="accept", score=95, warnings=["w"], critical_issues=[], regeneration_instructions="")
REGENERA = SimpleNamespace(action="regenerate", score=10, warnings=[], critical_issues=[], regeneration_instructions="mejora")


@pytest.fixture
def parches():
    with patch("agent.evaluate_paci_compliance", return_value=SIN_BLOQUEO) as comp, \
         patch("agent.interpret_critic_decision", return_value=ACEPTA) as dec, \
         patch("agent.get_reference_materials_async", new=AsyncMock(return_value="")) as libros:
        yield SimpleNamespace(comp=comp, dec=dec, libros=libros)


# ── phase="all": regresión del comportamiento actual ────────────────────────
@pytest.mark.asyncio
async def test_all_corre_el_flujo_completo_como_antes(parches):
    camino, c = Camino(), ctx()
    with patch("agent._run_with_timeout", camino), patch("agent._hitl_checkpoint", new=AsyncMock(return_value=(True, "", 0))):
        await correr(ag.PaciWorkflowAgent(), c)
    assert camino.labels == ["Agente 1", "Agente 2", "Agente 3 (it.1)", "Agente Crítico (it.1)"]
    assert c.session.state["status"] == "success"


@pytest.mark.asyncio
async def test_all_rechazo_del_docente_reintenta_solo_el_agente_2_con_su_feedback(parches):
    camino, c = Camino(), ctx()
    hitl = AsyncMock(side_effect=[(False, "muy largo", 2), (True, "", 0)])
    with patch("agent._run_with_timeout", camino), patch("agent._hitl_checkpoint", new=hitl):
        await correr(ag.PaciWorkflowAgent(), c)
    assert camino.labels[:3] == ["Agente 1", "Agente 2", "Agente 2"]
    assert c.session.state["hitl_feedback_a2"] == feedback_a2("muy largo")


@pytest.mark.asyncio
async def test_all_rechazo_agente_1_reanaliza_y_luego_adapta(parches):
    camino, c = Camino(), ctx()
    hitl = AsyncMock(side_effect=[(False, "x", 1), (True, "", 0)])
    with patch("agent._run_with_timeout", camino), patch("agent._hitl_checkpoint", new=hitl):
        await correr(ag.PaciWorkflowAgent(), c)
    assert camino.labels[:4] == ["Agente 1", "Agente 2", "Agente 1 (retry)", "Agente 2"]


@pytest.mark.asyncio
async def test_all_tres_rechazos_terminan_en_hitl_rejected(parches):
    camino, c = Camino(), ctx()
    hitl = AsyncMock(side_effect=[(False, "x", 2)] * 3)
    with patch("agent._run_with_timeout", camino), patch("agent._hitl_checkpoint", new=hitl):
        await correr(ag.PaciWorkflowAgent(), c)
    assert c.session.state["status"] == "hitl_rejected" and "Agente 3 (it.1)" not in camino.labels


# ── phase="a" ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fase_a_inicial_corre_agente_1_materiales_y_agente_2(parches):
    camino, c = Camino(), ctx(retry_agent=0)
    with patch("agent._run_with_timeout", camino):
        eventos = await correr(ag.PaciWorkflowAgent(phase="a"), c)
    assert camino.labels == ["Agente 1", "Agente 2"]
    assert parches.comp.called                                          # el gate normativo corrió
    assert delta(eventos)["materiales_referencia"] == ""               # viaja en el state_delta final


@pytest.mark.asyncio
async def test_fase_a_pasa_por_los_materiales_cuando_hay_colegio_ramo_y_curso(parches):
    parches.libros.return_value = "TEXTO DE LIBROS"
    camino = Camino()
    c = ctx(retry_agent=0, school_id="colegio_x", prompt_docente="matematica 5basico")
    with patch("agent._run_with_timeout", camino), \
         patch("agent.normalize_subject", return_value="matematica"), patch("agent.normalize_grade", return_value="5basico"):
        eventos = await correr(ag.PaciWorkflowAgent(phase="a"), c)
    assert parches.libros.await_count == 1
    assert "TEXTO DE LIBROS" in delta(eventos)["materiales_referencia"]
    assert "<documento_usuario" in delta(eventos)["materiales_referencia"]     # delimitador anti-injection intacto


@pytest.mark.asyncio
async def test_fase_a_bloqueada_por_normativa_se_detiene_en_el_agente_1(parches):
    parches.comp.return_value = SimpleNamespace(blocked=True, code="paci_vencido", reason="vencido")
    camino, c = Camino(), ctx(retry_agent=0)
    with patch("agent._run_with_timeout", camino):
        eventos = await correr(ag.PaciWorkflowAgent(phase="a"), c)
    assert camino.labels == ["Agente 1"]
    assert delta(eventos)["status"] == "validation_failed" and delta(eventos)["validation_code"] == "paci_vencido"


@pytest.mark.asyncio
async def test_fase_a_timeout_del_agente_1_llega_al_estado_final(parches):
    camino, c = Camino(status_en={"Agente 1": "timeout"}), ctx(retry_agent=0)
    with patch("agent._run_with_timeout", camino):
        eventos = await correr(ag.PaciWorkflowAgent(phase="a"), c)
    assert camino.labels == ["Agente 1"] and delta(eventos)["status"] == "timeout"


@pytest.mark.asyncio
async def test_fase_a_reintento_1_reanaliza_sin_gate_ni_materiales(parches):
    camino, c = Camino(), ctx(retry_agent=1, perfil_paci="P", materiales_referencia="M")
    with patch("agent._run_with_timeout", camino):
        eventos = await correr(ag.PaciWorkflowAgent(phase="a"), c)
    assert camino.labels == ["Agente 1 (retry)", "Agente 2"]
    assert not parches.comp.called and not parches.libros.called
    assert delta(eventos)["materiales_referencia"] == "M"              # se conserva lo ya guardado


@pytest.mark.asyncio
async def test_fase_a_reintento_2_solo_vuelve_a_adaptar(parches):
    camino, c = Camino(), ctx(retry_agent=2)
    with patch("agent._run_with_timeout", camino):
        await correr(ag.PaciWorkflowAgent(phase="a"), c)
    assert camino.labels == ["Agente 2"]


# ── phase="b" ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fase_b_acepta_a_la_primera(parches):
    camino, c = Camino(), ctx(perfil_paci="P", planificacion_adaptada="PL")
    with patch("agent._run_with_timeout", camino):
        eventos = await correr(ag.PaciWorkflowAgent(phase="b"), c)
    assert camino.labels == ["Agente 3 (it.1)", "Agente Crítico (it.1)"]
    assert delta(eventos)["status"] == "success" and delta(eventos)["warnings"] == ["w"]


@pytest.mark.asyncio
async def test_fase_b_regenera_hasta_el_maximo_y_entrega_la_ultima_version(parches):
    parches.dec.return_value = REGENERA
    camino, c = Camino(), ctx(perfil_paci="P", planificacion_adaptada="PL")
    with patch("agent._run_with_timeout", camino):
        eventos = await correr(ag.PaciWorkflowAgent(phase="b"), c)
    assert len(camino.labels) == 6 and camino.labels[-2] == "Agente 3 (it.3)"
    assert delta(eventos)["status"] == "fail"


def test_fase_desconocida_falla():
    with pytest.raises(ValueError, match="fase"):
        ag.PaciWorkflowAgent(phase="z")


def test_existen_las_tres_raices():
    assert ag.root_agent.phase == "all" and ag.root_agent_fase_a.phase == "a" and ag.root_agent_fase_b.phase == "b"
