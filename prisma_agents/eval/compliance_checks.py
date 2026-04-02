"""
compliance_checks.py — Checks deterministas de compliance normativo por agente.

Valida que los outputs cumplan requisitos estructurales y normativos
de los Decretos 83/2015, 170/2010 y 67/2018 sin necesidad de LLM.
"""

import re
from dataclasses import dataclass, field


@dataclass
class CheckResult:
    passed: bool
    rule: str
    detail: str


@dataclass
class AgentComplianceReport:
    agent: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        if not self.checks:
            return 0.0
        return sum(1 for c in self.checks if c.passed) / len(self.checks)

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


# ── AnalizadorPACI ───────────────────────────────────────────────────────────

def check_analizador_paci(perfil_paci: str) -> AgentComplianceReport:
    report = AgentComplianceReport(agent="analizador_paci")

    # 1. Tiene las 5 secciones esperadas
    sections = [
        r"(?i)1\.?\s*(diagnóstico|diagnostico)",
        r"(?i)2\.?\s*(perfil de aprendizaje)",
        r"(?i)3\.?\s*(estrategias|adecuaci[oó]n)",
        r"(?i)4\.?\s*(objetivos de aprendizaje|OA)",
        r"(?i)5\.?\s*(consideraciones|evaluaci[oó]n)",
    ]
    for i, pattern in enumerate(sections, 1):
        found = bool(re.search(pattern, perfil_paci))
        report.checks.append(CheckResult(
            passed=found,
            rule=f"seccion_{i}_presente",
            detail=f"Sección {i} {'encontrada' if found else 'ausente'} en el perfil PACI",
        ))

    # 2. Menciona tipo de NEE (permanente o transitoria)
    nee_mention = bool(re.search(r"(?i)(nee\s+(permanente|transitoria)|permanente|transitoria)", perfil_paci))
    report.checks.append(CheckResult(
        passed=nee_mention,
        rule="nee_tipo_clasificado",
        detail="Tipo de NEE (permanente/transitoria) clasificado según Decreto 170",
    ))

    # 3. Menciona algún diagnóstico conocido del Decreto 170
    diagnosticos = r"(?i)(TEA|DI|discapacidad intelectual|disfasia|TEL|TDAH|visual|auditiva|motora|DA\b|CIL)"
    diag_mention = bool(re.search(diagnosticos, perfil_paci))
    report.checks.append(CheckResult(
        passed=diag_mention,
        rule="diagnostico_d170_mencionado",
        detail="Diagnóstico reconocido por Decreto 170/2010 mencionado en el perfil",
    ))

    # 4. No menciona eximición (prohibida por Decreto 67 Art. 5)
    exencion = bool(re.search(r"(?i)(eximi[dr]|eximici[oó]n|exenci[oó]n de asignatura)", perfil_paci))
    report.checks.append(CheckResult(
        passed=not exencion,
        rule="sin_exencion_prohibida",
        detail="No contiene menciones de eximición (prohibida por D67/2018 Art. 5)",
    ))

    return report


# ── Adaptador ────────────────────────────────────────────────────────────────

def check_adaptador(planificacion_adaptada: str) -> AgentComplianceReport:
    report = AgentComplianceReport(agent="adaptador")

    # 1. Tiene al menos un tag de adaptación
    tags = [r"\[ACCESO\]", r"\[NO SIGNIFICATIVA\]", r"\[ADECUACIÓN SIGNIFICATIVA\]"]
    for tag_pattern in tags:
        found = bool(re.search(tag_pattern, planificacion_adaptada, re.IGNORECASE))
        tag_name = tag_pattern.strip(r"\[\]")
        report.checks.append(CheckResult(
            passed=found,
            rule=f"tag_{tag_name.lower().replace(' ', '_')}_presente",
            detail=f"Tag [{tag_name}] {'encontrado' if found else 'ausente'} en la planificación",
        ))

    # 2. Hay al menos un tag presente (cualquiera de los tres)
    any_tag = any(bool(re.search(p, planificacion_adaptada, re.IGNORECASE)) for p in tags)
    report.checks.append(CheckResult(
        passed=any_tag,
        rule="al_menos_un_tag_adaptacion",
        detail="La planificación incluye al menos un tag de tipo de adaptación DUA",
    ))

    # 3. No menciona eximición
    exencion = bool(re.search(r"(?i)(eximi[dr]|eximici[oó]n)", planificacion_adaptada))
    report.checks.append(CheckResult(
        passed=not exencion,
        rule="sin_exencion_prohibida",
        detail="No contiene menciones de eximición prohibidas",
    ))

    return report


# ── GeneradorRúbrica ─────────────────────────────────────────────────────────

