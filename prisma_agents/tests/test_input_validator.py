import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from utils.input_validator import validate_prompt_docente


def test_empty_prompt_is_allowed():
    """Prompt vacío es válido — es opcional."""
    validate_prompt_docente("")  # no debe lanzar


def test_none_prompt_is_allowed():
    """None se trata igual que vacío."""
    validate_prompt_docente(None)  # no debe lanzar


def test_valid_prompt_passes():
    validate_prompt_docente("El alumno necesita apoyo en comprensión lectora y escritura")


def test_short_nonempty_prompt_raises():
    with pytest.raises(ValueError, match="demasiado corto"):
        validate_prompt_docente("hola")


def test_single_word_raises():
    with pytest.raises(ValueError, match="demasiado corto"):
        validate_prompt_docente("SCOOBY")


def test_three_word_garbage_raises():
    with pytest.raises(ValueError, match="demasiado corto"):
        validate_prompt_docente("SCOOBY DOOBY DOO")


def test_four_word_prompt_raises():
    with pytest.raises(ValueError, match="demasiado corto"):
        validate_prompt_docente("algo muy poco contexto")


def test_exactly_five_words_passes():
    validate_prompt_docente("uno dos tres cuatro cinco")


def test_whitespace_only_is_treated_as_empty():
    """Espacios solos → se trata como vacío, no lanza."""
    validate_prompt_docente("   ")
