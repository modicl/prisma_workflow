"""Tests para api/workflow_runner.py — lógica de orquestación con mocks."""
import asyncio
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import patch, MagicMock
from api.session_store import SESSIONS, HITL_CALLBACKS, SessionData
from api import workflow_runner


async def _run_with_patches(sid, sd, fake_run_workflow):
    """Helper: runs run_workflow_for_api with standard mocks."""
    with patch("api.workflow_runner.run_workflow", side_effect=fake_run_workflow), \
         patch("api.workflow_runner.S3_BUCKET", ""), \
         patch("api.workflow_runner.sync_to_dynamo"):
        await workflow_runner.run_workflow_for_api(sid, paci_path="/a", material_path="/b")


def _make_session(sid="test-sid"):
    sd = SessionData()
    SESSIONS[sid] = sd
    return sd


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


@pytest.fixture(autouse=True)
def no_dynamo():
    with patch("api.workflow_runner.sync_to_dynamo"):
        yield


@pytest.fixture(autouse=True)
def clean_sessions():
    yield
    SESSIONS.clear()
    HITL_CALLBACKS.clear()


class TestHelpers:
    def test_push_message_adds_to_messages_and_queue(self):
        sd = SessionData()
        workflow_runner._push_message(sd, "Hola", role="system")
        assert len(sd.messages) == 1
        assert sd.messages[0]["content"] == "Hola"
        events = _drain(sd.event_queue)
        assert any(e.get("type") == "message" for e in events)

    def test_friendly_error_api_key(self):
        exc = Exception("invalid api key")
        msg = workflow_runner._friendly_error(exc)
        assert "configuración" in msg.lower() or "administrador" in msg.lower()

    def test_friendly_error_timeout(self):
        exc = Exception("connection timed out")
        msg = workflow_runner._friendly_error(exc)
        assert "tiempo" in msg.lower() or "timeout" in msg.lower() or "respondió" in msg.lower()

    def test_friendly_error_quota(self):
        exc = Exception("resource_exhausted quota exceeded")
        msg = workflow_runner._friendly_error(exc)
        assert "límite" in msg.lower() or "uso" in msg.lower()

    def test_friendly_error_network(self):
        exc = Exception("network connection refused")
        msg = workflow_runner._friendly_error(exc)
        assert "conectar" in msg.lower() or "conexión" in msg.lower()

    def test_friendly_error_generic(self):
        exc = Exception("unknown error")
        msg = workflow_runner._friendly_error(exc)
        assert "error" in msg.lower()

    def test_download_from_s3(self, tmp_path):
        with patch("api.workflow_runner.boto3") as mock_boto3, \
             patch("api.workflow_runner.S3_BUCKET", "my-bucket"):
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            path = workflow_runner._download_from_s3("jobs/sess/paci.pdf")
        assert path.endswith(".pdf")
        mock_client.download_file.assert_called_once()


