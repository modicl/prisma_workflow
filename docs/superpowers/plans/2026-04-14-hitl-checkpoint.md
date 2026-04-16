# HITL Checkpoint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar una pausa interactiva (Human-in-the-Loop) después del Agente 2 para que el profesor apruebe o rechace el análisis y la planificación antes de continuar al loop de rúbrica.

**Architecture:** La función `_hitl_checkpoint()` en `agent.py` maneja toda la interacción con el profesor vía `input()`. Usa una llamada LLM síncrona para clasificar la respuesta como aprobación o rechazo. El feedback se inyecta en `session.state` como texto formateado que los agentes leen mediante sus placeholders de instrucción. Si se agotan los 6 intentos sin aprobación, el flujo cancela con `status = "hitl_rejected"`.

**Tech Stack:** Python 3.11+, Google ADK 1.28.0, google-genai 1.70.0, pytest

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `prisma_agents/run.py` | Modificar | Inicializar `hitl_feedback_a1` y `hitl_feedback_a2` en session state |
| `prisma_agents/agents/analizador_paci.py` | Modificar | Agregar placeholder `{hitl_feedback_a1}` al final de `INSTRUCTION` |
| `prisma_agents/agents/adaptador.py` | Modificar | Agregar placeholder `{hitl_feedback_a2}` al final de `INSTRUCTION` |
| `prisma_agents/agent.py` | Modificar | Constante `MAX_HITL_ITERATIONS`, función `_hitl_checkpoint()`, loop HITL en `_run_async_impl` |
| `prisma_agents/tests/test_hitl.py` | Crear | Tests unitarios de `_hitl_checkpoint()` y `_classify_response()` |

---

## Task 1: Inicializar claves HITL en session state (`run.py`)

**Files:**
- Modify: `prisma_agents/run.py:94-100`

- [ ] **Step 1: Agregar las dos claves nuevas al estado inicial de sesión**

En `run.py`, localizar el bloque `state={...}` dentro de `run_workflow()` (línea ~94) y agregar las dos claves:

```python
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=effective_user_id,
        state={
            "paci_document": paci_text,
            "material_document": material_text,
            "critica_previa": "",
            "hitl_feedback_a1": "",   # feedback para AnalizadorPACI
            "hitl_feedback_a2": "",   # feedback para Adaptador
        },
    )
```

- [ ] **Step 2: Commit**

```bash
git add prisma_agents/run.py
git commit -m "feat(hitl): inicializa claves hitl_feedback en session state"
```

---

## Task 2: Agregar placeholders de feedback en instrucciones de agentes

**Files:**
- Modify: `prisma_agents/agents/analizador_paci.py`
- Modify: `prisma_agents/agents/adaptador.py`

- [ ] **Step 1: Agregar placeholder en `analizador_paci.py`**

Al final de la variable `INSTRUCTION`, antes de las comillas de cierre `"""`, agregar:

```python
INSTRUCTION = """...
(todo el texto existente sin cambios)
...
REGLA CRÍTICA: NO incluyas saludos, introducciones, ni comentarios conversacionales \
(ej. '¡Por supuesto!', 'Aquí tienes el análisis...'). Entrega EXCLUSIVAMENTE el \
contenido solicitado usando los encabezados indicados.

{hitl_feedback_a1}"""
```

El `{hitl_feedback_a1}` debe quedar como última línea dentro del string. Cuando está vacío el agente no ve nada adicional.

- [ ] **Step 2: Agregar placeholder en `adaptador.py`**

Al final de la variable `INSTRUCTION`, antes de las comillas de cierre `"""`, agregar:

```python
INSTRUCTION = """...
(todo el texto existente sin cambios)
...
REGLA CRÍTICA: NO incluyas saludos, introducciones, ni comentarios conversacionales \
(ej. '¡Absolutamente!', 'Procederé a adaptar...', 'A continuación presento...'). \
Entrega EXCLUSIVAMENTE el material educativo adaptado y nada más.

{hitl_feedback_a2}"""
```

- [ ] **Step 3: Commit**

```bash
git add prisma_agents/agents/analizador_paci.py prisma_agents/agents/adaptador.py
git commit -m "feat(hitl): agrega placeholders de feedback en instrucciones de agentes"
```

---

## Task 3: Implementar `_classify_response()` y sus tests

