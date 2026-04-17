import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.session_store import SESSIONS, SessionData

client = TestClient(app)


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
