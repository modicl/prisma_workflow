# Flujo Multi-Agente PACI (En desarrollo)

Sistema multi-agente para apoyar a docentes del sistema escolar chileno en la generación de **rúbricas de evaluación adaptadas** para estudiantes con Necesidades Educativas Especiales (NEE), a partir del PACI del alumno y el material educativo base.

Construido con **Google ADK** y el modelo **Gemini 2.5 Flash Lite**.

---

## ¿Qué hace?

Dado el PACI de un estudiante y un material educativo base, el sistema ejecuta automáticamente el siguiente flujo:

```
[PACI del alumno] + [Material base] + [Prompt opcional]
                        ↓
        Agente 1 — AnalizadorPACI
        Extrae NEE, perfil de aprendizaje, OA priorizados
        y consideraciones de evaluación desde el PACI.
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
        con 4 niveles de desempeño y condiciones     │
        de aplicación diferenciadas.                 │
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

## Requisitos

- Python 3.10+
- Una API Key de Google AI Studio ([obtener aquí](https://aistudio.google.com/app/apikey))

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

# 4. Configurar API Key
# Editar el archivo .env:
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=tu_api_key_aqui
```

---

## Uso

```bash
python run.py <paci_path> <material_path> [prompt_adicional] [user_id]
```

| Argumento          | Obligatorio | Descripción                                         |
| ------------------ | ----------- | --------------------------------------------------- |
| `paci_path`        | ✅           | Ruta al PACI del estudiante (`.pdf`, `.docx`, `.json`) |
| `material_path`    | ✅           | Ruta al material educativo base (`.pdf`, `.docx`)   |
| `prompt_adicional` | ❌           | Instrucción extra para los agentes                  |
| `user_id`          | ❌           | ID del docente (se genera UUID automáticamente)     |

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
```

### Formatos soportados

| Documento           | Formatos                 |
| ------------------- | ------------------------ |
| PACI del estudiante | `.pdf`, `.docx`, `.json` |
| Material base       | `.pdf`, `.docx`          |

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

## Estructura del proyecto

```
prisma_agents/
├── agent.py                  # Orquestador principal (PaciWorkflowAgent)
├── run.py                    # Script de ejecución CLI
├── requirements.txt
├── .env                      # API Key (no subir a repositorio)
├── dashboard.py              # Script interactivo de reportes de consumo de tokens API
├── agents/
│   ├── analizador_paci.py    # Agente 1: extrae perfil del PACI
│   ├── adaptador.py          # Agente 2: adapta el material educativo
│   ├── generador_rubrica.py  # Agente 3: genera la rúbrica
│   └── critico.py            # Agente Crítico: evalúa la rúbrica
└── utils/
    ├── document_loader.py    # Carga PDF, DOCX y JSON a texto
    └── token_tracker.py      # Lógica de rastreo de tokens y uso por agente en el EventLoop
```

---

## Notas

- El checkpoint HITL permite hasta **6 intentos** de revisión. Si el docente no aprueba tras 6 intentos, el proceso se cancela sin generar documento.
- El Agente Crítico puede rechazar la rúbrica hasta **3 veces**. Si tras 3 intentos no es aprobada, se entrega la última versión generada.
- Los PDF se procesan mediante la **API de Gemini Files**, lo que requiere conexión a internet y consume cuota de la API Key.
- El estado de la sesión y el histórico de los tokens de cada agente se manejan con identificadores únicos (`user_id`), permitiendo persistir las ejecuciones multi-docente de forma aislada en PostgreSQL.
