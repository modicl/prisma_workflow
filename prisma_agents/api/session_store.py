import asyncio
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class SessionData:
    phase: str = "running"
    messages: list = field(default_factory=list)
    hitl_response_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    hitl_data: Optional[dict] = None
    result: Optional[dict] = None
    docx_path: Optional[str] = None
    error: Optional[str] = None

SESSIONS: dict[str, SessionData] = {}
HITL_CALLBACKS: dict[str, object] = {}
