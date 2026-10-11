import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def conn():
    from rag import schema, store

    url = os.environ.get("RAG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RAG_TEST_DATABASE_URL no definido (hace falta Postgres con pgvector: docker compose up -d rag-db)")
    c = await store.conectar(url)
    await c.execute("DROP TABLE IF EXISTS fragmentos, documentos CASCADE")
    await schema.crear_esquema(c)
    yield c
    await c.close()
