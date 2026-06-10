"""Gates deterministas de compliance (Fase A).

Funciones puras que deciden si el flujo debe detenerse por incumplimiento
normativo (Decretos 170/2010, 83/2015, 67/2018). No tocan ADK ni LLM:
reciben los METADATOS ya parseados / el JSON del Crítico y una fecha de
referencia, de modo que son testeables en aislamiento.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from utils.nee_taxonomy import normalize_diagnostico

# Decreto 170/2010 exige reevaluación cada 2 años (24 meses). Bloqueamos de
# forma preventiva a partir de los 20 meses (decisión de producto: máxima
# restricción) para no entregar material con un informe a punto de vencer.
REEVAL_BLOCK_MONTHS = 20


@dataclass
class ComplianceResult:
    blocked: bool
    code: str = ""        # paci_incompleto | diagnostico_no_reconocido | informe_vencido
    reason: str = ""      # mensaje orientado al docente (incluye el decreto inline)
    decreto: str = ""     # "170/2010" | "83/2015" | ...


def _months_between(earlier: date, today: date) -> int:
    """Meses calendario completos entre `earlier` y `today` (>= 0)."""
    months = (today.year - earlier.year) * 12 + (today.month - earlier.month)
    if today.day < earlier.day:
        months -= 1
    return max(months, 0)


def _parse_iso_date(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    if not raw or raw.upper() in ("NO_ENCONTRADA", "NO_PROCESADO"):
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def evaluate_paci_compliance(metadatos: dict, today: date) -> ComplianceResult:
    """Evalúa los gates de PACI en orden de severidad. Primer fallo bloquea."""
    puede = (metadatos.get("puede_continuar") or "").strip().upper()
    if puede == "NO":
        motivo = (metadatos.get("motivo") or "").strip()
        detalle = ""
        if motivo and motivo.upper() not in ("N/A", "NA", "NO_PROCESADO", ""):
            detalle = f" Detalle: {motivo}."
        return ComplianceResult(
            True,
            "paci_incompleto",
            "El PACI no es procesable: contiene datos personales directos "
            "(nombre o RUT) o le faltan campos obligatorios (diagnóstico, "
            "vigencia, estrategias)." + detalle + " Revise el documento antes "
            "de reintentar. (Decreto 83/2015 — Ley 21.719)",
            "83/2015",
        )

    diagnostico = normalize_diagnostico(metadatos.get("diagnostico", ""))
    if diagnostico == "otro":
        return ComplianceResult(
            True,
            "diagnostico_no_reconocido",
            "El diagnóstico declarado no corresponde a una categoría NEE "
            "reconocida por el Decreto 170/2010. Verifique el diagnóstico del "
            "PACI. (Decreto 170/2010)",
            "170/2010",
        )

    report_date = _parse_iso_date(metadatos.get("fecha_informe", ""))
    if report_date is None:
        return ComplianceResult(
            True,
            "informe_vencido",
            "No se pudo determinar la fecha del informe clínico o "
            "psicopedagógico. El Decreto 170/2010 exige una reevaluación "
            "vigente. (Decreto 170/2010)",
            "170/2010",
        )
    if _months_between(report_date, today) >= REEVAL_BLOCK_MONTHS:
        return ComplianceResult(
            True,
            "informe_vencido",
            "El informe clínico o psicopedagógico supera la vigencia para "
            "reevaluación del Decreto 170/2010 (reevaluación cada 2 años). "
            "Se requiere una reevaluación actualizada. (Decreto 170/2010)",
            "170/2010",
        )

    return ComplianceResult(False)


@dataclass
class CriticDecision:
    action: str                       # "accept" | "regenerate" | "block_critical"
    score: int = 0
    warnings: list = field(default_factory=list)
    critical_issues: list = field(default_factory=list)
    regeneration_instructions: str = ""


def interpret_critic_decision(evaluacion: dict) -> CriticDecision:
    """Traduce el JSON del AgenteCritico a una acción del orquestador."""
    critical = evaluacion.get("critical_issues") or []
    warnings = evaluacion.get("warnings_for_teacher") or []
    score = int(evaluacion.get("score") or 0)

    if critical:
        return CriticDecision(
            "block_critical", score=score, warnings=warnings,
            critical_issues=critical,
        )

    if evaluacion.get("acceptable", False):
        return CriticDecision("accept", score=score, warnings=warnings)

    instructions = (evaluacion.get("regeneration_instructions") or "").strip()
    if not instructions:
        critique = evaluacion.get("critique", "")
        suggestions = evaluacion.get("suggestions") or []
        sug_text = "\n".join(f"- {s}" for s in suggestions)
        instructions = f"{critique}\n{sug_text}".strip()
    # Red de seguridad: nunca devolver feedback de regeneración vacío (flujo de alta
    # criticidad — un prompt sin contenido degradaría la siguiente iteración).
    if not instructions:
        instructions = "Sin retroalimentación específica del evaluador. Revise coherencia normativa y los 4 niveles de desempeño."

    return CriticDecision(
        "regenerate", score=score, warnings=warnings,
        regeneration_instructions=instructions,
    )
