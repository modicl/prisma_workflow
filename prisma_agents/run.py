"""
run.py — Script de ejecución CLI del flujo PACI.

Uso:
    python run.py <paci_path> <material_path> [prompt_adicional] [user_id]

Ejemplos:
    python run.py datos/paci.json datos/guia_matematicas.docx
    python run.py datos/paci.pdf datos/planificacion.docx "Foco en comprensión lectora" "docente_42"
"""

import asyncio
import sys
import os
import uuid
from dotenv import load_dotenv

# Carga .env desde la carpeta del script
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from utils.tracing import setup_tracing
setup_tracing()

from google.adk.runners import Runner
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.plugins.logging_plugin import LoggingPlugin
from google.genai.types import Content, Part

# Importar root_agent y utilidades desde el paquete
sys.path.insert(0, os.path.dirname(__file__))
from agent import root_agent, _extract_metadatos
from utils.curriculum_catalog import normalize_subject, normalize_grade
from utils.nee_taxonomy import normalize_diagnostico
from utils.document_loader import load_document
from utils.document_exporter import export_results_to_docx
from utils.input_validator import validate_prompt_docente

APP_NAME = "paci_workflow"


def _enrich_trace_span(state: dict, channel: str) -> None:
    """Añade metadata y tags post-run al span raíz activo via propagate_attributes."""
    from langfuse import propagate_attributes

    perfil = state.get("perfil_paci", "")
    meta = _extract_metadatos(perfil)
    subject = normalize_subject(meta["ramo"]) or meta["ramo"] or "desconocida"
    grade = normalize_grade(meta["curso"]) or meta["curso"] or "desconocido"
    diagnostico = normalize_diagnostico(meta["diagnostico"])
    status = state.get("status", "success")

    try:
        with propagate_attributes(
            tags=[channel, f"materia:{subject}", f"curso:{grade}", f"diagnostico:{diagnostico}", status],
            metadata={"materia": subject, "curso": grade, "diagnostico": diagnostico, "status": status},
        ):
            pass
    except Exception:
        pass


async def run_workflow(paci_path: str, material_path: str, prompt: str = "", user_id: str = "", school_id: str = "", api_session_id: str = "") -> dict:
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
    if school_id:
        print(f"  School:   {school_id}")
    if prompt:
        print(f"  Prompt:   {prompt}")
    print(f"{'='*60}\n")

    # Validar prompt antes de cargar documentos
    validate_prompt_docente(prompt)

    # Cargar documentos
    print("[Cargando documentos...]")
    paci_text = load_document(paci_path, label="PACI del Estudiante")
    material_text = load_document(material_path, label="Material Base")
    print("  ✓ Documentos cargados.\n")

    db_url = os.environ.get("BD_LOGS")
    if db_url:
        print("[Conectando a base de datos...]")
        session_service = DatabaseSessionService(db_url=db_url)
    else:
        print("  ⚠ BD_LOGS no configurado — usando sesión en memoria (sin persistencia).")
        session_service = InMemorySessionService()
    
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=effective_user_id,
        state={
            "paci_document": paci_text,
            "material_document": material_text,
            "critica_previa": "",  # vacío en la primera iteración
            "hitl_feedback_a1": "",   # feedback para AnalizadorPACI
            "hitl_feedback_a2": "",   # feedback para Adaptador
            "school_id": school_id,
            "materiales_referencia": "",  # se setea en agent.py tras AnalizadorPACI
            "prompt_docente": prompt,
            "api_session_id": api_session_id,
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

    from langfuse import get_client, propagate_attributes
    channel = "api" if api_session_id else "cli"
    trace_session_id = api_session_id if api_session_id else effective_user_id

    with get_client().start_as_current_observation(name="paci-workflow"):
        with propagate_attributes(
            user_id=effective_user_id,
            session_id=trace_session_id,
            trace_name="paci-workflow",
            metadata={
                "school_id": school_id or "sin_colegio",
                "channel": channel,
                "env": os.environ.get("ENV", "dev"),
            },
            tags=[channel],
        ):
            async for event in runner.run_async(
                user_id=effective_user_id,
                session_id=session.id,
                new_message=Content(parts=[Part(text=mensaje_inicial)]),
            ):
                pass

            final_session = await session_service.get_session(
                app_name=APP_NAME, user_id=effective_user_id, session_id=session.id
            )
            _state = final_session.state

        # Fuera del with propagate_attributes pero dentro de start_as_current_observation:
        # aquí el span raíz es el único activo → propagate_attributes lo encuentra directamente
        _enrich_trace_span(_state, channel)

    state = _state

    results = {
        "status": state.get("status", "success"),
        "perfil_paci": state.get("perfil_paci", ""),
        "planificacion_adaptada": state.get("planificacion_adaptada", ""),
        "rubrica_final": state.get("rubrica", ""),
        "docx_path": None,
    }

    # Exportar a DOCX solo si hay rúbrica generada
    # (hitl_rejected y timeout terminan sin rúbrica → no se genera documento)
    docx_path = None
    if results.get("rubrica_final"):
        print("[Generando archivo DOCX...]")
        try:
            base_name = os.path.basename(material_path).split('.')[0]
            output_name = f"rubrica_adaptada_{base_name}.docx"
            docx_path = export_results_to_docx(results, output_filename=output_name)
            results["docx_path"] = str(docx_path)
        except Exception as e:
            print(f"  x Error al exportar a DOCX: {e}\n")
    else:
        print(f"  ℹ Sin rúbrica generada (estado: {results['status']}) — DOCX omitido.")

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

    # Flush Langfuse traces before returning (critical for CLI mode — process exits after this)
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception:
        pass

    return results


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python run.py <paci_path> <material_path> [prompt_adicional] [user_id] [school_id]")
        sys.exit(1)

    paci_path = sys.argv[1]
    material_path = sys.argv[2]
    prompt_adicional = sys.argv[3] if len(sys.argv) > 3 else ""
    # Desde CLI el user_id es opcional; si se omite, run_workflow genera un UUID.
    user_id_arg = sys.argv[4] if len(sys.argv) > 4 else ""
    school_id_arg = sys.argv[5] if len(sys.argv) > 5 else ""

    asyncio.run(run_workflow(paci_path, material_path, prompt_adicional, user_id_arg, school_id_arg))
