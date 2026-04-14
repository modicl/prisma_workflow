# PACI Workflow — Multi-Agente Psicopedagógico

Sistema multi-agente que procesa documentos PACI (Programa de Adecuación Curricular Individual) y genera planificaciones adaptadas con rúbrica de evaluación, respetando la normativa educacional chilena (Decretos 83/2015, 170/2010 y 67/2018).

---

## Agentes del pipeline

| Agente               | Rol                                                                           |
| -------------------- | ----------------------------------------------------------------------------- |
| **AnalizadorPACI**   | Lee el PACI y extrae perfil del estudiante, diagnóstico NEE, OA y estrategias |
| **Adaptador**        | Adapta la planificación base según el perfil DUA, etiquetando cada adecuación |
| **GeneradorRúbrica** | Genera la rúbrica de evaluación alineada a los OA del PACI                    |
| **Crítico**          | Revisa la rúbrica y decide si es aceptable o propone correcciones             |

---

## Seguridad y Monitoreo

Todos los agentes del pipeline incorporan características vitales de protección y observabilidad:
- **Prevención de Prompt Injection**: Se aíslan los contenidos base (PACI, materiales) en etiquetas `<documento_usuario>` con directivas defensivas estrictas para cada agente.
- **Protección PII**: Eliminación inmediata de archivos de la API de Google File, evitando que el contenido PII resida durante el análisis.
- **Seguimiento de Tokens en PostgreSQL**: Se rastrean los gastos de tokens en tiempo real individualizados por cada agente (`event.usage_metadata`), archivándose de manera segura con aislamiento por sesión (usando `user_id`).

---

## Sistema de Evaluación

El sistema de evaluación (`eval/`) permite medir la calidad de los outputs del pipeline de forma automatizada. Combina dos tipos de evaluación complementarios.

### Tipos de evaluación

#### 1. Compliance checks (deterministas)

Valida estructuralmente los outputs sin usar LLM. Verifica reglas normativas concretas:

- **AnalizadorPACI**: presencia de las 5 secciones requeridas, clasificación de NEE (permanente/transitoria), diagnóstico del Decreto 170, ausencia de eximiciones.
- **Adaptador**: presencia de tags DUA (`[ACCESO]`, `[NO SIGNIFICATIVA]`, `[ADECUACIÓN SIGNIFICATIVA]`), ausencia de eximiciones.
- **GeneradorRúbrica**: 4 niveles de desempeño del D83/2015, sección de condiciones de aplicación, mínimo 2 criterios, notas para el docente, ausencia de eximiciones.
- **Crítico**: JSON válido, campos `acceptable` (bool), `critique` (string) y `suggestions` (lista), y si rechaza: mínimo 2 sugerencias.

Cada agente recibe un score de compliance entre 0 y 1 (checks pasados / total).

#### 2. LLM juez (Gemini)

Evalúa la calidad pedagógica comparando el output contra un **golden set** de referencia. Puntúa en escala 1–5 por dimensiones específicas de cada agente:

| Agente           | Dimensiones evaluadas                                                        |
| ---------------- | ---------------------------------------------------------------------------- |
| AnalizadorPACI   | Fidelidad a los OA del PACI, ausencia de alucinaciones                       |
| Adaptador        | Coherencia NEE↔adaptaciones, aplicación de los 3 pilares DUA                 |
| GeneradorRúbrica | Alineación OA-PACI, descriptores observables, coherencia con nivel funcional |
| Crítico          | Consistencia de decisión, feedback accionable                                |

El juez detecta automáticamente el tipo de NEE del caso (TEA, DI, TEL, Disfasia, TDAH, etc.) a partir del perfil generado por AnalizadorPACI, y carga el golden case correspondiente. Si no existe golden exacto, usa un `fallback` genérico (confianza baja).

### Score final (end-to-end)

Se calcula como promedio ponderado de compliance y LLM juez por agente:

| Agente           | Peso |
| ---------------- | ---- |
| GeneradorRúbrica | 40%  |
| Adaptador        | 25%  |
| AnalizadorPACI   | 20%  |
| Crítico          | 15%  |

**Umbral de aprobación**: score E2E ≥ 0.70 (equivalente a 3.5/5 normalizado).

Una caída de score LLM > 0.50 respecto al reporte anterior del mismo NEE se marca como **regresión**.

---

## Golden Set

Los casos de referencia viven en `eval/golden_set/<NEE>/expected_outputs.json`. Cada archivo contiene los outputs esperados para ese tipo de NEE y un flag `validated` que indica si fue revisado por un experto psicopedagogo.

Casos disponibles: `TEA`, `DI`, `TEL`, `Disfasia`, `TDAH`, `fallback`.

---

## Uso

```bash
# Evaluar con documentos reales (ejecuta el pipeline completo)
python eval/run_eval.py --paci docs_test/paci_test.pdf --material docs_test/material_base_test.pdf

# Evaluar un session state JSON ya guardado
python eval/run_eval.py --session eval/golden_set/TEA/expected_outputs.json

# Evaluar todos los casos del golden set (modo regresión)
python eval/run_eval.py --all

# Guardar output actual como golden case para un NEE (bootstrap)
python eval/run_eval.py --paci <path> --material <path> --save-golden TEA
```

Los reportes se guardan en `eval/reports/` con timestamp y versión del commit.

---

## Estructura del proyecto

```
prisma_agents/
├── dashboard.py          # Dashboard que analiza rendimiento y uso de tokens en DB
├── agent.py              # Orquestador principal
├── run.py                # Entry point del pipeline
├── agents/               # Implementación de cada agente
├── utils/                # Carga de documentos y `token_tracker.py`
└── eval/
    ├── run_eval.py       # Entry point de evaluación
    ├── compliance_checks.py  # Checks deterministas
    ├── llm_judge.py      # LLM-as-judge con Gemini
    ├── golden_set/       # Casos de referencia por NEE
    └── reports/          # Reportes generados
```

## Ejemplo de uso

python run.py datos/paci_juan.pdf datos/guia_ciencias.docx "prompt adicional"

El primer archivo es el perfil PACI, el segundo el material base a adaptar y el tercero es un prompt opcional para guiar a los agentes.
