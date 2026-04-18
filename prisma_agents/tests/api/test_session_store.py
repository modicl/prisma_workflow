import asyncio
from api.session_store import SessionData, SESSIONS, HITL_CALLBACKS

def test_session_data_defaults():
    sd = SessionData()
    assert sd.phase == "running"
    assert sd.messages == []
    assert sd.hitl_data is None
    assert sd.result is None
    assert sd.docx_path is None
    assert sd.error is None

def test_hitl_response_queue_is_asyncio_queue():
    sd = SessionData()
    assert isinstance(sd.hitl_response_queue, asyncio.Queue)

def test_sessions_dict_accepts_session_data():
    sd = SessionData()
    SESSIONS["test-fix-123"] = sd
    assert SESSIONS["test-fix-123"] is sd
    assert "test-fix-123" not in HITL_CALLBACKS
    del SESSIONS["test-fix-123"]


def test_each_session_gets_independent_queue():
    sd1 = SessionData()
    sd2 = SessionData()
    assert sd1.hitl_response_queue is not sd2.hitl_response_queue
