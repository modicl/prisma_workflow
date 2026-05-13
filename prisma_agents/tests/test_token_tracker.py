"""Tests para utils/token_tracker.py."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock
from utils.token_tracker import SessionTokenUsage, _extract_usage


def _make_event(prompt=100, candidates=50, total=150, author=None):
    usage = MagicMock()
    usage.prompt_token_count = prompt
    usage.candidates_token_count = candidates
    usage.total_token_count = total
    event = MagicMock()
    event.usage_metadata = usage
    return event


class TestExtractUsage:
    def test_extracts_from_usage_metadata(self):
        event = _make_event(prompt=100, candidates=50, total=150)
        result = _extract_usage(event)
        assert result == {"input": 100, "output": 50, "total": 150}

    def test_calculates_total_when_zero(self):
        event = _make_event(prompt=80, candidates=40, total=0)
        result = _extract_usage(event)
        assert result["total"] == 120

    def test_returns_none_when_all_zero(self):
        event = _make_event(prompt=0, candidates=0, total=0)
        result = _extract_usage(event)
        assert result is None

    def test_returns_none_when_no_usage_metadata(self):
        event = MagicMock()
        event.usage_metadata = None
        event.response = None
        result = _extract_usage(event)
        assert result is None

    def test_falls_back_to_response_usage(self):
        event = MagicMock()
        event.usage_metadata = None
        usage = MagicMock()
        usage.prompt_token_count = 60
        usage.candidates_token_count = 30
        usage.total_token_count = 90
        event.response.usage_metadata = usage
        result = _extract_usage(event)
        assert result == {"input": 60, "output": 30, "total": 90}


class TestSessionTokenUsage:
    def test_initial_state_empty(self):
        tracker = SessionTokenUsage()
        assert tracker.total_tokens == 0
        assert tracker.has_data is False

    def test_add_event_accumulates_by_agent(self):
        tracker = SessionTokenUsage()
        tracker.add_event("AgentA", _make_event(prompt=100, candidates=50, total=150))
        tracker.add_event("AgentA", _make_event(prompt=200, candidates=100, total=300))
        tracker.add_event("AgentB", _make_event(prompt=50, candidates=25, total=75))

        assert tracker.total_tokens == 525
        assert tracker.has_data is True

    def test_add_event_skips_no_usage(self):
        tracker = SessionTokenUsage()
        event = MagicMock()
        event.usage_metadata = None
        event.response = None
        tracker.add_event("AgentA", event)
        assert tracker.total_tokens == 0

    def test_to_dict_structure(self):
        tracker = SessionTokenUsage()
        tracker.add_event("AnalizadorPACI", _make_event(100, 50, 150))
        tracker.add_event("Adaptador", _make_event(200, 80, 280))

        d = tracker.to_dict()
        assert d["total"] == 430
        assert d["input"] == 300
        assert d["output"] == 130
        assert "AnalizadorPACI" in d["by_agent"]
        assert d["by_agent"]["AnalizadorPACI"]["calls"] == 1
        assert d["by_agent"]["Adaptador"]["input_tokens"] == 200

    def test_calls_counter_increments(self):
        tracker = SessionTokenUsage()
        for _ in range(3):
            tracker.add_event("AgentA", _make_event(10, 5, 15))
        assert tracker.to_dict()["by_agent"]["AgentA"]["calls"] == 3
