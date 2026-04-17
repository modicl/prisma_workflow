import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

@dataclass
class SessionData:
    phase: str = "running"
    messages: list = field(default_factory=list)
    hitl_response_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    hitl_data: Optional[dict] = None
    result: Optional[dict] = None
    docx_path: Optional[str] = None
    error: Optional[str] = None

HitlCallback = Callable[[dict, int, int], Awaitable[tuple[bool, str, int]]]

SESSIONS: dict[str, SessionData] = {}
HITL_CALLBACKS: dict[str, HitlCallback] = {}
