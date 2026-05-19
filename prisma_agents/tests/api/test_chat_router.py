import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from api.main import app
from api.auth import get_current_user
from api.session_store import SESSIONS, SessionData

_FAKE_USER = {"sub": "test-user-id", "email": "docente@test.com"}

app.dependency_overrides[get_current_user] = lambda: _FAKE_USER

client = TestClient(app)

# Disable DynamoDB for all tests — unit tests must not require AWS connectivity
pytestmark = pytest.mark.usefixtures("no_dynamo")

@pytest.fixture(autouse=True)
def no_dynamo():
    with patch("api.dynamo_store.enabled", return_value=False):
        yield


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_get_state_not_found():
    res = client.get("/chat/no-existe/state")
    assert res.status_code == 404


def test_get_state_returns_session():
    sid = "test-get-state-001"
    SESSIONS[sid] = SessionData()
    SESSIONS[sid].messages = [{"role": "system", "content": "Hola"}]
    res = client.get(f"/chat/{sid}/state")
    assert res.status_code == 200
    data = res.json()
    assert data["phase"] == "running"
    assert data["messages"][0]["content"] == "Hola"
    assert data["hitl_data"] is None
    del SESSIONS[sid]


def test_hitl_respond_session_not_found():
    res = client.post("/chat/no-existe/hitl", json={"approved": True})
    assert res.status_code == 404


def test_hitl_respond_wrong_phase():
    sid = "test-hitl-wrong-phase-002"
    SESSIONS[sid] = SessionData()  # phase = "running", not "awaiting_hitl"
    res = client.post(f"/chat/{sid}/hitl", json={"approved": True})
    assert res.status_code == 409
    del SESSIONS[sid]


def test_download_not_ready():
    sid = "test-download-not-ready-003"
    SESSIONS[sid] = SessionData()  # phase = "running", no docx
    res = client.get(f"/chat/{sid}/download")
    assert res.status_code == 404
    del SESSIONS[sid]


def test_download_session_not_found():
    res = client.get("/chat/no-existe-download/download")
    assert res.status_code == 404


def test_download_completed_returns_file(tmp_path):
    from docx import Document as DocxDocument
    docx_file = tmp_path / "rubrica.docx"
    d = DocxDocument()
    d.add_paragraph("Test")
    d.save(str(docx_file))

    sid = "test-download-ready-004"
    sd = SessionData()
    sd.phase = "completed"
    sd.docx_path = str(docx_file)
    SESSIONS[sid] = sd
    res = client.get(f"/chat/{sid}/download")
    assert res.status_code == 200
    del SESSIONS[sid]


def test_hitl_respond_ok():
    import asyncio
    sid = "test-hitl-ok-005"
    sd = SessionData()
    sd.phase = "awaiting_hitl"
    SESSIONS[sid] = sd

    # Drain the queue in background so the put doesn't block
    async def drain():
        return await sd.hitl_response_queue.get()

    res = client.post(f"/chat/{sid}/hitl", json={"approved": True, "reason": None})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    del SESSIONS[sid]


def test_cancel_session_not_found():
    res = client.post("/chat/no-existe-cancel/cancel")
    assert res.status_code == 404


def test_cancel_already_finished():
    sid = "test-cancel-done-006"
    sd = SessionData()
    sd.phase = "completed"
    SESSIONS[sid] = sd
    res = client.post(f"/chat/{sid}/cancel")
    assert res.status_code == 409
    del SESSIONS[sid]


def test_cancel_running_session():
    sid = "test-cancel-running-007"
    sd = SessionData()
    sd.phase = "running"
    sd.task = None
    SESSIONS[sid] = sd
    res = client.post(f"/chat/{sid}/cancel")
    assert res.status_code == 200
    assert SESSIONS[sid].phase == "error"
    assert SESSIONS[sid].workflow_status == "cancelled"
    del SESSIONS[sid]


def test_internal_run_missing_token():
    with patch("api.chat_router.INTERNAL_TOKEN", "secret"), \
         patch("api.dynamo_store.enabled", return_value=True), \
         patch("api.dynamo_store.get_session", return_value=None):
        res = client.post("/chat/internal/run/sess-001",
                          headers={"X-Internal-Token": "wrong"})
    assert res.status_code == 401


def test_internal_run_session_not_found_in_dynamo():
    with patch("api.chat_router.INTERNAL_TOKEN", ""), \
         patch("api.dynamo_store.get_session", return_value=None):
        res = client.post("/chat/internal/run/sess-missing")
    assert res.status_code == 404


