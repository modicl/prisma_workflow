# CLAUDE.md — P.R.I.S.M.A.

> Contexto de agente IA para el proyecto. No subir al repositorio.

---

## 1. Project Overview

**P.R.I.S.M.A.** es un sistema multi-agente Python que genera **rúbricas de evaluación adaptadas** para estudiantes con Necesidades Educativas Especiales (NEE) en el sistema escolar chileno. Recibe el PACI (Plan de Adecuaciones Curriculares Individualizadas) del estudiante y un material educativo base, y produce un `.docx` con la rúbrica adaptada. Opera bajo tres marcos normativos legales obligatorios: **Decreto 170/2010** (diagnóstico NEE), **Decreto 83/2015** (adecuaciones curriculares y DUA) y **Decreto 67/2018** (evaluación y calificación). Toda lógica que toque estos decretos es de alta criticidad — un error normativo produce documentos ilegales en el contexto escolar chileno.

El sistema tiene **dos modos de ejecución**: CLI directo (`run.py`) y API REST (FastAPI + event-driven AWS). El procesamiento de un flujo completo tarda entre 5 y 15 minutos por la cadena de llamadas LLM.

---

## 2. Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│  FRONTEND — React/Vite (puerto 5173, proxy /chat → 8000)        │
│  UploadForm → ChatWindow → HitlCard → Download                  │
└──────────────────────┬──────────────────────────────────────────┘
                       │ POST /chat/start (multipart)
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│  BACKEND API — FastAPI/Uvicorn (puerto 8000)                     │
│  chat_router.py  →  session_store (dict + DynamoDB)             │
│  workflow_runner.py  →  PaciWorkflowAgent                       │
└──────────┬──────────────────┬───────────────────────────────────┘
           │ dev (directo)     │ prod (event-driven)
           ▼                   ▼
    BackgroundTask       S3 PUT Event
    run_workflow          → Lambda (trigger_handler.py)
                          → POST /internal/run/{session_id}
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  AGENTE ORQUESTADOR — PaciWorkflowAgent (agent.py)              │
│                                                                  │
│  [Agente 1 — AnalizadorPACI]  ← google-adk LlmAgent             │
│        ↓ perfil_paci (session.state)                            │
│  [BookRepository]  ← S3 prisma-schools-repos (solo lectura)     │
│        ↓ materiales_referencia                                  │
│  ┌─ Loop HITL (max 3) ──────────────────────────┐               │
│  │  [Agente 2 — Adaptador]                      │               │
│  │  Checkpoint docente → asyncio.Queue          │               │
│  └──────────────────────────────────────────────┘               │
│  ┌─ Loop Crítico (max 3) ────────────────────────┐              │
│  │  [Agente 3 — GeneradorRúbrica]               │               │
│  │  [Agente Crítico]  → JSON {acceptable, ...}  │               │
│  └──────────────────────────────────────────────┘               │
│        ↓ rubrica_adaptada_<nombre>.docx                         │
└─────────────────────────────────────────────────────────────────┘

AWS:
  S3 "prisma-workflow"      → archivos de jobs (entrada) y results (salida)
  S3 "prisma-schools-repos" → repositorio materiales por colegio (read-only)
  DynamoDB "prisma-sessions" → estado de sesiones (TTL: 7 días)
  Lambda "prisma-trigger"   → bridge S3 event → /internal/run

PostgreSQL (BD_LOGS):
  → Tracking de tokens por agente/sesión (dashboard.py, sql/views.sql)
