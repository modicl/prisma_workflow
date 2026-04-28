import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.analizador_paci import make_analizador_paci_agent
from agents.adaptador import make_adaptador_agent
from agents.generador_rubrica import make_generador_rubrica_agent
from agents.critico import make_critico_agent


def test_analizador_paci_excludes_history():
    agent = make_analizador_paci_agent()
    assert agent.include_contents == "none", (
        "AnalizadorPACI debe tener include_contents='none' para no acumular historial en retries HITL"
    )


def test_adaptador_excludes_history():
    agent = make_adaptador_agent()
    assert agent.include_contents == "none"


def test_generador_rubrica_excludes_history():
    agent = make_generador_rubrica_agent()
    assert agent.include_contents == "none"


def test_critico_excludes_history():
    agent = make_critico_agent()
    assert agent.include_contents == "none"
