# ──────────────────────────────────────────────────────────────────────────
# prisma_workflow — Motor de IA multi-agente (FastAPI + google-adk)  →  :8000
# Multi-stage: (1) builder compila las deps en un venv, (2) runtime slim copia
# solo el venv + el código (imagen final sin toolchain de compilación).
#
# El WORKDIR de ejecución es /app/prisma_agents porque los imports internos son
# relativos a ese paquete (from api..., from agents..., from utils..., from run).
#
# Requiere en runtime (inyectar como env vars en ECS, NO hornear en la imagen):
#   GOOGLE_API_KEY / credenciales Gemini, AWS_*, S3_BUCKET, BD_LOGS,
#   INTERNAL_TOKEN, SUPABASE_URL, LANGFUSE_*  (ver CLAUDE.md §10)
# ──────────────────────────────────────────────────────────────────────────

# Stage 1: builder — instala dependencias en un virtualenv aislado
FROM python:3.12-slim AS builder

# Toolchain para compilar wheels que lo requieran (asyncpg, cryptography, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# venv aislado → fácil de copiar entero al runtime
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Stage 2: runtime — imagen slim sin compiladores
FROM python:3.12-slim AS runtime

# libpq/openssl en runtime para asyncpg/cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 appuser

# Trae el venv ya construido desde el builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY . .

# El exportador de DOCX hace doc.save(nombre_relativo) → escribe en el cwd antes
# de subirlo a S3. Damos propiedad del árbol a appuser para que pueda escribir.
RUN chown -R appuser:appuser /app

# Ejecuta desde el paquete: sus imports asumen prisma_agents/ en el sys.path raíz
WORKDIR /app/prisma_agents

USER appuser

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