def test_internal_run_starts_workflow():
    import asyncio
    sid = "test-internal-run-008"
    dynamo_item = {
        "paci_s3_key": "jobs/sess/paci.pdf",
        "material_s3_key": "jobs/sess/mat.docx",
        "prompt": "",
        "school_id": "colegio_demo",
    }

    async def fake_workflow(*args, **kwargs):
        pass

    with patch("api.chat_router.INTERNAL_TOKEN", ""), \
         patch("api.dynamo_store.get_session", return_value=dynamo_item), \
         patch("api.chat_router.run_workflow_for_api", side_effect=fake_workflow):
        res = client.post(f"/chat/internal/run/{sid}")

    assert res.status_code == 200
    assert res.json()["started"] is True
    SESSIONS.pop(sid, None)


# ── /start endpoint ───────────────────────────────────────────────────────────

def test_start_local_creates_session():
    from io import BytesIO
    with patch("api.chat_router.S3_BUCKET", ""), \
         patch("api.chat_router.run_workflow_for_api", return_value=None):
        res = client.post(
            "/chat/start",
            files={
                "paci_file": ("paci.pdf", BytesIO(b"%PDF content"), "application/pdf"),
                "material_file": ("mat.docx", BytesIO(b"PK content"), "application/vnd.openxmlformats"),
            },
            data={"prompt": "", "school_id": "colegio_demo"},
        )
    assert res.status_code == 201
    sid = res.json()["session_id"]
    assert sid
    SESSIONS.pop(sid, None)


# ── SSE /stream endpoint ──────────────────────────────────────────────────────

def test_stream_session_not_found():
    with patch("api.dynamo_store.enabled", return_value=False):
        res = client.get("/chat/nonexistent-stream/stream",
                         params={"token": "fake"})
    assert res.status_code == 404


def test_stream_already_completed_session():
    import json
    sid = "test-stream-completed-009"
    sd = SessionData()
    sd.phase = "completed"
    sd.workflow_status = "success"
    SESSIONS[sid] = sd

    with patch("api.dynamo_store.enabled", return_value=False):
        res = client.get(f"/chat/{sid}/stream", params={"token": "fake"})

    assert res.status_code == 200
    lines = [l for l in res.text.split("\n") if l.startswith("data:")]
    assert len(lines) >= 1
    event = json.loads(lines[0][5:])
    assert event.get("type") == "completed"
    del SESSIONS[sid]


def test_stream_already_error_session():
    import json
    sid = "test-stream-error-010"
    sd = SessionData()
    sd.phase = "error"
    sd.workflow_status = "error"
    sd.error = "Algo salió mal"
    SESSIONS[sid] = sd

    with patch("api.dynamo_store.enabled", return_value=False):
        res = client.get(f"/chat/{sid}/stream", params={"token": "fake"})

    assert res.status_code == 200
    lines = [l for l in res.text.split("\n") if l.startswith("data:")]
    assert any("error" in l for l in lines)
    del SESSIONS[sid]


def test_stream_running_session_with_queued_event():
    import json
    sid = "test-stream-running-011"
    sd = SessionData()
    sd.phase = "running"
    sd.event_queue.put_nowait({"type": "message", "content": "Procesando..."})
    sd.event_queue.put_nowait({"type": "completed", "workflow_status": "success"})
    SESSIONS[sid] = sd

    with patch("api.dynamo_store.enabled", return_value=False):
        res = client.get(f"/chat/{sid}/stream", params={"token": "fake"})

    assert res.status_code == 200
    lines = [l for l in res.text.split("\n") if l.startswith("data:")]
    types = [json.loads(l[5:]).get("type") for l in lines]
    assert "message" in types
    assert "completed" in types
    del SESSIONS[sid]


# ── DynamoDB paths ────────────────────────────────────────────────────────────

def test_get_state_from_dynamo():
    dynamo_item = {
        "phase": "completed",
        "messages": [{"role": "system", "content": "ok"}],
        "hitl_data": None,
        "error": None,
        "workflow_status": "success",
    }
    with patch("api.dynamo_store.enabled", return_value=True), \
         patch("api.dynamo_store.get_session", return_value=dynamo_item):
        res = client.get("/chat/dynamo-sess/state")
    assert res.status_code == 200
    assert res.json()["phase"] == "completed"


def test_get_state_from_dynamo_not_found():
    with patch("api.dynamo_store.enabled", return_value=True), \
         patch("api.dynamo_store.get_session", return_value=None):
        res = client.get("/chat/dynamo-missing/state")
    assert res.status_code == 404


def test_download_local_file_missing_on_disk():
    sid = "test-dl-missing-disk"
    sd = SessionData()
    sd.phase = "completed"
    sd.docx_path = "/tmp/nonexistent_file_xyz.docx"
    SESSIONS[sid] = sd
    res = client.get(f"/chat/{sid}/download")
    assert res.status_code == 404
    del SESSIONS[sid]


