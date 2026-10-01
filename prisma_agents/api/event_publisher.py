"""
Publicador de eventos del workflow hacia Kafka (monitor en vivo).

Contrato v1 (ver docs/superpowers/specs/2026-09-29-prisma-monitor-kafka-design.md):
  {"v": 1, "type": ..., "session_id": ..., "ts": ISO-8601 UTC ms, ...campos}

Reglas de diseño — el workflow NUNCA depende de Kafka:
  * `publish()` es síncrono y solo encola: no bloquea ni lanza, pase lo que pase.
  * Una tarea en segundo plano envía con AIOKafkaProducer (idempotente, acks=all).
  * Cola acotada: si Kafka está caído o lenta, se descarta y se avisa en el log.
  * Desactivado si KAFKA_BOOTSTRAP_SERVERS está vacío (igual que S3_BUCKET / DYNAMO_TABLE).
  * SIN PII: solo ids aleatorios, contadores y nombres de agente. Nada de texto de prompts,
    contenido del PACI, nombres, RUT, nombres de archivo ni correos.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_TOPIC = "prisma.agent-events"
QUEUE_MAX = 1000
SEND_TIMEOUT_S = 5.0
CLOSE_DRAIN_S = 3.0
_WARN_EVERY = 100          # no inundar el log si Kafka lleva rato caído


class EventPublisher:
    def __init__(
        self,
        bootstrap: str,
        topic: str,
        producer_factory: Optional[Callable] = None,
        queue_max: int = QUEUE_MAX,
        retry_base_s: float = 1.0,
    ):
        self.bootstrap = bootstrap
        self.topic = topic
        self._factory = producer_factory or self._default_factory
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_max)
        self._retry_base_s = retry_base_s
        self._task: Optional[asyncio.Task] = None
        self._producer = None
        self.dropped = 0
        self.sent = 0

    def _default_factory(self):
        from aiokafka import AIOKafkaProducer   # import perezoso: sin aiokafka el workflow igual arranca
        return AIOKafkaProducer(
            bootstrap_servers=self.bootstrap,
            client_id="prisma-workflow",
            enable_idempotence=True,
            acks="all",
            linger_ms=50,
            request_timeout_ms=10000,
        )

    # ── API ─────────────────────────────────────────────────────────────────
    def enqueue(self, event: dict) -> bool:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._drop("cola llena")
            return False
        self._ensure_worker()
        return True

    async def close(self) -> None:
        """Vacía lo pendiente (con tope de tiempo) y detiene el productor."""
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(self._queue.join(), CLOSE_DRAIN_S)
            except (asyncio.TimeoutError, Exception):
                pass
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._producer is not None:
            try:
                await asyncio.wait_for(self._producer.stop(), SEND_TIMEOUT_S)
            except Exception:
                pass
            self._producer = None

    # ── internos ────────────────────────────────────────────────────────────
    def _drop(self, motivo: str) -> None:
        self.dropped += 1
        if self.dropped == 1 or self.dropped % _WARN_EVERY == 0:
            logger.warning("evento de monitor descartado (%s); descartados en total: %d", motivo, self.dropped)

    def _ensure_worker(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return              # sin event loop (CLI/tests): queda encolado, no se envía
        self._task = loop.create_task(self._worker(), name="kafka-event-publisher")

    async def _connect(self) -> None:
        attempt = 0
        while self._producer is None:
            producer = None
            try:
                producer = self._factory()
                await producer.start()
                self._producer = producer
            except Exception as exc:
                if producer is not None:
                    try:
                        await producer.stop()
                    except Exception:
                        pass
                attempt += 1
                espera = min(30.0, self._retry_base_s * (2 ** (attempt - 1)))
                if attempt == 1 or attempt % 10 == 0:
                    logger.warning("Kafka no disponible (%s: %s); reintento en %.0fs", type(exc).__name__, exc, espera)
                await asyncio.sleep(espera)

    async def _worker(self) -> None:
        await self._connect()
        while True:
            event = await self._queue.get()
            try:
                await asyncio.wait_for(
                    self._producer.send(
                        self.topic,
                        value=json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
                        key=str(event.get("session_id", "")).encode("utf-8"),
                    ),
                    SEND_TIMEOUT_S,
                )
                self.sent += 1
            except Exception as exc:
                self._drop(f"{type(exc).__name__}: {exc}")
            finally:
                self._queue.task_done()


# ── API de módulo ───────────────────────────────────────────────────────────
_publisher: Optional[EventPublisher] = None


def _get_publisher() -> Optional[EventPublisher]:
    global _publisher
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not bootstrap:
        return None
    if _publisher is None:
        _publisher = EventPublisher(bootstrap, os.environ.get("KAFKA_TOPIC", "").strip() or DEFAULT_TOPIC)
    return _publisher


def publish(event_type: str, session_id: str, **fields) -> None:
    """Encola un evento para Kafka. Nunca lanza y nunca bloquea."""
    try:
        publisher = _get_publisher()
        if publisher is None or not session_id:
            return
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        publisher.enqueue({"v": SCHEMA_VERSION, "type": event_type, "session_id": session_id, "ts": ts, **fields})
    except Exception:
        logger.debug("no se pudo encolar el evento %s", event_type, exc_info=True)


async def shutdown() -> None:
    """Llamar al apagar la app: envía lo pendiente y cierra el productor."""
    if _publisher is not None:
        await _publisher.close()
