from rag.query import construir_consulta, detectar_nee


def test_detecta_etiquetas_de_nee_de_una_lista_cerrada():
    assert detectar_nee("Trastorno del Espectro Autista (TEA) grado 1") == ["TEA"]
    assert detectar_nee("TDAH y dislexia") == ["TDAH", "DEA"]
    assert detectar_nee("Discapacidad intelectual leve") == ["discapacidad intelectual"]
    assert detectar_nee("") == [] and detectar_nee("sin diagnóstico relevante") == []


def test_la_consulta_nunca_arrastra_texto_libre_del_diagnostico():
    nee = detectar_nee("María Pérez, RUT 12.345.678-9, TEA")
    consulta = construir_consulta("adaptador", "matematica", "3basico", nee)
    assert "María" not in consulta and "12.345" not in consulta
    assert "TEA" in consulta and "matemática" in consulta.lower() and "3.º básico" in consulta


def test_consulta_sin_asignatura_ni_curso_ni_nee_sigue_siendo_valida():
    c = construir_consulta("evaluacion", "", "", [])
    assert "evaluación" in c and c.strip()
    assert "None" not in c


def test_la_consulta_de_evaluacion_usa_el_lenguaje_del_art_5_del_d67():
    """Con la frase anterior el Art. 5 no entraba en los 3 primeros del D67; estas palabras son las de su texto."""
    c = construir_consulta("evaluacion", "matematica", "3basico", ["TEA"])
    assert "diversificaciones pertinentes" in c and "necesidades educativas especiales" in c
