from types import SimpleNamespace

import pytest

from rag.embedder import DIMENSIONES, GeminiEmbedder, a_literal


class ClienteFalso:
    """Imita client.models.embed_content: devuelve un vector de DIMENSIONES por texto."""

    def __init__(self, dims=DIMENSIONES, de_menos=0):
        self.llamadas = []
        self.dims = dims
        self.de_menos = de_menos
        self.models = SimpleNamespace(embed_content=self._embed)

    def _embed(self, model, contents, config):
        self.llamadas.append({"model": model, "n": len(contents), "tarea": config.task_type, "dims": config.output_dimensionality})
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5] * self.dims) for _ in contents[: len(contents) - self.de_menos]])


def test_a_literal_formato_pgvector():
    assert a_literal([1, 0.5, -2]) == "[1,0.5,-2]"


@pytest.mark.asyncio
async def test_documentos_se_parten_en_lotes_y_usan_la_tarea_de_documento():
    cliente = ClienteFalso()
    emb = GeminiEmbedder(client=cliente, lote=2)
    vectores = await emb.embeber_documentos(["a", "b", "c", "d", "e"])
    assert len(vectores) == 5 and all(len(v) == DIMENSIONES for v in vectores)
    assert [c["n"] for c in cliente.llamadas] == [2, 2, 1]
    assert {c["tarea"] for c in cliente.llamadas} == {"RETRIEVAL_DOCUMENT"}
    assert cliente.llamadas[0]["dims"] == DIMENSIONES and cliente.llamadas[0]["model"] == "gemini-embedding-001"


@pytest.mark.asyncio
async def test_consulta_usa_la_tarea_de_consulta():
    cliente = ClienteFalso()
    vector = await GeminiEmbedder(client=cliente).embeber_consulta("hola")
    assert len(vector) == DIMENSIONES and cliente.llamadas[0]["tarea"] == "RETRIEVAL_QUERY"


@pytest.mark.asyncio
async def test_si_la_api_devuelve_menos_vectores_falla_con_error_claro():
    with pytest.raises(ValueError, match="vectores"):
        await GeminiEmbedder(client=ClienteFalso(de_menos=1)).embeber_documentos(["a", "b"])


@pytest.mark.asyncio
async def test_si_la_dimension_no_coincide_falla():
    with pytest.raises(ValueError, match="dimensión"):
        await GeminiEmbedder(client=ClienteFalso(dims=3)).embeber_consulta("a")
