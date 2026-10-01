"""Los textos de retroalimentación del docente son los de siempre: este test los fija para detectar cualquier deriva."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.hitl_feedback import feedback_a1, feedback_a2


def test_feedback_a1_es_el_texto_original():
    assert feedback_a1("falta claridad") == (
        "\nRETROALIMENTACIÓN DEL DOCENTE — Debes revisar tu análisis "
        "considerando el siguiente problema señalado:\n"
        "\"falta claridad\"\n"
        "Ajusta tu respuesta para abordar específicamente este punto."
    )


def test_feedback_a2_es_el_texto_original():
    assert feedback_a2("muy largo") == (
        "\nRETROALIMENTACIÓN DEL DOCENTE — Debes revisar la adaptación "
        "considerando el siguiente problema señalado:\n"
        "\"muy largo\"\n"
        "Ajusta tu respuesta para abordar específicamente este punto."
    )
