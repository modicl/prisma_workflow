import pytest

from rag import schema, store

DOC = {"fuente_url": "https://x/d83.pdf", "tipo_documento": "norma", "titulo": "Decreto 83/2015", "sha256": "aaa", "anio": 2015, "vigente": True}


def vec(i):
    v = [0.0] * 768
    v[i] = 1.0
    return v


def frag(orden, ruta, texto, i, **meta):
    return {"orden": orden, "ruta": ruta, "texto": texto, "metadata": meta, "embedding": vec(i), "embedding_model": "fake", "embedding_version": "1"}


@pytest.mark.asyncio
async def test_crear_esquema_es_idempotente(conn):
    await schema.crear_esquema(conn)
    await schema.crear_esquema(conn)
    assert await conn.fetchval("SELECT count(*) FROM documentos") == 0


@pytest.mark.asyncio
async def test_reemplazar_documento_inserta_y_luego_reemplaza_sin_duplicar(conn):
    await store.reemplazar_documento(conn, DOC, [frag(0, "Art. 1", "uno", 0), frag(1, "Art. 2", "dos", 1)])
    await store.reemplazar_documento(conn, {**DOC, "sha256": "bbb"}, [frag(0, "Art. 1", "uno nuevo", 0)])
    assert await conn.fetchval("SELECT count(*) FROM documentos") == 1
    assert await conn.fetchval("SELECT count(*) FROM fragmentos") == 1
    previo = await store.obtener_documento(conn, DOC["fuente_url"])
    assert previo["sha256"] == "bbb"
    assert await store.obtener_documento(conn, "https://otra") is None


@pytest.mark.asyncio
async def test_buscar_vector_ordena_por_cercania(conn):
    await store.reemplazar_documento(conn, DOC, [frag(0, "Art. 1", "uno", 0), frag(1, "Art. 2", "dos", 1)])
    r = await store.buscar_vector(conn, vec(1), ["norma"], None, 5)
    assert [x["ruta"] for x in r] == ["Art. 2", "Art. 1"]
    assert r[0]["titulo"] == "Decreto 83/2015" and r[0]["anio"] == 2015


@pytest.mark.asyncio
async def test_buscar_texto_usa_raices_del_espanol(conn):
    await store.reemplazar_documento(conn, DOC, [frag(0, "Art. 1", "La evaluación diferenciada es un derecho", 0), frag(1, "Art. 2", "Otro tema", 1)])
    r = await store.buscar_texto(conn, ["evaluaciones", "diferenciadas"], ["norma"], None, 5)
    assert [x["ruta"] for x in r] == ["Art. 1"]
    assert await store.buscar_texto(conn, [], ["norma"], None, 5) == []


@pytest.mark.asyncio
async def test_los_documentos_no_vigentes_y_otros_tipos_quedan_fuera(conn):
    await store.reemplazar_documento(conn, DOC, [frag(0, "Art. 1", "alfa", 0)])
    await store.reemplazar_documento(conn, {**DOC, "fuente_url": "https://x/viejo.pdf", "vigente": False}, [frag(0, "Art. 9", "alfa", 0)])
    await store.reemplazar_documento(conn, {**DOC, "fuente_url": "https://x/o.pdf", "tipo_documento": "orientacion"}, [frag(0, "p. 1", "alfa", 0)])
    r = await store.buscar_vector(conn, vec(0), ["norma"], None, 10)
    assert [x["ruta"] for x in r] == ["Art. 1"]
    t = await store.buscar_texto(conn, ["alfa"], ["norma", "orientacion"], None, 10)
    assert sorted(x["ruta"] for x in t) == ["Art. 1", "p. 1"]


@pytest.mark.asyncio
async def test_filtro_de_metadatos(conn):
    curr = {**DOC, "fuente_url": "https://x/m3.html", "tipo_documento": "curriculo", "titulo": "Matemática 3.º básico"}
    await store.reemplazar_documento(conn, curr, [
        frag(0, "OA 01", "contar numeros", 0, asignatura="matematica", curso="3basico"),
        frag(1, "OA 01", "contar numeros", 1, asignatura="matematica", curso="4basico"),
    ])
    filtro = {"asignatura": "matematica", "curso": "3basico"}
    v = await store.buscar_vector(conn, vec(0), ["curriculo"], filtro, 5)
    t = await store.buscar_texto(conn, ["contar"], ["curriculo"], filtro, 5)
    assert len(v) == 1 and len(t) == 1 and v[0]["metadata"]["curso"] == "3basico"
