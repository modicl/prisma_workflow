# Chat API — PRISMA

Base URL: `http://localhost:8000` (dev) / `https://tu-backend.com` (producción)

---

## Arquitectura de infraestructura

> **Importante para el desarrollador frontend:** el backend no procesa los documentos directamente al recibir el upload. El flujo es event-driven usando tres servicios AWS. Entender esto es clave para integrar correctamente el frontend.

```
[Frontend]
    │  POST /chat/start (multipart: archivos + prompt)
    ▼
[FastAPI Backend]
  1. Escribe sesión en DynamoDB   → phase = "running"
  2. Sube archivos a S3           → jobs/{session_id}/paci.pdf
                                    jobs/{session_id}/material.pdf
  3. Retorna { session_id }       ← respuesta inmediata, < 1 segundo
                                          │
                              S3 dispara evento PUT automáticamente
                                          ▼
                                   [AWS Lambda]
                              Recibe el evento de S3,
                              extrae el session_id del key,
                              llama POST /internal/run/{session_id}
                              al backend (HTTP)
                                          │
                                          ▼
                              [FastAPI Backend — worker]
                              Descarga los archivos desde S3,
                              ejecuta el flujo multi-agente
                              (puede tardar 5-15 minutos),
                              actualiza DynamoDB en cada cambio de fase
```

**Servicios AWS involucrados:**

| Servicio | Configuración | Rol |
|---|---|---|
| **S3** | Bucket `prisma-workflow` | Archivos de entrada (`jobs/`) y rúbrica generada (`results/`) |
| **DynamoDB** | Tabla `prisma-sessions` | Estado de la sesión — fuente de verdad que lee el endpoint `/state` |
| **Lambda** | `prisma-trigger`, Python 3.12, 128MB, 10s timeout | Trigger que conecta el PUT de S3 con el backend del agente |

**Lo que esto implica para el frontend:**
- `POST /chat/start` responde en < 1s siempre — no bloquea esperando que el agente procese
- El agente puede tardar varios minutos en responder; por eso el frontend usa **polling**
- El estado se lee de DynamoDB, no de memoria del proceso — el frontend puede hacer polling aunque el servidor se reinicie

---

## POST `/chat/start`

Sube los documentos a S3, registra la sesión en DynamoDB y retorna el `session_id`. El agente comienza a ejecutarse de forma asíncrona segundos después (via Lambda → backend).

**Content-Type:** `multipart/form-data`

| Campo | Tipo | Requerido | Descripción |
|---|---|---|---|
| `paci_file` | File | ✅ | PACI del estudiante (`.pdf` o `.docx`) |
| `material_file` | File | ✅ | Material educativo base (`.pdf` o `.docx`) |
| `prompt` | string | ❌ | Instrucción adicional para el agente |
| `school_id` | string | ❌ | ID del colegio (default: `"colegio_demo"`) |

> El nombre original de los archivos no importa. El backend los renombra internamente como `paci.{ext}` y `material.{ext}` al subirlos a S3.

