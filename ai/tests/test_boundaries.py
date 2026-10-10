"""Negative tests: what the AI component must not be able to do."""

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai.config import Settings
from ai.redaction import redact, redact_value
from ai.service import create_app

AI_DIR = Path(__file__).resolve().parent.parent
SOURCES = [p for p in AI_DIR.glob("*.py")]

# The complete HTTP surface of the service. Anything new must be added here on purpose.
ALLOWED_ROUTES = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/"),
    ("GET", "/api/analyses"),
    ("POST", "/api/analyses"),
    ("GET", "/api/analyses/{build_id}"),
    ("POST", "/api/analyses/{build_id}/recommendations/{recommendation_id}/review"),
    ("GET", "/api/audit"),
    ("GET", "/api/stats"),
}
FORBIDDEN_MODULES = {"subprocess", "os.system", "shutil", "socket", "paramiko", "docker",
                     "boto3", "jenkins", "git", "pty", "ctypes", "multiprocessing"}


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(ingest_token="t", database_path=str(tmp_path / "r.db")))


def test_service_exposes_no_remediation_or_execution_endpoint(app):
    routes = {
        (method, route.path)
        for route in app.routes
        if hasattr(route, "methods") and route.path not in ("/openapi.json", "/docs",
                                                             "/docs/oauth2-redirect", "/redoc")
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    assert routes == ALLOWED_ROUTES


@pytest.mark.parametrize("path", [
    "/api/remediate", "/api/execute", "/api/builds", "/api/jenkins/build", "/api/deploy",
    "/api/analyses/devsecops-pipeline-7/recommendations/rec-1/apply",
    "/api/analyses/devsecops-pipeline-7/rerun",
])
def test_prohibited_actions_do_not_exist(app, path):
    assert TestClient(app).post(path, json={}).status_code in (404, 405)


def test_stored_records_cannot_be_modified_or_deleted_over_http(app):
    client = TestClient(app)
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)("/api/analyses/devsecops-pipeline-7").status_code == 405
        assert getattr(client, method)("/api/audit").status_code == 405


def test_ai_code_cannot_start_processes_or_reach_jenkins():
    assert len(SOURCES) >= 8
    for source in SOURCES:
        tree = ast.parse(source.read_text("utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                assert name not in ("eval", "exec", "system", "popen"), f"{source.name}: {name}"
        roots = {name.split(".")[0] for name in imported} | imported
        assert not roots & FORBIDDEN_MODULES, f"{source.name} imports {roots & FORBIDDEN_MODULES}"


def test_ai_component_has_no_jenkins_configuration():
    fields = set(Settings.__dataclass_fields__)
    assert not [name for name in fields if "jenkins" in name.lower()]
    for source in SOURCES:
        text = source.read_text("utf-8")
        assert "JENKINS_" not in text, f"{source.name} reads a Jenkins setting"


def test_only_the_llm_client_makes_outbound_requests():
    users = [s.name for s in SOURCES if "import httpx" in s.read_text("utf-8")]
    assert users == ["providers.py"]


def test_ui_never_injects_untrusted_text_as_html():
    script = (AI_DIR / "static" / "app.js").read_text("utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in script
    assert "href: build.url" not in script  # links only through the http(s) check


# ---------------------------------------------------------------- redaction


# Secret-shaped samples are assembled at runtime, so the repository itself contains no
# literal that a secret scanner would (rightly) report.
PEM = "-----{} RSA " + "PRIVATE KEY-----"
SECRET_SAMPLES = [
    ("DB_PASSWORD={}", "sup3r" + "s3cret!"),
    ('"api_key": "{}"', "abc123" + "def456ghi"),
    ("aws key {} used", "AKIA" + "IOSFODNN7EXAMPLE"),
    ("Authorization: Bearer {}", "abcdefghijklm" + "nopqrstuvwxyz"),
    ("clone https://deploy:{}@git.example.com/repo.git", "t0p" + "s3cret"),
    ("token {}", "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"),
    (PEM.format("BEGIN") + "\n{}\n" + PEM.format("END"), "MIIEow" + "IBAAKCAQEA"),
]


@pytest.mark.parametrize(("template", "secret"), SECRET_SAMPLES)
def test_secret_shapes_are_redacted(template, secret):
    cleaned = redact(template.format(secret))
    assert secret not in cleaned
    assert "[REDACTED]" in cleaned


def test_ordinary_text_is_left_alone():
    text = "assert 10000 == 9500 in app/tests/test_pricing.py:17 (exit code 1)"
    assert redact(text) == text


def test_redaction_walks_nested_structures():
    data = {"items": [{"content": "secret=abc12345", "attributes": {"line": 3}}]}
    assert redact_value(data) == {
        "items": [{"content": "secret=[REDACTED]", "attributes": {"line": 3}}]}
