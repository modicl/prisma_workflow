# Guía de integración SSE — Frontend PRISMA

**Rama:** `feature/refactorizaciones-generales`  
**Para:** Desarrollador del frontend productivo  
**Contexto:** El prototipo en `frontend/` usa React/Vite. El frontend productivo puede estar en cualquier stack. Este documento describe el contrato con el backend y los cambios necesarios, independiente del framework.

---

## Qué cambió en el backend

Se agregó un nuevo endpoint SSE que reemplaza el polling cada 2s:

| Endpoint antiguo (sigue funcionando) | Nuevo endpoint |
|---|---|
| `GET /chat/{session_id}/state` — snapshot del estado, sigue disponible para hidratación en page refresh | `GET /chat/{session_id}/stream` — stream SSE en tiempo real |

---

## Contrato del endpoint SSE

```
GET /chat/{session_id}/stream
Content-Type: text/event-stream
Cache-Control: no-cache
```

El servidor empuja eventos en formato SSE estándar (`data: <json>\n\n`) mientras el workflow corre. El stream cierra automáticamente cuando llega un evento `completed` o `error`.

### Todos los tipos de evento posibles

```jsonc
// El agente X empieza a procesar
{ "type": "agent_start", "agent": "Agente 1", "message": "Analizando PACI..." }

// El agente X terminó
{ "type": "agent_end", "agent": "Agente 1" }

// Mensaje del sistema (aparece en el historial de chat)
{ "type": "message", "role": "system", "content": "Documentos recibidos. Iniciando análisis..." }
{ "type": "message", "role": "agent",  "content": "✅ Proceso completado. La rúbrica está lista." }

// El docente debe revisar — el flujo queda pausado hasta que responda
{
  "type": "hitl_required",
  "attempt": 1,
  "max_attempts": 3,
  "hitl_data": {
    "perfil_paci":            "<texto del análisis del PACI>",
    "planificacion_adaptada": "<texto de la adaptación>",
    "attempt":                1,
    "max_attempts":           3
  }
}

// Flujo completado con éxito (workflow_status: "success" o "degraded")
{ "type": "completed", "workflow_status": "success" }
{ "type": "completed", "workflow_status": "degraded" }

// Error terminal (timeout, rechazo HITL, excepción)
{ "type": "error", "message": "Descripción amigable del error" }

// Keepalive (ignorar en el cliente, solo mantiene la conexión viva)
{ "type": "ping" }
```

### Valores posibles del campo `agent` en `agent_start` / `agent_end`

| Valor | Qué significa |
|---|---|
| `"Agente 1"` | AnalizadorPACI — primera pasada |
| `"Agente 1 (retry)"` | AnalizadorPACI re-ejecutado tras feedback del docente |
| `"Agente 2"` | Adaptador — adapta el material al perfil |
| `"Agente 3 (it.1)"` / `"(it.2)"` / `"(it.3)"` | GeneradorRubrica — cada iteración |
| `"Agente Crítico (it.1)"` / ... | AgenteCritico — evalúa la rúbrica |

### Secuencia típica de eventos en un flujo completo

```
message        — "Documentos recibidos..."
agent_start    — "Agente 1"  / "Analizando PACI..."
agent_end      — "Agente 1"
agent_start    — "Agente 2"  / "Adaptando material educativo..."
agent_end      — "Agente 2"
message        — "Revisión requerida — intento 1 de 3..."
hitl_required  — con hitl_data completo
                 [docente responde vía POST /chat/{id}/hitl]
agent_start    — "Agente 3 (it.1)" / "Generando rúbrica..."
agent_end      — "Agente 3 (it.1)"
agent_start    — "Agente Crítico (it.1)" / "Evaluando calidad..."
agent_end      — "Agente Crítico (it.1)"
message        — "✅ Proceso completado..."
completed      — { workflow_status: "success" }
                 [stream cierra]
```

---

## Cambios que debe aplicar el frontend productivo

### 1. Suscripción SSE al iniciar una sesión

Inmediatamente después de recibir el `session_id` desde `POST /chat/start`, abrir la conexión SSE:

```js
// Usando la API nativa del navegador (compatible con todos los frameworks)
const source = new EventSource(`/chat/${sessionId}/stream`)

source.onmessage = (e) => {
  const event = JSON.parse(e.data)
  if (event.type !== 'ping') handleEvent(event)
}

source.onerror = () => {
  // El stream cerró inesperadamente — hacer un GET /state para sincronizar
  source.close()
  fetchStateOnce(sessionId)
}

// Limpiar al desmontar el componente / salir de la pantalla
function cleanup() { source.close() }
```

### 2. Manejador de eventos

