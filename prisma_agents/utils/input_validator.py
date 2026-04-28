_MIN_WORDS = 5


def validate_prompt_docente(prompt: str | None) -> None:
    """Valida el prompt opcional del docente antes de iniciar el workflow.

    Regla: si se provee un valor (no vacío ni solo espacios), debe tener
    al menos _MIN_WORDS palabras. Prompts de 1–4 palabras son señal de
    entrada de prueba o accidental — se rechaza antes de consumir tokens.
    """
    if not prompt or not prompt.strip():
        return
    word_count = len(prompt.split())
    if word_count < _MIN_WORDS:
        raise ValueError(
            f"El prompt del docente es demasiado corto ({word_count} palabra(s)). "
            f"Ingresa al menos {_MIN_WORDS} palabras describiendo el contexto o "
            f"necesidades del estudiante, o déjalo vacío."
        )
