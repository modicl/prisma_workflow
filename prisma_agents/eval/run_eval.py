"""
run_eval.py — Entry point del sistema de evaluación multi-agente PACI.

Uso:
    # Correr pipeline ahora y evaluar el output
    python eval/run_eval.py --paci docs_test/paci_test.pdf --material docs_test/material_base_test.pdf

    # Evaluar un estado de sesión desde un JSON
    python eval/run_eval.py --session eval/golden_set/TEA/expected_outputs.json

    # Evaluar todos los casos del golden set
    python eval/run_eval.py --all

    # Guardar output como golden set bootstrap para un NEE
    python eval/run_eval.py --paci <path> --material <path> --save-golden TEA

    # Evaluar una sesión histórica de la BD por su ID
    python eval/run_eval.py --session-id e-44ae2650-ed52-4ea6-b38f-d14717f9b402

    # Evaluar 10% de sesiones de la BD no evaluadas aún
    python eval/run_eval.py --sample 10

    # Evaluar todos los 👎 (edge cases) no evaluados
    python eval/run_eval.py --edge-cases
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent / ".env")

from compliance_checks import run_all_compliance_checks
from llm_judge import run_llm_judge, extract_nee_type
from db_migrations import (
    run_migrations,
    get_unevaluated_sessions,
    get_edge_case_sessions,
    save_eval_result,
)

GOLDEN_SET_DIR = Path(__file__).parent / "golden_set"
REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

REGRESSION_THRESHOLD = 0.5
PASS_THRESHOLD = 3.5


# ── Carga de sesión ──────────────────────────────────────────────────────────

async def _run_pipeline(paci_path: str, material_path: str) -> dict:
    """Ejecuta el pipeline y retorna el session state final."""
    from run import run_workflow
    results = await run_workflow(paci_path, material_path)
    return {
        "perfil_paci": results.get("perfil_paci", ""),
        "planificacion_adaptada": results.get("planificacion_adaptada", ""),
        "rubrica": results.get("rubrica_final", ""),
        "evaluacion_critica": "",
        "status": results.get("status", ""),
    }


def load_session_from_file(path: str) -> dict:
    """Carga un estado de sesión desde un JSON."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("outputs", data)


async def load_session_from_db(session_id: str) -> dict:
    """Carga el session.state completo desde PostgreSQL."""
    db_url = os.environ.get("BD_LOGS")
    if not db_url:
        raise ValueError("BD_LOGS no configurado en .env")

    from google.adk.sessions.database_session_service import DatabaseSessionService
    session_service = DatabaseSessionService(db_url=db_url)
    session = await session_service.get_session(
        app_name="paci_workflow",
        user_id="docente",
        session_id=session_id,
    )
    if not session:
        raise ValueError(f"Sesión no encontrada: {session_id}")
    return dict(session.state)


# ── Scoring ──────────────────────────────────────────────────────────────────

def compute_end_to_end_score(compliance_reports: dict, judge_results: dict) -> float:
    weights = {
        "analizador_paci": 0.20,
        "adaptador": 0.25,
        "generador_rubrica": 0.40,
        "critico": 0.15,
    }
    total, total_weight = 0.0, 0.0
    for agent, weight in weights.items():
        compliance = compliance_reports.get(agent)
        judge = judge_results.get(agent)
        agent_score, count = 0.0, 0
        if compliance:
            agent_score += compliance.score
            count += 1
        if judge and judge.get("overall") is not None:
            agent_score += judge["overall"] / 5.0
            count += 1
        if count:
            total += (agent_score / count) * weight
            total_weight += weight
    return round(total / total_weight, 3) if total_weight else 0.0