**Files:**
- Modify: `prisma_agents/agent.py`
- Create: `prisma_agents/tests/__init__.py`
- Create: `prisma_agents/tests/test_hitl.py`

- [ ] **Step 1: Crear directorio de tests**

```bash
mkdir -p prisma_agents/tests
touch prisma_agents/tests/__init__.py
```

- [ ] **Step 2: Escribir tests para `_classify_response()`**

Crear `prisma_agents/tests/test_hitl.py`:

```python
"""Tests para la función _classify_response() del módulo agent."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch


def _make_mock_response(text: str):
    mock = MagicMock()
    mock.text = text
    return mock


class TestClassifyResponse:
    """Tests para clasificación LLM de respuesta del profesor."""

    def test_respuesta_aprobada_devuelve_true(self):
        from agent import _classify_response
        with patch("agent.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = _make_mock_response("APROBADO")
            assert _classify_response("si, está bien") is True

    def test_respuesta_rechazada_devuelve_false(self):
        from agent import _classify_response
        with patch("agent.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = _make_mock_response("RECHAZADO")
            assert _classify_response("no me parece correcto") is False

    def test_respuesta_con_espacios_y_minusculas(self):
        from agent import _classify_response
        with patch("agent.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = _make_mock_response("  aprobado  ")
            assert _classify_response("dale") is True

    def test_respuesta_inesperada_del_llm_devuelve_false(self):
        """Si el LLM no devuelve APROBADO ni RECHAZADO, se trata como rechazo."""
        from agent import _classify_response
        with patch("agent.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = _make_mock_response("No sé")
            assert _classify_response("quizás") is False
```

- [ ] **Step 3: Ejecutar tests para verificar que fallan**

```bash
cd prisma_agents
python -m pytest tests/test_hitl.py::TestClassifyResponse -v
```

Esperado: `ImportError` o `AttributeError` porque `_classify_response` no existe aún.

- [ ] **Step 4: Implementar `_classify_response()` en `agent.py`**

Agregar el import de `genai` al inicio de `agent.py`:

```python
from google import genai
```

Agregar la función después de los imports y antes de `MAX_ITERATIONS`:

```python
_CLASSIFY_PROMPT = (
    "Clasifica si el siguiente mensaje de un docente indica APROBACIÓN o RECHAZO "
    "del trabajo presentado. Responde únicamente con \"APROBADO\" o \"RECHAZADO\".\n\n"
    "Mensaje: \"{respuesta}\""
)


def _classify_response(respuesta: str) -> bool:
    """Usa un LLM para determinar si la respuesta del profesor es aprobación o rechazo.

    Retorna True si es aprobación, False si es rechazo o respuesta inesperada.
    """
    client = genai.Client()
    prompt = _CLASSIFY_PROMPT.format(respuesta=respuesta)
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
    )
    return response.text.strip().upper() == "APROBADO"
```

- [ ] **Step 5: Ejecutar tests para verificar que pasan**

```bash
cd prisma_agents
python -m pytest tests/test_hitl.py::TestClassifyResponse -v
```

Esperado: 4 tests en PASS.

- [ ] **Step 6: Commit**

```bash
git add prisma_agents/agent.py prisma_agents/tests/__init__.py prisma_agents/tests/test_hitl.py
git commit -m "feat(hitl): implementa _classify_response con LLM y sus tests"
```

---

## Task 4: Implementar `_hitl_checkpoint()` y sus tests

**Files:**
- Modify: `prisma_agents/agent.py`
- Modify: `prisma_agents/tests/test_hitl.py`

- [ ] **Step 1: Agregar tests para `_hitl_checkpoint()`**

Agregar al final de `prisma_agents/tests/test_hitl.py`:

```python
class TestHitlCheckpoint:
    """Tests para la función _hitl_checkpoint()."""

    def _make_state(self, perfil="Diagnóstico: TDAH. Estrategias: tiempo extendido.", plan="Adecuación: NO SIGNIFICATIVA."):
        return {"perfil_paci": perfil, "planificacion_adaptada": plan}

    def test_aprobacion_devuelve_true(self):
        from agent import _hitl_checkpoint
        state = self._make_state()
        with patch("builtins.input", return_value="si, todo bien"), \
             patch("agent._classify_response", return_value=True):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=1, max_attempts=6)
        assert aprobado is True
        assert razon == ""
        assert agente == 0

    def test_rechazo_elige_agente_1(self):
        from agent import _hitl_checkpoint
        state = self._make_state()
        with patch("builtins.input", side_effect=["no, el análisis está incompleto", "1"]), \
             patch("agent._classify_response", return_value=False):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=1, max_attempts=6)
        assert aprobado is False
        assert razon == "no, el análisis está incompleto"
        assert agente == 1

    def test_rechazo_elige_agente_2(self):
        from agent import _hitl_checkpoint
        state = self._make_state()
        with patch("builtins.input", side_effect=["la adaptación no aplica DUA", "2"]), \
             patch("agent._classify_response", return_value=False):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=1, max_attempts=6)
        assert aprobado is False
        assert razon == "la adaptación no aplica DUA"
        assert agente == 2

    def test_entrada_invalida_de_agente_repregunta(self):
        from agent import _hitl_checkpoint
        state = self._make_state()
        # Primera respuesta inválida ("3"), segunda válida ("2")
        with patch("builtins.input", side_effect=["no está bien", "3", "2"]), \
             patch("agent._classify_response", return_value=False):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=1, max_attempts=6)
        assert agente == 2

    def test_ultimo_intento_rechazado_retorna_agente_0(self):
        """En el último intento, si rechaza no se pregunta por agente — se cancela."""
        from agent import _hitl_checkpoint
        state = self._make_state()
        with patch("builtins.input", return_value="no, sigue mal"), \
             patch("agent._classify_response", return_value=False):
            aprobado, razon, agente = _hitl_checkpoint(state, attempt=6, max_attempts=6)
        assert aprobado is False
        assert agente == 0
```

- [ ] **Step 2: Ejecutar tests para verificar que fallan**

```bash
cd prisma_agents
python -m pytest tests/test_hitl.py::TestHitlCheckpoint -v
```

Esperado: `ImportError` porque `_hitl_checkpoint` no existe aún.

- [ ] **Step 3: Implementar `_hitl_checkpoint()` en `agent.py`**

Agregar después de `_classify_response()`:

```python
def _hitl_checkpoint(
    state: dict, attempt: int, max_attempts: int
) -> tuple[bool, str, int]:
    """Pausa interactiva para que el profesor apruebe o rechace los resultados.

    Retorna: (aprobado, razon, agente_a_reiniciar)
    - aprobado=True  → continúa el flujo; razon="" y agente_a_reiniciar=0
    - aprobado=False → razon contiene el feedback del profesor
                       agente_a_reiniciar: 1 o 2, o 0 si se agotaron intentos
    """
    restantes = max_attempts - attempt

    print("\n" + "═" * 60)
    print(f"  REVISIÓN DEL DOCENTE [{attempt}/{max_attempts}]")
    print("═" * 60)

    print("\n── RESUMEN ANÁLISIS PACI (Agente 1) ────────────────────")
    print(state.get("perfil_paci", "(sin datos)"))

    print("\n── RESUMEN PLANIFICACIÓN ADAPTADA (Agente 2) ───────────")
    print(state.get("planificacion_adaptada", "(sin datos)"))

    if restantes > 0:
        print(f"\n⚠  Quedan {restantes} intento(s) de revisión.")
    else:
        print("\n⚠  Este es el último intento de revisión.")

    print("\n" + "─" * 60)
    respuesta = input("¿Aprueba el análisis y la planificación? ").strip()

    if _classify_response(respuesta):
        return True, "", 0

    # Último intento agotado — cancela sin preguntar agente
    if attempt >= max_attempts:
        print(
            "\n✗ Proceso cancelado: se agotaron los intentos de revisión "
            "sin obtener aprobación del docente.\n"
        )
        return False, respuesta, 0

    # Pide agente a re-ejecutar
    while True:
        eleccion = input(
            "¿El problema está en el análisis del PACI (1) "
            "o en la adaptación del material (2)? "
        ).strip()
        if eleccion in ("1", "2"):
            return False, respuesta, int(eleccion)
        print("  Por favor ingresa 1 o 2.")
```

- [ ] **Step 4: Ejecutar tests para verificar que pasan**

```bash
cd prisma_agents
python -m pytest tests/test_hitl.py::TestHitlCheckpoint -v
```