```

**Modelo LLM:** `gemini-2.5-flash-lite` (todos los agentes)  
**SDK:** `google-adk==1.28.0`, `google-genai==1.70.0`

---

## 3. Repository Structure

```
prisma_workflow/
├── prisma_agents/               # Paquete principal del sistema de agentes
│   ├── agent.py                 # ⚠️ Orquestador PaciWorkflowAgent — lógica de flujo central
│   ├── run.py                   # Entrypoint CLI
│   ├── requirements.txt         # ⚠️ Dependencias fijadas — no cambiar versiones sin testear
│   ├── .env                     # ⚠️ Secretos locales — NUNCA subir al repo
│   ├── .env.template            # Plantilla sin valores reales
│   │
│   ├── agents/                  # Definiciones de cada LlmAgent
│   │   ├── analizador_paci.py   # ⚠️ Contiene sistema prompt con D170/D83 embebido
│   │   ├── adaptador.py         # ⚠️ Contiene sistema prompt con D83/DUA embebido
│   │   ├── generador_rubrica.py # ⚠️ Contiene sistema prompt con D67/D83 embebido
│   │   └── critico.py           # ⚠️ Output schema JSON estricto {acceptable, critique, suggestions}
│   │
│   ├── api/                     # Capa REST (FastAPI)
│   │   ├── main.py              # App FastAPI — carga .env, monta routers
│   │   ├── chat_router.py       # Endpoints públicos /chat/* e interno /internal/run
│   │   ├── session_store.py     # SessionData en memoria + HITL_CALLBACKS registry
│   │   ├── workflow_runner.py   # Puente FastAPI ↔ PaciWorkflowAgent (descarga S3, HITL callback)
│   │   ├── dynamo_store.py      # Wrapper DynamoDB (create/get/update)
│   │   └── server.py            # Uvicorn runner alternativo
│   │
│   ├── tools/
│   │   └── book_repository.py   # Acceso S3 materiales del colegio — solo lectura
│   │
│   ├── utils/
│   │   ├── document_loader.py   # Carga PDF (Gemini OCR), DOCX (XML), JSON → texto
│   │   ├── document_exporter.py # Genera el .docx de salida
│   │   ├── curriculum_catalog.py # ⚠️ Normaliza ramo/curso desde texto libre español
│   │   └── tracing.py           # Inicialización de Langfuse + GoogleADKInstrumentor (idempotente)
│   │
│   ├── eval/                    # Suite de evaluación (no es test unitario)
│   │   ├── compliance_checks.py # Checks deterministas contra D170/D83/D67
│   │   ├── llm_judge.py         # Evaluador LLM de calidad de outputs
│   │   ├── run_eval.py          # Runner de la suite de evaluación
│   │   └── db_migrations.py     # Migraciones schema BD de logs
│   │
│   ├── tests/                   # Tests unitarios pytest
│   │   ├── test_hitl.py
│   │   ├── test_curriculum_catalog.py
│   │   ├── test_book_repository.py
│   │   └── api/
│   │       ├── test_chat_router.py
│   │       └── test_session_store.py
│   │
│   ├── sql/
│   │   └── views.sql            # ⚠️ Vistas SQL para dashboard tokens — no modificar sin BD migración
│   │
│   └── token_reports/           # JSONs de consumo por sesión (generados automáticamente)
│
├── frontend/                    # SPA React/Vite
│   ├── src/
│   │   ├── App.jsx              # Router Upload ↔ Chat
│   │   ├── api.js               # Funciones fetch al backend
│   │   └── components/          # UploadForm, ChatWindow, HitlCard, MessageBubble, Spinner
│   ├── vite.config.js           # ⚠️ Proxy /chat → localhost:8000
│   └── package.json
│
├── lambda/
│   └── trigger_handler.py       # ⚠️ Lambda standalone — stdlib Python puro, sin dependencias externas
│
├── docs/                        # PDFs legales de referencia
│   ├── DTO-170_21-ABR-2010.pdf  # ⚠️ Decreto 170 — fuente normativa
│   ├── Decreto-83-2015.pdf      # ⚠️ Decreto 83 — fuente normativa
│   └── Decreto-67_31-DIC-2018.pdf # ⚠️ Decreto 67 — fuente normativa
│
└── docs_test/                   # Documentos de prueba
    ├── paci_test.pdf
    ├── material_base_test.pdf
    └── synthetic/               # PACIs sintéticos por diagnóstico (TEA, TDAH, DI, TEL, Disfasia)
