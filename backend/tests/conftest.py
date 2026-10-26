import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own data dir and SQLite file, the offline LLM, and no .env."""
    monkeypatch.setenv("DOCINTEL_NO_DOTENV", "1")
    monkeypatch.setenv("DOCINTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DOCINTEL_LLM_PROVIDER", "fake")
    monkeypatch.setenv("DOCINTEL_EMBEDDINGS_PROVIDER", "none")
    monkeypatch.setenv("DOCINTEL_RUN_WORKER_IN_API", "false")
    monkeypatch.delenv("DOCINTEL_DATABASE_URL", raising=False)
    from docintel.db import init_db, reset_engines
    from docintel.llm.client import set_llm
    from docintel.settings import get_settings

    get_settings.cache_clear()
    reset_engines()
    init_db()
    set_llm(None)
    yield
    set_llm(None)
    reset_engines()
    get_settings.cache_clear()