def check_generador_rubrica(rubrica: str) -> AgentComplianceReport:
    report = AgentComplianceReport(agent="generador_rubrica")

    # 1. Los 4 niveles de desempeño (D83/2015)
    niveles = [
        (r"(?i)\bLogrado\b", "nivel_logrado"),
        (r"(?i)(Medianamente Logrado|Med\.\s*Logrado)", "nivel_medianamente_logrado"),
        (r"(?i)(Por Lograr)", "nivel_por_lograr"),
        (r"(?i)(No Logrado)", "nivel_no_logrado"),
    ]
    for pattern, rule in niveles:
        found = bool(re.search(pattern, rubrica))
        report.checks.append(CheckResult(
            passed=found,
            rule=rule,
            detail=f"Nivel '{rule.replace('nivel_', '').replace('_', ' ').title()}' {'presente' if found else 'ausente'} en la rúbrica",
        ))

    # 2. Sección de condiciones de aplicación
    condiciones = bool(re.search(r"(?i)(condiciones de aplicaci[oó]n|condiciones para la evaluaci[oó]n)", rubrica))
    report.checks.append(CheckResult(
        passed=condiciones,
        rule="condiciones_aplicacion_presentes",
        detail="Sección 'Condiciones de Aplicación' presente (D83/2015 + D67/2018 Art. 5)",
    ))

    # 3. Al menos 2 criterios de evaluación (líneas de tabla o ítems numerados)
    criterios = re.findall(r"(?m)^\s*[\|\-\*]\s*.{10,}", rubrica)
    tiene_criterios = len(criterios) >= 2
    report.checks.append(CheckResult(
        passed=tiene_criterios,
        rule="minimo_2_criterios_evaluacion",
        detail=f"Se detectaron {len(criterios)} posibles criterios (mínimo requerido: 2)",
    ))

    # 4. No menciona eximición (D67/2018 Art. 5 — prohibición absoluta)
    exencion = bool(re.search(r"(?i)(eximi[dr]|eximici[oó]n|no ser evaluado)", rubrica))
    report.checks.append(CheckResult(
        passed=not exencion,
        rule="sin_exencion_prohibida",
        detail="No contiene menciones de eximición (prohibida tajantemente por D67/2018 Art. 5)",
    ))

    # 5. Notas para el docente presentes
    notas = bool(re.search(r"(?i)(notas para el docente|orientaciones|nota[s]?\s*docente)", rubrica))
    report.checks.append(CheckResult(
        passed=notas,
        rule="notas_docente_presentes",
        detail="Sección 'Notas para el Docente' presente",
    ))

    return report


# ── AgenteCrítico ────────────────────────────────────────────────────────────

def check_critico(evaluacion_critica: str) -> AgentComplianceReport:
    import json
    report = AgentComplianceReport(agent="critico")

    # 1. Es JSON válido
    parsed = None
    try:
        parsed = json.loads(evaluacion_critica.strip())
        valid_json = True
    except (json.JSONDecodeError, AttributeError):
        # Intento con extracción
        match = re.search(r'\{.*\}', evaluacion_critica or "", re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                valid_json = True
            except json.JSONDecodeError:
                valid_json = False
        else:
            valid_json = False

    report.checks.append(CheckResult(
        passed=valid_json,
        rule="json_valido",
        detail="La respuesta del Agente Crítico es JSON parseable",
    ))

    if not valid_json or parsed is None:
        report.checks.append(CheckResult(
            passed=False,
            rule="campo_acceptable_presente",
            detail="No se puede verificar campos — JSON inválido",
        ))
        return report

    # 2. Tiene campo 'acceptable' booleano
    has_acceptable = "acceptable" in parsed and isinstance(parsed["acceptable"], bool)
    report.checks.append(CheckResult(
        passed=has_acceptable,
        rule="campo_acceptable_presente",
        detail=f"Campo 'acceptable' presente como booleano: {parsed.get('acceptable', 'AUSENTE')}",
    ))

    # 3. Tiene campo 'critique' no vacío
    has_critique = "critique" in parsed and isinstance(parsed.get("critique"), str) and len(parsed["critique"].strip()) > 10
    report.checks.append(CheckResult(
        passed=has_critique,
        rule="campo_critique_presente",
        detail="Campo 'critique' presente con contenido significativo",
    ))

    # 4. Tiene campo 'suggestions' como lista
    has_suggestions = "suggestions" in parsed and isinstance(parsed.get("suggestions"), list)
    report.checks.append(CheckResult(
        passed=has_suggestions,
        rule="campo_suggestions_es_lista",
        detail=f"Campo 'suggestions' presente como lista ({len(parsed.get('suggestions', []))} items)",
    ))

    # 5. Si rechaza, tiene al menos 2 sugerencias
    if parsed.get("acceptable") is False:
        suggestions_count = len(parsed.get("suggestions", []))
        report.checks.append(CheckResult(
            passed=suggestions_count >= 2,
            rule="rechazo_con_2_sugerencias_minimo",
            detail=f"Rechazo incluye {suggestions_count} sugerencias (mínimo requerido: 2)",
        ))

    return report


# ── Runner global ────────────────────────────────────────────────────────────

def run_all_compliance_checks(session_state: dict) -> dict[str, AgentComplianceReport]:
    """Corre todos los checks deterministas sobre el estado de sesión del pipeline."""
    reports = {}

    if perfil := session_state.get("perfil_paci"):
        reports["analizador_paci"] = check_analizador_paci(perfil)

    if planif := session_state.get("planificacion_adaptada"):
        reports["adaptador"] = check_adaptador(planif)

    if rubrica := session_state.get("rubrica"):
        reports["generador_rubrica"] = check_generador_rubrica(rubrica)

    if critica := session_state.get("evaluacion_critica"):
        reports["critico"] = check_critico(critica)

    return reports