```js
function handleEvent(event) {
  switch (event.type) {

    case 'agent_start':
      // Mostrar en UI: qué agente está corriendo ahora
      // event.message contiene el texto en español, ej: "Analizando PACI..."
      setCurrentStep(event.message)
      break

    case 'agent_end':
      setCurrentStep('')
      break

    case 'message':
      // Agregar al historial de chat
      // event.role: "system" | "agent"
      // event.content: texto del mensaje
      appendMessage({ role: event.role, content: event.content })
      break

    case 'hitl_required':
      // El docente debe revisar — pausar UI y mostrar el checkpoint
      // event.hitl_data.perfil_paci          → análisis del PACI
      // event.hitl_data.planificacion_adaptada → propuesta de adaptación
      // event.hitl_data.attempt               → intento actual (1, 2, 3)
      // event.hitl_data.max_attempts          → máximo de intentos (3)
      showHitlCheckpoint(event.hitl_data)
      setPhase('awaiting_hitl')
      break

    case 'completed':
      // event.workflow_status: "success" | "degraded"
      // "success"  → rúbrica aprobada por el Agente Crítico
      // "degraded" → rúbrica generada como mejor esfuerzo (no pasó QA)
      setPhase('completed')
      setWorkflowStatus(event.workflow_status)
      break

    case 'error':
      // event.message: descripción amigable del error
      setPhase('error')
      setErrorMessage(event.message)
      break
  }
}
```

### 3. Respuesta al checkpoint HITL

Cuando el docente aprueba o rechaza, enviar la respuesta vía `POST`:

```js
// Aprobación
POST /chat/{session_id}/hitl
Content-Type: application/json

{ "approved": true }

// Rechazo con feedback
POST /chat/{session_id}/hitl
Content-Type: application/json

{
  "approved": false,
  "reason": "El análisis no refleja correctamente las adecuaciones significativas",
  "agent_to_retry": 1   // 1 = re-analizar PACI,  2 = re-adaptar material
}
```

Tras enviar la respuesta, el workflow se reanuda automáticamente y los eventos SSE siguen llegando por el stream ya abierto — no hay que reconectar.

### 4. Hidratación en page refresh

Si el usuario recarga la página, el stream SSE ya no tiene los eventos anteriores (la cola se pierde). Hacer una llamada única a `GET /chat/{session_id}/state` al montar para recuperar el estado actual:

```js
GET /chat/{session_id}/state

// Respuesta:
{
  "phase":           "running" | "awaiting_hitl" | "completed" | "error",
  "messages":        [ { "role": "system"|"agent", "content": "..." }, ... ],
  "hitl_data":       null | { perfil_paci, planificacion_adaptada, attempt, max_attempts },
  "error":           null | "mensaje de error",
  "workflow_status": null | "success" | "degraded" | "hitl_rejected" | "error"
}
```

Si `phase === "completed"` o `phase === "error"`, no abrir SSE — suscribirse al stream de una sesión terminada devuelve el evento terminal inmediatamente y cierra.

### 5. Descarga del documento

Disponible solo cuando `phase === "completed"`:

```
GET /chat/{session_id}/download
→ application/vnd.openxmlformats-officedocument.wordprocessingml.document
→ Content-Disposition: attachment; filename="rubrica.docx"
```

---

## Estados de la sesión y qué mostrar

| `phase` | `workflow_status` | Qué mostrar |
|---|---|---|
| `"running"` | — | Progreso en tiempo real con `currentStep` |
| `"awaiting_hitl"` | — | Formulario de revisión del docente |
| `"completed"` | `"success"` | Botón de descarga + mensaje de éxito |
| `"completed"` | `"degraded"` | Botón de descarga + advertencia de calidad |
| `"error"` | `"hitl_rejected"` | Mensaje: el análisis no obtuvo aprobación |
| `"error"` | `"error"` | Mensaje de error genérico |

---

## Lo que NO cambia respecto al prototipo

- `POST /chat/start` — misma interfaz (multipart form: `paci_file`, `material_file`, `prompt`, `school_id`)
- `POST /chat/{id}/hitl` — mismo contrato
- `GET /chat/{id}/download` — mismo contrato
- `GET /chat/{id}/state` — sigue disponible, solo se usa para hidratación

---

## Notas de implementación

- El stream SSE no requiere autenticación adicional (igual que el resto de endpoints `/chat/*`).
- Si el servidor está detrás de un proxy nginx, asegurarse de configurar `proxy_buffering off` y `proxy_read_timeout` mayor a 30s para que los keepalives pasen.
- El evento `ping` llega cada ~25s de inactividad para mantener viva la conexión — ignorarlo en el cliente.
- Los eventos `agent_start` / `agent_end` son opcionales de mostrar en UI — lo mínimo útil es mostrar `event.message` de `agent_start` como indicador de progreso.
