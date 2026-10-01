"""
Consumo de tokens de Gemini hacia el monitor en vivo (evento `llm_usage`).

Los agentes ADK se miden en `agent._run_with_timeout`. Este módulo cubre las llamadas DIRECTAS a Gemini
(fuera de ADK): lectura de PDF/DOCX y selección/transcripción de materiales. Sin él, «tokens usados» y
«costo promedio» subestimarían el consumo real (leer un PDF puede pesar más que un agente).

Esas funciones son utilidades sin idea de qué flujo las llamó, así que la sesión viaja en un ContextVar que
fija `workflow_runner`. En modo CLI la variable queda vacía y todo es no-op.

Sin PII: solo contadores, el nombre de la etapa y el modelo.
"""
from contextvars import ContextVar
from typing import Optional

# Sesión del flujo en curso (la fija workflow_runner). Un asyncio.Task hereda una copia del contexto; los
# hilos de un executor NO (ver book_repository.get_reference_materials_async, que copia el contexto).
monitor_session_id: ContextVar[str] = ContextVar("monitor_session_id", default="")


def _count(value) -> int:
    """Solo enteros positivos: descarta None, bool, textos y mocks."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def resolve_model(model_version, fallback) -> str:
    for candidate in (model_version, fallback):
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return "desconocido"


def usage_fields(usage, model: str, label: str, partial: bool = False) -> Optional[dict]:
    """Campos de un evento `llm_usage` a partir de un usage_metadata de Gemini, o None si no aplica.

    Los eventos parciales se ignoran para no contar dos veces el mismo consumo. `prompt_token_count` ya
    incluye los tokens servidos desde caché (`cached_content_token_count` es un subconjunto).
    """
    if usage is None or partial:
        return None
    entrada = _count(getattr(usage, "prompt_token_count", 0)) + _count(getattr(usage, "tool_use_prompt_token_count", 0))
    cache = _count(getattr(usage, "cached_content_token_count", 0))
    salida = _count(getattr(usage, "candidates_token_count", 0))
    razonamiento = _count(getattr(usage, "thoughts_token_count", 0))
    if not (entrada or salida or razonamiento):
        return None
    return {
        "agent": label, "model": model, "input_tokens": entrada, "cached_tokens": cache,
        "output_tokens": salida, "thoughts_tokens": razonamiento,
    }


def emit_direct_usage(response, *, agent: str, model: str) -> None:
    """Publica el consumo de una respuesta de `client.models.generate_content`. Nunca lanza."""
    session_id = monitor_session_id.get()
    if not session_id:
        return
    try:
        modelo = resolve_model(getattr(response, "model_version", None), model)
        fields = usage_fields(getattr(response, "usage_metadata", None), modelo, agent)
        if fields:
            from api.event_publisher import publish
            publish("llm_usage", session_id, **fields)
    except Exception:
        pass
