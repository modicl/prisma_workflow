# Flujo Multi-Agente PACI (En desarrollo)

Sistema multi-agente para apoyar a docentes del sistema escolar chileno en la generación de **rúbricas de evaluación adaptadas** para estudiantes con Necesidades Educativas Especiales (NEE), a partir del PACI del alumno y el material educativo base.

Construido con **Google ADK** y el modelo **Gemini 2.5 Flash Lite**.

---

## ¿Qué hace?

Dado el PACI de un estudiante y un material educativo base, el sistema ejecuta automáticamente el siguiente flujo:

```
[PACI del alumno] + [Material base] + [Prompt opcional] + [school_id]
                        ↓
        Agente 1 — AnalizadorPACI
        Extrae NEE, perfil de aprendizaje, OA priorizados,
        consideraciones de evaluación, ramo y curso del estudiante.
                        ↓
        Repositorio de Materiales (S3)
        Detecta ramo y curso automáticamente desde el perfil.
        Consulta el índice del colegio en S3 y usa Gemini para
        seleccionar los 3 materiales más relevantes para el perfil.
        Transcribe y entrega el contenido al GeneradorRúbrica.
                        ↓
        ┌─────── CHECKPOINT HITL (máx. 6 intentos) ──────────┐
        │ Agente 2 — Adaptador                               │
        │ Reescribe el material educativo base aplicando     │
        │ principios DUA y adecuaciones del Decreto 83/2015. │
        │                  ↓                                 │
        │ El docente revisa el análisis y la adaptación      │
        │ y decide si aprobar o rechazar.                    │
        │  ✅ Aprueba       → continúa el flujo              │
        │  ❌ Rechaza       → elige qué agente corregir      │
        │     Agente 1      → re-analiza el PACI             │
        │     Agente 2      → re-adapta el material          │
        │  ⛔ Sin aprobación → proceso cancelado             │
        └─────────────────────────────────────────────────────┘
                        ↓
        Agente 3 — GeneradorRúbrica  ←──────────────┐
        Genera una rúbrica de evaluación adaptada    │
        usando los materiales del colegio como       │
        referencia para alinear criterios y niveles  │
        con lo que el docente usa en el aula.        │
                        ↓                            │
        Agente Crítico                               │
        Evalúa la rúbrica contra el Decreto 83/2015, │
        Decreto 170/2010 y Decreto 67/2018.          │
        Si no es aceptable → retroalimentación ──────┘
        (máximo 3 intentos)
                        ↓
        RESULTADO: rubrica_adaptada_<nombre_material>.docx
```

---

## Marco normativo incorporado

Los agentes tienen conocimiento embebido de:

| Decreto              | Contenido relevante                                                                                                                                   |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Decreto 170/2010** | Clasificación y criterios diagnósticos de NEE permanentes (TEA, DI, visual, auditiva, disfasia, motora) y transitorias (DA, TEL, TDAH, CIL)           |
| **Decreto 83/2015**  | Tipos de adecuaciones curriculares (acceso, no significativas, significativas), principios DUA, estructura del PACI, evaluación basada en OA del PACI |
| **Decreto 67/2018**  | Normas de evaluación, calificación (escala 1.0–7.0) y promoción; diversificación obligatoria para alumnos con NEE                                     |

---

## Privacidad, Seguridad y Monitoreo

- **Protección de Datos (PII):** Los documentos PDF se eliminan de la API de Google Files inmediatamente tras su descarga, garantizando que el material sensible de los estudiantes no quede almacenado en sistemas externos.
- **Prevención de Prompt Injection:** Todos los documentos y contenidos alimentados por el usuario quedan restringidos mediante delimitadores XML (`<documento_usuario>`) y contramedidas para mitigar vectores de escape.
- **Aislamiento Multi-usuario:** Múltiples invocaciones de agentes se separan de manera segura bajo un identificador `user_id` dinámico para correcta trazabilidad.
- **Dashboard de Consumo de Tokens:** Script analítico (`dashboard.py`) para revisar el histórico del consumo de la API, analizando visualmente promedios, uso por agente, percentiles y detecciones de atipicidades desde vistas SQL en PostgreSQL.

---

## Repositorio de Materiales por Colegio

Cada colegio puede mantener su propio repositorio de materiales educativos en AWS S3. Cuando el flujo se ejecuta con un `school_id`, el sistema:

