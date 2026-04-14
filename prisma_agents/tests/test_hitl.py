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