def detect_regressions(current_scores: dict, previous_report_path: Path | None) -> list[str]:
    if not previous_report_path or not previous_report_path.exists():
        return []
    with open(previous_report_path, "r", encoding="utf-8") as f:
        previous = json.load(f)
    regressions = []
    prev_scores = previous.get("scores", {})
    for agent, data in current_scores.items():
        prev_llm = prev_scores.get(agent, {}).get("llm_judge")
        curr_llm = data.get("llm_judge")
        if prev_llm is not None and curr_llm is not None:
            if (prev_llm - curr_llm) > REGRESSION_THRESHOLD:
                regressions.append(
                    f"{agent}: score LLM bajó de {prev_llm:.2f} a {curr_llm:.2f} "
                    f"(caída de {prev_llm - curr_llm:.2f})"
                )
    return regressions


# ── Reporte ──────────────────────────────────────────────────────────────────

def build_report(
    session_state: dict,
    compliance_reports: dict,
    judge_results: dict,
    nee_type: str,
    golden_match: str,
    run_id: str,
    docente_reason: str = "",
) -> dict:
    scores = {}
    for agent in set(list(compliance_reports.keys()) + list(judge_results.keys())):
        compliance_score = compliance_reports[agent].score if agent in compliance_reports else None
        judge_score = judge_results[agent].get("overall") if agent in judge_results else None
        scores[agent] = {
            "compliance": round(compliance_score, 3) if compliance_score is not None else None,
            "llm_judge": round(judge_score, 3) if judge_score is not None else None,
            "confidence": judge_results.get(agent, {}).get("confidence"),
            "failed_checks": [
                {"rule": c.rule, "detail": c.detail}
                for c in (compliance_reports.get(agent).failed if agent in compliance_reports else [])
            ],
            "critical_issues": judge_results.get(agent, {}).get("critical_issues", []),
        }

    end_to_end = compute_end_to_end_score(compliance_reports, judge_results)
    overall_pass = end_to_end >= PASS_THRESHOLD / 5.0

    import subprocess
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(Path(__file__).parent.parent)
        ).decode().strip()
    except Exception:
        commit = "unknown"

    report = {
        "run_id": run_id,
        "pipeline_version": commit,
        "timestamp": datetime.now().isoformat(),
        "case_id": nee_type,
        "golden_match": golden_match,
        "scores": scores,
        "end_to_end": end_to_end,
        "pass": overall_pass,
        "regressions": [],
    }

    # Análisis de causa raíz para edge cases (cuando hay feedback del docente)
    if docente_reason:
        failing_agents = [
            agent for agent, data in scores.items()
            if (data.get("llm_judge") or 5) < 3.0 or data.get("failed_checks")
        ]
        report["root_cause"] = {
            "feedback_from_docente": docente_reason,
            "failing_agents": failing_agents,
            "suggestion": (
                f"Revisar prompts de: {', '.join(failing_agents)}"
                if failing_agents else "No se detectaron agentes con fallo claro — revisar manualmente"
            ),
        }

    return report


