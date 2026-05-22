"""
nee_taxonomy.py — Taxonomía canónica de diagnósticos NEE bajo Decreto 170/2010.

Define los IDs canónicos que el sistema usa para clasificar diagnósticos.
El AgentAnalizadorPACI recibe esta taxonomía como referencia y debe devolver
exactamente uno de estos IDs en el bloque ---METADATOS---.
"""

# ID canónico → lista de expresiones equivalentes (para validación y fallback)
NEE_TAXONOMY: dict[str, list[str]] = {
    # ── Permanentes ──────────────────────────────────────────────────────────
    "TEA": [
        "trastorno del espectro autista", "tea", "autismo", "autista",
        "espectro autista", "trastorno generalizado del desarrollo",
    ],
    "DI": [
        "discapacidad intelectual", "di", "deficiencia intelectual",
        "retraso mental", "discapacidad cognitiva",
    ],
    "DV": [
        "discapacidad visual", "dv", "baja visión", "baja vision",
        "ceguera", "déficit visual", "deficit visual",
    ],
    "DA": [
        "discapacidad auditiva", "da", "hipoacusia", "sordera",
        "déficit auditivo", "deficit auditivo", "hipoacusis",
    ],
    "DM": [
        "discapacidad motora", "dm", "déficit motor", "deficit motor",
        "discapacidad física", "discapacidad fisica", "discapacidad motriz",
    ],
    "Disfasia": [
        "disfasia", "trastorno severo del lenguaje",
        "trastorno grave del lenguaje",
    ],
    "Sordoceguera": [
        "sordoceguera", "discapacidad múltiple", "discapacidad multiple",
        "sordoceguera", "sordo-ceguera",
    ],
    # ── Transitorias ─────────────────────────────────────────────────────────
    "TDAH": [
        "trastorno de déficit atencional con hiperactividad",
        "trastorno por déficit de atención con hiperactividad",
        "tdah", "tda/tdah", "tda", "déficit atencional con hiperactividad",
        "deficit atencional con hiperactividad", "déficit atencional",
        "deficit atencional", "hiperactividad",
    ],
    "TEL": [
        "trastorno específico del lenguaje", "tel",
        "trastorno especifico del lenguaje",
        "retraso del lenguaje", "trastorno del lenguaje",
    ],
    "DEA": [
        "dificultad específica del aprendizaje", "dea",
        "dificultades específicas del aprendizaje",
        "dificultad especifica del aprendizaje",
        "dislexia", "discalculia", "disgrafía", "disgrafia",
        "trastorno de lectura", "trastorno del aprendizaje",
    ],
    "CIL": [
        "coeficiente intelectual limítrofe", "cil",
        "funcionamiento intelectual limítrofe", "fil",
        "ci limítrofe", "ci limitrofe", "inteligencia limítrofe",
        "inteligencia limitrofe",
    ],
}

# Tabla compacta para incluir en system prompts (texto plano)
NEE_TAXONOMY_PROMPT_TABLE = """\
IDs canónicos de diagnóstico NEE (Decreto 170/2010) — usa EXACTAMENTE uno de estos:

PERMANENTES:
  TEA        → Trastorno del Espectro Autista
  DI         → Discapacidad Intelectual
  DV         → Discapacidad Visual (baja visión / ceguera)
  DA         → Discapacidad Auditiva (hipoacusia / sordera)
  DM         → Discapacidad Motora
  Disfasia   → Trastorno Severo del Lenguaje
  Sordoceguera → Sordoceguera / Discapacidad Múltiple

TRANSITORIAS:
  TDAH       → Trastorno de Déficit Atencional con/sin Hiperactividad (TDA/TDAH)
  TEL        → Trastorno Específico del Lenguaje
  DEA        → Dificultad Específica del Aprendizaje (dislexia, discalculia, disgrafía)
  CIL        → Coeficiente Intelectual Limítrofe (FIL)"""


def normalize_diagnostico(raw: str) -> str:
    """Mapea cualquier expresión de diagnóstico al ID canónico.

    Si el valor ya es un ID canónico válido, lo retorna directamente.
    Si no reconoce la expresión, retorna 'otro'.
    """
    if not raw:
        return "otro"
    normalized = raw.strip().lower()
    # Coincidencia exacta con un ID canónico (case-insensitive)
    for canonical_id in NEE_TAXONOMY:
        if normalized == canonical_id.lower():
            return canonical_id
    # Coincidencia con expresiones equivalentes
    for canonical_id, expressions in NEE_TAXONOMY.items():
        for expr in expressions:
            if expr in normalized or normalized in expr:
                return canonical_id
    return "otro"
