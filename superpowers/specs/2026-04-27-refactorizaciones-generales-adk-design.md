# Diseño: Refactorizaciones Generales ADK — PRISMA

**Fecha:** 2026-04-27  
**Rama:** `feature/refactorizaciones-generales` (desde `develop`)  
**PR:** único PR que agrupa las tres capas  
**Estado:** Aprobado por usuario — pendiente implementación

---

## Contexto

PRISMA es un sistema multi-agente Python (Google ADK + Gemini 2.5 Flash Lite) que genera rúbricas de evaluación adaptadas para estudiantes con NEE. El sistema funciona correctamente; este PR introduce mejoras de robustez, arquitectura y UX antes de que entre en uso real en establecimientos.

Las mejoras no alteran la lógica normativa (Decretos 170, 83, 67) ni los contratos de I/O de los agentes con el sistema escolar.

---

## Alcance

Tres capas independientes implementadas en el mismo PR:

1. `output_schema` Pydantic en AnalizadorPACI y Adaptador
2. `LoopAgent` nativo ADK para el loop del crítico
3. SSE + ADK callbacks para observabilidad en tiempo real

---

## Sección 1 — `output_schema` Pydantic en AnalizadorPACI y Adaptador

### Problema

`_extract_subject_grade` en `agent.py` usa regex sobre un bloque `---METADATOS---` que el LLM puede formatear inconsistentemente. Si el formato varía, `subject_raw`/`grade_raw` quedan vacíos y el BookRepository no consulta los materiales del establecimiento.

### Solución

Agregar `output_schema` Pydantic a los dos agentes para garantizar estructura.

#### AnalizadorPACI — nuevo schema

```python
# agents/analizador_paci.py
from pydantic import BaseModel
from typing import Literal

class PerfilPaciOutput(BaseModel):
    ramo: str                                        # ej: "matematica"
    curso: str                                       # ej: "5_basico"
    diagnostico: str                                 # diagnóstico CIE-10/DSM del estudiante
    tipo_nee: Literal["permanente", "transitoria"]   # según Decreto 170
    analisis: str                                    # texto completo para agentes siguientes
```

#### Adaptador — nuevo schema

```python
# agents/adaptador.py
class PlanificacionAdaptadaOutput(BaseModel):
    resumen_hitl: str    # texto conciso para mostrar al docente en el checkpoint HITL
    planificacion: str   # texto completo para el GeneradorRubrica
```

#### Cambios en `agent.py`

- Eliminar `_extract_subject_grade` y su regex.
- Leer directamente desde el dict:

```python
perfil = ctx.session.state.get("perfil_paci", {})
subject_raw = perfil.get("ramo", "")
grade_raw   = perfil.get("curso", "")
```

- El HITL muestra `state["planificacion_adaptada"]["resumen_hitl"]` en lugar de `state["planificacion_adaptada"]` completo.

#### Cambios en system prompts (⚠ requieren revisión humana)

Con `output_schema`, ADK inyecta los valores como JSON serializado cuando un agente usa `{perfil_paci}` o `{planificacion_adaptada}` en su prompt. Los agentes afectados y el cambio requerido:

| Archivo | Variable actual | Cambio |
|---|---|---|
| `agents/adaptador.py` | `{perfil_paci}` | `{perfil_paci[analisis]}` |
| `agents/generador_rubrica.py` | `{perfil_paci}` | `{perfil_paci[analisis]}` |
| `agents/generador_rubrica.py` | `{planificacion_adaptada}` | `{planificacion_adaptada[planificacion]}` |
| `agents/critico.py` | `{perfil_paci}` | `{perfil_paci[analisis]}` |

**Estos cambios son de alta criticidad normativa y deben ser revisados por el responsable del proyecto antes del merge.**

#### Tests

- Actualizar `test_hitl.py` para que el estado de sesión use dicts en lugar de strings para `perfil_paci` y `planificacion_adaptada`.
- Agregar casos en `test_curriculum_catalog.py` que verifiquen que `normalize_subject` y `normalize_grade` siguen funcionando con los valores del dict.

---

## Sección 2 — `LoopAgent` nativo ADK para el loop del crítico

### Problema

El loop `GeneradorRubrica → AgenteCritico` vive como un `for iteration in range(...)` imperativo en `_run_async_impl` (~35 líneas) con lógica de estado dispersa: setear `critica_previa`, detectar `acceptable`, setear `status`. Es difícil de leer y el estado mutable entre iteraciones está implícito.

### Solución

Extraer el loop a un `LoopAgent` declarativo ADK. La terminación anticipada (`acceptable=True`) se maneja con un `after_agent_callback` en el AgenteCritico que emite `escalate=True`.

#### Callback de terminación

```python
# agent.py
def _after_critico_callback(callback_context) -> None:
    state = callback_context.state
    evaluacion = _parse_critic_json(state.get("evaluacion_critica", {}))

    if evaluacion.get("acceptable", False):
        callback_context.actions.escalate = True   # ADK detiene el LoopAgent
        return

    # Prepara retroalimentación para la siguiente iteración
    critique    = evaluacion.get("critique", "Sin descripción.")
    suggestions = "\n".join(f"- {s}" for s in evaluacion.get("suggestions", []))
    state["critica_previa"] = (
        f"RETROALIMENTACIÓN EVALUADOR:\n{critique}\n\n"
        f"SUGERENCIAS A INCORPORAR:\n{suggestions}"
    )
```

