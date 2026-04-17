# UI Backend — Chat Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Crear una interfaz chat React + Tailwind con backend FastAPI que permita al profesor subir el PACI y material base, interactuar con el agente multi-agente PACI, y aprobar/rechazar el checkpoint HITL directamente en el chat.

**Architecture:** FastAPI corre el agente en un background task. Cuando el agente llega al checkpoint HITL, escribe en una `asyncio.Queue` y queda suspendido esperando respuesta del frontend. React hace polling cada 2s al estado de la sesión y muestra la card HITL cuando corresponde. `school_id` es fijo como `"colegio_demo"`.

**Tech Stack:** Python 3.11+, FastAPI, Google ADK, React 18, Tailwind CSS 3, Vite 6. Tests: pytest + httpx.

---

## File Map

**Nuevos archivos backend:**
- `prisma_agents/api/session_store.py` — `SessionData` dataclass + dicts globales `SESSIONS` y `HITL_CALLBACKS`
- `prisma_agents/api/workflow_runner.py` — lanza `run_workflow` en modo API con callback HITL async
- `prisma_agents/api/chat_router.py` — endpoints `/chat/*`
- `prisma_agents/api/main.py` — FastAPI app unificada (health + chat_router)
- `prisma_agents/tests/api/__init__.py` — paquete de tests
- `prisma_agents/tests/api/test_session_store.py` — tests del store
- `prisma_agents/tests/api/test_chat_router.py` — tests de endpoints

**Archivos backend modificados:**
- `prisma_agents/agent.py` — `_hitl_checkpoint` se hace `async`, revisa `HITL_CALLBACKS` antes de usar `input()`
- `prisma_agents/run.py` — añade param `api_session_id`, lo incluye en `session.state`, retorna `docx_path` en results

**Nuevos archivos frontend:**
- `frontend/package.json`
- `frontend/vite.config.js`
- `frontend/tailwind.config.js`
- `frontend/postcss.config.js`
- `frontend/index.html`
- `frontend/src/main.jsx`
- `frontend/src/index.css`
- `frontend/src/App.jsx`
- `frontend/src/api.js`
- `frontend/src/components/Spinner.jsx`
- `frontend/src/components/MessageBubble.jsx`
- `frontend/src/components/UploadForm.jsx`
- `frontend/src/components/HitlCard.jsx`
- `frontend/src/components/ChatWindow.jsx`

---

## Task 1: Session Store

**Files:**
- Create: `prisma_agents/api/session_store.py`
- Create: `prisma_agents/tests/api/__init__.py`
- Create: `prisma_agents/tests/api/test_session_store.py`

- [ ] **Step 1: Escribir test que falla**

```python
# prisma_agents/tests/api/test_session_store.py
import asyncio
from api.session_store import SessionData, SESSIONS, HITL_CALLBACKS

def test_session_data_defaults():
    sd = SessionData()
    assert sd.phase == "running"
    assert sd.messages == []
    assert sd.hitl_data is None
    assert sd.result is None
    assert sd.docx_path is None
    assert sd.error is None

def test_hitl_response_queue_is_asyncio_queue():
    sd = SessionData()
    assert isinstance(sd.hitl_response_queue, asyncio.Queue)

def test_global_dicts_exist():
    assert isinstance(SESSIONS, dict)
    assert isinstance(HITL_CALLBACKS, dict)
```

- [ ] **Step 2: Verificar que el test falla**

Desde `prisma_agents/`:
```
pytest tests/api/test_session_store.py -v
```
Esperado: `ModuleNotFoundError: No module named 'api.session_store'`

- [ ] **Step 3: Crear `prisma_agents/tests/api/__init__.py`** (archivo vacío)

- [ ] **Step 4: Crear `prisma_agents/api/session_store.py`**

```python
import asyncio
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class SessionData:
    phase: str = "running"
    messages: list = field(default_factory=list)
    hitl_response_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    hitl_data: Optional[dict] = None
    result: Optional[dict] = None
    docx_path: Optional[str] = None
    error: Optional[str] = None

SESSIONS: dict[str, SessionData] = {}
HITL_CALLBACKS: dict[str, object] = {}
```

- [ ] **Step 5: Verificar que el test pasa**

```
pytest tests/api/test_session_store.py -v
```
Esperado: 3 tests PASSED

- [ ] **Step 6: Commit**

