"""Tests para api/main.py — CORS middleware y lifespan."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient
from api.main import app


def test_options_request_returns_204():
    """Cubre lines 30-36: el handler OPTIONS del CORSMiddleware."""
    with TestClient(app) as client:
        res = client.options("/health", headers={"Origin": "http://localhost:3000"})
    assert res.status_code == 204
    assert "access-control-allow-origin" in {k.lower() for k in res.headers}


def test_regular_response_has_cors_headers():
    """Cubre lines 38-44 (send_with_cors): respuesta normal incluye CORS headers."""
    with TestClient(app) as client:
        res = client.get("/health")
    assert res.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in res.headers}


def test_lifespan_context_runs():
    """Cubre line 49 (lifespan yield): el servidor arranca y se detiene sin error."""
    with TestClient(app) as client:
        res = client.get("/health")
    assert res.status_code == 200
