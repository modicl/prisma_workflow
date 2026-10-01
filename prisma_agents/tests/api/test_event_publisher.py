"""Tests para api/event_publisher.py — el publicador hacia Kafka no debe romper nunca el workflow."""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from unittest.mock import patch

from api import event_publisher
from api.event_publisher import EventPublisher, publish


class FakeProducer:
    def __init__(self, fail_send=False):
        self.sent = []
        self.started = False
        self.stopped = False
        self.fail_send = fail_send

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send(self, topic, value=None, key=None):
        if self.fail_send:
            raise RuntimeError("broker caído")
        self.sent.append((topic, key, json.loads(value)))


def _ev(n=0, sid="sesion-1"):
    return {"v": 1, "type": "agent_started", "session_id": sid, "ts": "2026-09-29T14:00:00.000Z", "n": n}


async def _esperar(cond, segundos=1.0):
    for _ in range(int(segundos / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    monkeypatch.setattr(event_publisher, "_publisher", None)
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("KAFKA_TOPIC", raising=False)


# ── EventPublisher ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_entrega_el_evento_con_el_session_id_como_clave():
    fake = FakeProducer()
    pub = EventPublisher("kafka:9092", "prisma.agent-events", producer_factory=lambda: fake)
    assert pub.enqueue(_ev()) is True
    assert await _esperar(lambda: fake.sent)
    topic, key, valor = fake.sent[0]
    assert topic == "prisma.agent-events" and key == b"sesion-1" and valor == _ev()
    await pub.close()


@pytest.mark.asyncio
async def test_conserva_el_orden():
    fake = FakeProducer()
    pub = EventPublisher("k", "t", producer_factory=lambda: fake)
    for n in range(20):
        pub.enqueue(_ev(n))
    assert await _esperar(lambda: len(fake.sent) == 20)
    assert [v["n"] for _t, _k, v in fake.sent] == list(range(20))
    await pub.close()


@pytest.mark.asyncio
async def test_cola_llena_descarta_sin_lanzar():
    async def nunca_conecta():
        await asyncio.Event().wait()

    class Colgado(FakeProducer):
        start = staticmethod(nunca_conecta)

    pub = EventPublisher("k", "t", producer_factory=lambda: Colgado(), queue_max=2)
    resultados = [pub.enqueue(_ev(n)) for n in range(5)]
    assert resultados == [True, True, False, False, False]
    assert pub.dropped == 3
    await pub.close()


@pytest.mark.asyncio
async def test_error_al_enviar_descarta_ese_evento_y_sigue_vivo():
    fake = FakeProducer(fail_send=True)
    pub = EventPublisher("k", "t", producer_factory=lambda: fake)
    pub.enqueue(_ev(1))
    assert await _esperar(lambda: pub.dropped == 1)
    fake.fail_send = False
    pub.enqueue(_ev(2))
    assert await _esperar(lambda: len(fake.sent) == 1)
    assert fake.sent[0][2]["n"] == 2
    await pub.close()


@pytest.mark.asyncio
async def test_si_la_conexion_falla_reintenta_hasta_conectar():
    fake = FakeProducer()
    intentos = []

    def factory():
        intentos.append(1)
        if len(intentos) == 1:
            raise ConnectionError("Kafka aún no levanta")
        return fake

    pub = EventPublisher("k", "t", producer_factory=factory, retry_base_s=0.01)
    pub.enqueue(_ev())
    assert await _esperar(lambda: fake.sent)
    assert len(intentos) == 2
    await pub.close()


@pytest.mark.asyncio
async def test_close_vacia_la_cola_y_detiene_el_productor():
    fake = FakeProducer()
    pub = EventPublisher("k", "t", producer_factory=lambda: fake)
    for n in range(5):
        pub.enqueue(_ev(n))
    await pub.close()
    assert len(fake.sent) == 5 and fake.stopped is True


def test_sin_event_loop_encola_pero_no_lanza():
    pub = EventPublisher("k", "t", producer_factory=lambda: FakeProducer())
    assert pub.enqueue(_ev()) is True


# ── publish() ───────────────────────────────────────────────────────────────
def test_desactivado_si_no_hay_bootstrap_no_crea_nada():
    publish("flow_started", "s1")
    assert event_publisher._publisher is None


def test_arma_el_sobre_v1_con_ts_utc_y_los_campos_extra():
    capturados = []

    class Stub:
        def enqueue(self, event):
            capturados.append(event)
            return True

    with patch.object(event_publisher, "_get_publisher", return_value=Stub()):
        publish("llm_usage", "s1", agent="Agente 1", input_tokens=10)
    (ev,) = capturados
    assert ev["v"] == 1 and ev["type"] == "llm_usage" and ev["session_id"] == "s1"
    assert ev["agent"] == "Agente 1" and ev["input_tokens"] == 10
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", ev["ts"])


def test_sin_session_id_no_publica():
    capturados = []

    class Stub:
        def enqueue(self, event):
            capturados.append(event)

    with patch.object(event_publisher, "_get_publisher", return_value=Stub()):
        publish("flow_started", "")
    assert capturados == []


def test_publish_jamas_lanza_aunque_todo_falle():
    with patch.object(event_publisher, "_get_publisher", side_effect=RuntimeError("boom")):
        publish("flow_started", "s1")


def test_lee_bootstrap_y_topic_del_entorno(monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    monkeypatch.setenv("KAFKA_TOPIC", "otro.topic")
    pub = event_publisher._get_publisher()
    assert pub.bootstrap == "kafka:9092" and pub.topic == "otro.topic"
    assert event_publisher._get_publisher() is pub          # singleton


@pytest.mark.asyncio
async def test_shutdown_sin_publicador_no_hace_nada():
    await event_publisher.shutdown()