1. **Detecta el ramo y curso** automáticamente desde el análisis del PACI (o desde el prompt del docente como fallback), normalizando aliases en español ("Matemáticas", "mate", "5° Básico", "quinto básico" → keys estandarizadas).
2. **Consulta el índice** del colegio en S3 (`schools/{school_id}/{ramo}/{curso}/index.json`), que lista los materiales disponibles con título, descripción y prioridad.
3. **Selecciona los 3 más relevantes** usando Gemini, comparando el índice contra el perfil del estudiante.
4. **Transcribe el contenido** de cada material (PDF vía Gemini Files API con OCR; DOCX via parseo XML local) y lo entrega al GeneradorRúbrica.

**La ventaja clave:** la rúbrica generada no es genérica — se alinea con los materiales, ejercicios y ejemplos que el docente realmente usa en el aula. Si el colegio trabaja con guías de fracciones específicas, la rúbrica refleja exactamente ese enfoque.

**Estructura del índice S3:**
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
      "priority": 1,
      "pages": 30,
      "tags": ["fracciones", "OA3"]
    }
  ]
}
```

> Si no se provee `school_id` o el colegio no tiene materiales para ese ramo/curso, el flujo continúa sin materiales de referencia (comportamiento anterior).

---

## Requisitos

- Python 3.10+
- Una API Key de Google AI Studio ([obtener aquí](https://aistudio.google.com/app/apikey))
- *(Opcional)* Credenciales AWS con permiso `s3:GetObject` sobre el bucket del repositorio de materiales

---

## Instalación

```bash
# 1. Clonar o descargar el repositorio
cd prisma_agents

# 2. Crear y activar entorno virtual
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
# Editar el archivo .env:
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=tu_api_key_aqui

# Opcional — solo si usas el repositorio de materiales S3:
AWS_ACCESS_KEY_ID=tu_access_key
AWS_SECRET_ACCESS_KEY=tu_secret_key
AWS_REGION=us-east-1
S3_BUCKET_NAME=prisma-schools-repos
```

---

## Uso

```bash
python run.py <paci_path> <material_path> [prompt_adicional] [user_id] [school_id]
```

| Argumento          | Obligatorio | Descripción                                            |
| ------------------ | ----------- | ------------------------------------------------------ |
| `paci_path`        | ✅           | Ruta al PACI del estudiante (`.pdf`, `.docx`, `.json`) |
| `material_path`    | ✅           | Ruta al material educativo base (`.pdf`, `.docx`)      |
| `prompt_adicional` | ❌           | Instrucción extra para los agentes                     |
| `user_id`          | ❌           | ID del docente (se genera UUID automáticamente)        |
| `school_id`        | ❌           | ID del colegio para consultar repositorio S3           |

### Probar con los documentos de ejemplo

```bash
cd prisma_agents
python run.py ../docs_test/paci_test.pdf ../docs_test/material_base_test.pdf
```

### Otros ejemplos

```bash
# Con instrucción adicional
python run.py ../docs_test/paci_test.pdf ../docs_test/material_base_test.pdf "Foco en comprensión lectora"

# PACI en JSON + material en DOCX
python run.py datos/paci_alumno.json datos/guia_matematicas.docx