#### Construcción del LoopAgent

```python
# agent.py — en __init__ de PaciWorkflowAgent
_critico = make_critico_agent(after_agent_callback=_after_critico_callback)
self.critic_loop = LoopAgent(
    name="CriticLoop",
    sub_agents=[_generador, _critico],
    max_iterations=MAX_ITERATIONS,
)
```

La factory `make_critico_agent` recibe `after_agent_callback` como parámetro opcional.

**Nota de integración con Sección 3:** cuando se agreguen los callbacks de progreso SSE, el AgenteCritico necesitará AMBOS el callback de terminación y el de progreso. Como ADK solo permite un `after_agent_callback` por agente, ambos se combinan en una función única:

```python
def _make_critico_after_callback(progress_after_cb):
    """Combina el callback de terminación con el de progreso SSE."""
    def combined(callback_context) -> None:
        _after_critico_callback(callback_context)   # termination + critica_previa
        progress_after_cb(callback_context)          # SSE agent_end event
    return combined
```

Este patrón se aplica también si cualquier otro agente necesita combinar callbacks en el futuro.

#### Reemplazo en `_run_async_impl`

El bloque actual de ~35 líneas se reemplaza por:

```python
async for event in _run_with_timeout(self.critic_loop, ctx, "CriticLoop"):
    yield event
if ctx.session.state.get("status") == "timeout":
    return

evaluacion = _parse_critic_json(ctx.session.state.get("evaluacion_critica", {}))
ctx.session.state["status"] = "success" if evaluacion.get("acceptable") else "fail"
```

#### Lo que NO cambia

- El loop HITL (Agente 1 + Agente 2 + checkpoint docente) permanece en `BaseAgent` con `asyncio.Queue`. `LoopAgent` no soporta suspensión por input externo asíncrono.
- `MAX_ITERATIONS`, `MAX_HITL_ITERATIONS`, `AGENT_TIMEOUT_SECONDS` y `_run_with_timeout` no cambian.
- `_parse_critic_json` no cambia.

#### Tests

- Agregar `test_critic_loop_callback.py` (o ampliar `test_hitl.py`) con:
  - Caso: callback escala cuando `acceptable=True` → `escalate=True` seteado.
  - Caso: callback setea `critica_previa` cuando `acceptable=False`.
  - No requiere LLM real — mockear `evaluacion_critica` en session state.

---

## Sección 3 — SSE + ADK callbacks para observabilidad en tiempo real

### Problema

El frontend hace `GET /chat/state/{session_id}` cada 2 segundos. El docente no sabe en qué paso está el sistema durante los 5-15 minutos de procesamiento — solo ve un spinner genérico.

### Solución

Agregar un endpoint SSE (`GET /chat/stream/{session_id}`) que el frontend consume con `EventSource`. Los eventos de progreso se pushean a una cola por sesión, alimentada por ADK callbacks en cada agente.

### Eventos definidos

```json
{"type": "agent_start",   "agent": "AnalizadorPACI",   "message": "Analizando PACI..."}
{"type": "agent_end",     "agent": "AnalizadorPACI"}
{"type": "agent_start",   "agent": "Adaptador",         "message": "Adaptando material educativo..."}
{"type": "agent_end",     "agent": "Adaptador"}
{"type": "hitl_required", "attempt": 1, "max_attempts": 3}
{"type": "agent_start",   "agent": "CriticLoop",        "message": "Generando rúbrica (iteración 1/3)..."}
{"type": "agent_end",     "agent": "CriticLoop"}
{"type": "completed",     "workflow_status": "success"}
{"type": "completed",     "workflow_status": "degraded"}
{"type": "completed",     "workflow_status": "hitl_rejected"}
{"type": "error",         "message": "Descripción amigable del error"}
```

### Cambio 1 — `SessionData` agrega cola de eventos

```python
# api/session_store.py
class SessionData(BaseModel):
    ...
    event_queue: asyncio.Queue = Field(default_factory=asyncio.Queue)
```

### Cambio 2 — Factory de callbacks ADK

```python
# agent.py
from api.session_store import SESSIONS

def _make_agent_callbacks(agent_label: str, message: str):
    def before_cb(callback_context) -> None:
        sid = callback_context.state.get("api_session_id", "")
        if sid and sid in SESSIONS:
            SESSIONS[sid].event_queue.put_nowait({
                "type": "agent_start", "agent": agent_label, "message": message
            })
    def after_cb(callback_context) -> None:
        sid = callback_context.state.get("api_session_id", "")
        if sid and sid in SESSIONS:
            SESSIONS[sid].event_queue.put_nowait({
                "type": "agent_end", "agent": agent_label
            })
    return before_cb, after_cb
```

Cada `LlmAgent` y el `LoopAgent` reciben su par de callbacks al construirse en `__init__`.

