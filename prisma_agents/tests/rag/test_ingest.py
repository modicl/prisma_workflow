import pytest

from rag import ingest, store
from tests.rag.fakes import FakeEmbedder

ENTRADA = {"url": "https://x/d67.pdf", "tipo_documento": "norma", "formato": "pdf", "titulo": "Decreto 67/2018", "anio": 2018, "decreto": "D67", "vigente": True}
TEXTO = "Artículo 1º.- Las evaluaciones deben diversificarse para todos los estudiantes.\n\nArtículo 2º.- La promoción se rige por este decreto."


@pytest.fixture
def con_texto(monkeypatch):
    monkeypatch.setattr(ingest, "texto_de_pdf", lambda datos: [datos.decode()])


@pytest.mark.asyncio
async def test_ingesta_nueva_luego_sin_cambios_luego_actualizada(conn, con_texto):
    emb = FakeEmbedder()
    descarga = lambda url: TEXTO.encode()
    r1 = await ingest.ingerir([ENTRADA], conn, emb, descarga, pausa=0)
    assert (r1["nuevos"], r1["fragmentos"], r1["errores"]) == (1, 2, [])
    r2 = await ingest.ingerir([ENTRADA], conn, emb, descarga, pausa=0)
    assert (r2["sin_cambios"], r2["nuevos"]) == (1, 0)
    r3 = await ingest.ingerir([ENTRADA], conn, emb, lambda u: (TEXTO + "\n\nArtículo 3º.- Nuevo.").encode(), pausa=0)
    assert (r3["actualizados"], r3["fragmentos"]) == (1, 3)
    assert await conn.fetchval("SELECT count(*) FROM fragmentos") == 3                      # sin duplicados


@pytest.mark.asyncio
async def test_forzar_reingiere_aunque_el_hash_no_cambie(conn, con_texto):
    emb, descarga = FakeEmbedder(), (lambda url: TEXTO.encode())
    await ingest.ingerir([ENTRADA], conn, emb, descarga, pausa=0)
    r = await ingest.ingerir([ENTRADA], conn, emb, descarga, pausa=0, forzar=True)
    assert r["actualizados"] == 1


@pytest.mark.asyncio
async def test_pdf_sin_texto_se_informa_y_no_se_guarda(conn, monkeypatch):
    monkeypatch.setattr(ingest, "texto_de_pdf", lambda datos: ["", "  "])
    r = await ingest.ingerir([ENTRADA], conn, FakeEmbedder(), lambda u: b"%PDF", pausa=0)
    assert r["sin_texto"] == [ENTRADA["url"]] and r["nuevos"] == 0
    assert await store.obtener_documento(conn, ENTRADA["url"]) is None


@pytest.mark.asyncio
async def test_un_error_en_un_documento_no_detiene_a_los_demas(conn, con_texto):
    malo = {**ENTRADA, "url": "https://x/malo.pdf"}

    def descarga(url):
        if "malo" in url:
            raise OSError("sin red")
        return TEXTO.encode()

    r = await ingest.ingerir([malo, ENTRADA], conn, FakeEmbedder(), descarga, pausa=0)
    assert r["nuevos"] == 1 and len(r["errores"]) == 1 and "malo.pdf" in r["errores"][0] and "OSError" in r["errores"][0]


@pytest.mark.asyncio
async def test_guarda_el_archivo_original_si_se_pide(conn, con_texto, tmp_path):
    await ingest.ingerir([ENTRADA], conn, FakeEmbedder(), lambda u: TEXTO.encode(), pausa=0, directorio_descargas=tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["x__d67.pdf"]


def test_descubrir_curriculo_extrae_cursos_y_omite_propuestas():
    ciclo = (
        '<a href="/curriculum/1o-6o-basico/matematica/3-basico">x</a>'
        '<a href="/curriculum/1o-6o-basico/matematica/3-basico">dup</a>'
        '<a href="/curriculum/1o-6o-basico/ingles-propuesta/3-basico">p</a>'
        '<a href="/curriculum/1o-6o-basico/matematica">no es curso</a>'
        '<a href="/curriculum/3o-4o-medio/ambiente-sostenibilidad/3-medio-fg">m</a>'
    )
    paginas = {"https://www.curriculumnacional.cl/curriculum/1o-6o-basico": ciclo.encode(),
               "https://www.curriculumnacional.cl/curriculum/3o-4o-medio": ciclo.encode()}
    r = ingest.descubrir_curriculo(lambda url: paginas[url], ["1o-6o-basico", "3o-4o-medio"])
    urls = [e["url"] for e in r]
    assert urls == ["https://www.curriculumnacional.cl/curriculum/1o-6o-basico/matematica/3-basico",
                    "https://www.curriculumnacional.cl/curriculum/3o-4o-medio/ambiente-sostenibilidad/3-medio-fg"]
    assert r[0]["asignatura"] == "matematica" and r[0]["curso"] == "3basico" and r[0]["tipo_documento"] == "curriculo"
    assert r[1]["modalidad"] == "fg" and r[1]["titulo"] == "Ambiente sostenibilidad 3.º medio — Currículum Nacional"
