import pytest

from rag.evaluar import recall_en_k

ITEMS = [
    {"pregunta": "q1", "doc": "83", "esperado": "diseño universal"},
    {"pregunta": "q2", "doc": "67", "esperado": "promoci[óo]n"},
]


def fila(titulo, texto):
    return {"titulo": titulo, "texto": texto, "ruta": "x"}


@pytest.mark.asyncio
async def test_recall_cuenta_aciertos_por_documento_y_patron():
    async def buscar(pregunta, k):
        return {"q1": [fila("Decreto 83/2015", "El Diseño Universal de aprendizaje...")],
                "q2": [fila("Decreto 67/2018", "nada relevante")]}[pregunta]

    r = await recall_en_k(ITEMS, buscar, k=5)
    assert (r["total"], r["aciertos"], r["recall"]) == (2, 1, 0.5)
    assert r["fallos"] == ["q2"]


@pytest.mark.asyncio
async def test_el_documento_correcto_con_texto_equivocado_no_cuenta():
    async def buscar(pregunta, k):
        return [fila("Decreto 83/2015", "otra cosa")]

    r = await recall_en_k(ITEMS[:1], buscar)
    assert r["aciertos"] == 0
