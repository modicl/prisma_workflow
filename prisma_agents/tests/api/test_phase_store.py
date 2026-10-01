"""Estado del flujo entre fases (S3 state/{id}.json): lista explícita de claves, cifrado y sin tocar jobs/."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError

from api import phase_store as ps


@pytest.fixture
def s3(monkeypatch):
    c = MagicMock()
    monkeypatch.setenv("S3_BUCKET", "bucket-test")
    monkeypatch.setattr(ps, "_client", lambda: c)
    return c


def _cuerpo(put_call):
    return json.loads(put_call.kwargs["Body"].decode("utf-8"))


def test_la_clave_va_bajo_state_y_nunca_bajo_jobs():
    assert ps.state_key("abc") == "state/abc.json"
    assert not ps.state_key("abc").startswith("jobs/")


def test_save_guarda_solo_las_claves_permitidas_y_cifra(s3):
    ps.save_state("s1", {"perfil_paci": "P", "api_session_id": "no", "otra": 1, "warnings": ["w"]})
    kw = s3.put_object.call_args.kwargs
    assert kw["Bucket"] == "bucket-test" and kw["Key"] == "state/s1.json"
    assert kw["ServerSideEncryption"] == "AES256"
    cuerpo = _cuerpo(s3.put_object.call_args)
    assert cuerpo == {"perfil_paci": "P", "warnings": ["w"]}      # api_session_id y otra NO se persisten


def test_load_devuelve_el_estado(s3):
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps({"perfil_paci": "P"}).encode())}
    assert ps.load_state("s1") == {"perfil_paci": "P"}


def test_load_de_un_objeto_ausente_lanza_phase_state_not_found(s3):
    """Foco de revisión 4: el estado expiró o se borró."""
    s3.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
    with pytest.raises(ps.PhaseStateNotFound):
        ps.load_state("s1")


def test_load_propaga_otros_errores_de_s3(s3):
    s3.get_object.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetObject")
    with pytest.raises(ClientError):
        ps.load_state("s1")


def test_update_state_mezcla_con_lo_existente(s3):
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps({"perfil_paci": "P"}).encode())}
    nuevo = ps.update_state("s1", hitl_reason="falta claridad")
    assert nuevo == {"perfil_paci": "P", "hitl_reason": "falta claridad"}
    assert _cuerpo(s3.put_object.call_args) == nuevo


def test_update_state_crea_si_no_existia(s3):
    s3.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
    assert ps.update_state("s1", hitl_reason="x") == {"hitl_reason": "x"}


def test_delete_state_ignora_errores(s3):
    s3.delete_object.side_effect = RuntimeError("boom")
    ps.delete_state("s1")                       # no lanza
    s3.delete_object.assert_called_once_with(Bucket="bucket-test", Key="state/s1.json")


def test_sin_bucket_configurado_falla_con_mensaje_claro(monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        ps.save_state("s1", {"perfil_paci": "P"})
