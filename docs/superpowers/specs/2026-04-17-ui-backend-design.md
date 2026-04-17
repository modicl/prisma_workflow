# UI Backend Design — PRISMA Chat Interface

**Date:** 2026-04-17  
**Branch:** feature/ui-backend  
**Status:** Approved

---

## Overview

Prototipo de interfaz chat para que el profesor interactúe con el agente multi-agente PACI. El objetivo es mostrar cómo se vería la interfaz a quien deba implementarla en producción.

---

## Architecture

```
React + Tailwind (puerto 5173)  ←→  FastAPI (puerto 8000)  ←→  PaciWorkflowAgent (Google ADK)
```

El frontend es una SPA React que se comunica con FastAPI via REST. FastAPI corre en el mismo Docker que el agente. El agente se ejecuta como background task de FastAPI usando `asyncio`.

---

## Backend — FastAPI Endpoints

### `POST /chat/start`
Recibe los archivos y parámetros, guarda temporalmente en disco, lanza el agente como background task y retorna el `session_id`.

**Form data (multipart):**
- `paci_file`: archivo PDF o DOCX (requerido)
- `material_file`: archivo PDF o DOCX (requerido)
- `prompt`: string opcional
- `school_id`: string, fijo en `"colegio_demo"` desde el frontend

**Response:**
```json
{ "session_id": "uuid-string" }
```

### `GET /chat/{session_id}/state`
Retorna el estado actual de la sesión. El frontend hace polling cada 2 segundos.

**Response:**
```json
{
  "phase": "running | awaiting_hitl | completed | error",
  "messages": [
    { "role": "system | agent", "content": "texto" }
  ],
  "hitl_data": {
    "perfil_paci": "...",
    "planificacion_adaptada": "...",
    "attempt": 1,
    "max_attempts": 6
  } | null,
  "error": "mensaje de error" | null
}
```

### `POST /chat/{session_id}/hitl`
Envía la respuesta del profesor al checkpoint HITL.

**Body:**
```json
{
  "approved": true | false,
  "reason": "texto del rechazo" | null,
  "agent_to_retry": 1 | 2 | null
}
```

**Response:**
```json
{ "ok": true }
```

### `GET /chat/{session_id}/download`
Descarga el archivo DOCX generado al finalizar.

**Response:** archivo DOCX (Content-Disposition: attachment)

### `GET /health`
Healthcheck existente, no modificar.

---

## Backend — Session State (en memoria)

Cada sesión vive en un dict global `SESSIONS: dict[str, SessionData]`.

```python
@dataclass
class SessionData:
    phase: str           # "running" | "awaiting_hitl" | "completed" | "error"
    messages: list[dict]
    hitl_queue: asyncio.Queue   # agente escribe aquí al llegar al checkpoint
    hitl_response: asyncio.Queue  # frontend escribe aquí al responder
    hitl_data: dict | None
    result: dict | None
    docx_path: str | None
    error: str | None
```

El agente reemplaza el `_hitl_checkpoint` de `agent.py` con una versión async que:
1. Escribe `hitl_data` en el state de la sesión
2. Cambia `phase` a `"awaiting_hitl"`
3. Hace `await hitl_response_queue.get()` (bloquea hasta que el profesor responde)
4. Cambia `phase` a `"running"` y continúa

---

## Frontend — Estructura React

```
frontend/
  src/
    components/
      UploadForm.jsx      # Pantalla 1: formulario de carga
      ChatWindow.jsx      # Pantalla 2: chat principal
      HitlCard.jsx        # Card de aprobación/rechazo HITL
      MessageBubble.jsx   # Burbuja de mensaje individual
      Spinner.jsx         # Spinner de carga
    App.jsx               # Router entre Upload y Chat
    api.js                # Funciones fetch al backend
  index.html
  tailwind.config.js
  vite.config.js
  package.json
```

---

## Frontend — Flujo de pantallas

**Pantalla 1 — UploadForm:**
- Inputs: PACI (drag & drop o click), Material base (drag & drop o click), Prompt libre
- `school_id = "colegio_demo"` enviado automáticamente, no visible al profesor
- Formatos aceptados: `.pdf`, `.docx`
- Botón "Iniciar" → llama `POST /chat/start` → navega a ChatWindow con el `session_id`

**Pantalla 2 — ChatWindow:**
- Polling a `GET /chat/{id}/state` cada 2 segundos mientras `phase === "running"`
- Spinner visible mientras `phase === "running"`
- Mensajes del sistema se agregan al chat conforme llegan
- Cuando `phase === "awaiting_hitl"`: detiene polling, muestra `HitlCard`
- Cuando `phase === "completed"`: muestra resultado resumido + botón "Descargar DOCX"
- Cuando `phase === "error"`: muestra mensaje de error en rojo

**HitlCard:**
- Muestra `perfil_paci` y `planificacion_adaptada` en acordeones colapsables
- Botones: "✅ Aprobar" / "❌ Rechazar"
- Si rechaza: input de texto para motivo + selector "¿Qué corregir? Agente 1 / Agente 2"
- Al enviar → llama `POST /chat/{id}/hitl` → reanuda polling

---

## Compatibilidad con código existente

- `run.py` no se modifica — sigue funcionando como CLI.
- `agent.py` (`PaciWorkflowAgent`) no se modifica directamente. El backend crea una versión del runner con un `hitl_checkpoint` async inyectado vía `session.state`.
- El checkpoint HITL actual usa `input()` bloqueante. La API lo reemplaza internamente pasando un callback async al estado de sesión antes de lanzar el runner.
- El `server.py` existente (feedback API) se mantiene. Los nuevos endpoints de chat se agregan en un router separado `api/chat_router.py`.

---

## Archivos a crear

| Archivo | Descripción |
|---|---|
| `prisma_agents/api/chat_router.py` | Endpoints `/chat/*` |
| `prisma_agents/api/session_store.py` | `SessionData` y dict global `SESSIONS` |
| `prisma_agents/api/main.py` | FastAPI app principal que monta `server.py` + `chat_router.py` |
| `frontend/` | Proyecto React + Tailwind (Vite) |

---

## Decisiones de diseño

- **school_id fijo**: `"colegio_demo"` hardcodeado en el frontend para este prototipo.
- **Sin autenticación**: prototipo, no producción.
- **Archivos temporales**: los PDF/DOCX subidos se guardan en `/tmp` y se eliminan tras cargar los documentos.
- **Sin persistencia de sesiones**: si el servidor reinicia, las sesiones se pierden. Aceptable para prototipo.
- **Polling cada 2s**: suficiente para UX sin sobrecargar el backend.