```bash
git add prisma_agents/api/session_store.py prisma_agents/tests/api/__init__.py prisma_agents/tests/api/test_session_store.py
git commit -m "feat: add session store for chat API"
```

---

## Task 2: Modificar agent.py — HITL async

**Files:**
- Modify: `prisma_agents/agent.py`

El objetivo: `_hitl_checkpoint` se convierte en `async`. Si hay una sesión API activa (`api_session_id` en state), usa el callback del `HITL_CALLBACKS` dict. Si no hay, usa `input()` como antes (modo CLI).

- [ ] **Step 1: Reemplazar `_hitl_checkpoint` en `prisma_agents/agent.py`**

Buscar la función `_hitl_checkpoint` (líneas ~82–131) y reemplazarla completa:

```python
async def _hitl_checkpoint(
    state: dict, attempt: int, max_attempts: int
) -> tuple[bool, str, int]:
    """Pausa para que el profesor apruebe o rechace. Modo API si hay callback, sino CLI."""
    api_session_id = state.get("api_session_id", "")
    if api_session_id:
        try:
            from api.session_store import HITL_CALLBACKS
            callback = HITL_CALLBACKS.get(api_session_id)
            if callback:
                return await callback(state, attempt, max_attempts)
        except ImportError:
            pass

    # CLI fallback
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

    loop = asyncio.get_event_loop()
    respuesta = await loop.run_in_executor(
        None, lambda: input("¿Aprueba el análisis y la planificación? ").strip()
    )

    if _classify_response(respuesta):
        return True, "", 0

    if attempt >= max_attempts:
        print(
            "\n✗ Proceso cancelado: se agotaron los intentos de revisión "
            "sin obtener aprobación del docente.\n"
        )
        return False, respuesta, 0

    while True:
        eleccion = await loop.run_in_executor(
            None,
            lambda: input(
                "¿El problema está en el análisis del PACI (1) "
                "o en la adaptación del material (2)? "
            ).strip()
        )
        if eleccion in ("1", "2"):
            return False, respuesta, int(eleccion)
        print("  Por favor ingresa 1 o 2.")
```

- [ ] **Step 2: Actualizar la llamada en `_run_async_impl`**

En `_run_async_impl`, buscar la línea:
```python
aprobado, razon, agente = _hitl_checkpoint(
    ctx.session.state, attempt=hitl_attempt, max_attempts=MAX_HITL_ITERATIONS
)
```
Reemplazar con:
```python
aprobado, razon, agente = await _hitl_checkpoint(
    ctx.session.state, attempt=hitl_attempt, max_attempts=MAX_HITL_ITERATIONS
)
```

- [ ] **Step 3: Verificar que los tests existentes siguen pasando**

```
pytest tests/ -v --ignore=tests/api -k "not test_hitl"
```
Esperado: todos PASSED (los tests no relacionados al HITL CLI no deben romperse)

- [ ] **Step 4: Commit**

```bash
git add prisma_agents/agent.py
git commit -m "feat: make _hitl_checkpoint async with API callback support"
```

---

## Task 3: Modificar run.py — api_session_id + docx_path en results

**Files:**
- Modify: `prisma_agents/run.py`

- [ ] **Step 1: Agregar `api_session_id` a la firma de `run_workflow`**

Buscar:
```python
async def run_workflow(paci_path: str, material_path: str, prompt: str = "", user_id: str = "", school_id: str = "") -> dict:
```
Reemplazar con:
```python
async def run_workflow(paci_path: str, material_path: str, prompt: str = "", user_id: str = "", school_id: str = "", api_session_id: str = "") -> dict:
```

- [ ] **Step 2: Incluir `api_session_id` en el estado inicial de sesión**

Buscar el bloque `state={` en `run_workflow` y añadir la clave al final del dict:
```python
state={
    "paci_document": paci_text,
    "material_document": material_text,
    "critica_previa": "",
    "hitl_feedback_a1": "",
    "hitl_feedback_a2": "",
    "school_id": school_id,
    "materiales_referencia": "",
    "prompt_docente": prompt,
    "api_session_id": api_session_id,   # <-- nueva línea
},
```

- [ ] **Step 3: Incluir `docx_path` en el dict `results`**

