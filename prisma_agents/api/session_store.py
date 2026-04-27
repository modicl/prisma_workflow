import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from api import dynamo_store


@dataclass
class SessionData:
    phase: str = "running"
    messages: list = field(default_factory=list)
    hitl_response_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    hitl_data: Optional[dict] = None
    result: Optional[dict] = None
    docx_path: Optional[str] = None
    error: Optional[str] = None
    workflow_status: Optional[str] = None  # "success" | "degraded" | "hitl_rejected" | "error"


HitlCallback = Callable[[dict, int, int], Awaitable[tuple[bool, str, int]]]

SESSIONS: dict[str, SessionData] = {}
HITL_CALLBACKS: dict[str, HitlCallback] = {}


def sync_to_dynamo(session_id: str, session_data: SessionData, **extra) -> None:
    """Mirror in-memory session state to DynamoDB (no-op if DynamoDB not configured)."""
    dynamo_store.update_session(
        session_id,
        phase=session_data.phase,
        messages=session_data.messages,
        hitl_data=session_data.hitl_data,
        error=session_data.error,
        workflow_status=session_data.workflow_status,
        **extra,
    )
