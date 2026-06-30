# P.R.I.S.M.A. — Workflow Multi-Agente

Sistema de inteligencia artificial que apoya a docentes del sistema escolar chileno en la generación de **rúbricas de evaluación adaptadas** para estudiantes con Necesidades Educativas Especiales (NEE), a partir del PACI del alumno y el material educativo base.

Construido con **Google Agent Development Kit (ADK)** y el modelo **Gemini 2.5 Flash Lite**, orquestando cuatro agentes especializados bajo un marco normativo embebido (Decreto 170, Decreto 83, Decreto 67).

---

## ¿Qué hace exactamente?

El docente entrega dos documentos:

1. El **PACI del estudiante** (Plan de Adecuación Curricular Individual), en formato PDF o DOCX.
2. El **material educativo base** que usará en clases, también en PDF o DOCX.

El sistema analiza ambos documentos, propone adaptaciones curriculares conforme al Decreto 83/2015 (DUA), espera la revisión y aprobación del docente (**checkpoint HITL**) y luego genera una rúbrica de evaluación adaptada al perfil real del alumno. El resultado final es un archivo `.docx` listo para usar.

### Flujo completo

```
[PACI del alumno]  +  [Material base]  +  [Prompt opcional del docente]
                              │
                    Agente 1 — AnalizadorPACI
                    Extrae NEE, perfil de aprendizaje, OA priorizados,
                    ramo y curso del estudiante.
                              │
               ┌──── CHECKPOINT HITL (máx. 6 intentos) ────┐
               │  Agente 2 — Adaptador                      │
               │  Reescribe el material aplicando DUA       │
               │  y adecuaciones del Decreto 83/2015.       │
               │                                            │
               │  El docente revisa y decide:               │
               │    ✅ Aprueba   → continúa                 │
               │    ❌ Rechaza   → elige qué agente corregir │
               └────────────────────────────────────────────┘
                              │
               ┌──── Loop interno (máx. 3 intentos) ────────┐
               │  Agente 3 — GeneradorRúbrica               │
               │  Genera rúbrica alineada al perfil PACI    │
               │  y a los materiales del colegio (S3).      │
               │              │                             │
               │  Agente Crítico                            │
               │  Valida contra Decreto 83, 170 y 67.       │
               │  Si no es aceptable → retroalimenta ───────┘
                              │
               RESULTADO: rubrica_adaptada_<nombre_material>.docx
```

---

## Requisitos previos