Buscar el bloque donde se construye `results` (después de recuperar `final_session`):
```python
results = {
    "status": state.get("status", "success"),
    "perfil_paci": state.get("perfil_paci", ""),
    "planificacion_adaptada": state.get("planificacion_adaptada", ""),
    "rubrica_final": state.get("rubrica", ""),
}
```
Reemplazar con:
```python
results = {
    "status": state.get("status", "success"),
    "perfil_paci": state.get("perfil_paci", ""),
    "planificacion_adaptada": state.get("planificacion_adaptada", ""),
    "rubrica_final": state.get("rubrica", ""),
    "docx_path": None,   # se rellena abajo si la exportación fue exitosa
}
```

Luego, justo después de la línea `docx_path = export_results_to_docx(...)`, agregar:
```python
        results["docx_path"] = str(docx_path)
```
(dentro del `try` de exportación, después de asignar `docx_path`)

- [ ] **Step 4: Verificar sintaxis**

```
python -c "import ast; ast.parse(open('run.py').read()); print('OK')"
```
Esperado: `OK`

- [ ] **Step 5: Commit**

```bash
git add prisma_agents/run.py
git commit -m "feat: add api_session_id param and docx_path to run_workflow result"
```

---

## Task 4: Workflow Runner para API

**Files:**
- Create: `prisma_agents/api/workflow_runner.py`

- [ ] **Step 1: Crear `prisma_agents/api/workflow_runner.py`**

```python
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.session_store import SESSIONS, HITL_CALLBACKS
from run import run_workflow


async def run_workflow_for_api(
    session_id: str,
    paci_path: str,
    material_path: str,
    prompt: str,
    school_id: str,
) -> None:
    session_data = SESSIONS[session_id]

    async def hitl_callback(state: dict, attempt: int, max_attempts: int) -> tuple[bool, str, int]:
        session_data.hitl_data = {
            "perfil_paci": state.get("perfil_paci", ""),
            "planificacion_adaptada": state.get("planificacion_adaptada", ""),
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
        session_data.phase = "awaiting_hitl"
        session_data.messages.append({
            "role": "system",
            "content": f"Revisión requerida — intento {attempt} de {max_attempts}. Por favor revise el análisis y la planificación.",
        })

        response = await session_data.hitl_response_queue.get()
        session_data.phase = "running"
        session_data.hitl_data = None

        approved = response.get("approved", False)
        reason = response.get("reason") or ""
        agent_to_retry = int(response.get("agent_to_retry") or 0)
        return approved, reason, agent_to_retry

    HITL_CALLBACKS[session_id] = hitl_callback

    try:
        session_data.messages.append({
            "role": "system",
            "content": "Documentos recibidos. Iniciando análisis del PACI...",
        })
        results = await run_workflow(
            paci_path=paci_path,
            material_path=material_path,
            prompt=prompt,
            user_id=session_id,
            school_id=school_id,
            api_session_id=session_id,
        )
        session_data.result = results
        session_data.docx_path = results.get("docx_path")
        session_data.phase = "completed"
        session_data.messages.append({
            "role": "agent",
            "content": "✅ Proceso completado. La rúbrica adaptada está lista para descargar.",
        })
    except Exception as exc:
        session_data.phase = "error"
        session_data.error = str(exc)
        session_data.messages.append({
            "role": "system",
            "content": f"❌ Error durante el procesamiento: {str(exc)}",
        })
    finally:
        HITL_CALLBACKS.pop(session_id, None)
        for path in [paci_path, material_path]:
            try:
                os.remove(path)
            except OSError:
                pass
```

- [ ] **Step 2: Verificar sintaxis**

```
python -c "import ast; ast.parse(open('api/workflow_runner.py').read()); print('OK')"
```
Esperado: `OK`

- [ ] **Step 3: Commit**

```bash
git add prisma_agents/api/workflow_runner.py
git commit -m "feat: add workflow runner for API with async HITL callback"
```

---

## Task 5: Chat Router + Tests

**Files:**
- Create: `prisma_agents/api/chat_router.py`
- Create: `prisma_agents/tests/api/test_chat_router.py`

- [ ] **Step 1: Escribir tests que fallan**

