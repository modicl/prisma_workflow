import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from utils.curriculum_catalog import normalize_subject, normalize_grade


def test_normalize_grade_with_degree_symbol():
    assert normalize_grade("5° Básico") == "5basico"


def test_normalize_grade_word_form():
    assert normalize_grade("quinto básico") == "5basico"


def test_normalize_grade_no_accent():
    assert normalize_grade("5° basico") == "5basico"


def test_normalize_grade_medio():
    assert normalize_grade("1° Medio") == "1medio"


def test_normalize_grade_cuarto_medio():
    assert normalize_grade("Cuarto Medio") == "4medio"


def test_normalize_grade_unknown_returns_none():
    assert normalize_grade("kinder") is None


def test_normalize_grade_empty_returns_none():
    assert normalize_grade("") is None


def test_normalize_subject_matematica():
    assert normalize_subject("Matemáticas") == "matematica"


def test_normalize_subject_alias_mate():
    assert normalize_subject("mate") == "matematica"


def test_normalize_subject_no_accent():
    assert normalize_subject("matematicas") == "matematica"


def test_normalize_subject_lenguaje_full():
    assert normalize_subject("Lenguaje y Comunicación") == "lenguaje"


def test_normalize_subject_castellano():
    assert normalize_subject("castellano") == "lenguaje"


def test_normalize_subject_historia():
    assert normalize_subject("Historia") == "historia"


def test_normalize_subject_unknown_returns_none():
    assert normalize_subject("filosofía") is None


def test_normalize_subject_case_insensitive():
    assert normalize_subject("MATEMÁTICAS") == "matematica"


def test_normalize_subject_ciencias_sociales_is_historia():
    assert normalize_subject("ciencias sociales") == "historia"


def test_normalize_grade_primer_año_medio_is_1medio():
    assert normalize_grade("primer año medio") == "1medio"


# ── Tests de matching desde texto libre (prompt del docente como fallback) ──

def test_normalize_subject_from_full_sentence():
    assert normalize_subject("necesito rúbrica para matemáticas de 5to básico") == "matematica"


def test_normalize_grade_from_full_sentence():
    assert normalize_grade("necesito rúbrica para matemáticas de 5to básico") == "5basico"


def test_normalize_subject_fallback_with_noise():
    assert normalize_subject("el estudiante tiene TEA, trabaja lenguaje y comunicación") == "lenguaje"


def test_normalize_grade_from_informal_prompt():
    assert normalize_grade("genera rubrica para cuarto medio historia") == "4medio"


def test_normalize_subject_none_when_not_in_prompt():
    assert normalize_subject("necesito una rúbrica adaptada para el estudiante") is None


def test_normalize_grade_none_when_not_in_prompt():
    assert normalize_grade("el estudiante tiene dificultades de lectura") is None