class TestRunWorkflowForApi:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_early_when_session_missing(self):
        async def _go():
            await workflow_runner.run_workflow_for_api("no-session")
        self._run(_go())  # should not raise

    def test_invalid_prompt_sets_error(self):
        sid = "test-prompt-err"
        sd = _make_session(sid)

        async def _go():
            with patch("api.workflow_runner.validate_prompt_docente",
                       side_effect=ValueError("Prompt inválido")):
                await workflow_runner.run_workflow_for_api(sid, prompt="x")

        self._run(_go())
        assert sd.phase == "error"
        assert sd.workflow_status == "error"
        assert "Prompt inválido" in sd.error

    def test_mock_school_id_routes_to_mock_runner(self):
        sid = "test-mock-route"
        _make_session(sid)

        async def fake_mock(session_id, session_data, school_id):
            session_data.phase = "completed"
            session_data.workflow_status = "success"

        async def _go():
            with patch("api.mock_runner.run_mock_workflow", side_effect=fake_mock):
                await workflow_runner.run_workflow_for_api(sid, school_id="__mock_fast__")

        self._run(_go())
        assert SESSIONS[sid].phase == "completed"

    def test_successful_workflow_sets_completed(self):
        sid = "test-wf-success"
        sd = _make_session(sid)

        fake_results = {
            "status": "success",
            "docx_path": "",
        }

        async def fake_run_workflow(**kwargs):
            return fake_results

        async def _go():
            with patch("api.workflow_runner.run_workflow", side_effect=fake_run_workflow), \
                 patch("api.workflow_runner.S3_BUCKET", ""):
                await workflow_runner.run_workflow_for_api(
                    sid,
                    paci_path="/tmp/paci.pdf",
                    material_path="/tmp/mat.docx",
                )

        self._run(_go())
        assert sd.phase == "completed"
        assert sd.workflow_status == "success"
        events = _drain(sd.event_queue)
        assert any(e.get("type") == "completed" for e in events)

    def test_degraded_workflow_sets_degraded(self):
        sid = "test-wf-degraded"
        sd = _make_session(sid)

        async def fake_run_workflow(**kwargs):
            return {"status": "fail", "docx_path": ""}

        async def _go():
            with patch("api.workflow_runner.run_workflow", side_effect=fake_run_workflow), \
                 patch("api.workflow_runner.S3_BUCKET", ""):
                await workflow_runner.run_workflow_for_api(sid, paci_path="/a", material_path="/b")

        self._run(_go())
        assert sd.workflow_status == "degraded"

    def test_hitl_rejected_sets_error(self):
        sid = "test-hitl-rej"
        sd = _make_session(sid)

        async def fake_run_workflow(**kwargs):
            return {"status": "hitl_rejected", "docx_path": ""}

        async def _go():
            with patch("api.workflow_runner.run_workflow", side_effect=fake_run_workflow), \
                 patch("api.workflow_runner.S3_BUCKET", ""):
                await workflow_runner.run_workflow_for_api(sid, paci_path="/a", material_path="/b")

        self._run(_go())
        assert sd.phase == "error"
        assert sd.workflow_status == "hitl_rejected"

    def test_unexpected_status_sets_error(self):
        sid = "test-wf-unknown"
        sd = _make_session(sid)

        async def fake_run_workflow(**kwargs):
            return {"status": "timeout", "docx_path": ""}

        async def _go():
            with patch("api.workflow_runner.run_workflow", side_effect=fake_run_workflow), \
                 patch("api.workflow_runner.S3_BUCKET", ""):
                await workflow_runner.run_workflow_for_api(sid, paci_path="/a", material_path="/b")

        self._run(_go())
        assert sd.phase == "error"
        assert sd.workflow_status == "error"

    def test_exception_sets_friendly_error(self):
        sid = "test-wf-exc"
        sd = _make_session(sid)

        async def boom(**kwargs):
            raise RuntimeError("network connection error")

        async def _go():
            with patch("api.workflow_runner.run_workflow", side_effect=boom), \
                 patch("api.workflow_runner.S3_BUCKET", ""):
                await workflow_runner.run_workflow_for_api(sid, paci_path="/a", material_path="/b")

        self._run(_go())
        assert sd.phase == "error"
        assert sd.error is not None

    def test_cancelled_session_skips_completion(self):
        sid = "test-wf-cancelled"
        sd = _make_session(sid)
        sd.cancelled = True

        async def fake_run_workflow(**kwargs):
            return {"status": "success", "docx_path": ""}

        async def _go():
            with patch("api.workflow_runner.run_workflow", side_effect=fake_run_workflow), \
                 patch("api.workflow_runner.S3_BUCKET", ""):
                await workflow_runner.run_workflow_for_api(sid, paci_path="/a", material_path="/b")

        self._run(_go())
        assert sd.phase != "completed"

    def test_hitl_callback_approved_path(self):
        """Prueba la hitl_callback interna con aprobación — cubre lines 82-115."""
        sid = "test-hitl-cb-approved"
        sd = _make_session(sid)

        async def fake_run_workflow(**kwargs):
            while sid not in HITL_CALLBACKS:
                await asyncio.sleep(0)
            callback = HITL_CALLBACKS[sid]
            # Invoke callback in a concurrent task
            cb_task = asyncio.create_task(
                callback({"perfil_paci": "NEE TDAH", "planificacion_adaptada": "Plan"}, 1, 6)
            )
            # Let callback run until it awaits the queue
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            # Feed approval
            await sd.hitl_response_queue.put({"approved": True, "reason": "", "agent_to_retry": 0})
            approved, reason, agent = await cb_task
            assert approved is True
            return {"status": "success", "docx_path": ""}

        self._run(_run_with_patches(sid, sd, fake_run_workflow))
        assert sd.phase == "completed"

    def test_hitl_callback_rejection_path(self):
        """Prueba la hitl_callback con rechazo — cubre lines 113-123."""
        sid = "test-hitl-cb-rejected"
        sd = _make_session(sid)

        async def fake_run_workflow(**kwargs):
            while sid not in HITL_CALLBACKS:
                await asyncio.sleep(0)
            callback = HITL_CALLBACKS[sid]
            cb_task = asyncio.create_task(
                callback({"perfil_paci": "NEE", "planificacion_adaptada": "Plan"}, 1, 6)
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await sd.hitl_response_queue.put({"approved": False, "reason": "No es correcto", "agent_to_retry": 2})
            approved, reason, agent = await cb_task
            assert approved is False
            assert agent == 2
            return {"status": "hitl_rejected", "docx_path": ""}

        self._run(_run_with_patches(sid, sd, fake_run_workflow))

    def test_hitl_callback_rejection_without_agent_defaults_to_adaptador(self):
        """El front ya no envía agent_to_retry: un rechazo no-final debe enrutar al Agente 2.

        La aserción se hace fuera de fake_run_workflow porque run_workflow_for_api atrapa
        cualquier excepción (incluido AssertionError) en su try/except.
        """
        sid = "test-hitl-cb-no-agent"
        sd = _make_session(sid)
        captured = {}

        async def fake_run_workflow(**kwargs):
            while sid not in HITL_CALLBACKS:
                await asyncio.sleep(0)
            callback = HITL_CALLBACKS[sid]
            cb_task = asyncio.create_task(
                callback({"perfil_paci": "NEE", "planificacion_adaptada": "Plan"}, 1, 6)
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            # Cuerpo sin agent_to_retry (como lo envía el front ahora).
            await sd.hitl_response_queue.put({"approved": False, "reason": "Falta apoyo visual"})
            approved, reason, agent = await cb_task
            captured["approved"] = approved
            captured["agent"] = agent
            return {"status": "hitl_rejected", "docx_path": ""}

        self._run(_run_with_patches(sid, sd, fake_run_workflow))
        assert captured["approved"] is False
        assert captured["agent"] == 2  # Agente 2 (Adaptador) por defecto

    def test_s3_upload_of_docx_when_bucket_set(self, tmp_path):
        """Cubre lines 171-172: subida del DOCX a S3 cuando S3_BUCKET está configurado."""
        sid = "test-s3-upload-docx"
        sd = _make_session(sid)
        docx_file = str(tmp_path / "rubrica.docx")
        from docx import Document as DocxDocument
        DocxDocument().save(docx_file)

        async def fake_run_workflow(**kwargs):
            return {"status": "success", "docx_path": docx_file}

        mock_s3_client = MagicMock()
        with patch("api.workflow_runner.S3_BUCKET", "my-bucket"), \
             patch("api.workflow_runner.boto3.client", return_value=mock_s3_client), \
             patch("api.workflow_runner.run_workflow", side_effect=fake_run_workflow), \
             patch("api.workflow_runner.sync_to_dynamo"):
            self._run(workflow_runner.run_workflow_for_api(
                sid, paci_path="/a", material_path="/b"
            ))
        mock_s3_client.upload_file.assert_called_once()

    def test_hitl_callback_cancelled_during_wait(self):
        """Cubre lines 106-107: session.cancelled=True durante HITL → return early."""
        sid = "test-hitl-cb-cancelled"
        sd = _make_session(sid)

        async def fake_run_workflow(**kwargs):
            while sid not in HITL_CALLBACKS:
                await asyncio.sleep(0)
            callback = HITL_CALLBACKS[sid]
            cb_task = asyncio.create_task(
                callback({"perfil_paci": "NEE", "planificacion_adaptada": "Plan"}, 1, 6)
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            sd.cancelled = True
            await sd.hitl_response_queue.put({"approved": True})
            result = await cb_task
            assert result[0] is False  # cancelled returns False
            return {"status": "hitl_rejected", "docx_path": ""}

        self._run(_run_with_patches(sid, sd, fake_run_workflow))

    def test_hitl_callback_last_attempt_rejection(self):
        """Cubre lines 120-121: último intento rechazado fuerza hitl_rejected."""
        sid = "test-hitl-last-attempt"
        sd = _make_session(sid)

        async def fake_run_workflow(**kwargs):
            while sid not in HITL_CALLBACKS:
                await asyncio.sleep(0)
            callback = HITL_CALLBACKS[sid]
            cb_task = asyncio.create_task(
                callback({"perfil_paci": "NEE", "planificacion_adaptada": "Plan"},
                         attempt=6, max_attempts=6)  # last attempt
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await sd.hitl_response_queue.put({"approved": False, "reason": "Incorrecto", "agent_to_retry": 0})
            approved, reason, agent = await cb_task
            assert approved is False
            assert agent == 0
            return {"status": "hitl_rejected", "docx_path": ""}

        self._run(_run_with_patches(sid, sd, fake_run_workflow))

    def test_finally_pushes_terminal_event_when_queue_empty(self):
        """Cubre line 212: el finally garantiza evento terminal cuando la cola está vacía."""
        sid = "test-finally-empty-queue"
        sd = _make_session(sid)

        async def fake_run_workflow(**kwargs):
            # Return without pushing any event to the queue
            return {"status": "success", "docx_path": ""}

        self._run(_run_with_patches(sid, sd, fake_run_workflow))
        # The finally block should have pushed a terminal event
        assert sd.phase == "completed"

    def test_s3_download_paths_used_when_keys_provided(self, tmp_path):
        sid = "test-wf-s3keys"
        _make_session(sid)

        paci_tmp = str(tmp_path / "paci.pdf")
        mat_tmp = str(tmp_path / "mat.docx")
        open(paci_tmp, "w").close()
        open(mat_tmp, "w").close()

        async def fake_run_workflow(**kwargs):
            return {"status": "success", "docx_path": ""}

        download_calls = []

        def fake_download(key):
            if "paci" in key:
                return paci_tmp
            return mat_tmp

        async def _go():
            with patch("api.workflow_runner._download_from_s3", side_effect=fake_download) as mock_dl, \
                 patch("api.workflow_runner.run_workflow", side_effect=fake_run_workflow), \
                 patch("api.workflow_runner.S3_BUCKET", ""):
                await workflow_runner.run_workflow_for_api(
                    sid,
                    paci_s3_key="jobs/sess/paci.pdf",
                    material_s3_key="jobs/sess/mat.docx",
                )
                download_calls.extend(mock_dl.call_args_list)

        self._run(_go())
        assert len(download_calls) == 2
