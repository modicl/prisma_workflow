"""
llm_judge.py — LLM-as-judge para evaluación de calidad pedagógica.

Usa Gemini para evaluar cada output del pipeline comparando contra
el golden set correspondiente al tipo de NEE del caso.
"""

import json
import os
import re
from pathlib import Path

from google import genai

MODEL = "gemini-2.5-flash-lite"
GOLDEN_SET_DIR = Path(__file__).parent / "golden_set"

# Dimensiones de evaluación por agente (escala 1-5)
DIMENSIONS = {
    "analizador_paci": [
        ("oa_extraidos_fielmente", "¿Los OA extraídos corresponden a los del PACI real sin inventar información?"),
        ("sin_alucinaciones", "¿El perfil evita incluir datos clínicos o diagnósticos no mencionados en el PACI?"),
    ],
    "adaptador": [
        ("coherencia_nee_adaptaciones", "¿Las adaptaciones aplicadas son coherentes con el tipo de NEE identificado?"),
        ("principios_dua_aplicados", "¿Se aplican los 3 pilares DUA: representación múltiple, expresión flexible y participación/motivación?"),
    ],
    "generador_rubrica": [
        ("alineacion_oa_paci", "¿Los criterios de la rúbrica evalúan los OA del PACI y no los OA generales del curso?"),
        ("criterios_observables", "¿Los descriptores usan verbos de acción observables y medibles (no estados internos)?"),
        ("coherencia_nivel_nee", "¿Los descriptores son apropiados para el nivel funcional del estudiante con esa NEE?"),
    ],
    "critico": [
        ("consistencia_decision", "¿La decisión acceptable/rejected es coherente con el análisis en 'critique'?"),
        ("feedback_accionable", "Si rechazó, ¿las sugerencias son específicas y accionables para el GeneradorRúbrica?"),
    ],
}

JUDGE_PROMPT_TEMPLATE = """Eres un evaluador experto en educación inclusiva y normativa educacional chilena \
(Decreto 83/2015, Decreto 170/2010, Decreto 67/2018).

Tu tarea es evaluar la CALIDAD PEDAGÓGICA del output de un agente de IA.

═══════════════════════════════════════════════════════════════
CASO DE REFERENCIA (Golden Set — NEE: {nee_type})
═══════════════════════════════════════════════════════════════
{golden_reference}

═══════════════════════════════════════════════════════════════
OUTPUT A EVALUAR ({agent_name})
═══════════════════════════════════════════════════════════════
{output_to_evaluate}

═══════════════════════════════════════════════════════════════
DIMENSIONES A EVALUAR
═══════════════════════════════════════════════════════════════
{dimensions_text}

Evalúa CADA dimensión en escala 1-5:
  5 = Excelente, cumple completamente
  4 = Bien, cumple con observaciones menores
  3 = Regular, cumple parcialmente
  2 = Deficiente, incumplimiento notable
  1 = Muy deficiente, no cumple

Responde ÚNICAMENTE con JSON válido, sin texto antes ni después:

{{
  "agent": "{agent_name}",
  "nee_type": "{nee_type}",
  "golden_match": "{golden_match}",
  "scores": {{
    "<dimension_id>": {{"score": <1-5>, "justification": "<máx 2 oraciones>"}}
  }},
  "overall": <promedio float>,
  "pass": <true si overall >= 3.5>,
  "critical_issues": ["<issue si score <= 2>"]
}}"""


def extract_nee_type(perfil_paci: str) -> str:
    """Extrae el tipo de NEE del perfil PACI generado por AnalizadorPACI."""
    nee_patterns = [
        (r"(?i)\bTEA\b|trastorno del espectro autista", "TEA"),
        (r"(?i)\bDI\b|discapacidad intelectual", "DI"),
        (r"(?i)\bTEL\b|trastorno específico del lenguaje", "TEL"),
        (r"(?i)\bdisfasia\b", "Disfasia"),
        (r"(?i)\bTDAH\b|trastorno por déficit de atención", "TDAH"),
        (r"(?i)\bdiscapacidad visual\b|\bceguera\b|\bbaja visión\b", "Visual"),
        (r"(?i)\bdiscapacidad auditiva\b|\bsordera\b|\bhipoacusia\b", "Auditiva"),
        (r"(?i)\bdiscapacidad motora\b|\bmotora\b", "Motora"),
        (r"(?i)\bDA\b|dificultad de aprendizaje", "DA"),
    ]
    for pattern, nee_label in nee_patterns:
        if re.search(pattern, perfil_paci):
            return nee_label
    return "fallback"