Los callbacks solo operan en modo API (`api_session_id` presente en state). En modo CLI no hay `SESSIONS`, por lo que la condición `sid and sid in SESSIONS` los neutraliza sin error.

### Cambio 3 — Nuevo endpoint SSE en `chat_router.py`

```python
@router.get("/stream/{session_id}")
async def stream_session(session_id: str):
    sd = SESSIONS.get(session_id)
    if not sd:
        raise HTTPException(status_code=404, detail="Session not found")

    async def generator():
        while True:
            try:
                event = await asyncio.wait_for(sd.event_queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield "data: {\"type\": \"ping\"}\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") in ("completed", "error"):
                break

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

El endpoint `GET /state/{session_id}` se mantiene sin cambios para snapshots al recargar la página.

**Garantía de cierre del SSE:** el bloque `finally` de `workflow_runner.py` debe siempre pushear un evento terminal a `event_queue` para que el generador SSE no quede colgado si el workflow muere sin pasar por el `except`. Patrón:

```python
finally:
    # Garantiza que el SSE generator siempre recibe un evento de cierre
    sd = SESSIONS.get(session_id)
    if sd and sd.event_queue.empty():
        sd.event_queue.put_nowait({"type": "error", "message": "El proceso fue interrumpido inesperadamente."})
    # ... limpieza de archivos y callbacks existente
```

### Cambio 4 — Frontend

**`frontend/src/api.js`** — nueva función:

```js
export function subscribeToSession(sessionId, onEvent, onError) {
    const source = new EventSource(`/chat/stream/${sessionId}`)
    source.onmessage = (e) => {
        const event = JSON.parse(e.data)
        if (event.type !== "ping") onEvent(event)
    }
    source.onerror = () => {
        onError?.()
        source.close()
    }
    return () => source.close()
}
```

**`frontend/src/components/ChatWindow.jsx`** — reemplaza el polling por `subscribeToSession`. El intervalo de polling (`setInterval`) se elimina; los eventos SSE actualizan el estado del componente directamente. Se mantiene una llamada inicial a `GET /state` para hidratar el estado al montar (page refresh).

### Tests

- Agregar `tests/api/test_sse.py`: verifica que el endpoint SSE hace streaming de eventos y cierra cuando llega un evento `completed`.
- El test mockea `SESSIONS[session_id].event_queue` sin levantar el servidor completo.

---

## Archivos modificados

| Archivo | Tipo de cambio |
|---|---|
| `prisma_agents/agents/analizador_paci.py` | Agregar `PerfilPaciOutput`, `output_schema`, ajustar instruction ⚠ |
| `prisma_agents/agents/adaptador.py` | Agregar `PlanificacionAdaptadaOutput`, `output_schema`, ajustar instruction ⚠ |
| `prisma_agents/agents/generador_rubrica.py` | Ajustar referencias `{perfil_paci}` → `{perfil_paci[analisis]}` ⚠ |
| `prisma_agents/agents/critico.py` | Ajustar referencia `{perfil_paci}` → `{perfil_paci[analisis]}`; agregar param `after_agent_callback` ⚠ |
| `prisma_agents/agent.py` | Eliminar `_extract_subject_grade`; agregar `_after_critico_callback`, `_make_agent_callbacks`; construir `LoopAgent`; simplificar `_run_async_impl` |
| `prisma_agents/api/session_store.py` | Agregar `event_queue` a `SessionData` |
| `prisma_agents/api/chat_router.py` | Agregar endpoint `GET /stream/{session_id}` |
| `prisma_agents/api/workflow_runner.py` | Pushear eventos `hitl_required`, `completed`, `error` a `event_queue` |
| `frontend/src/api.js` | Agregar `subscribeToSession` |
| `frontend/src/components/ChatWindow.jsx` | Reemplazar polling por SSE |
| `prisma_agents/tests/test_hitl.py` | Actualizar fixtures para dicts en lugar de strings |
| `prisma_agents/tests/api/test_sse.py` | Nuevo — tests del endpoint SSE |

⚠ = contiene system prompts de alta criticidad normativa, requieren revisión humana antes del merge.

---

## Orden de implementación

1. Setup de rama: `git checkout develop && git checkout -b feature/refactorizaciones-generales`
2. Sección 1: `output_schema` en los dos agentes + actualizar `agent.py` (eliminación regex)
3. Sección 2: `LoopAgent` + callback de terminación + tests
4. Sección 3: `event_queue` en SessionData + callbacks ADK + endpoint SSE + frontend
5. Ejecutar `pytest tests/ -v` completo
6. PR a `develop`

---

## Restricciones y decisiones

- El loop HITL permanece en `BaseAgent` — `LoopAgent` no soporta suspensión por input externo.
- Los callbacks ADK son no-ops en modo CLI (sin `api_session_id` en state) — no hay regresión en `run.py`.
- El endpoint `/state/{session_id}` se conserva — compatibilidad con page refresh.
- `AGENT_TIMEOUT_SECONDS`, `MAX_ITERATIONS`, `MAX_HITL_ITERATIONS` no cambian.
- Los cambios a system prompts requieren revisión humana antes del merge (CLAUDE.md § 10).
