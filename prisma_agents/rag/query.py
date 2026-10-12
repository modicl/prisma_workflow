"""Construcción de la consulta al buscador SIN datos identificables.

Solo viajan al servicio de embeddings: asignatura, curso y etiquetas de NEE de una lista cerrada. Nunca nombre, RUT ni
texto libre del PACI (el diagnóstico se reduce a etiquetas).
"""
import re
import unicodedata

from rag.nombres import asignatura_legible, curso_legible

# etiqueta -> expresiones (sin tildes, en minúsculas) que la activan
_NEE = {
    "TEA": ("tea", "espectro autista", "autismo", "autista"),
    "TDAH": ("tdah", "tda", "deficit atencional", "hiperactividad"),
    "DEA": ("dea", "dislexia", "discalculia", "dificultad especifica del aprendizaje", "dificultades especificas del aprendizaje"),
    "TEL": ("tel", "trastorno especifico del lenguaje", "disfasia"),
    "discapacidad intelectual": ("discapacidad intelectual", "retardo mental"),
    "funcionamiento intelectual limítrofe": ("limitrofe", "fil"),
    "discapacidad visual": ("discapacidad visual", "ceguera", "baja vision"),
    "discapacidad auditiva": ("discapacidad auditiva", "sordera", "hipoacusia"),
    "discapacidad motora": ("discapacidad motora", "discapacidad motriz"),
}

_FRASE = {
    "adaptador": "adecuaciones curriculares diversificación de la enseñanza diseño universal de aprendizaje PACI",
    "evaluacion": "diversificaciones pertinentes de la evaluación y adecuaciones curriculares para estudiantes con necesidades educativas especiales, calificación y promoción",
    "curriculo": "objetivos de aprendizaje",
}


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))


def detectar_nee(diagnostico: str) -> list[str]:
    texto = _sin_tildes(diagnostico or "")
    return [
        etiqueta for etiqueta, claves in _NEE.items()
        if any(re.search(rf"\b{re.escape(c)}\b", texto) for c in claves)
    ]


def construir_consulta(punto: str, asignatura: str, curso: str, nee: list[str]) -> str:
    partes = [_FRASE[punto]]
    if nee:
        partes.append(" ".join(nee))
    if asignatura:
        partes.append(asignatura_legible(asignatura))
    if curso:
        partes.append(curso_legible(curso))
    return " ".join(partes)