def print_report(report: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  REPORTE DE EVALUACIÓN — {report['run_id']}")
    print(f"{'='*60}")
    print(f"  NEE:          {report['case_id']}  (golden: {report['golden_match']})")
    print(f"  Pipeline:     {report['pipeline_version']}")
    print(f"  Score E2E:    {report['end_to_end']:.3f}  {'✅ PASS' if report['pass'] else '❌ FAIL'}")

    if report.get("regressions"):
        print(f"\n  ⚠️  REGRESIONES DETECTADAS:")
        for r in report["regressions"]:
            print(f"     - {r}")

    if report.get("root_cause"):
        rc = report["root_cause"]
        print(f"\n  🔍 CAUSA RAÍZ (edge case):")
        print(f"     Feedback docente: {rc['feedback_from_docente']}")
        print(f"     Agentes con fallo: {', '.join(rc['failing_agents']) or 'ninguno detectado'}")
        print(f"     Sugerencia: {rc['suggestion']}")

    print(f"\n  SCORES POR AGENTE:")
    for agent, data in report["scores"].items():
        comp = f"{data['compliance']:.2f}" if data["compliance"] is not None else "N/A"
        llm = f"{data['llm_judge']:.2f}" if data["llm_judge"] is not None else "N/A"
        conf = f" [{data['confidence']}]" if data["confidence"] else ""
        print(f"    {agent:25s}  compliance={comp}  llm_judge={llm}{conf}")
        for fc in data.get("failed_checks", []):
            print(f"      ✗ {fc['rule']}: {fc['detail']}")
        for ci in data.get("critical_issues", []):
            print(f"      ⚠ {ci}")

    print(f"{'='*60}\n")


# ── Golden set bootstrap ─────────────────────────────────────────────────────

def save_as_golden(session_state: dict, nee_type: str) -> None:
    dest_dir = GOLDEN_SET_DIR / nee_type
    dest_dir.mkdir(exist_ok=True)
    golden = {
        "case_id": nee_type,
        "validated": False,
        "validator": None,
        "created_at": datetime.now().isoformat(),
        "outputs": {
            "perfil_paci": session_state.get("perfil_paci", ""),
            "planificacion_adaptada": session_state.get("planificacion_adaptada", ""),
            "rubrica": session_state.get("rubrica", ""),
            "evaluacion_critica": session_state.get("evaluacion_critica", ""),
        },
    }
    out_path = dest_dir / "expected_outputs.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(golden, f, ensure_ascii=False, indent=2)
    print(f"  ✓ Golden set guardado en: {out_path}")
    print(f"  ⚠️  Marcado como no validado. Solicitar revisión de psicopedagogo.")


# ── Evaluación ────────────────────────────────────────────────────────────────

async def evaluate_session(
    session_state: dict,
    run_id: str,
    docente_reason: str = "",
) -> dict:
    print("[1/3] Corriendo checks deterministas...")
    compliance_reports = run_all_compliance_checks(session_state)

    print("[2/3] Corriendo LLM juez...")
    judge_results, nee_type, golden_match = run_llm_judge(session_state)

    print("[3/3] Calculando scores y generando reporte...")
    report = build_report(
        session_state, compliance_reports, judge_results,
        nee_type, golden_match, run_id, docente_reason,
    )

    previous_reports = sorted(REPORTS_DIR.glob("*.json"))
    if previous_reports:
        with open(previous_reports[-1]) as f:
            prev = json.load(f)
        if prev.get("case_id") == nee_type:
            report["regressions"] = detect_regressions(report["scores"], previous_reports[-1])

    return report


async def _evaluate_and_save(
    session_state: dict,
    run_id: str,
    triggered_by: str,
    docente_reason: str = "",
    save_to_db: bool = True,
) -> dict:
    """Evalúa, imprime, guarda reporte en disco y opcionalmente en la BD."""
    report = await evaluate_session(session_state, run_id, docente_reason)
    print_report(report)

    report_path = REPORTS_DIR / f"{run_id}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  Reporte guardado: {report_path}")

    if save_to_db:
        db_url = os.environ.get("BD_LOGS")
        if db_url:
            await save_eval_result(db_url, report, triggered_by)
            print(f"  ✓ Resultado guardado en eval_results (BD)")

    return report


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Sistema de evaluación PACI Workflow")
    parser.add_argument("--paci", help="Ruta al PACI (PDF/DOCX/JSON)")
    parser.add_argument("--material", help="Ruta al material base (PDF/DOCX)")
    parser.add_argument("--session", help="Ruta a un JSON de session state")
    parser.add_argument("--session-id", metavar="UUID", help="ID de sesión en BD para evaluar run histórico")
    parser.add_argument("--all", action="store_true", help="Evaluar todos los casos del golden set")
    parser.add_argument("--sample", type=int, metavar="PCT", help="Evaluar PCT%% de sesiones no evaluadas (ej: 10)")
    parser.add_argument("--edge-cases", action="store_true", help="Evaluar todos los 👎 no evaluados")
    parser.add_argument("--save-golden", metavar="NEE_TYPE", help="Guardar output como golden case")
    args = parser.parse_args()

    db_url = os.environ.get("BD_LOGS")

    # Asegurar migraciones
    if db_url:
        await run_migrations(db_url)

    # ── Modo --all: golden set completo ─────────────────────────────────────
    if args.all:
        cases = [d for d in GOLDEN_SET_DIR.iterdir() if d.is_dir() and d.name != "fallback"]
        if not cases:
            print("No hay casos golden. Usa --save-golden para crear uno.")
            return
        for case_dir in sorted(cases):
            expected_file = case_dir / "expected_outputs.json"
            if not expected_file.exists():
                print(f"⚠ {case_dir.name}: sin expected_outputs.json, saltando.")
                continue
            print(f"\n── Evaluando golden: {case_dir.name} ──")
            state = load_session_from_file(str(expected_file))
            run_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{case_dir.name}"
            await _evaluate_and_save(state, run_id, triggered_by="golden_set", save_to_db=False)
        return

    # ── Modo --sample: muestreo de sesiones de la BD ─────────────────────────
    if args.sample is not None:
        if not db_url:
            print("Error: BD_LOGS requerido para --sample")
            sys.exit(1)
        session_ids = await get_unevaluated_sessions(db_url, args.sample)
        if not session_ids:
            print("No hay sesiones nuevas para evaluar.")
            return
        print(f"\n[Muestreo {args.sample}%] {len(session_ids)} sesión(es) a evaluar\n")
        for sid in session_ids:
            print(f"\n── Evaluando sesión: {sid} ──")
            try:
                state = await load_session_from_db(sid)
                run_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{sid[:8]}"
                await _evaluate_and_save(state, run_id, triggered_by="sample")
            except Exception as e:
                print(f"  ✗ Error en sesión {sid}: {e}")
        return

    # ── Modo --edge-cases: sesiones con 👎 ───────────────────────────────────
    if args.edge_cases:
        if not db_url:
            print("Error: BD_LOGS requerido para --edge-cases")
            sys.exit(1)
        edge_cases = await get_edge_case_sessions(db_url)
        if not edge_cases:
            print("No hay edge cases pendientes de evaluar.")
            return
        print(f"\n[Edge cases] {len(edge_cases)} sesión(es) con 👎 a evaluar\n")
        for sid, reason in edge_cases:
            print(f"\n── Edge case: {sid} ──")
            if reason:
                print(f"   Feedback docente: {reason}")
            try:
                state = await load_session_from_db(sid)
                run_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{sid[:8]}_edge"
                await _evaluate_and_save(state, run_id, triggered_by="edge_case", docente_reason=reason)
            except Exception as e:
                print(f"  ✗ Error en sesión {sid}: {e}")
        return

    # ── Modo --session-id: sesión específica de la BD ────────────────────────
    if args.session_id:
        if not db_url:
            print("Error: BD_LOGS requerido para --session-id")
            sys.exit(1)
        print(f"[Cargando sesión desde BD: {args.session_id}]")
        state = await load_session_from_db(args.session_id)
        run_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.session_id[:8]}"
        await _evaluate_and_save(state, run_id, triggered_by="manual")
        return

    # ── Modo clásico: archivo JSON o pipeline fresco ─────────────────────────
    if args.session:
        print(f"[Cargando sesión desde archivo: {args.session}]")
        state = load_session_from_file(args.session)
    elif args.paci and args.material:
        print(f"[Ejecutando pipeline: {args.paci} + {args.material}]")
        state = await _run_pipeline(args.paci, args.material)
    else:
        parser.print_help()
        sys.exit(1)

    if args.save_golden:
        save_as_golden(state, args.save_golden)
        print("\n¿Deseas también evaluar este caso? (s/n): ", end="")
        if input().strip().lower() != "s":
            return

    run_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    await _evaluate_and_save(state, run_id, triggered_by="manual", save_to_db=False)


if __name__ == "__main__":
    asyncio.run(main())
