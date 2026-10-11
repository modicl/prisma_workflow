"""Embeddings detrás de una interfaz propia: el índice depende del modelo de embeddings, no del LLM que genera.

Cada fila de `fragmentos` guarda `embedding_model` y `embedding_version`; si algún día se cambia de modelo, se reindexa
con `python -m rag.ingest --forzar`.
"""
import asyncio
import os
from typing import Protocol

DIMENSIONES = 768


class Embedder(Protocol):
    nombre: str
    version: str
    dimensiones: int

    async def embeber_documentos(self, textos: list[str]) -> list[list[float]]: ...

    async def embeber_consulta(self, texto: str) -> list[float]: ...


def a_literal(vector) -> str:
    """Formato de texto que pgvector acepta con `$1::vector` (así no hace falta el paquete `pgvector`)."""
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


class GeminiEmbedder:
    nombre = "gemini-embedding-001"
    version = "768-v1"
    dimensiones = DIMENSIONES

    def __init__(self, client=None, lote: int = 50, timeout_ms: int = 120_000):
        self._client = client
        self.lote = lote
        self.timeout_ms = timeout_ms

    def _cliente(self):
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=os.environ.get("GOOGLE_API_KEY"), http_options=types.HttpOptions(timeout=self.timeout_ms)
            )
        return self._client

    def _embeber(self, textos: list[str], tarea: str) -> list[list[float]]:
        from google.genai import types

        respuesta = self._cliente().models.embed_content(
            model=self.nombre,
            contents=textos,
            config=types.EmbedContentConfig(task_type=tarea, output_dimensionality=self.dimensiones),
        )
        vectores = [list(e.values) for e in respuesta.embeddings]
        if len(vectores) != len(textos):
            raise ValueError(f"El servicio devolvió {len(vectores)} vectores para {len(textos)} textos")
        for v in vectores:
            if len(v) != self.dimensiones:
                raise ValueError(f"Vector con dimensión {len(v)}; se esperaba {self.dimensiones}")
        return vectores

    async def embeber_documentos(self, textos: list[str]) -> list[list[float]]:
        salida: list[list[float]] = []
        for i in range(0, len(textos), self.lote):
            salida += await asyncio.to_thread(self._embeber, textos[i : i + self.lote], "RETRIEVAL_DOCUMENT")
        return salida

    async def embeber_consulta(self, texto: str) -> list[float]:
        return (await asyncio.to_thread(self._embeber, [texto], "RETRIEVAL_QUERY"))[0]
