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


def test_any_nonempty_prompt_passes():
    """Cualquier texto es válido — el prompt es libre y opcional."""
    validate_prompt_docente("hola")
    validate_prompt_docente("SCOOBY")
    validate_prompt_docente("SCOOBY DOOBY DOO")
    validate_prompt_docente("algo muy poco contexto")
    validate_prompt_docente("uno dos tres cuatro cinco")
    validate_prompt_docente("El alumno necesita apoyo en comprensión lectora y escritura")


def test_whitespace_only_is_treated_as_empty():
    """Espacios solos → se trata como vacío, no lanza."""
    validate_prompt_docente("   ")