| Requisito | Detalle |
|---|---|
| **Python** | 3.10 o superior |
| **Google AI API Key** | Obtener en [aistudio.google.com](https://aistudio.google.com/app/apikey) — el plan gratuito es suficiente para pruebas |
| **Supabase** | Proyecto con autenticación habilitada (para verificación JWT en el modo API) |
| **AWS** *(opcional)* | Credenciales IAM con `s3:GetObject` sobre el bucket de materiales y acceso a DynamoDB si se usa la arquitectura event-driven |
| **PostgreSQL** *(opcional)* | Solo para el dashboard de consumo de tokens ADK |

> Para una prueba rápida en local **solo se necesita la Google AI API Key**. AWS y PostgreSQL son opcionales.

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone <url-del-repo>
cd prisma_workflow

# 2. Crear y activar entorno virtual
python -m venv venv

# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt
```

---

## Configuración de variables de entorno

Dentro de la carpeta `prisma_agents/` hay un archivo `.env.example`. Cópialo como `.env` en la misma carpeta y completa los valores:

```bash
cp prisma_agents/.env.example prisma_agents/.env
# Luego edita prisma_agents/.env con tus credenciales reales
```

Las variables mínimas para arrancar en local son:

```env
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=tu_api_key_aqui

# Para el modo API (uvicorn), también se necesita Supabase:
SUPABASE_URL=https://<tu-proyecto>.supabase.co
SUPABASE_JWT_SECRET=tu_jwt_secret
```

El resto de las variables (AWS, DynamoDB, Langfuse, PostgreSQL) son opcionales y el sistema opera sin ellas, con las siguientes diferencias:

| Variable ausente | Comportamiento |
|---|---|
| `S3_BUCKET_NAME` | No se consultan materiales de referencia por colegio |
| `S3_BUCKET` / `DYNAMO_TABLE` | El estado de sesión solo se mantiene en memoria |
| `BD_LOGS` | No se registra el historial de tokens ADK |
| `LANGFUSE_*` | Sin trazabilidad de llamadas LLM en Langfuse |

---

## Uso — Modo CLI (ejecución directa)

El modo más simple: se ejecuta el flujo completo desde la terminal, con el checkpoint HITL interactivo en consola.

```bash
cd prisma_agents

python run.py <paci_path> <material_path> [prompt_adicional] [user_id] [school_id]
```

| Argumento | Obligatorio | Descripción |
|---|---|---|
| `paci_path` | ✅ | Ruta al PACI del estudiante (`.pdf`, `.docx`, `.json`) |
| `material_path` | ✅ | Ruta al material educativo base (`.pdf`, `.docx`) |
| `prompt_adicional` | ❌ | Instrucción extra para orientar a los agentes |
| `user_id` | ❌ | ID del docente (se genera un UUID si se omite) |
| `school_id` | ❌ | ID del colegio para consultar materiales en S3 |

### Ejemplos

```bash
# Prueba rápida con los documentos de ejemplo incluidos en el repo
python run.py ../docs_test/paci_test.pdf ../docs_test/material_base_test.pdf

# Con instrucción adicional al agente
python run.py datos/paci.pdf datos/material.docx "Foco en comprensión lectora"

# Con repositorio de materiales del colegio en S3
python run.py datos/paci.pdf datos/material.docx "" "" "colegio_demo"
```

### Formatos de documento soportados

| Tipo | Formatos | Método de extracción |
|---|---|---|
| PACI del estudiante | `.pdf`, `.docx`, `.json` | Gemini OCR / XML / JSON |
| Material base | `.pdf`, `.docx` | Gemini OCR / XML |
| Materiales de referencia S3 | `.pdf`, `.docx` | Gemini OCR / XML |

> **Nota:** `.doc` (Word 97-2003) no está soportado. Guardar como `.docx` o `.pdf` antes de usar.

---

## Uso — Modo API (servidor FastAPI)

El modo API expone los endpoints REST necesarios para que un frontend consuma el flujo de forma asíncrona con streaming SSE.

```bash
cd prisma_agents
uvicorn api.main:app --port 8000 --reload
```

La documentación interactiva queda disponible en `http://localhost:8000/docs`.

### Endpoints principales

| Endpoint | Descripción |
|---|---|
| `POST /chat/start` | Inicia sesión subiendo PACI y material *(solo en desarrollo local; en producción lo maneja `prisma-ms-docs`)* |
| `GET /chat/{id}/stream` | Stream SSE con actualizaciones de progreso en tiempo real |
| `GET /chat/{id}/state` | Estado actual de la sesión (fase, mensajes, datos HITL) |
| `POST /chat/{id}/hitl` | Envía la aprobación o el rechazo del docente |
| `GET /chat/{id}/download` | Descarga el `.docx` generado |
| `GET /health` | Healthcheck |

En la arquitectura de producción, la carga de archivos la realiza el microservicio `prisma-ms-docs` (NestJS), que sube los documentos a S3 y dispara el workflow vía Lambda. Este backend solo recibe la llamada interna de la Lambda (`POST /chat/internal/run/{session_id}`).

---

## Frontend prototipo — solo para previsualización

> ⚠️ **La carpeta `frontend/` contiene un prototipo de interfaz que NO es el producto final.**
>
> Su único propósito es demostrar a grandes rasgos cómo se ve el flujo agéntico desde la perspectiva del usuario: la carga de documentos, el seguimiento de progreso, el checkpoint HITL y la descarga del resultado. No está diseñada para producción ni representa la UI definitiva del sistema.
>
> El frontend real del proyecto es **`prisma-front`** (repositorio separado), construido con React 19 y conectado a los microservicios NestJS correspondientes.

### Cómo correr el prototipo localmente

Requiere Node.js 18+ y que el backend FastAPI esté corriendo en el puerto 8000.

```bash
# 1. Asegurarse de que el backend esté activo
cd prisma_agents
uvicorn api.main:app --port 8000 --reload

# 2. En otra terminal, arrancar el frontend prototipo
cd frontend
npm install
npm run dev
# Abre http://localhost:5173
```

El frontend prototipo funciona con el backend directamente (sin Lambda ni DynamoDB), lo que lo hace ideal para probar el flujo completo en un entorno de desarrollo sin infraestructura AWS.

---

## Checkpoint HITL — Revisión del docente

Después del Agente 2, el flujo se **pausa** y presenta un resumen del análisis y la adaptación para que el docente lo revise. Esto ocurre tanto en la terminal (modo CLI) como a través del endpoint `/hitl` (modo API).

- **Aprueba** → el flujo continúa con la generación de la rúbrica.
- **Rechaza** → el docente indica el motivo y elige qué agente corregir:
  - Agente 1: re-analiza el PACI con el feedback inyectado.
  - Agente 2: re-adapta el material con el feedback inyectado.
- Se permiten hasta **6 intentos** de revisión. Si ninguno es aprobado, el proceso se cancela con estado `hitl_rejected`.

---

## Repositorio de materiales por colegio (S3)

Si se configura `S3_BUCKET_NAME` y se provee un `school_id`, el sistema consulta los materiales educativos del colegio almacenados en S3 para alinear la rúbrica con lo que el docente realmente usa en el aula.

**Estructura del índice en S3:**

```
schools/{school_id}/{ramo}/{curso}/index.json
```

```json
{
  "school_id": "colegio_demo",
  "subject": "matematica",
  "grade": "5basico",
  "materials": [
    {
      "filename": "guia_fracciones.pdf",
      "title": "Cuadernillo fracciones",
      "description": "Ejercicios de fracciones con contextos cotidianos",
      "priority": 1
    }
  ]
}
```

Si el colegio no tiene materiales configurados para el ramo o curso detectado, el flujo continúa normalmente sin ellos.

---

## Estructura del proyecto

```
prisma_workflow/
│
├── requirements.txt              # Dependencias Python
│
├── prisma_agents/
│   ├── .env.example              # Plantilla de variables de entorno (copiar como .env)
│   ├── .env                      # Variables locales (NO subir al repositorio)
│   │
│   ├── agent.py                  # Orquestador principal (PaciWorkflowAgent)
│   ├── run.py                    # Punto de entrada CLI
│   ├── dashboard.py              # Dashboard de consumo de tokens ADK
│   │
│   ├── agents/
│   │   ├── analizador_paci.py    # Agente 1: extrae perfil NEE desde el PACI
│   │   ├── adaptador.py          # Agente 2: adapta el material (DUA + Decreto 83)
│   │   ├── generador_rubrica.py  # Agente 3: genera la rúbrica de evaluación
│   │   └── critico.py            # Agente Crítico: valida la rúbrica contra decretos
│   │
│   ├── api/
│   │   ├── main.py               # FastAPI app (CORS, lifespan, montaje de routers)
│   │   ├── chat_router.py        # Endpoints /chat/* y /internal/run
│   │   ├── auth.py               # Verificación JWT Supabase (ES256 + JWKS)
│   │   ├── session_store.py      # Estado en memoria + sync a DynamoDB
│   │   ├── workflow_runner.py    # Puente FastAPI ↔ agente (descarga S3, callback HITL)
│   │   ├── dynamo_store.py       # Wrapper DynamoDB (create / get / update sesión)
│   │   ├── mock_runner.py        # Runner simulado para pruebas sin LLM
│   │   └── schemas.py            # Modelos Pydantic de request/response
│   │
│   ├── tools/
│   │   └── book_repository.py    # Acceso S3: lee índice, selecciona y transcribe materiales
│   │
│   ├── utils/
│   │   ├── document_loader.py    # Carga PDF (Gemini OCR), DOCX (XML) y JSON a texto
│   │   ├── document_exporter.py  # Genera el .docx final con formato limpio
│   │   ├── curriculum_catalog.py # Normaliza ramo/curso desde texto libre en español
│   │   ├── input_validator.py    # Valida el prompt del docente con LLM
│   │   └── token_tracker.py      # Rastreo de tokens por agente en el EventLoop
│   │
│   ├── eval/                     # Scripts de evaluación y compliance checks
│   └── tests/                    # Pruebas unitarias e integración
│
├── lambda/
│   └── trigger_handler.py        # Lambda trigger: evento S3 → POST /internal/run
│
├── frontend/                     # ⚠️ PROTOTIPO — solo para previsualizar el flujo
│   ├── src/
│   │   ├── App.jsx
│   │   ├── api.js
│   │   └── components/
│   │       ├── UploadForm.jsx    # Pantalla de carga de documentos
│   │       ├── ChatWindow.jsx    # Seguimiento de progreso con polling
│   │       ├── HitlCard.jsx      # Tarjeta de revisión HITL
│   │       └── MessageBubble.jsx
│   ├── vite.config.js            # Proxy /chat → puerto 8000
│   └── package.json
│
└── docs_test/                    # Documentos de ejemplo para pruebas rápidas
    ├── paci_test.pdf
    └── material_base_test.pdf
```

---

## Marco normativo incorporado

Los agentes tienen conocimiento embebido de los siguientes decretos del Ministerio de Educación de Chile:

| Decreto | Contenido relevante |
|---|---|
| **Decreto 170/2010** | Clasificación y criterios diagnósticos de NEE permanentes (TEA, DI, visual, auditiva, disfasia, motora) y transitorias (DA, TEL, TDAH, CIL) |
| **Decreto 83/2015** | Tipos de adecuaciones curriculares (acceso, no significativas, significativas), principios DUA, estructura del PACI |
| **Decreto 67/2018** | Normas de evaluación, calificación (escala 1.0–7.0) y promoción; diversificación obligatoria para NEE |

---

## Notas importantes

- Los archivos PDF se procesan mediante la **API de Gemini Files**, lo que requiere conexión a internet y consume cuota de la API Key. Los archivos se eliminan inmediatamente después de su lectura (protección de datos PII).
- El checkpoint HITL permite hasta **6 intentos** de revisión. Si el docente no aprueba en ese límite, el proceso se cancela.
- El Agente Crítico puede rechazar la rúbrica hasta **3 veces**; si no la aprueba tras los tres intentos, se entrega la última versión generada.
- El repositorio S3 de materiales es de **solo lectura** para el agente: nunca escribe ni modifica materiales.
