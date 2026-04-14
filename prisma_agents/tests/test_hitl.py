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
