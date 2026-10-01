"""Textos de retroalimentación que el docente le da al Agente 1 (análisis) o al Agente 2 (adaptación) al rechazar.

Se extrajeron de agent.py SIN cambiar una coma: los usan tanto el bucle HITL en memoria (modo completo) como las fases
de Step Functions (workflow_runner). Fuente única para que ambos caminos no se desvíen.
"""


def feedback_a1(razon: str) -> str:
    return (
        f"\nRETROALIMENTACIÓN DEL DOCENTE — Debes revisar tu análisis "
        f"considerando el siguiente problema señalado:\n"
        f"\"{razon}\"\n"
        f"Ajusta tu respuesta para abordar específicamente este punto."
    )


def feedback_a2(razon: str) -> str:
    return (
        f"\nRETROALIMENTACIÓN DEL DOCENTE — Debes revisar la adaptación "
        f"considerando el siguiente problema señalado:\n"
        f"\"{razon}\"\n"
        f"Ajusta tu respuesta para abordar específicamente este punto."
    )
