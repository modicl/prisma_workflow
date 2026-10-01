"""Enganches del monitor en agent._run_with_timeout: eventos de agente, reintentos y tokens."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.genai.errors import ServerError

import agent as agent_mod


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, event_type, session_id, **fields):
        self.calls.append((event_type, session_id, fields))

    def types(self):
        return [c[0] for c in self.calls]

    def of(self, event_type):
        return [c[2] for c in self.calls if c[0] == event_type]


def make_ctx(session_id="sesion-1"):
    state = {"api_session_id": session_id} if session_id else {}
    return SimpleNamespace(session=SimpleNamespace(state=state))


def usage_event(prompt=1000, cand=100, thoughts=0, cached=0, tool=0, partial=False, model="gemini-x"):
    um = SimpleNamespace(prompt_token_count=prompt, candidates_token_count=cand, thoughts_token_count=thoughts,
                         cached_content_token_count=cached, tool_use_prompt_token_count=tool)
    return SimpleNamespace(usage_metadata=um, partial=partial, model_version=model, author="X")


class FakeAgent:
    """Agente ADK falso: cada elemento de `script` es un evento o una excepción a lanzar."""

    def __init__(self, *runs, model="gemini-agent-model"):
        self.runs = list(runs)
        self.model = model

    async def run_async(self, ctx):
        run = self.runs.pop(0) if self.runs else []
        for item in run:
            if isinstance(item, BaseException):
                raise item
            yield item


async def consumir(agent, ctx, label="Agente 1"):
    return [e async for e in agent_mod._run_with_timeout(agent, ctx, label)]


@pytest.fixture
def rec():
    r = Recorder()
    with patch("api.event_publisher.publish", r):
        yield r


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(agent_mod, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(agent_mod, "_503_RETRY_DELAYS", [0, 0])


@pytest.mark.asyncio
async def test_agente_exitoso_publica_started_usage_y_finished_en_orden(rec):
    await consumir(FakeAgent([usage_event(prompt=1000, cand=100, thoughts=30, cached=400)]), make_ctx())
    assert rec.types() == ["agent_started", "llm_usage", "agent_finished"]
    assert rec.calls[0][1] == "sesion-1"
    assert rec.of("agent_started")[0] == {"agent": "Agente 1"}
    assert rec.of("llm_usage")[0] == {"agent": "Agente 1", "model": "gemini-x", "input_tokens": 1000,
                                       "cached_tokens": 400, "output_tokens": 100, "thoughts_tokens": 30}
    fin = rec.of("agent_finished")[0]
    assert fin["ok"] is True and isinstance(fin["duration_ms"], int) and fin["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_los_eventos_del_agente_siguen_fluyendo_intactos(rec):
    e1, e2 = usage_event(), usage_event(prompt=5)
    salida = await consumir(FakeAgent([e1, e2]), make_ctx())
    assert salida == [e1, e2]


@pytest.mark.asyncio
async def test_eventos_parciales_o_sin_uso_no_generan_llm_usage(rec):
    sin_uso = SimpleNamespace(usage_metadata=None, partial=False, model_version=None, author="X")
    await consumir(FakeAgent([usage_event(partial=True), sin_uso, usage_event(prompt=0, cand=0)]), make_ctx())
    assert "llm_usage" not in rec.types()


@pytest.mark.asyncio
async def test_tokens_de_herramientas_cuentan_como_entrada(rec):
    await consumir(FakeAgent([usage_event(prompt=100, tool=25)]), make_ctx())
    assert rec.of("llm_usage")[0]["input_tokens"] == 125


@pytest.mark.asyncio
async def test_modelo_cae_al_del_agente_si_el_evento_no_lo_trae(rec):
    await consumir(FakeAgent([usage_event(model=None)], model="gemini-del-agente"), make_ctx())
    assert rec.of("llm_usage")[0]["model"] == "gemini-del-agente"


@pytest.mark.asyncio
async def test_en_modo_cli_sin_api_session_id_no_publica_nada(rec):
    await consumir(FakeAgent([usage_event()]), make_ctx(session_id=None))
    assert rec.calls == []


@pytest.mark.asyncio
async def test_timeout_publica_retry_y_luego_finaliza_ok(rec):
    await consumir(FakeAgent([TimeoutError()], [usage_event()]), make_ctx())
    assert rec.types() == ["agent_started", "agent_retry", "llm_usage", "agent_finished"]
    assert rec.of("agent_retry")[0] == {"agent": "Agente 1", "attempt": 1, "reason": "timeout"}
    assert rec.of("agent_finished")[0]["ok"] is True


@pytest.mark.asyncio
async def test_timeouts_agotados_terminan_con_ok_false_y_status_timeout(rec):
    ctx = make_ctx()
    await consumir(FakeAgent([TimeoutError()], [TimeoutError()], [TimeoutError()]), ctx)
    assert rec.types() == ["agent_started", "agent_retry", "agent_retry", "agent_finished"]
    assert rec.of("agent_finished")[0]["ok"] is False
    assert ctx.session.state["status"] == "timeout"       # comportamiento previo intacto


@pytest.mark.asyncio
async def test_503_publica_retry_con_reason_503(rec):
    err = ServerError(503, {"error": {"message": "sobrecarga", "status": "UNAVAILABLE"}})
    await consumir(FakeAgent([err], [usage_event()]), make_ctx())
    assert rec.of("agent_retry")[0] == {"agent": "Agente 1", "attempt": 1, "reason": "503"}
    assert rec.of("agent_finished")[0]["ok"] is True


@pytest.mark.asyncio
async def test_otro_error_del_servidor_se_propaga_como_antes(rec):
    err = ServerError(500, {"error": {"message": "interno", "status": "INTERNAL"}})
    with pytest.raises(ServerError):
        await consumir(FakeAgent([err]), make_ctx())


@pytest.mark.asyncio
async def test_si_el_publicador_explota_el_agente_sigue_igual():
    def roto(*a, **k):
        raise RuntimeError("kafka")

    with patch("api.event_publisher.publish", roto):
        salida = await consumir(FakeAgent([usage_event()]), make_ctx())
    assert len(salida) == 1
