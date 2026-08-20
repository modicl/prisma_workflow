# prisma_workflow — FastAPI + Google ADK (chat HITL)  →  :8000
# Multi-stage: (1) build (instala deps), (2) runtime slim.

# Stage 1: build
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: runtime
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copiar dependencias instaladas
COPY --from=builder /install /usr/local

# Copiar código fuente
COPY prisma_agents/ ./prisma_agents/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "prisma_agents.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
