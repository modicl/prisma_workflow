"""
run_eval.py — Entry point del sistema de evaluación multi-agente PACI.

Uso:
    # Evaluar un caso específico pasando archivos PACI y material
    python eval/run_eval.py --paci docs_test/paci_test.pdf --material docs_test/material_base_test.pdf

    # Evaluar un estado de sesión JSON ya guardado
    python eval/run_eval.py --session eval/golden_set/TEA/expected_outputs.json

    # Evaluar todos los casos del golden set (modo regresión)
    python eval/run_eval.py --all

    # Guardar estado de sesión actual como golden set para un NEE (bootstrap)
    python eval/run_eval.py --paci <path> --material <path> --save-golden TEA
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Asegurar imports desde el paquete prisma_agents
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent / ".env")

from compliance_checks import run_all_compliance_checks
from llm_judge import run_llm_judge, extract_nee_type

GOLDEN_SET_DIR = Path(__file__).parent / "golden_set"
REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

REGRESSION_THRESHOLD = 0.5  # caída de score que se marca como regresión
PASS_THRESHOLD = 3.5         # score mínimo LLM juez para considerar "pass"


# ── Carga de sesión ──────────────────────────────────────────────────────────

async def _run_pipeline(paci_path: str, material_path: str) -> dict:
    """Ejecuta el pipeline de producción y retorna el session state final."""
    from run import run_workflow
    results = await run_workflow(paci_path, material_path)
    # run_workflow retorna los resultados pero no el estado completo;
    # reconstruimos el formato que espera el evaluador
    return {
        "perfil_paci": results.get("perfil_paci", ""),
        "planificacion_adaptada": results.get("planificacion_adaptada", ""),
        "rubrica": results.get("rubrica_final", ""),
        "evaluacion_critica": "",  # no expuesto en run_workflow, usar --session para evaluarlo
        "status": results.get("status", ""),
    }


def load_session_from_file(path: str) -> dict:
    """Carga un estado de sesión desde un JSON (golden set o exportado)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Soporta tanto formato golden set como estado directo
    return data.get("outputs", data)


# ── Scoring ──────────────────────────────────────────────────────────────────

def compute_end_to_end_score(compliance_reports: dict, judge_results: dict) -> float:
    """Promedio ponderado: rúbrica tiene mayor peso (es el entregable principal)."""
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

        agent_score = 0.0
        count = 0
        if compliance:
            agent_score += compliance.score  # 0-1
            count += 1
        if judge and judge.get("overall") is not None:
            agent_score += judge["overall"] / 5.0  # normalizar 1-5 a 0-1
            count += 1
        if count:
            total += (agent_score / count) * weight
            total_weight += weight

    return round(total / total_weight, 3) if total_weight else 0.0


def detect_regressions(current_scores: dict, previous_report_path: Path | None) -> list[str]:
    """Compara scores actuales contra el reporte anterior y detecta caídas significativas."""
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
    overall_pass = end_to_end >= PASS_THRESHOLD / 5.0  # normalizado

    import subprocess
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(Path(__file__).parent.parent)
        ).decode().strip()
    except Exception:
        commit = "unknown"

    return {
        "run_id": run_id,
        "pipeline_version": commit,
        "timestamp": datetime.now().isoformat(),
        "case_id": nee_type,
        "golden_match": golden_match,
        "scores": scores,
        "end_to_end": end_to_end,
        "pass": overall_pass,
        "regressions": [],  # se completa en el caller
    }


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
    """Guarda el estado de sesión actual como golden case para el NEE indicado."""
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


# ── Main ─────────────────────────────────────────────────────────────────────

async def evaluate_session(session_state: dict, run_id: str) -> dict:
    """Evalúa un session state completo y retorna el reporte."""
    print("[1/3] Corriendo checks deterministas...")
    compliance_reports = run_all_compliance_checks(session_state)

    print("[2/3] Corriendo LLM juez...")
    judge_results, nee_type, golden_match = run_llm_judge(session_state)

    print("[3/3] Calculando scores y generando reporte...")
    report = build_report(session_state, compliance_reports, judge_results, nee_type, golden_match, run_id)

    # Detectar regresiones vs reporte anterior
    previous_reports = sorted(REPORTS_DIR.glob("*.json"))
    if len(previous_reports) >= 1:
        last_report = previous_reports[-1]
        # Solo comparar si es el mismo NEE
        with open(last_report) as f:
            prev = json.load(f)
        if prev.get("case_id") == nee_type:
            report["regressions"] = detect_regressions(report["scores"], last_report)

    return report


async def run_all_golden_cases() -> None:
    """Evalúa todos los casos golden disponibles."""
    cases = [d for d in GOLDEN_SET_DIR.iterdir() if d.is_dir() and d.name != "fallback"]
    if not cases:
        print("No hay casos golden disponibles. Usa --save-golden para crear uno.")
        return

    for case_dir in sorted(cases):
        expected_file = case_dir / "expected_outputs.json"
        if not expected_file.exists():
            print(f"⚠ Caso {case_dir.name} sin expected_outputs.json, saltando.")
            continue

        print(f"\n── Evaluando caso: {case_dir.name} ──")
        session_state = load_session_from_file(str(expected_file))
        run_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{case_dir.name}"
        report = await evaluate_session(session_state, run_id)
        print_report(report)

        report_path = REPORTS_DIR / f"{run_id}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  Reporte guardado: {report_path}")


async def main():
    parser = argparse.ArgumentParser(description="Sistema de evaluación PACI Workflow")
    parser.add_argument("--paci", help="Ruta al documento PACI (PDF/DOCX/JSON)")
    parser.add_argument("--material", help="Ruta al material base (PDF/DOCX)")
    parser.add_argument("--session", help="Ruta a un JSON de session state ya guardado")
    parser.add_argument("--all", action="store_true", help="Evaluar todos los casos del golden set")
    parser.add_argument("--save-golden", metavar="NEE_TYPE", help="Guardar output como golden case para el NEE indicado")
    args = parser.parse_args()

    if args.all:
        await run_all_golden_cases()
        return

    # Obtener session state
    if args.session:
        print(f"[Cargando sesión desde archivo: {args.session}]")
        session_state = load_session_from_file(args.session)
    elif args.paci and args.material:
        print(f"[Ejecutando pipeline: {args.paci} + {args.material}]")
        session_state = await _run_pipeline(args.paci, args.material)
    else:
        parser.print_help()
        sys.exit(1)

    # Guardar como golden si se solicitó
    if args.save_golden:
        save_as_golden(session_state, args.save_golden)
        print("\n¿Deseas también evaluar este caso? (s/n): ", end="")
        if input().strip().lower() != "s":
            return

    # Evaluar
    run_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    report = await evaluate_session(session_state, run_id)
    print_report(report)

    # Guardar reporte
    report_path = REPORTS_DIR / f"{run_id}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Reporte guardado en: {report_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
