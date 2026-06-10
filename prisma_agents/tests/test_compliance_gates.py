from datetime import date

from utils.compliance_gates import evaluate_paci_compliance, _months_between

TODAY = date(2026, 6, 10)


def _meta(**over):
    base = {
        "ramo": "Matemáticas",
        "curso": "5° Básico",
        "diagnostico": "TEA",
        "fecha_informe": "2025-09-01",  # ~9 meses → vigente
        "puede_continuar": "SI",
    }
    base.update(over)
    return base


def test_months_between_basic():
    assert _months_between(date(2024, 6, 10), TODAY) == 24
    assert _months_between(date(2026, 5, 11), TODAY) == 0
    assert _months_between(date(2024, 6, 11), TODAY) == 23  # falta 1 día para 24


def test_paci_conforme_no_bloquea():
    r = evaluate_paci_compliance(_meta(), TODAY)
    assert r.blocked is False
    assert r.code == ""


def test_puede_continuar_no_bloquea():
    r = evaluate_paci_compliance(_meta(puede_continuar="NO"), TODAY)
    assert r.blocked is True
    assert r.code == "paci_incompleto"
    assert "83/2015" in r.decreto


def test_diagnostico_no_reconocido_bloquea():
    r = evaluate_paci_compliance(_meta(diagnostico="resfrío común"), TODAY)
    assert r.blocked is True
    assert r.code == "diagnostico_no_reconocido"


def test_fecha_informe_ausente_bloquea():
    r = evaluate_paci_compliance(_meta(fecha_informe="NO_ENCONTRADA"), TODAY)
    assert r.blocked is True
    assert r.code == "informe_vencido"


def test_fecha_informe_invalida_bloquea():
    r = evaluate_paci_compliance(_meta(fecha_informe="ayer"), TODAY)
    assert r.blocked is True
    assert r.code == "informe_vencido"


def test_informe_19_meses_pasa():
    r = evaluate_paci_compliance(_meta(fecha_informe="2024-11-10"), TODAY)
    assert r.blocked is False


def test_informe_20_meses_bloquea():
    r = evaluate_paci_compliance(_meta(fecha_informe="2024-10-10"), TODAY)
    assert r.blocked is True
    assert r.code == "informe_vencido"


def test_informe_30_meses_bloquea():
    r = evaluate_paci_compliance(_meta(fecha_informe="2023-12-10"), TODAY)
    assert r.blocked is True
    assert r.code == "informe_vencido"


def test_precedencia_puede_continuar_gana():
    r = evaluate_paci_compliance(
        _meta(puede_continuar="NO", diagnostico="resfrío"), TODAY
    )
    assert r.code == "paci_incompleto"


def test_motivo_se_incluye_en_el_mensaje():
    r = evaluate_paci_compliance(
        _meta(puede_continuar="NO", motivo="diagnóstico, período de vigencia"), TODAY
    )
    assert r.blocked is True
    assert r.code == "paci_incompleto"
    assert "período de vigencia" in r.reason


def test_motivo_na_usa_mensaje_generico():
    r = evaluate_paci_compliance(_meta(puede_continuar="NO", motivo="N/A"), TODAY)
    assert r.code == "paci_incompleto"
    assert "faltan campos obligatorios" in r.reason


def test_pii_detectado_bloquea_con_prioridad():
    # PII tiene prioridad sobre cualquier otro fallo y devuelve su propio código/mensaje.
    r = evaluate_paci_compliance(
        _meta(pii_detectado=True, puede_continuar="NO", diagnostico="resfrío"), TODAY
    )
    assert r.blocked is True
    assert r.code == "pii_detectado"
    assert "21.719" in r.decreto
    assert "código interno" in r.reason


from utils.compliance_gates import interpret_critic_decision


def test_critic_block_critical():
    d = interpret_critic_decision({
        "acceptable": False, "must_regenerate": True, "score": 30,
        "critical_issues": ["C1"], "warnings_for_teacher": [],
        "regeneration_instructions": "x",
    })
    assert d.action == "block_critical"
    assert d.critical_issues == ["C1"]


def test_critic_accept_sin_warnings():
    d = interpret_critic_decision({
        "acceptable": True, "must_regenerate": False, "score": 88,
        "critical_issues": [], "warnings_for_teacher": [],
        "regeneration_instructions": "",
    })
    assert d.action == "accept"
    assert d.warnings == []


def test_critic_accept_con_warnings():
    d = interpret_critic_decision({
        "acceptable": True, "must_regenerate": False, "score": 70,
        "critical_issues": [], "warnings_for_teacher": ["Revisar Q2"],
        "regeneration_instructions": "",
    })
    assert d.action == "accept"
    assert d.warnings == ["Revisar Q2"]


def test_critic_regenerate_usa_instructions():
    d = interpret_critic_decision({
        "acceptable": False, "must_regenerate": True, "score": 40,
        "critical_issues": [], "warnings_for_teacher": [],
        "regeneration_instructions": "Agrega 4 niveles de desempeño.",
    })
    assert d.action == "regenerate"
    assert "4 niveles" in d.regeneration_instructions


def test_critic_regenerate_fallback_a_critique():
    d = interpret_critic_decision({
        "acceptable": False, "score": 40, "critical_issues": [],
        "warnings_for_teacher": [], "regeneration_instructions": "",
        "critique": "Faltan criterios.", "suggestions": ["Añadir criterio X"],
    })
    assert d.action == "regenerate"
    assert "Faltan criterios." in d.regeneration_instructions
    assert "Añadir criterio X" in d.regeneration_instructions


def test_critic_fallback_de_json_malformado_va_a_regenerate():
    # Contrato cruzado: el dict que produce _parse_critic_json ante un JSON inválido
    # debe ser interpretado como 'regenerate' con instrucción no vacía.
    from agent import _parse_critic_json

    fallback = _parse_critic_json("esto no es json")
    d = interpret_critic_decision(fallback)
    assert d.action == "regenerate"
    assert d.regeneration_instructions.strip() != ""


def test_critic_regenerate_nunca_vacio():
    # Crítico degenerado: rechaza sin dar ninguna pista → debe usarse el default.
    d = interpret_critic_decision({
        "acceptable": False, "score": 40, "critical_issues": [],
        "warnings_for_teacher": [], "regeneration_instructions": "",
        "critique": "", "suggestions": [],
    })
    assert d.action == "regenerate"
    assert d.regeneration_instructions.strip() != ""
