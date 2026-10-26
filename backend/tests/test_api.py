import asyncio

from fastapi.testclient import TestClient

from docintel.llm.client import LLMClient, set_llm
from docintel.settings import get_settings
from docintel.synth.generator import build_document, find_spec, render_pdf

from .oracle import oracle


def _client():
    from docintel.api.app import create_app
    return TestClient(create_app())


def _process_all():
    """Run queued jobs the way the worker does, in-process."""
    from docintel.queue.jobs import claim, complete
    from docintel.queue.worker import Worker
    worker = Worker(get_settings(), concurrency=1)
    while (job := claim("test-worker")) is not None:
        asyncio.run(worker.execute(job))
        complete(job.id, "test-worker")


def test_upload_process_review_dashboard_search():
    spec = find_spec("en", "services_agreement", require=lambda s: s.auto_renewal and "draft" not in s.tags)
    pdf = render_pdf(build_document(spec))
    set_llm(LLMClient(get_settings(), responder=oracle(spec)))
    with _client() as client:
        r = client.post("/api/documents", files=[("files", ("msa.pdf", pdf, "application/pdf"))],
                        data={"lane": "interactive"})
        assert r.status_code == 200
        doc_id = r.json()["documents"][0]["id"]
        bad = client.post("/api/documents", files=[("files", ("x.txt", b"not a pdf", "text/plain"))])
        assert bad.json()["documents"][0]["error"]

        _process_all()
        d = client.get(f"/api/documents/{doc_id}").json()
        assert d["document"]["status"] in ("ready", "needs_review")
        assert d["facts"]["parties"] == [spec.party_a, spec.party_b]
        assert d["pages"] and d["pages"][0]["route"] == "text_layer" and d["extraction"]["mode"] == "full_document"
        renewal = next(f for f in d["fields"] if f["field"] == "auto_renewal")
        assert renewal["evidence"][0]["boxes"], "evidence must be highlightable"
        assert client.get(f"/api/documents/{doc_id}/file").content.startswith(b"%PDF")

        # a reviewer corrects the notice period: derived facts follow
        r = client.post(f"/api/documents/{doc_id}/review",
                        json={"field": "non_renewal_notice", "action": "correct", "value": {"amount": 3, "unit": "months"}})
        assert r.status_code == 200 and r.json()["facts"]["non_renewal_notice_days"] == 90
        from docintel.extraction.schema import REVIEW_FIELDS
        listed = [f["field"] for f in client.get(f"/api/documents/{doc_id}").json()["fields"]]
        assert listed == [f for f in REVIEW_FIELDS if f in listed] and "document_type" in listed

        dash = client.get("/api/dashboard").json()
        assert dash["kpis"]["documents"] == 1 and dash["kpis"]["processed"] == 1
        hits = client.post("/api/search", json={"query": "renew notice", "filters": {}}).json()["results"]
        assert hits and hits[0]["document_id"] == doc_id
        none = client.post("/api/search", json={"query": "renew", "filters": {"doc_type": "lease"}}).json()["results"]
        assert none == []

        for path in ("/api/health", "/api/settings", "/api/jobs", "/api/monitoring/overview", "/api/monitoring/drift",
                     "/api/monitoring/canary", "/api/cost/measured", "/api/cost/pareto", "/api/review",
                     "/api/eval/sets", "/api/eval/runs"):
            assert client.get(path).status_code == 200, path
        proj = client.post("/api/cost/project", json={"n_docs": 1000}).json()
        assert proj["total_usd"] > 0
        mon = client.get("/api/monitoring/overview").json()
        assert mon["extraction"]["count"] == 1 and any(f["field"] == "non_renewal_notice" for f in mon["fields"])


def test_settings_endpoint_hides_secrets(monkeypatch):
    monkeypatch.setenv("DOCINTEL_LLM_API_KEY", "super-secret-value")
    monkeypatch.setenv("DOCINTEL_DATABRICKS_HOST", "https://private-host.example")
    get_settings.cache_clear()
    with _client() as client:
        body = client.get("/api/settings").text
    assert "super-secret-value" not in body and "private-host" not in body
