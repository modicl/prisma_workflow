"""
run.py — Script de ejecución CLI del flujo PACI.

Uso:
    python run.py <paci_path> <material_path> [prompt_adicional] [user_id]

Ejemplos:
    python run.py datos/paci.json datos/guia_matematicas.docx
    python run.py datos/paci.pdf datos/planificacion.docx "Foco en comprensión lectora" "docente_42"
"""

import asyncio
import json
import sys
import os
import uuid
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Carga .env desde la carpeta del script
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from google.adk.runners import Runner
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.plugins.logging_plugin import LoggingPlugin
from google.genai.types import Content, Part

# Importar root_agent y utilidades desde el paquete
sys.path.insert(0, os.path.dirname(__file__))
from agent import root_agent
from utils.document_loader import load_document
from utils.document_exporter import export_results_to_docx
from utils.token_tracker import SessionTokenUsage

TOKEN_REPORTS_DIR = Path(__file__).parent / "token_reports"


def _save_token_report(session_id: str, tracker: SessionTokenUsage, status: str) -> None:
    """Persiste el reporte de tokens de la sesión en token_reports/."""
    TOKEN_REPORTS_DIR.mkdir(exist_ok=True)
    report = {
        "session_id": session_id,
        "timestamp": datetime.now().isoformat(),
        "status": status,
        "tokens": tracker.to_dict(),
    }
    out_path = TOKEN_REPORTS_DIR / f"tokens_{session_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    if tracker.has_data:
        print(f"  ✓ Token report: {report['tokens']['total']:,} tokens totales → {out_path.name}")

APP_NAME = "paci_workflow"


async def run_workflow(paci_path: str, material_path: str, prompt: str = "", user_id: str = "") -> dict:
    """Ejecuta el flujo multi-agente PACI y retorna los resultados.

    Args:
        user_id: Identificador del docente que inicia el flujo. Lo provee el
                 frontend al llamar al endpoint. Si se omite se genera un UUID
                 para uso desde CLI, garantizando aislamiento entre ejecuciones.
    """
    # Garantizar aislamiento por sesión: nunca mezclar datos de distintos usuarios.
    effective_user_id = user_id if user_id else str(uuid.uuid4())

    print(f"\n{'='*60}")
    print("  FLUJO MULTI-AGENTE PACI — Iniciando")
    print(f"{'='*60}")
    print(f"  PACI:     {paci_path}")
    print(f"  Material: {material_path}")
    print(f"  User ID:  {effective_user_id}")
    if prompt:
        print(f"  Prompt:   {prompt}")
    print(f"{'='*60}\n")

    # Cargar documentos
    print("[Cargando documentos...]")
    paci_text = load_document(paci_path, label="PACI del Estudiante")
    material_text = load_document(material_path, label="Material Base")
    print("  ✓ Documentos cargados.\n")

    # Configurar sesión con estado inicial en PostgreSQL
    db_url = os.environ.get("BD_LOGS")
    if not db_url:
        raise ValueError("Error: La variable de entorno BD_LOGS no está configurada en el .env")
        
    print("[Conectando a base de datos...]")
    session_service = DatabaseSessionService(db_url=db_url)
    
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=effective_user_id,
        state={
            "paci_document": paci_text,
            "material_document": material_text,
            "critica_previa": "",  # vacío en la primera iteración
            "hitl_feedback_a1": "",   # feedback para AnalizadorPACI
            "hitl_feedback_a2": "",   # feedback para Adaptador
        },
    )

    runner = Runner(
        agent=root_agent,
        session_service=session_service,
        app_name=APP_NAME,
        plugins=[LoggingPlugin()]
    )

    # Mensaje inicial para activar el flujo
    mensaje_inicial = prompt if prompt else "Inicia el flujo PACI con los documentos proporcionados."

    tracker = SessionTokenUsage()

    async for event in runner.run_async(
        user_id=effective_user_id,
        session_id=session.id,
        new_message=Content(parts=[Part(text=mensaje_inicial)]),
    ):
        # Capturar tokens por agente
        author = getattr(event, "author", None) or "unknown"
        tracker.add_event(author, event)

    # Recuperar resultados del estado de sesión
    final_session = await session_service.get_session(
        app_name=APP_NAME, user_id=effective_user_id, session_id=session.id
    )
    state = final_session.state

    results = {
        "status": state.get("status", "success"),
        "perfil_paci": state.get("perfil_paci", ""),
        "planificacion_adaptada": state.get("planificacion_adaptada", ""),
        "rubrica_final": state.get("rubrica", ""),
    }

    # Persistir reporte de tokens
    _save_token_report(session.id, tracker, results["status"])

    # Exportar a DOCX
    print("[Generando archivo DOCX...]")
    docx_path = None
    try:
        base_name = os.path.basename(material_path).split('.')[0]
        output_name = f"rubrica_adaptada_{base_name}.docx"
        docx_path = export_results_to_docx(results, output_filename=output_name)
    except Exception as e:
        print(f"  x Error al exportar a DOCX: {e}\n")

    # Resumen final — el contenido queda en el DOCX, no en consola
    print(f"\n{'='*60}")
    print("  FLUJO COMPLETADO")
    print(f"{'='*60}")
    print(f"  Estado : {results['status']}")
    if docx_path:
        print(f"  Archivo: {docx_path}")
    else:
        print("  Archivo: no generado (ver error arriba)")
    print(f"{'='*60}\n")

    return results


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python run.py <paci_path> <material_path> [prompt_adicional] [user_id]")
        sys.exit(1)

    paci_path = sys.argv[1]
    material_path = sys.argv[2]
    prompt_adicional = sys.argv[3] if len(sys.argv) > 3 else ""
    # Desde CLI el user_id es opcional; si se omite, run_workflow genera un UUID.
    user_id_arg = sys.argv[4] if len(sys.argv) > 4 else ""

    asyncio.run(run_workflow(paci_path, material_path, prompt_adicional, user_id_arg))
