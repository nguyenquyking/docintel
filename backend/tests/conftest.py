import os

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own data dir, the offline LLM and no .env. The database is a fresh
    SQLite file — or, when DOCINTEL_TEST_DATABASE_URL is set (CI), a Postgres database
    emptied for each test, so the queue's SKIP LOCKED path is exercised too."""
    monkeypatch.setenv("DOCINTEL_NO_DOTENV", "1")
    monkeypatch.setenv("DOCINTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DOCINTEL_LLM_PROVIDER", "fake")
    monkeypatch.setenv("DOCINTEL_EMBEDDINGS_PROVIDER", "none")
    monkeypatch.setenv("DOCINTEL_RUN_WORKER_IN_API", "false")
    pg = os.environ.get("DOCINTEL_TEST_DATABASE_URL")
    if pg:
        monkeypatch.setenv("DOCINTEL_DATABASE_URL", pg)
    else:
        monkeypatch.delenv("DOCINTEL_DATABASE_URL", raising=False)
    from docintel.db import get_engine, init_db, reset_engines
    from docintel.llm.client import set_llm
    from docintel.models import Base
    from docintel.settings import get_settings

    get_settings.cache_clear()
    reset_engines()
    if pg:
        Base.metadata.drop_all(get_engine())
    init_db()
    set_llm(None)
    yield
    set_llm(None)
    reset_engines()
    get_settings.cache_clear()
