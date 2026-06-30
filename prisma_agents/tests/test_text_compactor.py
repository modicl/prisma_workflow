import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.text_compactor import compact_text


# ---------------------------------------------------------------------------
# Whitespace — colapsar ruido sin alterar contenido
# ---------------------------------------------------------------------------

def test_collapses_runs_of_blank_lines_to_single():
    text = "Párrafo uno.\n\n\n\n\nPárrafo dos."
    assert compact_text(text) == "Párrafo uno.\n\nPárrafo dos."


def test_strips_trailing_whitespace_per_line():
    text = "Línea con espacios.   \nOtra línea.\t\t"
    assert compact_text(text) == "Línea con espacios.\nOtra línea."


def test_collapses_internal_space_runs_to_single():
    text = "Palabra1     palabra2\tpalabra3"
    assert compact_text(text) == "Palabra1 palabra2 palabra3"


def test_normalizes_crlf_to_lf():
    text = "Línea uno.\r\nLínea dos.\r\n"
    assert compact_text(text) == "Línea uno.\nLínea dos."


def test_strips_leading_and_trailing_whitespace_of_whole_text():
    text = "\n\n  Contenido real.  \n\n"
    assert compact_text(text) == "Contenido real."


# ---------------------------------------------------------------------------
# Boilerplate — quitar marcadores de página repetidos del OCR
# ---------------------------------------------------------------------------

def test_removes_pure_page_number_lines():
    text = "Contenido importante.\n12\nMás contenido."
    assert compact_text(text) == "Contenido importante.\nMás contenido."


def test_removes_pagina_n_marker_lines():
    text = "Texto legal del PACI.\nPágina 3 de 10\nSiguiente sección."
    assert compact_text(text) == "Texto legal del PACI.\nSiguiente sección."


def test_keeps_numbers_that_are_part_of_content():
    text = "El estudiante obtuvo 12 de 20 puntos."
    assert compact_text(text) == "El estudiante obtuvo 12 de 20 puntos."


# ---------------------------------------------------------------------------
# Dedup — líneas idénticas consecutivas (headers repetidos del OCR)
# ---------------------------------------------------------------------------

def test_removes_consecutive_duplicate_lines():
    text = "ESTABLECIMIENTO X\nESTABLECIMIENTO X\nDatos del alumno."
    assert compact_text(text) == "ESTABLECIMIENTO X\nDatos del alumno."


def test_keeps_non_consecutive_repeated_lines():
    text = "Objetivo\nMatemática\nObjetivo\nLenguaje"
    assert compact_text(text) == "Objetivo\nMatemática\nObjetivo\nLenguaje"


# ---------------------------------------------------------------------------
# Propiedades — fidelidad e idempotencia (crítico para compliance legal)
# ---------------------------------------------------------------------------

def test_empty_string_returns_empty():
    assert compact_text("") == ""


def test_whitespace_only_returns_empty():
    assert compact_text("   \n\n\t  \n") == ""


def test_is_idempotent():
    text = "Diagnóstico:   TEA.\n\n\n\nAdecuación   curricular.\n5\nFin."
    once = compact_text(text)
    assert compact_text(once) == once


def test_preserves_all_content_words_in_order():
    text = "  Decreto 83   establece    DUA.\n\n\nEvaluación\t diferenciada.  "
    result = compact_text(text)
    assert result.split() == ["Decreto", "83", "establece", "DUA.", "Evaluación", "diferenciada."]