def test_download_dynamo_presigned_url():
    dynamo_item = {
        "phase": "completed",
        "docx_s3_key": "results/sess/rubrica.docx",
    }
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"
    with patch("api.dynamo_store.enabled", return_value=True), \
         patch("api.dynamo_store.get_session", return_value=dynamo_item), \
         patch("api.chat_router.S3_BUCKET", "my-bucket"), \
         patch("api.chat_router.boto3.client", return_value=mock_s3):
        res = client.get("/chat/sess-s3/download")
    assert res.status_code == 200
    data = res.json()
    assert "url" in data
    assert "presigned" in data["url"]


def test_download_dynamo_not_completed_returns_404():
    dynamo_item = {"phase": "running", "docx_s3_key": ""}
    with patch("api.dynamo_store.enabled", return_value=True), \
         patch("api.dynamo_store.get_session", return_value=dynamo_item):
        res = client.get("/chat/sess-not-done/download")
    assert res.status_code == 404


def test_cancel_running_session_with_task():
    import asyncio
    sid = "test-cancel-with-task-012"
    sd = SessionData()
    sd.phase = "running"
    mock_task = MagicMock()
    mock_task.done.return_value = False
    sd.task = mock_task
    SESSIONS[sid] = sd
    res = client.post(f"/chat/{sid}/cancel")
    assert res.status_code == 200
    mock_task.cancel.assert_called_once()
    del SESSIONS[sid]


def test_start_s3_path_creates_session():
    from io import BytesIO
    mock_s3 = MagicMock()
    with patch("api.chat_router.S3_BUCKET", "my-bucket"), \
         patch("api.chat_router.boto3.client", return_value=mock_s3), \
         patch("api.dynamo_store.create_session"):
        res = client.post(
            "/chat/start",
            files={
                "paci_file": ("paci.pdf", BytesIO(b"%PDF content"), "application/pdf"),
                "material_file": ("mat.docx", BytesIO(b"PK content"), "application/octet-stream"),
            },
            data={"prompt": "", "school_id": "colegio_demo"},
        )
    assert res.status_code == 201
    assert "session_id" in res.json()
    SESSIONS.pop(res.json()["session_id"], None)


def test_start_s3_upload_error_returns_500():
    from io import BytesIO
    mock_s3 = MagicMock()
    mock_s3.put_object.side_effect = Exception("S3 unavailable")
    with patch("api.chat_router.S3_BUCKET", "my-bucket"), \
         patch("api.chat_router.boto3.client", return_value=mock_s3), \
         patch("api.dynamo_store.create_session"):
        res = client.post(
            "/chat/start",
            files={
                "paci_file": ("paci.pdf", BytesIO(b"%PDF"), "application/pdf"),
                "material_file": ("mat.docx", BytesIO(b"PK"), "application/octet-stream"),
            },
            data={"prompt": "", "school_id": "demo"},
        )
    assert res.status_code == 500


def test_safe_ext_none_filename():
    """Cubre line 40: _safe_ext cuando filename es None devuelve el default."""
    from api.chat_router import _safe_ext
    assert _safe_ext(None, ".pdf") == ".pdf"
    assert _safe_ext("", ".docx") == ".docx"


def test_start_local_write_error_returns_500():
    """Cubre lines 111-115: error al escribir los archivos en disco."""
    from io import BytesIO
    with patch("api.chat_router.S3_BUCKET", ""), \
         patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
        res = client.post(
            "/chat/start",
            files={
                "paci_file": ("paci.pdf", BytesIO(b"%PDF"), "application/pdf"),
                "material_file": ("mat.docx", BytesIO(b"PK"), "application/octet-stream"),
            },
            data={"prompt": "", "school_id": "demo"},
        )
    assert res.status_code == 500


def test_stream_waits_for_session_when_dynamo_enabled():
    """Cubre el loop de espera (lines 155-159) cuando sesión llega desde DynamoDB."""
    import asyncio as _asyncio
    sid = "test-stream-dynamo-wait-013"
    sd = SessionData()
    sd.phase = "completed"
    sd.workflow_status = "success"

    async def mock_sleep(delay):
        SESSIONS[sid] = sd

    with patch("api.chat_router.asyncio.sleep", side_effect=mock_sleep), \
         patch("api.dynamo_store.enabled", return_value=True):
        res = client.get(f"/chat/{sid}/stream", params={"token": "fake"})

    assert res.status_code == 200
    SESSIONS.pop(sid, None)


def test_download_dynamo_session_none_returns_404():
    """Cubre line 361: DynamoDB habilitado pero sesión no encontrada."""
    with patch("api.dynamo_store.enabled", return_value=True), \
         patch("api.dynamo_store.get_session", return_value=None):
        res = client.get("/chat/nonexistent-dl/download")
    assert res.status_code == 404
