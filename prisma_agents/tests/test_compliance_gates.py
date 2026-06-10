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