```

---

## 4. Development Commands

### Instalación

```bash
cd prisma_agents
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
cp .env.template .env          # → completar GOOGLE_API_KEY mínimo
```

```bash
cd frontend
npm install
```

### Ejecución

| Comando | Qué hace |
|---|---|
| `python run.py <paci> <material>` | CLI: ejecuta flujo completo con HITL interactivo en terminal |
| `python run.py <paci> <material> "" "" "colegio_demo"` | CLI con repositorio S3 del colegio |
| `uvicorn api.main:app --port 8000 --reload` | API REST en desarrollo (desde `prisma_agents/`) |
| `npm run dev` | Frontend Vite en `http://localhost:5173` (desde `frontend/`) |

### Build

| Comando | Qué hace |
|---|---|
| `npm run build` | Build de producción del frontend (genera `frontend/dist/`) |
| `vite preview` | Preview del build de producción |

### Test

| Comando | Qué hace |
|---|---|
| `pytest tests/` | Todos los tests unitarios (desde `prisma_agents/`) |
| `pytest tests/test_hitl.py` | Solo tests del flujo HITL |
| `pytest tests/test_curriculum_catalog.py` | Solo normalización ramo/curso |
| `pytest tests/api/` | Solo tests de la capa API |
| `python eval/run_eval.py` | Suite de evaluación de calidad (usa LLM — consume cuota) |

### Lint / Format

No hay linter configurado en el proyecto. Convención: seguir PEP 8, usar type hints.

---

## 5. Environment & Configuration

Archivo de referencia: `prisma_agents/.env.template`  
Archivo activo: `prisma_agents/.env` (no subir al repo)

### Requeridas siempre

| Variable | Secreto | Descripción |
|---|---|---|
| `GOOGLE_API_KEY` | ✅ | API Key de Google AI Studio |
| `GOOGLE_GENAI_USE_VERTEXAI` | ❌ | `0` para AI Studio, `1` para Vertex AI. Default: `0` |

### Opcional — Observabilidad (Langfuse)

| Variable | Secreto | Descripción |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | ✅ | Public key del proyecto Langfuse. Obtener en cloud.langfuse.com → Settings → API Keys |
| `LANGFUSE_SECRET_KEY` | ✅ | Secret key del proyecto Langfuse |
| `LANGFUSE_HOST` | ❌ | URL del servidor Langfuse. Default: `https://us.cloud.langfuse.com` |

Sin estas claves el sistema funciona normalmente — el tracing simplemente no se activa (ver `utils/tracing.py`).

**Nota PII:** los spans de ADK capturados incluyen el contenido de los prompts LLM, que en PRISMA contiene datos de estudiantes (diagnósticos, información personal de menores). Configurar retención de datos y controles de acceso apropiados en el proyecto Langfuse antes de habilitar en producción.

### Opcional — Persistencia de sesiones ADK (PostgreSQL)

| Variable | Secreto | Descripción |
|---|---|---|
| `BD_LOGS` | ✅ | Connection string PostgreSQL para persistir sesiones ADK entre pasos del agente. Si no se configura, se usa sesión en memoria (válido para CLI/dev). Ej: `postgresql+asyncpg://user:pass@host:5432/db` |

### Requeridas para repositorio de materiales (S3 colegios)

| Variable | Secreto | Descripción |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | ✅ | IAM key con política `PrismaSchoolsReposReadOnly` |
| `AWS_SECRET_ACCESS_KEY` | ✅ | IAM secret correspondiente |
| `AWS_REGION` | ❌ | Default: `us-east-1` |
| `S3_BUCKET_NAME` | ❌ | Default: `prisma-schools-repos` |

### Requeridas para arquitectura event-driven (producción)

| Variable | Secreto | Descripción |
|---|---|---|
| `S3_BUCKET` | ❌ | Bucket de jobs/resultados. Ej: `prisma-workflow`. Vacío → modo local |
| `DYNAMO_TABLE` | ❌ | Tabla DynamoDB. Ej: `prisma-sessions`. Vacío → solo memoria |
| `INTERNAL_TOKEN` | ✅ | Secreto compartido backend ↔ Lambda para `/internal/run` |