```python
# prisma_agents/tests/api/test_chat_router.py
import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.session_store import SESSIONS, SessionData

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_get_state_not_found():
    res = client.get("/chat/no-existe/state")
    assert res.status_code == 404


def test_get_state_returns_session():
    sid = "test-get-state-001"
    SESSIONS[sid] = SessionData()
    SESSIONS[sid].messages = [{"role": "system", "content": "Hola"}]
    res = client.get(f"/chat/{sid}/state")
    assert res.status_code == 200
    data = res.json()
    assert data["phase"] == "running"
    assert data["messages"][0]["content"] == "Hola"
    assert data["hitl_data"] is None
    del SESSIONS[sid]


def test_hitl_respond_session_not_found():
    res = client.post("/chat/no-existe/hitl", json={"approved": True})
    assert res.status_code == 404


def test_hitl_respond_wrong_phase():
    sid = "test-hitl-wrong-phase-002"
    SESSIONS[sid] = SessionData()  # phase = "running", not "awaiting_hitl"
    res = client.post(f"/chat/{sid}/hitl", json={"approved": True})
    assert res.status_code == 409
    del SESSIONS[sid]


def test_download_not_ready():
    sid = "test-download-not-ready-003"
    SESSIONS[sid] = SessionData()  # phase = "running", no docx
    res = client.get(f"/chat/{sid}/download")
    assert res.status_code == 404
    del SESSIONS[sid]
```

- [ ] **Step 2: Verificar que los tests fallan**

```
pytest tests/api/test_chat_router.py -v
```
Esperado: `ModuleNotFoundError` o `ImportError`

- [ ] **Step 3: Crear `prisma_agents/api/chat_router.py`**

```python
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.session_store import SESSIONS, SessionData
from api.workflow_runner import run_workflow_for_api

router = APIRouter(prefix="/chat")

UPLOAD_DIR = Path("/tmp/prisma_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


class HitlResponseBody(BaseModel):
    approved: bool
    reason: Optional[str] = None
    agent_to_retry: Optional[int] = None


@router.post("/start")
async def start_chat(
    background_tasks: BackgroundTasks,
    paci_file: UploadFile = File(...),
    material_file: UploadFile = File(...),
    prompt: str = Form(""),
    school_id: str = Form("colegio_demo"),
):
    session_id = str(uuid.uuid4())

    paci_ext = Path(paci_file.filename).suffix if paci_file.filename else ".pdf"
    material_ext = Path(material_file.filename).suffix if material_file.filename else ".docx"

    paci_path = UPLOAD_DIR / f"{session_id}_paci{paci_ext}"
    material_path = UPLOAD_DIR / f"{session_id}_material{material_ext}"

    paci_path.write_bytes(await paci_file.read())
    material_path.write_bytes(await material_file.read())

    SESSIONS[session_id] = SessionData()

    background_tasks.add_task(
        run_workflow_for_api,
        session_id=session_id,
        paci_path=str(paci_path),
        material_path=str(material_path),
        prompt=prompt,
        school_id=school_id,
    )

    return {"session_id": session_id}


@router.get("/{session_id}/state")
async def get_state(session_id: str):
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
    return {
        "phase": sd.phase,
        "messages": sd.messages,
        "hitl_data": sd.hitl_data,
        "error": sd.error,
    }


@router.post("/{session_id}/hitl")
async def respond_hitl(session_id: str, body: HitlResponseBody):
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
    if sd.phase != "awaiting_hitl":
        raise HTTPException(status_code=409, detail="La sesión no está esperando revisión HITL")
    await sd.hitl_response_queue.put({
        "approved": body.approved,
        "reason": body.reason,
        "agent_to_retry": body.agent_to_retry,
    })
    return {"ok": True}


@router.get("/{session_id}/download")
async def download_result(session_id: str):
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    sd = SESSIONS[session_id]
    if sd.phase != "completed" or not sd.docx_path:
        raise HTTPException(status_code=404, detail="Resultado no disponible aún")
    return FileResponse(
        sd.docx_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=Path(sd.docx_path).name,
    )
```

- [ ] **Step 4: Verificar que los tests pasan** (main.py aún no existe — se crea en Task 6)

Primero crear `main.py` mínimo para desbloquear los tests (se completa en Task 6):

```python
# prisma_agents/api/main.py  (versión mínima para tests)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.chat_router import router as chat_router

app = FastAPI(title="PRISMA Chat API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(chat_router)

@app.get("/health")
async def health():
    return {"status": "ok"}
```

