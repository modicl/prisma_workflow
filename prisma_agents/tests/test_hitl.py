"""Tests para la función _classify_response() del módulo agent."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch


def _make_mock_response(text: str):
    mock = MagicMock()
    mock.text = text
    return mock


class TestClassifyResponse:
    """Tests para clasificación LLM de respuesta del profesor."""

    def test_respuesta_aprobada_devuelve_true(self):
        from agent import _classify_response
        with patch("agent._get_genai_client") as mock_get:
            mock_get.return_value.models.generate_content.return_value = _make_mock_response("APROBADO")
            assert _classify_response("si, está bien") is True

    def test_respuesta_rechazada_devuelve_false(self):
        from agent import _classify_response
        with patch("agent._get_genai_client") as mock_get:
            mock_get.return_value.models.generate_content.return_value = _make_mock_response("RECHAZADO")
            assert _classify_response("no me parece correcto") is False

    def test_respuesta_con_espacios_y_minusculas(self):
        from agent import _classify_response
        with patch("agent._get_genai_client") as mock_get:
            mock_get.return_value.models.generate_content.return_value = _make_mock_response("  aprobado  ")
            assert _classify_response("dale") is True

    def test_respuesta_inesperada_del_llm_devuelve_false(self):
        """Si el LLM no devuelve APROBADO ni RECHAZADO, se trata como rechazo."""
        from agent import _classify_response
        with patch("agent._get_genai_client") as mock_get:
            mock_get.return_value.models.generate_content.return_value = _make_mock_response("No sé")
            assert _classify_response("quizás") is False


class TestHitlCheckpoint:
    """Tests para la función _hitl_checkpoint()."""

    def _make_state(self, perfil="Diagnóstico: TDAH. Estrategias: tiempo extendido.", plan="Adecuación: NO SIGNIFICATIVA."):
        return {"perfil_paci": perfil, "planificacion_adaptada": plan}

    def test_aprobacion_devuelve_true(self):
        from agent import _hitl_checkpoint
        state = self._make_state()
        with patch("builtins.input", return_value="si, todo bien"), \
             patch("agent._classify_response", return_value=True):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=1, max_attempts=6)
        assert aprobado is True
        assert razon == ""
        assert agente == 0

    def test_rechazo_elige_agente_1(self):
        from agent import _hitl_checkpoint
        state = self._make_state()
        with patch("builtins.input", side_effect=["no, el análisis está incompleto", "1"]), \
             patch("agent._classify_response", return_value=False):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=1, max_attempts=6)
        assert aprobado is False
        assert razon == "no, el análisis está incompleto"
        assert agente == 1

    def test_rechazo_elige_agente_2(self):
        from agent import _hitl_checkpoint
        state = self._make_state()
        with patch("builtins.input", side_effect=["la adaptación no aplica DUA", "2"]), \
             patch("agent._classify_response", return_value=False):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=1, max_attempts=6)
        assert aprobado is False
        assert razon == "la adaptación no aplica DUA"
        assert agente == 2

    def test_entrada_invalida_de_agente_repregunta(self):
        from agent import _hitl_checkpoint
        state = self._make_state()
        # Primera respuesta inválida ("3"), segunda válida ("2")
        with patch("builtins.input", side_effect=["no está bien", "3", "2"]), \
             patch("agent._classify_response", return_value=False):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=1, max_attempts=6)
        assert agente == 2

    def test_ultimo_intento_rechazado_retorna_agente_0(self):
        """En el último intento, si rechaza no se pregunta por agente — se cancela."""
        from agent import _hitl_checkpoint
        state = self._make_state()
        with patch("builtins.input", return_value="no, sigue mal"), \
             patch("agent._classify_response", return_value=False):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=6, max_attempts=6)
        assert aprobado is False
        assert agente == 0


import asyncio
import pytest


class TestHitlLoop:
    """Tests de integración para el loop HITL en PaciWorkflowAgent."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _make_ctx(self, state: dict):
        """Crea un InvocationContext mock con session.state."""
        ctx = MagicMock()
        ctx.session.state = state
        return ctx

    def _make_state(self):
        return {
            "paci_document": "doc paci",
            "material_document": "material",
            "critica_previa": "",
            "hitl_feedback_a1": "",
            "hitl_feedback_a2": "",
            "perfil_paci": "Diagnóstico: TDAH",
            "planificacion_adaptada": "Adecuación NO SIGNIFICATIVA",
            "rubrica": "rubrica generada",
            "evaluacion_critica": '{"acceptable": true, "critique": "", "suggestions": []}',
        }

    def test_aprobacion_directa_no_cancela(self):
        """Si el profesor aprueba en el primer intento, status no es hitl_rejected."""
        from agent import PaciWorkflowAgent

        async def empty_async_gen(*args, **kwargs):
            return
            yield

        state = self._make_state()
        ctx = self._make_ctx(state)
        agent = PaciWorkflowAgent()

        async def run():
            with patch("agent._hitl_checkpoint", return_value=(True, "", 0)), \
                 patch("agent._run_with_timeout", side_effect=empty_async_gen):
                async for _ in agent._run_async_impl(ctx):
                    pass

        self._run(run())
        assert state.get("status") != "hitl_rejected"

    def test_intentos_agotados_cancela_con_hitl_rejected(self):
        """Si se agotan los intentos (agente=0), status queda en hitl_rejected."""
        from agent import PaciWorkflowAgent

        async def empty_async_gen(*args, **kwargs):
            return
            yield

        state = self._make_state()
        ctx = self._make_ctx(state)
        agent = PaciWorkflowAgent()

        async def run():
            # Siempre rechaza con agente=0 (último intento agotado)
            with patch("agent._hitl_checkpoint", return_value=(False, "siempre mal", 0)), \
                 patch("agent._run_with_timeout", side_effect=empty_async_gen):
                async for _ in agent._run_async_impl(ctx):
                    pass

        self._run(run())
        assert state.get("status") == "hitl_rejected"

    def test_rechazo_agente2_reinyecta_feedback_a2(self):
        """Si el profesor rechaza eligiendo agente 2, se inyecta hitl_feedback_a2."""
        from agent import PaciWorkflowAgent

        async def empty_async_gen(*args, **kwargs):
            return
            yield

        state = self._make_state()
        ctx = self._make_ctx(state)
        agent = PaciWorkflowAgent()

        # Primera llamada rechaza con agente 2, segunda aprueba
        hitl_responses = [(False, "falta DUA", 2), (True, "", 0)]
        call_count = {"n": 0}

        def mock_hitl(s, attempt, max_attempts):
            result = hitl_responses[call_count["n"]]
            call_count["n"] += 1
            return result

        async def run():
            with patch("agent._hitl_checkpoint", side_effect=mock_hitl), \
                 patch("agent._run_with_timeout", side_effect=empty_async_gen):
                async for _ in agent._run_async_impl(ctx):
                    pass

        self._run(run())
        assert "falta DUA" in state.get("hitl_feedback_a2", "")
        assert state.get("status") != "hitl_rejected"