### Variables Lambda (`prisma-trigger`)

| Variable | Secreto | Descripción |
|---|---|---|
| `BACKEND_INTERNAL_URL` | ❌ | URL base del backend. Ej: `https://tu-backend.com` |
| `INTERNAL_TOKEN` | ✅ | El mismo valor que en el backend |

**Modo local sin AWS:** dejar `S3_BUCKET`, `DYNAMO_TABLE` e `INTERNAL_TOKEN` vacíos. El sistema usa disco local y diccionario en memoria.

---

## 6. Key Architectural Decisions

- **DECISIÓN:** Orquestador custom (`PaciWorkflowAgent(BaseAgent)`) en lugar de `SequentialAgent`/`LoopAgent` de ADK — **RAZÓN:** los loops HITL y de crítico requieren lógica condicional, reintentos y comunicación inter-agente que `SequentialAgent` no soporta nativamente — **IMPLICACIÓN:** no refactorizar a agentes ADK declarativos sin reimplementar toda la lógica de estado; el estado viaja en `ctx.session.state` como dict mutable.

- **DECISIÓN:** Comunicación HITL vía `asyncio.Queue` (`sd.hitl_response_queue`) en modo API — **RAZÓN:** el agente corre en una corutina de background y necesita suspenderse hasta recibir la respuesta del docente sin bloquear el event loop — **IMPLICACIÓN:** nunca reemplazar la Queue por polling a BD; el callback en `HITL_CALLBACKS[session_id]` debe existir antes de que el agente llegue al checkpoint.

- **DECISIÓN:** DynamoDB como store de estado + dict en memoria — **RAZÓN:** el polling del frontend (`GET /state` cada 2s) debe ser fast sin depender de que el proceso del agente esté vivo; DynamoDB permite que el frontend lea estado incluso si el backend reinicia — **IMPLICACIÓN:** siempre escribir a DynamoDB **antes** de cambiar estado en memoria cuando el orden importa (ej. crear sesión en Dynamo antes del PUT a S3 en `/chat/start`).

- **DECISIÓN:** Lambda trigger liviano (`stdlib` Python puro, sin dependencias) — **RAZÓN:** la Lambda solo necesita leer el evento S3, leer session_id de DynamoDB y llamar un endpoint HTTP; dependencias externas aumentan cold start — **IMPLICACIÓN:** no agregar `boto3` ni librerías de terceros a `trigger_handler.py`; usar solo `urllib.request` y módulos stdlib.

- **DECISIÓN:** Eliminación inmediata de PDFs de la Gemini Files API tras descarga — **RAZÓN:** compliance PII — los PDFs contienen datos de menores de edad (diagnósticos, RUT); no pueden quedar en sistemas externos — **IMPLICACIÓN:** este comportamiento en `document_loader.py` es un requerimiento legal, no una optimización.

- **DECISIÓN:** Delimitadores XML `<documento_usuario>` en todos los inputs al LLM — **RAZÓN:** mitigación de prompt injection — los documentos PACI/material pueden contener texto adversarial — **IMPLICACIÓN:** no remover estos delimitadores para "simplificar" los prompts; son una barrera de seguridad activa.

- **DECISIÓN:** Agente Crítico con `output_schema` JSON estricto `{acceptable, critique, suggestions}` — **RAZÓN:** el parseador `_parse_critic_json` en `agent.py` depende de este contrato para decidir si el loop de rúbrica continúa — **IMPLICACIÓN:** si se modifica el schema del Agente Crítico, actualizar `_parse_critic_json` y todos los tests relacionados.

- **DECISIÓN:** Normalización de ramo/curso con `curriculum_catalog.py` en español — **RAZÓN:** los docentes escriben "mate", "Matemáticas", "5° Básico", "quinto" — se necesita mapear a keys canónicas para consultar S3 — **IMPLICACIÓN:** antes de agregar un ramo o grado nuevo al catálogo, agregar también todos sus aliases comunes en español.

