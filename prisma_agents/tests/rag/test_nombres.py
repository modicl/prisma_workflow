from rag.nombres import asignatura_legible, curso_legible, modalidad_de_curso, slug_a_asignatura, slug_a_curso


def test_slug_a_curso_basico_y_medio():
    assert slug_a_curso("3-basico") == "3basico"
    assert slug_a_curso("1-medio") == "1medio"


def test_slug_a_curso_con_modalidad_de_3_y_4_medio():
    assert slug_a_curso("3-medio-fg") == "3medio"
    assert modalidad_de_curso("3-medio-fg") == "fg"
    assert modalidad_de_curso("4-medio-hc") == "hc"
    assert modalidad_de_curso("3-basico") is None


def test_slug_a_curso_desconocido():
    assert slug_a_curso("planes-de-estudio") is None


def test_slug_a_asignatura_mapea_a_las_claves_del_catalogo():
    assert slug_a_asignatura("lengua-literatura") == "lenguaje"
    assert slug_a_asignatura("lenguaje-comunicacion") == "lenguaje"
    assert slug_a_asignatura("matematica") == "matematica"
    assert slug_a_asignatura("historia-geografia-ciencias-sociales") == "historia"


def test_slug_a_asignatura_desconocida_se_conserva():
    assert slug_a_asignatura("ambiente-sostenibilidad") == "ambiente-sostenibilidad"


def test_nombres_legibles():
    assert curso_legible("3basico") == "3.º básico"
    assert curso_legible("2medio") == "2.º medio"
    assert curso_legible("") == ""
    assert asignatura_legible("ciencias_naturales") == "Ciencias Naturales"
    assert asignatura_legible("ambiente-sostenibilidad") == "Ambiente sostenibilidad"
