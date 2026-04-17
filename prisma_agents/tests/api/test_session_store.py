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

def test_global_dicts_exist():
    assert isinstance(SESSIONS, dict)
    assert isinstance(HITL_CALLBACKS, dict)
