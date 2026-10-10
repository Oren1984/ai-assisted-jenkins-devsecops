"""Integration checks on the repository itself: Compose, Jenkinsfile, secrets, boundaries.

Standard library + pytest only, so this runs on the host. The Compose checks need the
docker CLI but not a running stack.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JENKINSFILE = (ROOT / "Jenkinsfile").read_text("utf-8")
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".terraform"}
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".tf", ".groovy", ".sh", ".js",
                 ".html", ".css", ".example", ""}


def repository_files():
    for path in ROOT.rglob("*"):
        if path.is_file() and not SKIP_DIRS & set(path.relative_to(ROOT).parts):
            if path.name != ".env" and path.suffix in TEXT_SUFFIXES:
                yield path


# ---------------------------------------------------------------- docker compose


@pytest.fixture(scope="module")
def compose() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    env = {**os.environ, "JENKINS_ADMIN_ID": "admin", "JENKINS_ADMIN_PASSWORD": "placeholder",
           "AI_INGEST_TOKEN": "placeholder"}
    result = subprocess.run(
        ["docker", "compose", "--profile", "tools", "config", "--format", "json"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_compose_defines_the_expected_services(compose):
    assert set(compose["services"]) == {"jenkins", "agent", "app", "ai", "tests"}


def test_every_published_port_is_bound_to_localhost(compose):
    published = [port for service in compose["services"].values()
                 for port in service.get("ports", [])]
    assert len(published) == 3
    assert all(port.get("host_ip") == "127.0.0.1" for port in published)


def test_no_service_gets_the_docker_socket_or_extra_privileges(compose):
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            assert "docker.sock" not in str(volume.get("source", "")), name
        assert not service.get("privileged"), name
        assert not service.get("cap_add"), name
        assert service.get("network_mode") != "host", name


def test_ai_service_gets_no_jenkins_access_and_no_source_mount(compose):
    ai = compose["services"]["ai"]
    assert not [key for key in ai["environment"] if "JENKINS" in key.upper()]
    assert [(v["type"], v["target"]) for v in ai["volumes"]] == [("volume", "/data")]
    assert ai.get("read_only") is True


def test_agent_source_mounts_are_read_only_and_exclude_secrets(compose):
    binds = [v for v in compose["services"]["agent"]["volumes"] if v["type"] == "bind"]
    assert binds and all(v.get("read_only") for v in binds)
    targets = {v["target"] for v in binds}
    assert "/src" not in targets  # never the whole repository (it would expose .env and .git)
    assert not [t for t in targets if t.endswith((".env", ".git"))]


def test_no_cloud_credentials_are_configured(compose):
    for name, service in compose["services"].items():
        keys = [key for key in service.get("environment", {}) if key.startswith(("AWS_", "ARM_"))]
        assert not keys, name


def test_services_have_health_checks(compose):
    for name in ("jenkins", "app", "ai"):
        assert compose["services"][name].get("healthcheck"), name


# ---------------------------------------------------------------- Jenkinsfile and Jenkins


def test_jenkinsfile_is_a_declarative_pipeline_with_the_required_stages():
    assert re.search(r"^pipeline \{", JENKINSFILE, re.M)
    stages = re.findall(r"stage\('([^']+)'\)", JENKINSFILE)
    assert stages == [
        "Source Preparation", "Dependency Installation", "Static Checks", "Unit Tests",
        "Security Scanning", "Terraform Validation", "Evidence Packaging",
        "AI Failure Analysis", "Pipeline Summary"]


def test_jenkinsfile_runs_terraform_only_through_the_allowlist():
    calls = re.findall(r"terraform[^\n]*", JENKINSFILE)
    commands = [c for c in calls if "tf_safe.py" in c]
    assert len(commands) == 3
    for line in JENKINSFILE.splitlines():
        if line.strip().startswith("//"):
            continue
        assert not re.search(r"\bterraform\s+(-chdir\S*\s+)?(plan|apply|destroy|import)\b", line)


@pytest.mark.parametrize("forbidden", [
    r"\bdocker\s+(run|build|push|exec)\b", r"\bgit\s+(push|commit)\b", r"\bkubectl\b",
    r"\baws\s", r"\bbuild\s+job:", r"\bsshagent\b", r"currentBuild\.result\s*=",
    r"\bretry\(",
])
def test_jenkinsfile_has_no_deployment_remediation_or_result_override(forbidden):
    assert not re.search(forbidden, JENKINSFILE)


def test_ai_stage_runs_after_evidence_and_cannot_fail_the_build():
    ai_stage = JENKINSFILE.split("stage('AI Failure Analysis')")[1].split("stage(")[0]
    assert "submit_evidence.py" in ai_stage
    assert "withCredentials" in ai_stage  # the token is bound, not written into the file
    submit = (ROOT / "ci" / "submit_evidence.py").read_text("utf-8")
    assert "return 0" in submit and "return 1" not in submit and "sys.exit(1)" not in submit


def test_jenkins_controller_is_locked_down_by_configuration():
    casc = (ROOT / "jenkins/configuration/jenkins.yaml").read_text("utf-8")
    assert "numExecutors: 0" in casc
    assert "allowsSignup: false" in casc
    assert "allowAnonymousRead: false" in casc
    assert "slaveAgentPort: -1" in casc
    assert '"${JENKINS_ADMIN_PASSWORD}"' in casc and '"${AI_INGEST_TOKEN}"' in casc


# ---------------------------------------------------------------- secrets


def test_env_file_is_ignored_and_the_example_holds_only_placeholders():
    assert ".env" in (ROOT / ".gitignore").read_text("utf-8").splitlines()
    example = (ROOT / ".env.example").read_text("utf-8")
    assert "JENKINS_ADMIN_PASSWORD=change-me\n" in example
    assert "AI_INGEST_TOKEN=change-me-too\n" in example
    assert re.search(r"^LLM_API_KEY=$", example, re.M)


def test_local_secret_values_appear_nowhere_in_the_repository():
    env_file = ROOT / ".env"
    if not env_file.is_file():
        pytest.skip("no local .env")
    secrets = [line.split("=", 1)[1].strip() for line in env_file.read_text("utf-8").splitlines()
               if line.startswith(("JENKINS_ADMIN_PASSWORD=", "AI_INGEST_TOKEN=", "LLM_API_KEY="))]
    example = (ROOT / ".env.example").read_text("utf-8")
    secrets = [value for value in secrets if len(value) >= 12 and value not in example]
    if not secrets:
        pytest.skip(".env still holds the .env.example placeholders")
    for path in repository_files():
        text = path.read_text("utf-8", errors="replace")
        assert not [s for s in secrets if s in text], path


@pytest.mark.parametrize("pattern", [
    r"AKIA[0-9A-Z]{16}", r"ghp_[A-Za-z0-9]{36}", r"sk-[A-Za-z0-9]{32,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)(password|secret|token|api_key)\s*[:=]\s*[\"'][A-Za-z0-9+/_-]{20,}[\"']",
])
def test_no_hardcoded_secret_shapes(pattern):
    hits = [str(path.relative_to(ROOT)) for path in repository_files()
            if re.search(pattern, path.read_text("utf-8", errors="replace"))
            and path.name != "test_repository.py"]
    assert hits == []


def test_terraform_example_has_no_backend_and_no_credentials():
    for path in (ROOT / "terraform").glob("*.tf"):
        text = path.read_text("utf-8")
        assert "backend " not in text
        assert not re.search(r"access_key|secret_key|profile\s*=", text)