- **DECISIÓN:** Timeout por agente de 90s con 2 reintentos automáticos — **RAZÓN:** Gemini 2.5 Flash Lite puede demorarse en documentos grandes; sin timeout el flujo puede colgar indefinidamente — **IMPLICACIÓN:** las constantes `AGENT_TIMEOUT_SECONDS`, `MAX_RETRIES_ON_TIMEOUT`, `RETRY_DELAY_SECONDS` en `agent.py` están calibradas empíricamente; no reducir sin medir latencia real.

- **DECISIÓN:** Repositorio S3 de materiales es aditivo y opcional — **RAZÓN:** si el colegio no tiene materiales o el ramo/curso no se reconoce, el flujo debe continuar y generar una rúbrica genérica — **IMPLICACIÓN:** nunca hacer fallar el flujo completo por un error del BookRepository; capturar excepciones y continuar con `materiales_referencia = ""`.

---

## 7. Code Style & Conventions

### Nombrado

- Archivos: `snake_case.py` (Python), `PascalCase.jsx` (React)
- Clases: `PascalCase` — ej. `PaciWorkflowAgent`, `SessionData`
- Funciones/métodos: `snake_case` — funciones privadas con prefijo `_`
- Variables: `snake_case`; constantes de módulo en `UPPER_SNAKE_CASE`
- Agentes ADK: factory function `make_<nombre>_agent()` → retorna `LlmAgent`

### Estructura de módulos

- Cada agente vive en su propio archivo en `agents/`; expone solo una función factory `make_*`
- Los system prompts de los agentes se definen como strings en el mismo archivo del agente, no en archivos separados
- `agent.py` es el único lugar que instancia y coordina agentes — no instanciar agentes desde la API o herramientas
- La API no importa `agent.py` directamente; usa `workflow_runner.py` como capa de adaptación

### Patrones preferidos

- Estado del flujo viaja **exclusivamente** en `ctx.session.state` (dict) — no usar variables de instancia del agente para estado entre steps
- El resultado de cada agente se escribe en `ctx.session.state` con una key fija: `perfil_paci`, `planificacion_adaptada`, `rubrica`, `evaluacion_critica`
- Feedback HITL se inyecta en `ctx.session.state["hitl_feedback_a1"]` / `["hitl_feedback_a2"]`
- Para leer documentos: siempre usar `document_loader.py` — no usar `pdfplumber` ni `python-docx` directamente en los agentes
- Manejo de errores en la API: `HTTPException` de FastAPI — no `raise Exception` crudo

### Anti-patrones — NUNCA hacer

- No llamar `_get_genai_client()` directamente desde un agente ADK — los agentes deben usar ADK, no el SDK directo (excepto `_classify_response` en `agent.py` que es intencional)
- No usar `.doc` (Word 97-2003) — el loader no lo soporta; documentar el error al usuario
- No guardar secretos en el código fuente ni en comentarios
- No hacer el flujo principal fallar silenciosamente — todo error de status debe quedar en `ctx.session.state["status"]`
- No asumir que `school_id` existe — siempre tratar como opcional

### Tipos compartidos

- `SessionData`: `prisma_agents/api/session_store.py`
- `CheckResult`, `AgentComplianceReport`: `prisma_agents/eval/compliance_checks.py`

---

## 8. Testing Strategy

### Estructura

```
prisma_agents/tests/
├── test_hitl.py              # Tests de clasificación HITL (_classify_response, _hitl_checkpoint)
├── test_curriculum_catalog.py # Tests de normalización ramo/curso
├── test_book_repository.py   # Tests del acceso S3 (con mocks de boto3)
└── api/
    ├── test_chat_router.py   # Tests de endpoints FastAPI
    └── test_session_store.py # Tests de SessionData y sync a DynamoDB
```

### Cobertura obligatoria

- Toda lógica de clasificación HITL (`_classify_response`) — debe cubrir aprobado, rechazado, respuesta inesperada
- Normalización de ramo/curso (`curriculum_catalog.py`) — es la pieza más propensa a regresar con texto libre en español
- Checks de compliance normativo (`compliance_checks.py`) — validan contratos legales
- Endpoints de la API: `/start`, `/state`, `/hitl`, `/download`

