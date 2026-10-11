"""Nombres canónicos de curso y asignatura, compartidos por la ingesta y la consulta.

Las claves son las mismas de `utils/curriculum_catalog.py` ("3basico", "matematica"), para que lo que extrae el
workflow del PACI coincida con lo que se guardó al ingerir el currículo.
"""
import re

_ASIGNATURA_POR_SLUG = {
    "matematica": "matematica",
    "lengua-literatura": "lenguaje",
    "lenguaje-comunicacion": "lenguaje",
    "ciencias-naturales": "ciencias_naturales",
    "historia-geografia-ciencias-sociales": "historia",
    "educacion-fisica-salud": "educacion_fisica",
    "ingles": "ingles",
    "artes-visuales": "artes",
    "tecnologia": "tecnologia",
}

_NOMBRE_ASIGNATURA = {
    "matematica": "Matemática",
    "lenguaje": "Lenguaje y Comunicación",
    "ciencias_naturales": "Ciencias Naturales",
    "historia": "Historia, Geografía y Ciencias Sociales",
    "educacion_fisica": "Educación Física y Salud",
    "ingles": "Inglés",
    "artes": "Artes Visuales",
    "tecnologia": "Tecnología",
}

_CURSO = re.compile(r"^(\d)-(basico|medio)(?:-([a-z]+))?$")


def slug_a_curso(slug: str) -> str | None:
    m = _CURSO.match(slug or "")
    return f"{m.group(1)}{m.group(2)}" if m else None


def modalidad_de_curso(slug: str) -> str | None:
    m = _CURSO.match(slug or "")
    return m.group(3) if m else None


def slug_a_asignatura(slug: str) -> str:
    return _ASIGNATURA_POR_SLUG.get(slug, slug)


def curso_legible(curso: str) -> str:
    m = re.match(r"^(\d)(basico|medio)$", curso or "")
    if not m:
        return curso or ""
    return f"{m.group(1)}.º {'básico' if m.group(2) == 'basico' else 'medio'}"


def asignatura_legible(clave: str) -> str:
    if clave in _NOMBRE_ASIGNATURA:
        return _NOMBRE_ASIGNATURA[clave]
    texto = (clave or "").replace("_", " ").replace("-", " ").strip()
    return texto[:1].upper() + texto[1:]
