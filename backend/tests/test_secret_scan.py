import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "secret_scan.py"
spec = importlib.util.spec_from_file_location("secret_scan", SCRIPT)
scan = importlib.util.module_from_spec(spec)
sys.modules["secret_scan"] = scan          # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(scan)


def _rules(text, denylist=()):
    return [f.rule for f in scan.scan_text("x", text, list(denylist))]


def test_blocks_provider_tokens_and_hosts():
    assert "databricks-pat" in _rules("token = " + "dapi" + "a" * 32)
    assert "github-token" in _rules("ghp_" + "A1" * 18)
    assert "google-api-key" in _rules("AIza" + "B" * 35)
    assert "cloud-workspace-host" in _rules("https://" + "adb-123.7.azuredatabricks" + ".net")
    assert "private-key" in _rules("-----BEGIN " + "RSA PRIVATE KEY-----")
    assert "credential-assignment" in _rules("client_secret = '" + "Zx8kLq2Wm9Rt4Yp7Vb3N" + "'")


def test_ignores_code_and_placeholders():
    assert _rules("api_key = settings.llm_api_key") == []
    assert _rules("DOCINTEL_LLM_API_KEY=") == []
    assert _rules("password = 'changeme-changeme'") == []


def test_local_denylist_and_forbidden_files(tmp_path):
    deny = tmp_path / "deny"
    deny.write_text("internal-corp-name\nre:route\\.[a-z]+\n")
    rules = scan.load_denylist(str(deny))
    assert len(_rules("we call route.secret here and Internal-Corp-Name", rules)) == 2
    assert scan.check_name(".env") and scan.check_name("backend/data/docintel.db") and not scan.check_name(".env.example")
