"""
curriculum_catalog.py — Diccionario de normalización de ramos y cursos.

Mapea texto libre extraído del PACI a los valores canónicos usados en S3:
  - Curso: "1basico" … "8basico", "1medio" … "4medio"
  - Ramo:  "matematica", "lenguaje", "ciencias_naturales", etc.
"""

import unicodedata

GRADE_ALIASES: dict[str, list[str]] = {
    "1basico": ["1° básico", "primero básico", "1ro básico", "primer año básico", "1º básico", "1ero básico", "primer año"],
    "2basico": ["2° básico", "segundo básico", "2do básico", "2º básico", "segundo año básico"],
    "3basico": ["3° básico", "tercero básico", "3ro básico", "3º básico", "tercer año básico"],
    "4basico": ["4° básico", "cuarto básico", "4to básico", "4º básico", "cuarto año básico"],
    "5basico": ["5° básico", "quinto básico", "5to básico", "5º básico", "quinto año básico"],
    "6basico": ["6° básico", "sexto básico", "6to básico", "6º básico", "sexto año básico"],
    "7basico": ["7° básico", "séptimo básico", "7mo básico", "7º básico", "séptimo año básico"],
    "8basico": ["8° básico", "octavo básico", "8vo básico", "8º básico", "octavo año básico"],
    "1medio":  ["1° medio", "primero medio", "1ro medio", "1º medio", "primer año medio"],
    "2medio":  ["2° medio", "segundo medio", "2do medio", "2º medio", "segundo año medio"],
    "3medio":  ["3° medio", "tercero medio", "3ro medio", "3º medio", "tercer año medio"],
    "4medio":  ["4° medio", "cuarto medio", "4to medio", "4º medio", "cuarto año medio"],
}

SUBJECT_ALIASES: dict[str, list[str]] = {
    "matematica":         ["matemáticas", "matemática", "mate", "math", "matematicas", "matematica"],
    "lenguaje":           ["lenguaje y comunicación", "lenguaje y comunicacion", "lenguaje", "castellano", "lengua y literatura"],
    "ciencias_naturales": ["ciencias naturales", "ciencias", "biología", "biologia", "cc. naturales"],
    "historia":           ["historia, geografía y cs. sociales", "historia, geografia y cs. sociales", "historia", "cs. sociales", "ciencias sociales", "historia y geografía", "historia y geografia"],
    "educacion_fisica":   ["educación física", "educacion fisica", "ed. física", "ed. fisica", "deportes", "ef"],
    "ingles":             ["inglés", "ingles", "english"],
    "artes":              ["artes visuales", "artes", "música", "musica", "arte"],
    "tecnologia":         ["tecnología", "tecnologia", "informática", "informatica"],
}


def _strip_accents(text: str) -> str:
    """Elimina tildes y convierte a minúsculas para comparación robusta."""
    nfkd = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_grade(text: str) -> str | None:
    """Retorna el código canónico de curso ('5basico', '1medio', etc.) o None si no hay match."""
    if not text:
        return None
    normalized = _strip_accents(text)
    for canonical, aliases in GRADE_ALIASES.items():
        for alias in aliases:
            if _strip_accents(alias) in normalized or normalized in _strip_accents(alias):
                return canonical
    return None


def normalize_subject(text: str) -> str | None:
    """Retorna el código canónico de ramo ('matematica', 'lenguaje', etc.) o None si no hay match."""
    if not text:
        return None
    normalized = _strip_accents(text)
    for canonical, aliases in SUBJECT_ALIASES.items():
        for alias in aliases:
            if _strip_accents(alias) in normalized or normalized in _strip_accents(alias):
                return canonical
    return None
