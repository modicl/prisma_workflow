from agent import _extract_metadatos

PERFIL = """## 1. Diagnóstico
Bla bla.

---METADATOS---
RAMO: Matemáticas
CURSO: 5° Básico
DIAGNOSTICO: TEA
FECHA_INFORME: 2025-09-01
PUEDE_CONTINUAR: SI
---FIN_METADATOS---"""


def test_extrae_campos_nuevos():
    meta = _extract_metadatos(PERFIL)
    assert meta["ramo"] == "Matemáticas"
    assert meta["diagnostico"] == "TEA"
    assert meta["fecha_informe"] == "2025-09-01"
    assert meta["puede_continuar"] == "SI"


def test_campos_ausentes_default_vacio():
    meta = _extract_metadatos("sin bloque de metadatos")
    assert meta["fecha_informe"] == ""
    assert meta["puede_continuar"] == ""