**Response `201`**
```json
{ "session_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Errores**
- `500` — Error al subir archivos a S3 o al escribir en DynamoDB.

---

## GET `/chat/{session_id}/state`

Retorna el estado actual de la sesión. **El frontend debe llamar este endpoint cada 2 segundos** mientras `phase === "running"`.

El estado se lee desde **DynamoDB** — no desde memoria del proceso del backend. Esto significa que el polling funciona aunque el servidor se reinicie.

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

| Valor | Significado | Qué debe hacer el frontend |
|---|---|---|
| `running` | El agente está procesando | Mostrar spinner, seguir haciendo polling |
| `awaiting_hitl` | Esperando revisión del docente | Detener polling, mostrar `HitlCard` con los datos de `hitl_data` |
| `completed` | Flujo completado | Mostrar botón de descarga, detener polling |
| `error` | Error irrecuperable | Mostrar mensaje del campo `error`, detener polling |

**Campo `hitl_data`** — solo presente cuando `phase === "awaiting_hitl"`
```json
{
  "perfil_paci": "Texto completo del análisis del Agente 1...",
  "planificacion_adaptada": "Texto completo de la adaptación del Agente 2...",
  "attempt": 1,
  "max_attempts": 6
}
```

**Errores**
- `404` — Sesión no encontrada en DynamoDB.

---

## POST `/chat/{session_id}/hitl`

Envía la decisión del docente en el checkpoint de revisión. Desbloquea el agente para continuar el flujo. El backend retoma desde donde pausó — no reinicia el proceso.

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
| `approved` | boolean | `true` aprueba y continúa. `false` rechaza y reintenta. |
| `reason` | string \| null | Motivo del rechazo. Requerido si `approved: false`. |
| `agent_to_retry` | 1 \| 2 \| null | Qué agente re-ejecutar al rechazar. `1` = re-analiza el PACI. `2` = re-adapta el material. Requerido si `approved: false`. |

**Ejemplo de rechazo:**
```json
{
  "approved": false,
  "reason": "El perfil no menciona las dificultades motoras del estudiante",
  "agent_to_retry": 1
}
```

**Response `200`**
```json
{ "ok": true }
```

Después de llamar este endpoint, reanudar el polling a `/state` — el agente vuelve a `phase: "running"`.

**Errores**
- `404` — Sesión no encontrada.
- `409` — La sesión no está en fase `awaiting_hitl` (no se puede enviar respuesta HITL ahora).

---

## GET `/chat/{session_id}/download`

Descarga el archivo `.docx` generado. El backend lo lee desde S3 y lo entrega como stream — no se genera ninguna URL intermedia.

Solo disponible cuando `phase === "completed"`.

**Response `200`**
```
Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document
Content-Disposition: attachment; filename="rubrica.docx"
```

Retorna el archivo directamente. Puede usarse como `href` de un `<a>` o llamarse con `fetch()` y crear un Blob.

**Errores**
- `404` — Sesión no encontrada, proceso no completado, o archivo no disponible.

---

## GET `/health`

Healthcheck del servidor.

**Response `200`**
```json
{ "status": "ok" }
```

---

## Flujo completo de integración

```
1. Docente llena el formulario y presiona "Iniciar"
   → POST /chat/start { paci_file, material_file, prompt }
   ← { session_id: "abc-123" }          (< 1 segundo)

2. Frontend navega a la pantalla de chat y comienza polling
   → GET /chat/abc-123/state  (cada 2s)
   ← { phase: "running", messages: [...] }

   [Lambda dispara, backend descarga archivos de S3, agente ejecuta]
   [Pueden pasar varios minutos]

3. Agente pausa en checkpoint HITL
   → GET /chat/abc-123/state
   ← { phase: "awaiting_hitl", hitl_data: { perfil_paci, planificacion_adaptada, attempt: 1, max_attempts: 6 } }

4. Frontend detiene polling y muestra HitlCard con los textos del análisis

5. Docente aprueba o rechaza
   → POST /chat/abc-123/hitl { approved: true }
   ← { ok: true }

6. Frontend reanuda polling
   → GET /chat/abc-123/state
   ← { phase: "running" }

   [Si rechazó → agente re-ejecuta el agente elegido y vuelve al HITL]
   [Si aprobó → agente continúa con Generador de Rúbrica y Agente Crítico]

7. Flujo completado
   → GET /chat/abc-123/state
   ← { phase: "completed" }

8. Docente descarga la rúbrica
   → GET /chat/abc-123/download
   ← rubrica.docx (stream directo desde S3)
```

---

## Notas de implementación

- **Polling:** detener el intervalo cuando `phase` sea `"completed"`, `"error"`, o `"awaiting_hitl"`. Reanudar solo después de enviar la respuesta HITL.
- **Mensajes:** el campo `messages` es acumulativo — cada poll devuelve todos los mensajes desde el inicio. No concatenar con mensajes anteriores, reemplazar el estado completo.
- **HITL máximo 6 intentos:** si el docente rechaza 6 veces sin aprobar, `phase` pasa a `"error"` con el mensaje correspondiente.
- **Timeout de agentes:** si un agente tarda más de 90 segundos sin responder, el sistema reintenta automáticamente hasta 2 veces antes de marcar `phase: "error"`.
