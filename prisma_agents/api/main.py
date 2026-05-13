from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(Path(__file__).parent.parent / ".env")

from api.chat_router import router as chat_router

_CORS_HEADERS = [
    (b"access-control-allow-origin", b"*"),
    (b"access-control-allow-methods", b"GET, POST, PUT, DELETE, OPTIONS, PATCH"),
    (b"access-control-allow-headers", b"*"),
]


class CORSMiddleware:
    """ASGI-level CORS — agrega el header en todos los responses incluyendo streaming."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") == "OPTIONS":
            await send({
                "type": "http.response.start",
                "status": 204,
                "headers": _CORS_HEADERS + [(b"content-length", b"0"), (b"access-control-max-age", b"86400")],
            })
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", [])) + _CORS_HEADERS
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cors)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="P.R.I.S.M.A. API",
    description="""
Motor de agentes IA que ejecuta el flujo multi-agente de generación de rúbricas adaptadas
para estudiantes con Necesidades Educativas Especiales (NEE) bajo el marco normativo chileno
(Decreto 170, Decreto 83, Decreto 67).

**Este servicio no recibe archivos del cliente.** La carga de documentos y el registro
de sesiones es responsabilidad del microservicio `prisma-ms-docs` (NestJS, puerto 3002).

## Arquitectura del sistema

```
[Frontend]
    │
    ▼
[prisma-ms-docs] ── sube archivos a S3 ──────────────────┐
    │               crea sesión en DynamoDB               │
    │                                                     │ S3 PUT event
    │                                              [Lambda prisma-trigger]
    │                                                     │
    │                                                     ▼
    └──────────────────────────────────► [ESTE SERVICIO — FastAPI]
                                          POST /chat/internal/run/{session_id}
                                               │
                                          workflow multi-agente (5–15 min)
                                               │ SSE
                                          GET /chat/{session_id}/stream
```

## Responsabilidades de este servicio

- Ejecutar el workflow de agentes al ser invocado por la Lambda (`/internal/run`)
- Exponer el estado de la sesión en tiempo real via SSE (`/stream`) y polling (`/state`)
- Gestionar la revisión HITL del docente (`/hitl`)
- Servir el DOCX generado (`/download`)

## Variables de entorno requeridas

| Variable | Descripción |
|---|---|
| `GOOGLE_API_KEY` | API Key de Google AI Studio (requerida siempre) |
| `S3_BUCKET` | Bucket de jobs y resultados. Vacío → modo local |
| `DYNAMO_TABLE` | Tabla DynamoDB de sesiones. Vacío → solo memoria |
| `INTERNAL_TOKEN` | Secreto compartido backend ↔ Lambda `prisma-trigger` |

## Flujo HITL

El workflow puede pausarse y requerir revisión docente (`phase: awaiting_hitl`).
El cliente escucha el SSE stream y responde via `POST /chat/{session_id}/hitl`
al recibir un evento `hitl_required`. El flujo tiene un máximo de 3 iteraciones HITL.
    """,
    version="1.0.0",
    openapi_tags=[
        {
            "name": "Chat",
            "description": "Ciclo de vida de una sesión: iniciar, seguir progreso, cancelar y descargar resultado.",
        },
        {
            "name": "HITL",
            "description": "Human-in-the-loop: revisión docente del plan de adaptación curricular generado por el Agente Adaptador.",
        },
        {
            "name": "Internal",
            "description": (
                "Uso exclusivo de la Lambda `prisma-trigger`. "
                "Autenticado con el header `X-Internal-Token`. "
                "No llamar directamente desde el cliente."
            ),
        },
        {
            "name": "Dev",
            "description": (
                "Endpoints de conveniencia para desarrollo local. "
                "En producción estas funciones las realiza `prisma-ms-docs`. "
                "No exponer ni llamar en entornos productivos."
            ),
        },
        {
            "name": "System",
            "description": "Health check y estado del servicio.",
        },
    ],
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware)

app.include_router(chat_router)


@app.get("/health", tags=["System"], summary="Health check")
async def health():
    return {"status": "ok"}