def load_golden_case(nee_type: str) -> tuple[dict, str]:
    """
    Carga el golden case para el NEE dado.
    Retorna (golden_data, match_type) donde match_type es 'exact' o 'fallback'.
    """
    case_dir = GOLDEN_SET_DIR / nee_type
    expected_file = case_dir / "expected_outputs.json"

    if expected_file.exists():
        with open(expected_file, "r", encoding="utf-8") as f:
            return json.load(f), "exact"

    # Fallback genérico
    fallback_file = GOLDEN_SET_DIR / "fallback" / "expected_outputs.json"
    if fallback_file.exists():
        with open(fallback_file, "r", encoding="utf-8") as f:
            return json.load(f), "fallback"

    return {}, "none"


def _format_golden_reference(golden_data: dict, agent_name: str) -> str:
    """Formatea la sección relevante del golden case para el prompt del juez."""
    key_map = {
        "analizador_paci": "perfil_paci",
        "adaptador": "planificacion_adaptada",
        "generador_rubrica": "rubrica",
        "critico": "evaluacion_critica",
    }
    key = key_map.get(agent_name)
    if not key or not golden_data.get(key):
        return "(No hay referencia golden disponible para este agente)"

    content = golden_data[key]
    validated = golden_data.get("validated", False)
    note = "✅ Validado por experto" if validated else "⚠️ Bootstrap no validado (línea base provisional)"
    return f"[{note}]\n\n{content}"


def _format_dimensions(agent_name: str) -> str:
    dims = DIMENSIONS.get(agent_name, [])
    return "\n".join(f"- {dim_id}: {description}" for dim_id, description in dims)


def judge_agent_output(
    agent_name: str,
    output: str,
    nee_type: str,
    golden_data: dict,
    golden_match: str,
) -> dict:
    """Evalúa el output de un agente usando el LLM juez."""
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))

    golden_ref = _format_golden_reference(golden_data, agent_name)
    dimensions_text = _format_dimensions(agent_name)

    if not dimensions_text:
        return {
            "agent": agent_name,
            "nee_type": nee_type,
            "golden_match": golden_match,
            "scores": {},
            "overall": None,
            "pass": None,
            "critical_issues": ["No hay dimensiones definidas para este agente"],
        }

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        nee_type=nee_type,
        golden_reference=golden_ref,
        agent_name=agent_name,
        output_to_evaluate=output[:4000],  # truncar para no exceder tokens
        dimensions_text=dimensions_text,
        golden_match=golden_match,
    )

    response = client.models.generate_content(model=MODEL, contents=prompt)
    raw = response.text.strip()

    # Parseo robusto
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                result = {
                    "agent": agent_name,
                    "error": "JSON inválido en respuesta del juez",
                    "raw_response": raw[:500],
                }
        else:
            result = {
                "agent": agent_name,
                "error": "Sin JSON en respuesta del juez",
                "raw_response": raw[:500],
            }

    # Marcar confianza baja si se usó fallback
    if golden_match == "fallback":
        result["confidence"] = "low"
    elif golden_match == "none":
        result["confidence"] = "very_low"
    else:
        result["confidence"] = "high"

    return result


def run_llm_judge(session_state: dict) -> dict[str, dict]:
    """
    Corre el LLM juez para todos los agentes con output disponible.
    Retorna dict de resultados por agente.
    """
    perfil = session_state.get("perfil_paci", "")
    nee_type = extract_nee_type(perfil) if perfil else "fallback"
    golden_data, golden_match = load_golden_case(nee_type)

    agent_output_map = {
        "analizador_paci": perfil,
        "adaptador": session_state.get("planificacion_adaptada", ""),
        "generador_rubrica": session_state.get("rubrica", ""),
        "critico": session_state.get("evaluacion_critica", ""),
    }

    results = {}
    for agent_name, output in agent_output_map.items():
        if not output:
            continue
        results[agent_name] = judge_agent_output(
            agent_name=agent_name,
            output=output,
            nee_type=nee_type,
            golden_data=golden_data,
            golden_match=golden_match,
        )

    return results, nee_type, golden_match
