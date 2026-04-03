"""
token_tracker.py — Captura y acumula uso de tokens de los eventos Google ADK.

Los eventos del runner exponen `usage_metadata` con conteos de tokens por
llamada al modelo. Este módulo los acumula por agente y retorna un dict
serializable para persistir en el reporte de la sesión.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class AgentTokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0


class SessionTokenUsage:
    """Acumula tokens de una sesión completa, desglosados por agente."""

    def __init__(self):
        self._agents: dict[str, AgentTokenUsage] = {}

    def add_event(self, author: str, event) -> None:
        """Extrae usage_metadata del evento y lo acumula al agente."""
        usage = _extract_usage(event)
        if usage is None:
            return
        if author not in self._agents:
            self._agents[author] = AgentTokenUsage()
        a = self._agents[author]
        a.input_tokens += usage["input"]
        a.output_tokens += usage["output"]
        a.total_tokens += usage["total"]
        a.calls += 1

    @property
    def total_tokens(self) -> int:
        return sum(a.total_tokens for a in self._agents.values())

    @property
    def has_data(self) -> bool:
        return self.total_tokens > 0

    def to_dict(self) -> dict:
        return {
            "total": self.total_tokens,
            "input": sum(a.input_tokens for a in self._agents.values()),
            "output": sum(a.output_tokens for a in self._agents.values()),
            "by_agent": {name: asdict(u) for name, u in self._agents.items()},
        }


def _extract_usage(event) -> Optional[dict]:
    """Extrae usage_metadata con fallbacks para distintas versiones de ADK."""
    usage = getattr(event, "usage_metadata", None)

    # Fallback: algunos eventos lo exponen via .response
    if usage is None:
        resp = getattr(event, "response", None)
        if resp:
            usage = getattr(resp, "usage_metadata", None)

    if usage is None:
        return None

    input_t = getattr(usage, "prompt_token_count", 0) or 0
    output_t = getattr(usage, "candidates_token_count", 0) or 0
    total_t = getattr(usage, "total_token_count", 0) or 0

    # Si total_token_count no viene, calcularlo
    if total_t == 0 and (input_t > 0 or output_t > 0):
        total_t = input_t + output_t

    if total_t == 0:
        return None  # evento sin tokens reales (tool calls, etc.)

    return {"input": input_t, "output": output_t, "total": total_t}
