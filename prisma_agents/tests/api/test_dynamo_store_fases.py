"""Campos y operaciones atómicas de DynamoDB para las fases (token de tarea y candado de ejecución)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import MagicMock
from botocore.exceptions import ClientError

from api import dynamo_store as ds


def _err(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "UpdateItem")


@pytest.fixture
def cli(monkeypatch):
    c = MagicMock()
    monkeypatch.setattr(ds, "TABLE", "t")
    monkeypatch.setattr(ds, "_client", c)
    return c


def test_update_session_acepta_task_token_y_started_at(cli):
    ds.update_session("s1", task_token="tok", started_at="123.5")
    kw = cli.update_item.call_args.kwargs
    assert set(kw["ExpressionAttributeNames"].values()) == {"task_token", "started_at"}
    assert {"S": "tok"} in kw["ExpressionAttributeValues"].values()


def test_task_token_vacio_se_guarda_como_cadena_vacia(cli):
    ds.update_session("s1", task_token=None)
    assert {"S": ""} in cli.update_item.call_args.kwargs["ExpressionAttributeValues"].values()


def test_get_session_devuelve_los_campos_de_fase(cli):
    cli.get_item.return_value = {"Item": {"session_id": {"S": "s1"}, "phase": {"S": "awaiting_hitl"},
                                          "task_token": {"S": "tok"}, "started_at": {"S": "100.5"}}}
    item = ds.get_session("s1")
    assert item["task_token"] == "tok" and item["started_at"] == 100.5
    cli.get_item.return_value = {"Item": {"session_id": {"S": "s1"}, "phase": {"S": "running"}}}
    item = ds.get_session("s1")
    assert item["task_token"] is None and item["started_at"] is None


def test_acquire_phase_true_si_toma_el_candado(cli):
    assert ds.acquire_phase("s1", "a:1", ttl_seconds=60) is True
    kw = cli.update_item.call_args.kwargs
    assert "attribute_not_exists(running_until)" in kw["ConditionExpression"]
    assert kw["ExpressionAttributeValues"][":p"] == {"S": "a:1"}


def test_acquire_phase_false_si_ya_esta_tomado(cli):
    cli.update_item.side_effect = _err("ConditionalCheckFailedException")
    assert ds.acquire_phase("s1", "a:1") is False


def test_acquire_phase_propaga_otros_errores(cli):
    cli.update_item.side_effect = _err("InternalServerError")
    with pytest.raises(ClientError):
        ds.acquire_phase("s1", "a:1")


def test_release_phase_borra_el_candado(cli):
    ds.release_phase("s1")
    assert "REMOVE running_phase, running_until" in cli.update_item.call_args.kwargs["UpdateExpression"]


def test_consume_token_devuelve_el_token_y_lo_borra(cli):
    cli.update_item.return_value = {"Attributes": {"task_token": {"S": "tok"}}}
    assert ds.consume_token("s1") == "tok"
    kw = cli.update_item.call_args.kwargs
    assert kw["UpdateExpression"] == "REMOVE task_token" and kw["ReturnValues"] == "UPDATED_OLD"
    assert "attribute_exists(task_token)" in kw["ConditionExpression"]


def test_consume_token_none_si_ya_fue_usado(cli):
    """Foco de revisión 2: el segundo clic del docente no obtiene token."""
    cli.update_item.side_effect = _err("ConditionalCheckFailedException")
    assert ds.consume_token("s1") is None


def test_sin_dynamodb_las_operaciones_son_inocuas(monkeypatch):
    monkeypatch.setattr(ds, "TABLE", "")
    assert ds.acquire_phase("s1", "a:1") is True
    assert ds.consume_token("s1") is None
    ds.release_phase("s1")
