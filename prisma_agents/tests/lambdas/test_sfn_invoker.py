"""Lambda `invoker`: Step Functions -> POST al ALB. Solo HTTP, lista blanca de fases, sin registrar tokens ni cuerpos."""
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lambda"))

import pytest
from unittest.mock import MagicMock, patch

import sfn_invoker as inv

SID = "123e4567-e89b-42d3-a456-426614174000"


class Resp:
    def __init__(self, status=202):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setenv("BACKEND_INTERNAL_URL", "http://backend:8000/")
    monkeypatch.setenv("INTERNAL_TOKEN", "tok-interno")
    monkeypatch.setenv("RETRY_ATTEMPTS", "3")
    with patch.object(inv.time, "sleep"):
        yield


def test_la_fase_a_hace_post_con_el_token_interno_y_solo_las_claves_permitidas():
    abierto = MagicMock(return_value=Resp(202))
    with patch.object(inv.urllib.request, "urlopen", abierto):
        res = inv.handler({"phase": "a", "session_id": SID, "task_token": "TOK", "attempt": 2, "feedback_agent": 1,
                           "texto_del_docente": "NO DEBE PASAR"}, None)
    assert res == {"http_status": 202}
    req = abierto.call_args.args[0]
    assert req.full_url == f"http://backend:8000/chat/internal/phase/a/{SID}" and req.get_method() == "POST"
    assert req.get_header("X-internal-token") == "tok-interno"
    cuerpo = json.loads(req.data)
    assert cuerpo == {"task_token": "TOK", "attempt": 2, "feedback_agent": 1}      # la clave extra se descarta


@pytest.mark.parametrize("fase", ["a", "b", "wait", "finalize"])
def test_las_cuatro_fases_permitidas_van_a_su_ruta(fase):
    abierto = MagicMock(return_value=Resp(200))
    with patch.object(inv.urllib.request, "urlopen", abierto):
        inv.handler({"phase": fase, "session_id": SID}, None)
    assert abierto.call_args.args[0].full_url.endswith(f"/chat/internal/phase/{fase}/{SID}")


@pytest.mark.parametrize("fase", ["run", "../admin", "A", "", None, "a/../b"])
def test_rechaza_fases_fuera_de_la_lista_blanca(fase):
    with patch.object(inv.urllib.request, "urlopen") as abierto:
        with pytest.raises(ValueError):
            inv.handler({"phase": fase, "session_id": SID}, None)
    abierto.assert_not_called()


@pytest.mark.parametrize("sid", ["../etc/passwd", "abc", "", SID + "/x", "123e4567-e89b-42d3-a456-42661417400g"])
def test_rechaza_session_id_que_no_sea_uuid(sid):
    with patch.object(inv.urllib.request, "urlopen") as abierto:
        with pytest.raises(ValueError):
            inv.handler({"phase": "a", "session_id": sid}, None)
    abierto.assert_not_called()


def test_un_error_4xx_no_se_reintenta():
    err = urllib.error.HTTPError("u", 404, "no", {}, None)
    with patch.object(inv.urllib.request, "urlopen", MagicMock(side_effect=err)) as abierto:
        with pytest.raises(urllib.error.HTTPError):
            inv.handler({"phase": "a", "session_id": SID}, None)
    assert abierto.call_count == 1


def test_un_error_5xx_se_reintenta_y_luego_falla():
    err = urllib.error.HTTPError("u", 503, "no", {}, None)
    with patch.object(inv.urllib.request, "urlopen", MagicMock(side_effect=err)) as abierto:
        with pytest.raises(urllib.error.HTTPError):
            inv.handler({"phase": "a", "session_id": SID}, None)
    assert abierto.call_count == 3


def test_un_fallo_transitorio_de_red_se_recupera():
    abierto = MagicMock(side_effect=[urllib.error.URLError("sin ruta"), Resp(202)])
    with patch.object(inv.urllib.request, "urlopen", abierto):
        assert inv.handler({"phase": "b", "session_id": SID}, None) == {"http_status": 202}
    assert abierto.call_count == 2


def test_no_registra_tokens_ni_cuerpos(capsys):
    with patch.object(inv.urllib.request, "urlopen", MagicMock(return_value=Resp(202))):
        inv.handler({"phase": "a", "session_id": SID, "task_token": "TOKEN-SECRETO"}, None)
    salida = capsys.readouterr()
    assert "TOKEN-SECRETO" not in salida.out + salida.err and "tok-interno" not in salida.out + salida.err
