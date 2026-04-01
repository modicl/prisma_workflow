"""
run.py — Script de ejecución CLI del flujo PACI.

Uso:
    python run.py <paci_path> <material_path> [prompt_adicional]

Ejemplos:
    python run.py datos/paci.json datos/guia_matematicas.docx
    python run.py datos/paci.pdf datos/planificacion.docx "Foco en comprensión lectora"
"""

import asyncio
import sys
import os
from dotenv import load_dotenv

# Carga .env desde la carpeta del script
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

# Importar root_agent y utilidades desde el paquete
sys.path.insert(0, os.path.dirname(__file__))
from agent import root_agent
from utils.document_loader import load_document

APP_NAME = "paci_workflow"
USER_ID = "docente"


async def run_workflow(paci_path: str, material_path: str, prompt: str = "") -> dict:
    """Ejecuta el flujo multi-agente PACI y retorna los resultados."""

    print(f"\n{'='*60}")
    print("  FLUJO MULTI-AGENTE PACI — Iniciando")
    print(f"{'='*60}")
    print(f"  PACI:     {paci_path}")
    print(f"  Material: {material_path}")
    if prompt:
        print(f"  Prompt:   {prompt}")
    print(f"{'='*60}\n")

    # Cargar documentos
    print("[Cargando documentos...]")
    paci_text = load_document(paci_path, label="PACI del Estudiante")
    material_text = load_document(material_path, label="Material Base")
    print("  ✓ Documentos cargados.\n")

    # Configurar sesión con estado inicial
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        state={
            "paci_document": paci_text,
            "material_document": material_text,
            "critica_previa": "",  # vacío en la primera iteración
        },
    )

    runner = Runner(
        agent=root_agent,
        session_service=session_service,
        app_name=APP_NAME,
    )

    # Mensaje inicial para activar el flujo
    mensaje_inicial = prompt if prompt else "Inicia el flujo PACI con los documentos proporcionados."

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=Content(parts=[Part(text=mensaje_inicial)]),
    ):
        # Los agentes imprimen su progreso en consola vía print()
        pass

    # Recuperar resultados del estado de sesión
    final_session = await session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session.id
    )
    state = final_session.state

    results = {
        "perfil_paci": state.get("perfil_paci", ""),
        "planificacion_adaptada": state.get("planificacion_adaptada", ""),
        "rubrica_final": state.get("rubrica", ""),
    }

    # Imprimir resultados finales
    print(f"\n{'='*60}")
    print("  RESULTADOS FINALES")
    print(f"{'='*60}\n")

    print("── PERFIL PACI ──────────────────────────────────────────")
    print(results["perfil_paci"] or "(vacío)")

    print("\n── PLANIFICACIÓN ADAPTADA ───────────────────────────────")
    print(results["planificacion_adaptada"] or "(vacío)")

    print("\n── RÚBRICA FINAL ────────────────────────────────────────")
    print(results["rubrica_final"] or "(vacío)")

    print(f"\n{'='*60}\n")

    return results


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python run.py <paci_path> <material_path> [prompt_adicional]")
        sys.exit(1)

    paci_path = sys.argv[1]
    material_path = sys.argv[2]
    prompt_adicional = sys.argv[3] if len(sys.argv) > 3 else ""

    asyncio.run(run_workflow(paci_path, material_path, prompt_adicional))