# Con repositorio de materiales del colegio
python run.py datos/paci.pdf datos/material.docx "" "" "colegio_demo"
```

### Formatos soportados

| Documento                    | Formatos                 | Método de extracción                            |
| ---------------------------- | ------------------------ | ----------------------------------------------- |
| PACI del estudiante          | `.pdf`, `.docx`, `.json` | Gemini OCR / XML directo / JSON                 |
| Material base                | `.pdf`, `.docx`          | Gemini OCR / XML directo                        |
| Materiales de referencia S3  | `.pdf`, `.docx`          | Gemini OCR / XML directo                        |

- **PDF**: se sube a la Gemini Files API con instrucción OCR explícita. Funciona tanto con PDFs de texto seleccionable como con documentos escaneados.
- **DOCX**: se extrae texto iterando todos los elementos `<w:t>` del XML interno del archivo. Captura párrafos, **cuadros de texto**, tablas, encabezados y pies de página — incluso en documentos con formularios o layouts complejos que python-docx no lee correctamente.
- **.doc (Word 97-2003)**: no soportado. Guardar como `.docx` o `.pdf`.

> El formato `.json` es para PACI exportados desde formularios digitales (Google Forms, plataformas MINEDUC, etc.)

### Dashboard de Consumo de Tokens

Para consultar las métricas de tokens usados por cada agente y detectar consumo excesivo (se prevee usar una API más adelante):

```bash
python dashboard.py                 # Datos guardados del mes actual
python dashboard.py --all           # Historial completo guardado en BD
python dashboard.py --create-views  # Inicializar/resetear vistas SQL de tracking
python dashboard.py --html          # Exportar informe como un dashboard web local (HTML)
```

---

## HITL — Checkpoint del docente

Después de que el Agente 2 genera la planificación adaptada, el flujo se **pausa** y presenta al docente un resumen del análisis y la adaptación para su revisión.

El docente puede escribir en lenguaje natural — el sistema usa un LLM para clasificar si la respuesta es positiva o negativa, sin lista de palabras clave.

```
══════════════════════════════════════════════════════════════
  REVISIÓN DEL DOCENTE [1/6]
══════════════════════════════════════════════════════════════

── RESUMEN ANÁLISIS PACI (Agente 1) ────────────────────
...

── RESUMEN PLANIFICACIÓN ADAPTADA (Agente 2) ───────────
...

⚠  Quedan 5 intento(s) de revisión.
──────────────────────────────────────────────────────────────
¿Aprueba el análisis y la planificación?
```

Si **rechaza**, el sistema pide la razón (ese mismo mensaje es el feedback) y pregunta qué agente corregir:

```
¿El problema está en el análisis del PACI (1) o en la adaptación del material (2)?
```

- Elige **1** → el feedback se inyecta en el Agente 1, que re-analiza el PACI, y luego el Agente 2 re-adapta el material.
- Elige **2** → el feedback se inyecta solo en el Agente 2, que re-adapta el material.
- Si se agotan los **6 intentos** sin aprobación → el proceso se cancela (`status: hitl_rejected`).

---

## Output

```
══════════════════════════════════════════════════════════
  FLUJO COMPLETADO
══════════════════════════════════════════════════════════
  Estado : success
  Archivo: /ruta/rubrica_adaptada_<nombre_material>.docx
══════════════════════════════════════════════════════════
```

El contenido completo queda en el archivo `.docx` generado.

---

## Prototipo de interfaz (UI)

La rama `feature/ui-backend` incluye un prototipo funcional de la interfaz web. Su propósito es **visualizar el flujo completo** — cómo el docente interactúa con el agente — y servir como referencia de UX para quien deba implementar la interfaz en producción.

### Arquitectura

```
React + Tailwind (puerto 5173)  ←→  FastAPI (puerto 8000)  ←→  PaciWorkflowAgent
```

FastAPI corre en el mismo proceso que el agente. El frontend es una SPA React que se comunica vía REST.

### Cómo correrlo

```bash
# Backend (desde prisma_agents/)
uvicorn api.main:app --port 8000 --reload

