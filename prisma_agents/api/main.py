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
    from utils.tracing import setup_tracing
    setup_tracing()
    yield
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception:
        pass


app = FastAPI(title="PRISMA Chat API", lifespan=lifespan)
app.add_middleware(CORSMiddleware)

app.include_router(chat_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
