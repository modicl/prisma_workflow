import pytest

from rag.chunking import fragmentar_curriculo, fragmentar_norma, fragmentar_orientacion, limpiar_encabezados

NORMA = """Decreto 67, EDUCACIÓN (2018) Biblioteca del Congreso Nacional de Chile - www.leychile.cl - documento generado el 24-Ago-2020 página 1 de 9
VISTOS: lo dispuesto en la Constitución y en la ley, considerando la necesidad de normas mínimas nacionales sobre evaluación, calificación y promoción escolar para todos los estudiantes del país, sin distinción alguna entre los establecimientos que reciben aporte del Estado.

Artículo 1º.- El presente decreto establece normas mínimas.

Artículo 2°.- Para los efectos de este decreto se entenderá por reglamento el instrumento interno.

Artículo 5º bis.- Los establecimientos deberán diversificar la evaluación.
"""


def test_norma_un_fragmento_por_articulo_con_ruta_y_metadatos():
    fr = fragmentar_norma(NORMA, "D67")
    rutas = [f.ruta for f in fr]
    assert rutas == ["Considerandos", "Art. 1", "Art. 2", "Art. 5 bis"]
    assert fr[1].metadata == {"decreto": "D67", "articulo": "1"}
    assert "normas mínimas" in fr[1].texto
    assert fr[3].metadata["articulo"] == "5 bis"


def test_norma_descarta_los_encabezados_repetidos_de_leychile():
    assert "leychile" not in " ".join(f.texto for f in fragmentar_norma(NORMA, "D67"))
    assert "Biblioteca" not in limpiar_encabezados("a\nBiblioteca del Congreso Nacional x\nb")


def test_norma_articulo_largo_se_parte_sin_perder_contenido():
    cuerpo = "\n\n".join(f"Párrafo {i} " + "palabra " * 60 for i in range(20))
    fr = fragmentar_norma(f"Artículo 1º.- Inicio.\n\n{cuerpo}", "D83", max_chars=1000)
    assert len(fr) > 1 and fr[0].ruta == "Art. 1 (parte 1)" and fr[1].ruta == "Art. 1 (parte 2)"
    assert all(len(f.texto) <= 1000 for f in fr)
    assert "Párrafo 19" in fr[-1].texto and all(f.metadata["articulo"] == "1" for f in fr)


def test_norma_sin_articulos_se_conserva_como_texto_completo():
    fr = fragmentar_norma("Texto corrido sin articulado. " * 5, "OTRO")
    assert len(fr) == 1 and fr[0].ruta == "Texto completo" and fr[0].metadata == {"decreto": "OTRO"}


def test_orientacion_agrupa_parrafos_con_pagina_y_solape():
    paginas = ["\n\n".join(f"Pagina{n} parrafo{k} " + "x" * 400 for k in range(3)) for n in (1, 2, 3)]
    fr = fragmentar_orientacion(paginas, max_chars=1000, solape=100)
    assert len(fr) >= 3
    assert fr[0].ruta.startswith("p. 1") or fr[0].ruta.startswith("pp. 1")
    assert fr[1].texto.startswith(fr[0].texto[-100:].lstrip())
    assert all(len(f.texto) <= 1000 + 101 for f in fr)
    assert "Pagina3 parrafo2" in fr[-1].texto


def test_orientacion_vacia_o_en_blanco_no_genera_fragmentos():
    assert fragmentar_orientacion(["", "   \n\n  "]) == []


HTML = """
<div class="items-wrapper"><h3 id="eje-1" class="link"><a href="#">Números y operaciones</a></h3>
<div class="item-wrappers">
<div class="item-wrapper"><h4 class="wrapper-title-oa"><span class="oa-title">Objetivo de aprendizaje MA03 OA 01</span><span class="number-title">   MA03 OA 01</span></h4>
<div class="field field--name-description"><div class="tex2jax_process"><p>Contar números del 0 al 1 000 de 5 en 5.</p></div></div></div>
<div class="item-wrapper prioritized"><h4 class="wrapper-title-oa prioritized"><span class="oa-title">Objetivo de aprendizaje MA03 OA 02</span><span class="number-title">   MA03 OA 02</span></h4>
<div class="field field--name-description"><p>Leer números hasta 1000.</p></div></div>
</div>
<h3 id="eje-2" class="link"><a href="#">Geometría</a></h3>
<div class="item-wrappers">
<div class="item-wrapper"><h4 class="wrapper-title-oa"><span class="oa-title">Objetivo de aprendizaje MA03 OA 15</span></h4>
<div class="field field--name-description"><p>Describir figuras 3D.</p></div></div>
<div class="item-wrapper"><h4><span class="oa-title">Habilidad no es un OA</span></h4><div class="field--name-description"><p>ignorar</p></div></div>
</div></div>
"""


def test_curriculo_un_fragmento_por_oa_con_eje_y_metadatos():
    pytest.importorskip("bs4")
    fr = fragmentar_curriculo(HTML, "matematica", "3basico")
    assert [f.ruta for f in fr] == [
        "3.º básico · Matemática · OA 01", "3.º básico · Matemática · OA 02", "3.º básico · Matemática · OA 15",
    ]
    assert fr[0].metadata == {"asignatura": "matematica", "curso": "3basico", "eje": "Números y operaciones", "oa": "MA03 OA 01", "priorizado": False}
    assert fr[1].metadata["priorizado"] is True
    assert fr[2].metadata["eje"] == "Geometría"
    assert "Contar números del 0 al 1 000" in fr[0].texto and "Números y operaciones" in fr[0].texto


def test_curriculo_con_modalidad_la_guarda_en_los_metadatos():
    pytest.importorskip("bs4")
    fr = fragmentar_curriculo(HTML, "ambiente-sostenibilidad", "3medio", modalidad="fg")
    assert fr[0].metadata["modalidad"] == "fg"
    assert fr[0].ruta.startswith("3.º medio · Ambiente sostenibilidad")


def test_curriculo_sin_oa_devuelve_lista_vacia():
    pytest.importorskip("bs4")
    assert fragmentar_curriculo("<html><body>nada</body></html>", "matematica", "3basico") == []