### Ejecución antes de commit

```bash
# Desde prisma_agents/
pytest tests/ -v --tb=short
```

### Mocks disponibles

- `unittest.mock.patch("agent._get_genai_client")` → mockear llamadas Gemini en tests de HITL
- `unittest.mock.patch("boto3.client")` → mockear S3/DynamoDB en tests del BookRepository y API
- No usar mocks para `curriculum_catalog.py` — es lógica pura sin I/O

### Eval (no es test unitario)

`eval/run_eval.py` corre el flujo completo con documentos sintéticos en `docs_test/synthetic/`. Consume cuota de la API Key. Ejecutar solo cuando se modifiquen los system prompts de los agentes o la lógica de orquestación.

---

## 9. External Services & Integrations

### Google AI / Gemini

- **Propósito:** Motor LLM de todos los agentes y clasificador HITL. Procesamiento OCR de PDFs vía Gemini Files API.
- **Configuración local:** API Key en `.env` (`GOOGLE_API_KEY`). Obtener en [aistudio.google.com](https://aistudio.google.com/app/apikey).
- **Modelo:** `gemini-2.5-flash-lite` — hardcodeado en cada archivo `agents/*.py` como constante `MODEL`
- **Límites importantes:** Los PDFs subidos a Gemini Files API se eliminan inmediatamente tras descarga (requerimiento PII). Rate limits dependen del tier de la API Key.
- **Cliente SDK:** `google.genai.Client()` — inicializado lazy en `agent.py:_get_genai_client()`. ADK lo gestiona internamente para los LlmAgents.

### PostgreSQL (BD de logs)

- **Propósito:** Persistencia del historial de tokens por agente/sesión para el dashboard de consumo.
- **Configuración local:** Cualquier instancia PostgreSQL; connection string en `BD_LOGS`. Las vistas SQL se inicializan con `python dashboard.py --create-views`.
- **Schema:** Ver `prisma_agents/sql/views.sql`
- **Cliente/SDK:** `asyncpg` — usado en `token_tracker.py`
- **Límites:** No es crítico para el flujo del agente; si falla la BD de logs, el flujo continúa.

### AWS S3 — Repositorio de materiales (`prisma-schools-repos`)

- **Propósito:** Almacén de materiales educativos por colegio, ramo y curso. Solo lectura para el sistema.
- **Estructura de paths:** `schools/{school_id}/{ramo}/{curso}/index.json` + archivos `.pdf`/`.docx`
- **Configuración local:** Credenciales IAM con política `PrismaSchoolsReposReadOnly` en `.env`. Sin credenciales → flujo continúa sin materiales.
- **Cliente/SDK:** `boto3` en `tools/book_repository.py`
- **Límites:** El agente selecciona máximo 3 materiales por sesión. Timeout implícito por `AGENT_TIMEOUT_SECONDS`.

### AWS S3 — Jobs y resultados (`prisma-workflow`)

- **Propósito:** Almacenamiento temporal de archivos de entrada (`jobs/`) y rúbricas generadas (`results/`).
- **Configuración local:** Vaciar `S3_BUCKET` en `.env` para usar disco local (`/tmp/prisma_uploads/`).
- **Cliente/SDK:** `boto3` en `chat_router.py` y `workflow_runner.py`

### AWS DynamoDB (`prisma-sessions`)

- **Propósito:** Store de estado de sesiones para polling del frontend y resiliencia ante reinicios del backend.
- **TTL automático:** 7 días por ítem.
- **Configuración local:** Vaciar `DYNAMO_TABLE` en `.env` para usar solo dict en memoria.
- **Cliente/SDK:** `boto3` en `api/dynamo_store.py`

### AWS Lambda (`prisma-trigger`)

- **Propósito:** Trigger event-driven — recibe PUT event de S3, lee session_id de DynamoDB, llama `POST /internal/run/{session_id}`.
- **Código:** `lambda/trigger_handler.py` — stdlib Python puro, sin dependencias externas.
- **Variables de entorno Lambda:** `BACKEND_INTERNAL_URL` + `INTERNAL_TOKEN`
- **Activado en:** producción únicamente. En dev el backend lanza el flujo directamente como BackgroundTask.

---

## 10. Agent Behavior Rules

### Archivos de SOLO LECTURA — No modificar sin revisión humana

- `prisma_agents/agents/*.py` — los system prompts contienen conocimiento normativo legal compilado manualmente
- `prisma_agents/sql/views.sql` — cambios requieren migración de BD
- `lambda/trigger_handler.py` — debe mantenerse stdlib Python puro
- `docs/*.pdf` — fuentes legales de referencia
- `prisma_agents/requirements.txt` — cambios de versión pueden romper la integración ADK/genai

### Cambios que REQUIEREN confirmación humana

- Modificar límites de iteración (`MAX_ITERATIONS`, `MAX_HITL_ITERATIONS` en `agent.py`)
- Cambiar el modelo LLM (`MODEL = "gemini-2.5-flash-lite"`) en cualquier agente
- Alterar el contrato del Agente Crítico (output schema JSON)
- Modificar la lógica de eliminación de PDFs de Gemini Files API (requerimiento legal)
- Cambiar el timeout de agentes (`AGENT_TIMEOUT_SECONDS`)
- Cualquier cambio a los delimitadores XML de prompt injection (`<documento_usuario>`)

### Orden preferido para explorar el código

1. Leer `README.md` para entender el flujo general
2. Leer `agent.py` para entender el orquestador antes de tocar cualquier agente
3. Leer el archivo del agente específico (en `agents/`) para entender su contrato de I/O
4. Leer `api/session_store.py` y `api/chat_router.py` antes de modificar la API
5. Leer `eval/compliance_checks.py` para entender qué se valida normativamente

### Cómo interpretar errores comunes

| Error | Causa probable | Acción |
|---|---|---|
| `HITL callback for session X not found` | La sesión no registró su callback antes de llegar al checkpoint | Verificar `HITL_CALLBACKS[session_id]` en `workflow_runner.py` |
| `status: timeout` en session.state | El agente no respondió en 90s tras 3 intentos | Revisar logs de Gemini; puede ser cuota o documento muy largo |
| `status: hitl_rejected` | El docente rechazó 3 veces sin aprobar | Comportamiento esperado — no es un error del sistema |
| `status: fail` | Agente Crítico rechazó la rúbrica 3 veces | Se entrega la última versión generada de todas formas |
| `_parse_critic_json` recibe string | ADK no aplicó el output_schema | Verificar que `output_schema` esté definido en `critico.py` |
| `SessionData not found (API mode)` | Lambda disparó `/internal/run` antes de que `/start` creara la sesión | Verificar orden: DynamoDB create ANTES de S3 PUT en `/start` |
| PDF `.doc` no procesado | Word 97-2003 no soportado | Instrucciones al usuario: guardar como `.docx` o `.pdf` |

### Qué hacer cuando un comando falla

- `pytest` falla → leer el traceback completo; los tests de HITL requieren que el path `prisma_agents/` esté en `sys.path`
- `uvicorn` no arranca → verificar que el `.env` tenga al menos `GOOGLE_API_KEY`; revisar que el `venv` esté activado
- `pip install -r requirements.txt` falla → verificar Python 3.10+; `google-adk` requiere Python ≥ 3.10
- La suite de eval falla por cuota → es esperado en cuentas gratuitas; usar documentos sintéticos más cortos en `docs_test/synthetic/`

### Contexto pedagógico crítico

Los diagnósticos en el sistema tienen consecuencias legales y educativas reales. Ante cualquier duda sobre si un cambio podría afectar la correcta interpretación de un diagnóstico NEE o alterar las adecuaciones curriculares generadas:

1. No hacer el cambio
2. Reportarlo al responsable del proyecto
3. Consultar la fuente normativa en `docs/`