```
pytest tests/api/test_chat_router.py -v
```
Esperado: 6 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add prisma_agents/api/chat_router.py prisma_agents/tests/api/test_chat_router.py
git commit -m "feat: add chat router endpoints with tests"
```

---

## Task 6: main.py (app unificada)

**Files:**
- Modify: `prisma_agents/api/main.py` (completar la versión mínima del task anterior)

La versión mínima creada en Task 5 ya es la versión final. Solo agregar el lifespan explícito:

- [ ] **Step 1: Actualizar `prisma_agents/api/main.py`**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.chat_router import router as chat_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="PRISMA Chat API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 2: Re-ejecutar todos los tests del API**

```
pytest tests/api/ -v
```
Esperado: todos PASSED

- [ ] **Step 3: Verificar que el servidor arranca**

```
uvicorn api.main:app --port 8000 --reload
```
Esperado: `Application startup complete.` en los logs. Ctrl+C para detener.

- [ ] **Step 4: Commit**

```bash
git add prisma_agents/api/main.py
git commit -m "feat: add unified FastAPI main app"
```

---

## Task 7: Frontend — Scaffolding

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.js`
- Create: `frontend/tailwind.config.js`
- Create: `frontend/postcss.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.jsx`
- Create: `frontend/src/index.css`

- [ ] **Step 1: Crear `frontend/package.json`**

```json
{
  "name": "prisma-chat",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.17",
    "vite": "^6.0.5"
  }
}
```

- [ ] **Step 2: Crear `frontend/vite.config.js`**

```javascript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/chat': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    }
  }
})
```

- [ ] **Step 3: Crear `frontend/tailwind.config.js`**

```javascript
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: { extend: {} },
  plugins: [],
}
```

- [ ] **Step 4: Crear `frontend/postcss.config.js`**

```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 5: Crear `frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="es">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>PRISMA — Generador de Rúbricas</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Crear `frontend/src/main.jsx`**

```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

- [ ] **Step 7: Crear `frontend/src/index.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 8: Instalar dependencias**

Desde `frontend/`:
```
npm install
```
Esperado: `node_modules/` creado sin errores.

- [ ] **Step 9: Verificar que Vite arranca** (sin componentes aún, fallará hasta que exista App.jsx)

```
npm run dev
```
Detendrá con error sobre App.jsx — eso es esperado. Ctrl+C.

- [ ] **Step 10: Commit**

```bash
git add frontend/
git commit -m "feat: scaffold React + Tailwind frontend with Vite"
```

---

## Task 8: api.js — Funciones fetch

**Files:**
- Create: `frontend/src/api.js`

- [ ] **Step 1: Crear `frontend/src/api.js`**

```javascript
export async function startChat({ paciFile, materialFile, prompt }) {
  const form = new FormData()
  form.append('paci_file', paciFile)
  form.append('material_file', materialFile)
  form.append('prompt', prompt)
  form.append('school_id', 'colegio_demo')

  const res = await fetch('/chat/start', { method: 'POST', body: form })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Error ${res.status}: ${text}`)
  }
  return res.json()
}

export async function getSessionState(sessionId) {
  const res = await fetch(`/chat/${sessionId}/state`)
  if (!res.ok) throw new Error(`Error ${res.status}`)
  return res.json()
}

export async function respondHitl(sessionId, { approved, reason, agentToRetry }) {
  const res = await fetch(`/chat/${sessionId}/hitl`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      approved,
      reason: reason || null,
      agent_to_retry: agentToRetry || null,
    }),
  })
  if (!res.ok) throw new Error(`Error ${res.status}`)
  return res.json()
}

