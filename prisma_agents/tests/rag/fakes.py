"""Dobles de prueba del RAG: un Embedder determinista que no llama a ninguna API."""
import hashlib
import math
import re


class FakeEmbedder:
    """Bolsa de palabras hasheada a 768 dimensiones: textos con palabras en común quedan cerca."""

    nombre = "fake"
    version = "1"
    dimensiones = 768

    def _vec(self, texto: str) -> list[float]:
        v = [0.0] * self.dimensiones
        for palabra in set(re.findall(r"\w+", texto.lower())):
            v[int(hashlib.md5(palabra.encode()).hexdigest(), 16) % self.dimensiones] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    async def embeber_documentos(self, textos: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in textos]

    async def embeber_consulta(self, texto: str) -> list[float]:
        return self._vec(texto)
