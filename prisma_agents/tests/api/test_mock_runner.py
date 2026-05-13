"""Tests para api/mock_runner.py — escenarios de simulación sin LLM."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import patch
from api.session_store import SessionData
from api import mock_runner


def _make_session():
    return SessionData()


def _drain_queue(q):
    """Drena todos los items de una asyncio.Queue y los devuelve como lista."""
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


class TestPush:
    def test_push_adds_message_and_event(self):
        sd = _make_session()
        mock_runner._push(sd, "Hola docente", role="system")
        assert len(sd.messages) == 1
        assert sd.messages[0]["content"] == "Hola docente"
        assert sd.messages[0]["role"] == "system"
        events = _drain_queue(sd.event_queue)
        assert len(events) == 1
        assert events[0]["type"] == "message"
        assert events[0]["content"] == "Hola docente"


class TestMakePlaceholderDocx:
    def test_creates_valid_docx_file(self):
        path = mock_runner._make_placeholder_docx()
        assert os.path.exists(path)
        assert path.endswith(".docx")
        from docx import Document
        doc = Document(path)
        full_text = " ".join(p.text for p in doc.paragraphs)
        assert "simulador" in full_text.lower() or "rúbrica" in full_text.lower() or "prisma" in full_text.lower()
        os.unlink(path)


class TestRunMockWorkflow:
    """Tests de los escenarios de mock sin esperas reales (STEP_DELAY → 0)."""

    def _run(self, coro):
        return asyncio.run(coro)

    @pytest.fixture(autouse=True)
    def zero_delay(self):
        with patch.object(mock_runner, "STEP_DELAY", 0):
            yield

    @pytest.fixture(autouse=True)
    def no_dynamo(self):
        with patch("api.mock_runner.sync_to_dynamo"):
            yield

    def test_fast_scenario_completes_success(self):
        sd = _make_session()
        self._run(mock_runner.run_mock_workflow("sid-fast", sd, "__mock_fast__"))
        assert sd.phase == "completed"
        assert sd.workflow_status == "success"
        assert sd.docx_path is not None
        assert os.path.exists(sd.docx_path)
        os.unlink(sd.docx_path)

    def test_fast_scenario_pushes_agent_events(self):
        sd = _make_session()
        self._run(mock_runner.run_mock_workflow("sid-fast2", sd, "__mock_fast__"))
        events = _drain_queue(sd.event_queue)
        types = [e["type"] for e in events]
        assert "agent_start" in types
        assert "completed" in types

    def test_degraded_scenario_completes_as_degraded(self):
        sd = _make_session()
        self._run(mock_runner.run_mock_workflow("sid-deg", sd, "__mock_degraded__"))
        assert sd.phase == "completed"
        assert sd.workflow_status == "degraded"
        assert sd.docx_path is not None
        os.unlink(sd.docx_path)

    def test_error_scenario_sets_error_phase(self):
        sd = _make_session()
        self._run(mock_runner.run_mock_workflow("sid-err", sd, "__mock_error__"))
        assert sd.phase == "error"
        assert sd.workflow_status == "error"
        assert sd.error is not None
        events = _drain_queue(sd.event_queue)
        assert any(e["type"] == "error" for e in events)

    def test_success_scenario_with_hitl_approval(self):
        sd = _make_session()

        async def _run_with_approval():
            task = asyncio.create_task(
                mock_runner.run_mock_workflow("sid-ok", sd, "__mock_success__")
            )
            # Esperar hasta que el workflow pida HITL
            while sd.phase != "awaiting_hitl":
                await asyncio.sleep(0)
            # Aprobar
            await sd.hitl_response_queue.put({"approved": True})
            await task

        asyncio.run(_run_with_approval())
        assert sd.phase == "completed"
        assert sd.workflow_status == "success"
        assert sd.docx_path is not None
        os.unlink(sd.docx_path)

    def test_success_scenario_with_hitl_rejection(self):
        sd = _make_session()

        async def _run_with_rejection():
            task = asyncio.create_task(
                mock_runner.run_mock_workflow("sid-rej", sd, "__mock_success__")
            )
            while sd.phase != "awaiting_hitl":
                await asyncio.sleep(0)
            await sd.hitl_response_queue.put({"approved": False})
            await task

        asyncio.run(_run_with_rejection())
        assert sd.phase == "error"
        assert sd.workflow_status == "hitl_rejected"

    def test_unknown_scenario_defaults_to_success(self):
        sd = _make_session()

        async def _run_with_approval():
            task = asyncio.create_task(
                mock_runner.run_mock_workflow("sid-unk", sd, "__mock_unknown__")
            )
            while sd.phase != "awaiting_hitl":
                await asyncio.sleep(0)
            await sd.hitl_response_queue.put({"approved": True})
            await task

        asyncio.run(_run_with_approval())
        assert sd.phase == "completed"
        os.unlink(sd.docx_path)