Esperado: 5 tests en PASS.

- [ ] **Step 5: Commit**

```bash
git add prisma_agents/agent.py prisma_agents/tests/test_hitl.py
git commit -m "feat(hitl): implementa _hitl_checkpoint con interaccion CLI"
```

---

## Task 5: Integrar el loop HITL en `_run_async_impl` y sus tests

**Files:**
- Modify: `prisma_agents/agent.py`
- Modify: `prisma_agents/tests/test_hitl.py`

- [ ] **Step 1: Agregar tests de integración del loop HITL**

Agregar al final de `prisma_agents/tests/test_hitl.py`:

```python
import asyncio
import pytest


class TestHitlLoop:
    """Tests de integración para el loop HITL en PaciWorkflowAgent."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _make_ctx(self, state: dict):
        """Crea un InvocationContext mock con session.state."""
        ctx = MagicMock()
        ctx.session.state = state
        return ctx

    def _empty_async_gen(self):
        """Generador async vacío para simular agentes que no emiten eventos."""
        return
        yield  # hace que sea un generador async

    def test_aprobacion_directa_continua_flujo(self):
        """Si el profesor aprueba en el primer intento, el estado no es hitl_rejected."""
        from agent import PaciWorkflowAgent
        agent = PaciWorkflowAgent()
        state = {
            "paci_document": "doc paci",
            "material_document": "material",
            "critica_previa": "",
            "hitl_feedback_a1": "",
            "hitl_feedback_a2": "",
            "perfil_paci": "Diagnóstico: TDAH",
            "planificacion_adaptada": "Adecuación NO SIGNIFICATIVA",
            "rubrica": "rubrica generada",
            "evaluacion_critica": '{"acceptable": true, "critique": "", "suggestions": []}',
        }
        ctx = self._make_ctx(state)

        async def run():
            with patch("agent._hitl_checkpoint", return_value=(True, "", 0)), \
                 patch("agent._run_with_timeout", side_effect=lambda ag, c, l: self._empty_async_gen()):
                async for _ in agent._run_async_impl(ctx):
                    pass

        self._run(run())
        assert state.get("status") != "hitl_rejected"

    def test_intentos_agotados_cancela_con_hitl_rejected(self):
        """Si se agotan los 6 intentos, status queda en hitl_rejected."""
        from agent import PaciWorkflowAgent
        agent = PaciWorkflowAgent()
        state = {
            "paci_document": "doc paci",
            "material_document": "material",
            "critica_previa": "",
            "hitl_feedback_a1": "",
            "hitl_feedback_a2": "",
            "perfil_paci": "Diagnóstico: TDAH",
            "planificacion_adaptada": "Adecuación",
        }
        ctx = self._make_ctx(state)

        # Rechaza siempre con agente 0 (último intento agotado)
        async def run():
            with patch("agent._hitl_checkpoint", return_value=(False, "siempre mal", 0)), \
                 patch("agent._run_with_timeout", side_effect=lambda ag, c, l: self._empty_async_gen()):
                async for _ in agent._run_async_impl(ctx):
                    pass

        self._run(run())
        assert state.get("status") == "hitl_rejected"
```

- [ ] **Step 2: Ejecutar tests para verificar que fallan**

```bash
cd prisma_agents
python -m pytest tests/test_hitl.py::TestHitlLoop -v
```

Esperado: tests fallan porque el loop HITL aún no existe en `_run_async_impl`.

- [ ] **Step 3: Agregar la constante `MAX_HITL_ITERATIONS` en `agent.py`**

Al inicio de `agent.py`, junto a las otras constantes:

```python
MAX_ITERATIONS = 3
MAX_HITL_ITERATIONS = 6      # intentos de revisión del profesor
AGENT_TIMEOUT_SECONDS = 90
MAX_RETRIES_ON_TIMEOUT = 2
RETRY_DELAY_SECONDS = 5
```

- [ ] **Step 4: Reemplazar el flujo lineal de Agente 1 y 2 en `_run_async_impl` con el loop HITL**

Reemplazar este bloque en `_run_async_impl`:

