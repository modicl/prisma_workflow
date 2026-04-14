# HITL Checkpoint — Diseño

**Fecha:** 2026-04-14  
**Alcance:** CLI únicamente (`run.py` + `agent.py`)  
**Feature:** Pausa interactiva entre Agente 2 y el loop de rúbrica para aprobación del profesor.

---

## Resumen

Después de que el Agente 2 (Adaptador) termina su trabajo, el flujo se pausa y le presenta al profesor un resumen del análisis (Agente 1) y la planificación adaptada (Agente 2). El profesor aprueba o rechaza. Si rechaza, indica la razón y elige qué agente re-ejecutar. El ciclo se repite hasta que el profesor aprueba. Si se agotan los 6 intentos sin aprobación, el proceso **se cancela** con estado `"hitl_rejected"`.

---

## Flujo completo

```
Agente 1 → perfil_paci
↓
[LOOP HITL — max 6 intentos]
  Agente 2 → planificacion_adaptada
  ↓
  _hitl_checkpoint() — muestra resumen, pide aprobación
  ├─ Aprueba → break
  └─ Rechaza →
       - El mensaje de rechazo ES el feedback (no se pide de nuevo)
       - Pregunta: "¿Problema en análisis (1) o adaptación (2)?"
       - Agente 1 → re-corre Agente 1 + Agente 2
       - Agente 2 → re-corre solo Agente 2
       - Si intentos agotados → aviso al profesor + cancela el proceso (status = "hitl_rejected")
↓ (solo si aprobado)
Loop Agente 3 + Crítico (sin cambios)
```

---

## Constantes nuevas

```python
MAX_HITL_ITERATIONS = 6   # intentos de revisión del profesor
# MAX_ITERATIONS = 3 se mantiene para el loop de rúbrica
```

---

## Función `_hitl_checkpoint()`

**Firma:**
```python
def _hitl_checkpoint(state: dict, attempt: int, max_attempts: int) -> tuple[bool, str, int]:
    """
    Retorna: (aprobado, razon, agente_a_reiniciar)
    Si aprobado=True, razon="" y agente_a_reiniciar=0
    """
```

**Salida que imprime:**
1. Resumen compacto de `perfil_paci` (diagnóstico + estrategias)
2. Resumen compacto de `planificacion_adaptada` (adecuaciones aplicadas)
3. Aviso de intentos: `"[Revisión {attempt}/{max_attempts}] — Quedan {restantes} intentos."`
4. Pregunta: `"¿Aprueba el análisis y la planificación? "`

**Detección de respuesta positiva** — llamada LLM (gemini-2.5-flash-lite):

Se hace una llamada síncrona al modelo con un prompt de clasificación binaria:

```
Clasifica si el siguiente mensaje de un docente indica APROBACIÓN o RECHAZO 
del trabajo presentado. Responde únicamente con "APROBADO" o "RECHAZADO".

Mensaje: "{respuesta_del_profesor}"
```

Retorna `True` si el modelo responde `"APROBADO"`, `False` si responde `"RECHAZADO"`.
Esto permite que el profesor escriba cualquier texto natural positivo o negativo.

**Si rechaza:**
1. El texto ingresado se guarda como `feedback_text` directamente
2. Pregunta adicional: `"¿El problema está en el análisis del PACI (1) o en la adaptación del material (2)? "`
3. Acepta `"1"` o `"2"`; si no reconoce la entrada, repregunta
4. Si es el último intento disponible y el profesor rechaza: imprime aviso y retorna `(False, reason, 0)` para señalizar cancelación

---

## Inyección del feedback en session.state

Dos claves nuevas inicializadas como `""` en `run.py`:

| Clave | Usado por |
|-------|-----------|
| `hitl_feedback_a1` | AnalizadorPACI |
| `hitl_feedback_a2` | Adaptador |

**Regla de llenado en el orquestador:**

```python
# Profesor elige agente 1:
state["hitl_feedback_a1"] = (
    f"\n⚠ RETROALIMENTACIÓN DEL DOCENTE — Debes revisar tu análisis "
    f"considerando el siguiente problema señalado:\n"
    f"\"{reason}\"\n"
    f"Ajusta tu respuesta para abordar específicamente este punto."
)
state["hitl_feedback_a2"] = ""   # reset adaptador para re-correr limpio

# Profesor elige agente 2:
state["hitl_feedback_a2"] = (
    f"\n⚠ RETROALIMENTACIÓN DEL DOCENTE — Debes revisar la adaptación "
    f"considerando el siguiente problema señalado:\n"
    f"\"{reason}\"\n"
    f"Ajusta tu respuesta para abordar específicamente este punto."
)
# hitl_feedback_a1 no se toca (el análisis ya fue aprobado implícitamente)
```

**Agentes que se re-corren según elección:**

| Elección | Re-corre |
|----------|----------|
| 1 | Agente 1 → Agente 2 |
| 2 | Solo Agente 2 |

---

## Cambios en instrucciones de agentes

Al final de cada instrucción se agrega el placeholder:

**AnalizadorPACI (`analizador_paci.py`):**
```
{hitl_feedback_a1}
```

**Adaptador (`adaptador.py`):**
```
{hitl_feedback_a2}
```

Cuando la clave es `""`, el agente no ve nada. Cuando tiene contenido, recibe una instrucción clara y accionable formateada por el orquestador.

---

## Cambios en `run.py`

Agregar al estado inicial de sesión:

```python
state={
    "paci_document": paci_text,
    "material_document": material_text,
    "critica_previa": "",
    "hitl_feedback_a1": "",   # nuevo
    "hitl_feedback_a2": "",   # nuevo
},
```

---

## Archivos modificados

| Archivo | Cambio |
|---------|--------|
| `prisma_agents/agent.py` | Constante `MAX_HITL_ITERATIONS`, función `_hitl_checkpoint()`, loop HITL en `_run_async_impl` |
| `prisma_agents/agents/analizador_paci.py` | Agregar `{hitl_feedback_a1}` al final de `INSTRUCTION` |
| `prisma_agents/agents/adaptador.py` | Agregar `{hitl_feedback_a2}` al final de `INSTRUCTION` |
| `prisma_agents/run.py` | Inicializar `hitl_feedback_a1` y `hitl_feedback_a2` en session state |
