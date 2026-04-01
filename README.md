# Flujo Multi-Agente PACI (Versión alpha)

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
        Agente 2 — Adaptador
        Reescribe el material educativo base aplicando
        principios DUA y adecuaciones del Decreto 83/2015.
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
        RESULTADO: Perfil PACI + Planificación adaptada + Rúbrica final
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
python run.py <paci_path> <material_path> [prompt_adicional]
```

### Formatos soportados

| Documento           | Formatos                 |
| ------------------- | ------------------------ |
| PACI del estudiante | `.pdf`, `.docx`, `.json` |
| Material base       | `.pdf`, `.docx`          |

> El formato `.json` es para PACI exportados desde formularios digitales (Google Forms, plataformas MINEDUC, etc.)

### Ejemplos

```bash
# PACI en PDF + material en DOCX
python run.py datos/paci.pdf datos/guia_matematicas.docx

# PACI en JSON + material en PDF
python run.py datos/paci_alumno.json datos/texto_historia.pdf

# Con instrucción adicional
python run.py datos/paci.pdf datos/planificacion.docx "Foco en comprensión lectora"
```

---

## Output

El sistema imprime en consola el progreso de cada agente y al final entrega tres resultados:

```
── PERFIL PACI ──────────────────────────────────────────
Diagnóstico, NEE, perfil de aprendizaje, OA priorizados
y consideraciones de evaluación extraídos del PACI.

── PLANIFICACIÓN ADAPTADA ───────────────────────────────
Material educativo base reescrito con adecuaciones DUA
y etiquetas [ACCESO] / [NO SIGNIFICATIVA] / [ADECUACIÓN SIGNIFICATIVA].

── RÚBRICA FINAL ────────────────────────────────────────
Condiciones de aplicación + tabla de rúbrica con 4 niveles
de desempeño + notas para el docente.
```

---

## Estructura del proyecto

```
prisma_agents/
├── agent.py                  # Orquestador principal (PaciWorkflowAgent)
├── run.py                    # Script de ejecución CLI
├── requirements.txt
├── .env                      # API Key (no subir a repositorio)
├── agents/
│   ├── analizador_paci.py    # Agente 1: extrae perfil del PACI
│   ├── adaptador.py          # Agente 2: adapta el material educativo
│   ├── generador_rubrica.py  # Agente 3: genera la rúbrica
│   └── critico.py            # Agente Crítico: evalúa la rúbrica
└── utils/
    └── document_loader.py    # Carga PDF, DOCX y JSON a texto
```

---

## Notas

- El Agente Crítico puede rechazar la rúbrica hasta **3 veces**. En cada rechazo entrega retroalimentación específica al Generador para que la corrija. Si tras 3 intentos no es aprobada, se entrega la última versión generada.
- Los PDF se procesan mediante la **API de Gemini Files**, lo que requiere conexión a internet y consume cuota de la API Key.
- El estado de la sesión (perfil, planificación, rúbrica) se mantiene en memoria durante la ejecución y no se persiste entre corridas.