# Frontend (desde frontend/)
npm install
npm run dev          # abre en http://localhost:5173
```

### Flujo de pantallas

**Pantalla 1 — Carga de documentos**

El docente sube el PACI del estudiante y el material base (PDF o DOCX), con un campo de prompt libre opcional. Al presionar "Iniciar" se llama `POST /chat/start` y la interfaz navega al chat.

**Pantalla 2 — Chat con el agente**

Muestra mensajes de progreso mientras el agente trabaja. Un spinner indica que el procesamiento está activo (polling cada 2 segundos al backend). Los mensajes aparecen en burbujas a medida que cada etapa del flujo completa.

**Checkpoint HITL — Revisión del docente**

Cuando el Agente 2 termina la adaptación, el flujo se pausa y se muestra una tarjeta de revisión con:
- El análisis del PACI generado por el Agente 1 (en acordeón colapsable)
- La planificación adaptada generada por el Agente 2 (en acordeón colapsable)
- Botones **Aprobar** / **Rechazar**

Si rechaza: el docente escribe el motivo y elige qué agente corregir (Agente 1 o Agente 2). El flujo se reanuda automáticamente con el feedback inyectado.

**Pantalla final**

Al completarse el flujo, aparece un botón para descargar la rúbrica adaptada en formato `.docx`.

### Endpoints de la API

Ver documentación completa en [`prisma_agents/api/README.md`](prisma_agents/api/README.md).

| Endpoint | Descripción |
|---|---|
| `POST /chat/start` | Inicia sesión, lanza el agente en background |
| `GET /chat/{id}/state` | Estado actual (fase, mensajes, datos HITL) |
| `POST /chat/{id}/hitl` | Envía aprobación o rechazo del docente |
| `GET /chat/{id}/download` | Descarga el `.docx` generado |
| `GET /health` | Healthcheck |

### Consideraciones para producción

El prototipo deliberadamente omite aspectos que deberán resolverse en una implementación real:

- **Autenticación:** no hay login ni control de acceso. En producción se requiere al menos autenticación por docente.
- **Persistencia de sesiones:** las sesiones viven en memoria; un reinicio del servidor las pierde. En producción usar Redis u otro store persistente.
- **Limpieza de sesiones:** las sesiones completadas nunca se eliminan del diccionario en memoria. En producción agregar TTL o limpieza periódica.
- **Múltiples colegios:** `school_id` está fijo como `"colegio_demo"`. En producción debe ser configurable por docente o institución.
- **Infraestructura:** frontend y backend separados en contenedores distintos, con un proxy inverso (nginx) sirviendo el frontend y enrutando `/chat/*` al backend.

---

## Estructura del proyecto

```
prisma_agents/
├── agent.py                  # Orquestador principal (PaciWorkflowAgent)
├── run.py                    # Script de ejecución CLI
├── requirements.txt
├── .env                      # API Key + credenciales AWS (no subir a repositorio)
├── dashboard.py              # Script interactivo de reportes de consumo de tokens API
├── api/
│   ├── main.py               # FastAPI app principal
│   ├── chat_router.py        # Endpoints /chat/*
│   ├── session_store.py      # Estado de sesiones en memoria
│   ├── workflow_runner.py    # Puente entre FastAPI y el agente (callback HITL async)
│   └── README.md             # Documentación de la API
├── agents/
│   ├── analizador_paci.py    # Agente 1: extrae perfil del PACI (incluye ramo/curso)
│   ├── adaptador.py          # Agente 2: adapta el material educativo
│   ├── generador_rubrica.py  # Agente 3: genera la rúbrica (usa materiales S3)
│   └── critico.py            # Agente Crítico: evalúa la rúbrica
├── tools/
│   └── book_repository.py   # Acceso S3: lee índice, selecciona y transcribe materiales
└── utils/
    ├── document_loader.py    # Carga PDF (Gemini OCR), DOCX (XML) y JSON a texto
    ├── curriculum_catalog.py # Normaliza ramo/curso desde texto libre en español
    └── token_tracker.py      # Lógica de rastreo de tokens y uso por agente en el EventLoop

frontend/
├── src/
│   ├── App.jsx               # Router entre UploadForm y ChatWindow
│   ├── api.js                # Funciones fetch al backend
│   └── components/
│       ├── UploadForm.jsx    # Pantalla 1: carga de documentos
│       ├── ChatWindow.jsx    # Pantalla 2: chat con polling
│       ├── HitlCard.jsx      # Tarjeta de revisión HITL
│       ├── MessageBubble.jsx # Burbuja de mensaje
│       └── Spinner.jsx       # Indicador de carga
├── vite.config.js            # Proxy /chat → puerto 8000
└── package.json
```

---

## Notas

- El checkpoint HITL permite hasta **6 intentos** de revisión. Si el docente no aprueba tras 6 intentos, el proceso se cancela sin generar documento.
- El Agente Crítico puede rechazar la rúbrica hasta **3 veces**. Si tras 3 intentos no es aprobada, se entrega la última versión generada.
- Los PDF se procesan mediante la **API de Gemini Files**, lo que requiere conexión a internet y consume cuota de la API Key.
- El estado de la sesión y el histórico de los tokens de cada agente se manejan con identificadores únicos (`user_id`), permitiendo persistir las ejecuciones multi-docente de forma aislada en PostgreSQL.
- El repositorio S3 es de **solo lectura** para el agente. Nunca escribe ni modifica materiales.
- Si el colegio no tiene materiales para el ramo/curso detectado, el flujo **continúa normalmente** sin interrupciones — el repositorio S3 es opcional y aditivo.