```python
        # ── Agente 1: Análisis del PACI ──────────────────────────────────────
        print("\n[Agente 1] Analizando PACI...\n")
        async for event in _run_with_timeout(analizador_paci_agent, ctx, "Agente 1"):
            yield event
        if ctx.session.state.get("status") == "timeout":
            return

        # ── Agente 2: Adaptación del material ────────────────────────────────
        print("\n[Agente 2] Adaptando material educativo...\n")
        async for event in _run_with_timeout(adaptador_agent, ctx, "Agente 2"):
            yield event
        if ctx.session.state.get("status") == "timeout":
            return
```

Por este nuevo bloque:

```python
        # ── Agente 1: Análisis del PACI ──────────────────────────────────────
        print("\n[Agente 1] Analizando PACI...\n")
        async for event in _run_with_timeout(analizador_paci_agent, ctx, "Agente 1"):
            yield event
        if ctx.session.state.get("status") == "timeout":
            return

        # ── Loop HITL: Agente 2 + aprobación del profesor ────────────────────
        for hitl_attempt in range(1, MAX_HITL_ITERATIONS + 1):
            print("\n[Agente 2] Adaptando material educativo...\n")
            async for event in _run_with_timeout(adaptador_agent, ctx, "Agente 2"):
                yield event
            if ctx.session.state.get("status") == "timeout":
                return

            aprobado, razon, agente = _hitl_checkpoint(
                ctx.session.state, attempt=hitl_attempt, max_attempts=MAX_HITL_ITERATIONS
            )

            if aprobado:
                break

            # Intentos agotados — cancela
            if agente == 0:
                ctx.session.state["status"] = "hitl_rejected"
                return

            # Inyectar feedback formateado según el agente elegido
            if agente == 1:
                ctx.session.state["hitl_feedback_a1"] = (
                    f"\nRETROALIMENTACIÓN DEL DOCENTE — Debes revisar tu análisis "
                    f"considerando el siguiente problema señalado:\n"
                    f"\"{razon}\"\n"
                    f"Ajusta tu respuesta para abordar específicamente este punto."
                )
                ctx.session.state["hitl_feedback_a2"] = ""
                print("\n[Agente 1] Re-analizando PACI con feedback del docente...\n")
                async for event in _run_with_timeout(analizador_paci_agent, ctx, "Agente 1 (retry)"):
                    yield event
                if ctx.session.state.get("status") == "timeout":
                    return
            else:  # agente == 2
                ctx.session.state["hitl_feedback_a2"] = (
                    f"\nRETROALIMENTACIÓN DEL DOCENTE — Debes revisar la adaptación "
                    f"considerando el siguiente problema señalado:\n"
                    f"\"{razon}\"\n"
                    f"Ajusta tu respuesta para abordar específicamente este punto."
                )
```

- [ ] **Step 5: Ejecutar todos los tests**

```bash
cd prisma_agents
python -m pytest tests/test_hitl.py -v
```

Esperado: todos los tests en PASS.

- [ ] **Step 6: Commit final**

```bash
git add prisma_agents/agent.py prisma_agents/tests/test_hitl.py
git commit -m "feat(hitl): integra loop HITL en PaciWorkflowAgent con cancelacion por intentos agotados"
```

---

## Self-Review

**Cobertura del spec:**
- ✅ Pausa después del Agente 2
- ✅ Muestra resumen de Agente 1 y Agente 2
- ✅ Pide aprobación al profesor
- ✅ Clasificación LLM (no heurística) de respuesta positiva/negativa
- ✅ Feedback del profesor = mensaje de rechazo (no se pide dos veces)
- ✅ Pregunta qué agente re-ejecutar (1 o 2)
- ✅ Agente 1 elegido → re-corre Agente 1 + Agente 2
- ✅ Agente 2 elegido → re-corre solo Agente 2
- ✅ `MAX_HITL_ITERATIONS = 6`
- ✅ Intentos agotados → cancela con `status = "hitl_rejected"`, NO continúa
- ✅ Feedback inyectado como texto formateado con instrucción explícita
- ✅ Inicialización de claves en `run.py`
- ✅ Placeholders `{hitl_feedback_a1}` y `{hitl_feedback_a2}` en instrucciones

**Consistencia de tipos:**
- `_hitl_checkpoint` retorna `tuple[bool, str, int]` — consistente en definición y tests
- `_classify_response` retorna `bool` — consistente en todos los usos
- `agente == 0` señaliza cancelación — consistente entre `_hitl_checkpoint` y el loop en `_run_async_impl`
