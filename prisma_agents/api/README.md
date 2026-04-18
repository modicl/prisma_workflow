# Chat API — PRISMA

Base URL: `http://localhost:8000`

---

## POST `/chat/start`

Inicia una sesión del flujo multi-agente. Recibe los documentos del docente, guarda los archivos temporalmente y lanza el agente como tarea en background.

**Content-Type:** `multipart/form-data`

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `paci_file` | File | ✅ | PACI del estudiante (`.pdf` o `.docx`) |
| `material_file` | File | ✅ | Material educativo base (`.pdf` o `.docx`) |
| `prompt` | string | ❌ | Instrucción adicional para el agente |
| `school_id` | string | ❌ | ID del colegio para el repositorio S3 (default: `"colegio_demo"`) |

**Response `201`**
```json
{ "session_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Errores**
- `500` — Error al guardar los archivos subidos en disco.

---

## GET `/chat/{session_id}/state`

Retorna el estado actual de la sesión. El frontend hace polling a este endpoint cada 2 segundos.

**Path params**

| Parámetro | Descripción |
|---|---|
| `session_id` | UUID retornado por `/chat/start` |

**Response `200`**
```json
{
  "phase": "running",
  "messages": [
    { "role": "system", "content": "Documentos recibidos. Iniciando análisis del PACI..." },
    { "role": "agent",  "content": "✅ Proceso completado. La rúbrica adaptada está lista para descargar." }
  ],
  "hitl_data": null,
  "error": null
}
```

**Valores de `phase`**

| Valor | Significado |
|---|---|
| `running` | El agente está procesando |
| `awaiting_hitl` | Esperando revisión del docente (ver `POST /hitl`) |
| `completed` | Flujo completado, `.docx` disponible |
| `error` | Error irrecuperable, ver campo `error` |

**Campo `hitl_data`** (presente solo cuando `phase === "awaiting_hitl"`)
```json
{
  "perfil_paci": "Texto del análisis del Agente 1...",
  "planificacion_adaptada": "Texto de la adaptación del Agente 2...",
  "attempt": 1,
  "max_attempts": 6
}
```

**Errores**
- `404` — Sesión no encontrada.

---

## POST `/chat/{session_id}/hitl`

Envía la decisión del docente en el checkpoint de revisión. Desbloquea el agente para continuar el flujo.

**Path params**

| Parámetro | Descripción |
|---|---|
| `session_id` | UUID de la sesión |

**Body `application/json`**
```json
{
  "approved": true,
  "reason": null,
  "agent_to_retry": null
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `approved` | boolean | `true` aprueba, `false` rechaza |
| `reason` | string \| null | Motivo del rechazo (requerido si `approved: false`) |
| `agent_to_retry` | 1 \| 2 \| null | Qué agente re-ejecutar: `1` = AnalizadorPACI, `2` = Adaptador. Solo cuando `approved: false` |

**Response `200`**
```json
{ "ok": true }
```

**Errores**
- `404` — Sesión no encontrada.
- `409` — La sesión no está en fase `awaiting_hitl`.

---

## GET `/chat/{session_id}/download`

Descarga el archivo `.docx` generado al finalizar el flujo.

**Path params**

| Parámetro | Descripción |
|---|---|
| `session_id` | UUID de la sesión |

**Response `200`**

Archivo `.docx` con header:
```
Content-Disposition: attachment; filename="rubrica_adaptada_<nombre_material>.docx"
Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document
```

**Errores**
- `404` — Sesión no encontrada, proceso no completado, o archivo no encontrado en disco.

---

## GET `/health`

Healthcheck del servidor.

**Response `200`**
```json
{ "status": "ok" }
```

---

## Flujo típico

```
POST /chat/start          → { session_id }
  ↓
GET  /chat/{id}/state     → phase: "running"   (polling cada 2s)
  ↓
GET  /chat/{id}/state     → phase: "awaiting_hitl"  (agente pausado)
  ↓
POST /chat/{id}/hitl      → { ok: true }        (docente aprueba o rechaza)
  ↓
GET  /chat/{id}/state     → phase: "running"   (sigue procesando)
  ↓
GET  /chat/{id}/state     → phase: "completed"
  ↓
GET  /chat/{id}/download  → rubrica.docx
```
