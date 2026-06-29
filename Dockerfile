# ──────────────────────────────────────────────────────────────────────────
# prisma_workflow — Motor de IA multi-agente (FastAPI + google-adk)  →  :8000
# Multi-stage Alpine: (1) builder compila deps en un venv, (2) runtime slim
# copia solo el venv + el codigo.
#
# Requiere variables en runtime/ECS o GitHub Actions secrets, NO horneadas:
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_REGION,
#   GOOGLE_API_KEY, S3_BUCKET, DYNAMO_TABLE, INTERNAL_TOKEN, BD_LOGS, LANGFUSE_*
# ──────────────────────────────────────────────────────────────────────────

# Stage 1: builder
FROM python:3.12-alpine AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv

RUN apk add --no-cache \
    build-base \
    cargo \
    libffi-dev \
    musl-dev \
    openssl-dev \
    python3-dev

WORKDIR /app

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Stage 2: runtime
FROM python:3.12-alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PORT=8000

RUN apk add --no-cache \
    ca-certificates \
    libffi \
    libstdc++ \
    openssl \
    && addgroup -S app \
    && adduser -S app -G app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY . .

# El exportador genera DOCX temporales en el cwd antes de subirlos a S3.
RUN chown -R app:app /app

WORKDIR /app/prisma_agents

USER app

EXPOSE 8000

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