export function getDownloadUrl(sessionId) {
  return `/chat/${sessionId}/download`
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat: add API client functions"
```

---

## Task 9: Spinner + MessageBubble

**Files:**
- Create: `frontend/src/components/Spinner.jsx`
- Create: `frontend/src/components/MessageBubble.jsx`

- [ ] **Step 1: Crear `frontend/src/components/Spinner.jsx`**

```jsx
export default function Spinner() {
  return (
    <div className="flex items-center gap-2 text-gray-500 text-sm py-2 px-1">
      <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin flex-shrink-0" />
      <span>El agente está procesando...</span>
    </div>
  )
}
```

- [ ] **Step 2: Crear `frontend/src/components/MessageBubble.jsx`**

```jsx
export default function MessageBubble({ role, content }) {
  const isAgent = role === 'agent'
  return (
    <div className={`flex ${isAgent ? 'justify-start' : 'justify-end'} mb-3`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap leading-relaxed ${
          isAgent
            ? 'bg-blue-50 text-blue-900 rounded-tl-sm'
            : 'bg-gray-100 text-gray-700 rounded-tr-sm'
        }`}
      >
        {content}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Spinner.jsx frontend/src/components/MessageBubble.jsx
git commit -m "feat: add Spinner and MessageBubble components"
```

---

## Task 10: UploadForm

**Files:**
- Create: `frontend/src/components/UploadForm.jsx`

- [ ] **Step 1: Crear `frontend/src/components/UploadForm.jsx`**

```jsx
import { useState, useRef } from 'react'
import { startChat } from '../api'

function FileDropZone({ label, file, onChange }) {
  const inputRef = useRef(null)

  function handleDrop(e) {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f) onChange(f)
  }

  return (
    <div className="mb-4">
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <div
        onDrop={handleDrop}
        onDragOver={e => e.preventDefault()}
        onClick={() => inputRef.current.click()}
        className="border-2 border-dashed border-gray-300 rounded-xl p-5 text-center cursor-pointer hover:border-blue-400 hover:bg-blue-50 transition-colors"
      >
        {file ? (
          <span className="text-sm text-green-600 font-medium">✓ {file.name}</span>
        ) : (
          <span className="text-sm text-gray-400">
            Arrastra o haz click — <span className="font-medium">.pdf</span> o <span className="font-medium">.docx</span>
          </span>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx"
          className="hidden"
          onChange={e => onChange(e.target.files[0] || null)}
        />
      </div>
    </div>
  )
}

export default function UploadForm({ onStart }) {
  const [paciFile, setPaciFile] = useState(null)
  const [materialFile, setMaterialFile] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    if (!paciFile || !materialFile) {
      setError('Debes subir ambos archivos.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const { session_id } = await startChat({ paciFile, materialFile, prompt })
      onStart(session_id)
    } catch (err) {
      setError(err.message)
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-gray-100 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl p-8 w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-900 tracking-tight">PRISMA</h1>
          <p className="text-sm text-gray-500 mt-1">Generador de Rúbricas Adaptadas para NEE</p>
        </div>

        <form onSubmit={handleSubmit}>
          <FileDropZone label="PACI del estudiante" file={paciFile} onChange={setPaciFile} />
          <FileDropZone label="Material base del curso" file={materialFile} onChange={setMaterialFile} />

          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Instrucción adicional{' '}
              <span className="text-gray-400 font-normal">(opcional)</span>
            </label>
            <textarea
              className="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              rows={3}
              placeholder="Ej: Foco en comprensión lectora, actividades kinestésicas..."
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
            />
          </div>

          {error && (
            <p className="text-red-500 text-sm mb-4 bg-red-50 rounded-lg px-3 py-2">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 text-white font-semibold py-3 rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Iniciando...
              </span>
            ) : (
              'Iniciar análisis ▶'
            )}
          </button>
        </form>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/UploadForm.jsx
git commit -m "feat: add UploadForm component"
```

---

## Task 11: HitlCard

**Files:**
- Create: `frontend/src/components/HitlCard.jsx`

- [ ] **Step 1: Crear `frontend/src/components/HitlCard.jsx`**

```jsx
import { useState } from 'react'

function Accordion({ title, content }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border border-amber-200 rounded-xl mb-2 overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full text-left px-4 py-2.5 flex justify-between items-center text-sm font-medium text-amber-900 hover:bg-amber-100 transition-colors"
      >
        {title}
        <span className="text-xs">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="px-4 pb-3 pt-1 text-xs text-gray-700 whitespace-pre-wrap max-h-52 overflow-y-auto bg-white border-t border-amber-100">
          {content || '(sin datos)'}
        </div>
      )}
    </div>
  )
}

export default function HitlCard({ hitlData, onRespond }) {
  const [approved, setApproved] = useState(null)
  const [reason, setReason] = useState('')
  const [agentToRetry, setAgentToRetry] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const canSubmit =
    approved === true || (approved === false && reason.trim() && agentToRetry !== null)

  async function handleConfirm() {
    if (!canSubmit) return
    setSubmitting(true)
    await onRespond({
      approved,
      reason: approved ? null : reason,
      agentToRetry: approved ? null : agentToRetry,
    })
  }

  return (
    <div className="border-2 border-amber-400 bg-amber-50 rounded-2xl p-4 my-2">
      <p className="text-sm font-semibold text-amber-800 mb-3">
        ⚠ Revisión requerida — intento {hitlData.attempt} de {hitlData.max_attempts}
      </p>

      <Accordion title="📋 Análisis PACI (Agente 1)" content={hitlData.perfil_paci} />
      <Accordion title="📝 Planificación Adaptada (Agente 2)" content={hitlData.planificacion_adaptada} />

      <div className="flex gap-2 mt-4">
        <button
          onClick={() => { setApproved(true); setAgentToRetry(null); setReason('') }}
          className={`flex-1 py-2 rounded-xl text-sm font-medium transition-colors border ${
            approved === true
              ? 'bg-green-600 text-white border-green-600'
              : 'bg-white text-green-700 border-green-400 hover:bg-green-50'
          }`}
        >
          ✅ Aprobar
        </button>
        <button
          onClick={() => setApproved(false)}
          className={`flex-1 py-2 rounded-xl text-sm font-medium transition-colors border ${
            approved === false
              ? 'bg-red-500 text-white border-red-500'
              : 'bg-white text-red-500 border-red-400 hover:bg-red-50'
          }`}
        >
          ❌ Rechazar
        </button>
      </div>

      {approved === false && (
        <div className="mt-3 space-y-2">
          <textarea
            className="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            rows={2}
            placeholder="Describe el problema encontrado (requerido)"
            value={reason}
            onChange={e => setReason(e.target.value)}
          />
          <p className="text-xs font-medium text-gray-600">¿Qué se debe corregir?</p>
          <div className="flex gap-2">
            {[
              { id: 1, label: 'Análisis del PACI' },
              { id: 2, label: 'Adaptación del material' },
            ].map(({ id, label }) => (
              <button
                key={id}
                onClick={() => setAgentToRetry(id)}
                className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
                  agentToRetry === id
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {approved !== null && (
        <button
          onClick={handleConfirm}
          disabled={!canSubmit || submitting}
          className="w-full mt-3 bg-blue-600 text-white font-semibold py-2.5 rounded-xl hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed text-sm transition-colors"
        >
          {submitting ? 'Enviando...' : 'Confirmar'}
        </button>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/HitlCard.jsx
git commit -m "feat: add HitlCard component for HITL checkpoint"
```

---

## Task 12: ChatWindow

**Files:**
- Create: `frontend/src/components/ChatWindow.jsx`

- [ ] **Step 1: Crear `frontend/src/components/ChatWindow.jsx`**

```jsx
import { useState, useEffect, useRef } from 'react'
import MessageBubble from './MessageBubble'
import HitlCard from './HitlCard'
import Spinner from './Spinner'
import { getSessionState, respondHitl, getDownloadUrl } from '../api'

export default function ChatWindow({ sessionId }) {
  const [phase, setPhase] = useState('running')
  const [messages, setMessages] = useState([])
  const [hitlData, setHitlData] = useState(null)
  const [error, setError] = useState(null)
  const bottomRef = useRef(null)

  useEffect(() => {
    if (phase !== 'running') return

    const interval = setInterval(async () => {
      try {
        const data = await getSessionState(sessionId)
        setMessages(data.messages || [])
        if (data.hitl_data) setHitlData(data.hitl_data)
        if (data.error) setError(data.error)
        setPhase(data.phase)
      } catch (err) {
        setError(err.message)
        setPhase('error')
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [phase, sessionId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, hitlData, phase])

  async function handleHitlRespond(response) {
    await respondHitl(sessionId, response)
    setHitlData(null)
    setPhase('running')
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-gray-100 flex flex-col items-center p-4">
      <div className="w-full max-w-2xl bg-white rounded-2xl shadow-xl flex flex-col h-[90vh]">

        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-100 flex-shrink-0">
          <h1 className="font-bold text-gray-900 text-lg">PRISMA — Flujo PACI</h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Sesión {sessionId.slice(0, 8)}… ·{' '}
            <span className={
              phase === 'completed' ? 'text-green-500' :
              phase === 'error' ? 'text-red-500' :
              phase === 'awaiting_hitl' ? 'text-amber-500' :
              'text-blue-500'
            }>
              {phase === 'running' && 'Procesando'}
              {phase === 'awaiting_hitl' && 'Esperando revisión'}
              {phase === 'completed' && 'Completado'}
              {phase === 'error' && 'Error'}
            </span>
          </p>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {messages.map((msg, i) => (
            <MessageBubble key={i} role={msg.role} content={msg.content} />
          ))}

          {phase === 'awaiting_hitl' && hitlData && (
            <HitlCard hitlData={hitlData} onRespond={handleHitlRespond} />
          )}

          {phase === 'running' && <Spinner />}

          {phase === 'completed' && (
            <div className="flex justify-center mt-6">
              <a
                href={getDownloadUrl(sessionId)}
                download
                className="bg-green-600 text-white font-semibold px-8 py-3 rounded-xl hover:bg-green-700 transition-colors text-sm shadow-md"
              >
                ⬇ Descargar Rúbrica (.docx)
              </a>
            </div>
          )}

          {phase === 'error' && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700 mt-2">
              ❌ {error || 'Ocurrió un error durante el procesamiento.'}
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/ChatWindow.jsx
git commit -m "feat: add ChatWindow with polling and HITL integration"
```

---

## Task 13: App.jsx — Router raíz

**Files:**
- Create: `frontend/src/App.jsx`

- [ ] **Step 1: Crear `frontend/src/App.jsx`**

```jsx
import { useState } from 'react'
import UploadForm from './components/UploadForm'
import ChatWindow from './components/ChatWindow'

export default function App() {
  const [sessionId, setSessionId] = useState(null)

  return sessionId
    ? <ChatWindow sessionId={sessionId} />
    : <UploadForm onStart={setSessionId} />
}
```

- [ ] **Step 2: Verificar que el frontend compila**

Desde `frontend/`:
```
npm run build
```
Esperado: `dist/` generado sin errores.

- [ ] **Step 3: Levantar frontend en dev y verificar que la pantalla inicial carga**

```
npm run dev
```
Abrir `http://localhost:5173` — debe verse el formulario de carga con los dos drop zones y el botón "Iniciar análisis ▶".

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "feat: add App root router between UploadForm and ChatWindow"
```

---

## Task 14: Instrucciones de ejecución y prueba de integración manual

**Files:** ninguno nuevo — solo validación.

- [ ] **Step 1: Levantar el backend**

Desde `prisma_agents/` (con el venv activado y `.env` configurado con `BD_LOGS` y `GOOGLE_API_KEY`):
```
uvicorn api.main:app --port 8000 --reload
```

- [ ] **Step 2: Levantar el frontend**

Desde `frontend/` (en otra terminal):
```
npm run dev
```

- [ ] **Step 3: Verificar health endpoint**

```
curl http://localhost:8000/health
```
Esperado: `{"status":"ok"}`

- [ ] **Step 4: Verificar FastAPI docs**

Abrir `http://localhost:8000/docs` — deben aparecer los endpoints:
- `GET /health`
- `POST /chat/start`
- `GET /chat/{session_id}/state`
- `POST /chat/{session_id}/hitl`
- `GET /chat/{session_id}/download`

- [ ] **Step 5: Prueba de flujo completo**

1. Abrir `http://localhost:5173`
2. Subir `paci_test.pdf` (en la raíz del repo) como PACI
3. Subir `material_base_test.pdf` como material base
4. Dejar prompt vacío, hacer click en "Iniciar análisis ▶"
5. Verificar que aparece el spinner y el mensaje "Documentos recibidos. Iniciando análisis del PACI..."
6. Esperar a que aparezca la card HITL (puede tardar 1-2 minutos)
7. Expandir los acordeones y revisar el análisis
8. Hacer click en "✅ Aprobar" → "Confirmar"
9. Verificar que el spinner vuelve y el flujo continúa
10. Al finalizar, verificar que aparece el botón "⬇ Descargar Rúbrica (.docx)" y que la descarga funciona

- [ ] **Step 6: Commit final**

```bash
git add -A
git commit -m "feat: complete chat UI prototype — React + FastAPI + async HITL"
```

---

## Notas de instalación

**Dependencias Python adicionales requeridas** (agregar a `requirements.txt`):
```
python-multipart==0.0.20
httpx==0.28.1
pytest-asyncio==0.24.0
```

Instalar:
```
pip install python-multipart httpx pytest-asyncio
```

**Variable de entorno necesaria:** el `.env` en `prisma_agents/` debe tener `BD_LOGS` y `GOOGLE_API_KEY` configurados (ya existentes del CLI).
