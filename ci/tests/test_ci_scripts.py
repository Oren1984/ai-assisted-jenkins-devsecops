"""Pipeline helper scripts: Terraform allowlist, step recording, evidence packaging."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai import analyzer
from ai.config import Settings
from ai.schemas import EvidencePackage
from ci import tf_safe

CI_DIR = Path(__file__).resolve().parent.parent
ROOT = CI_DIR.parent

# ---------------------------------------------------------------- terraform allowlist


@pytest.mark.parametrize("args", [
    ["fmt", "-check", "-recursive"],
    ["init", "-backend=false", "-input=false"],
    ["validate", "-json"],
    ["version"],
])
def test_validation_commands_are_allowed(args):
    assert tf_safe.check(args) is None


@pytest.mark.parametrize("args", [
    ["plan"], ["apply"], ["apply", "-auto-approve"], ["destroy"], ["import", "a", "b"],
    ["state", "rm", "x"], ["refresh"], ["taint", "x"], ["workspace", "new", "x"],
    ["login"], ["console"], ["init"], ["init", "-backend=true"],
    ["init", "-backend=false", "-backend-config=bucket=x"], ["fmt"], [],
])
def test_everything_else_is_refused(args):
    assert tf_safe.check(args) is not None


def test_refused_command_never_starts_terraform(monkeypatch, capsys):
    def explode(*args, **kwargs):
        raise AssertionError("terraform must not be started")

    monkeypatch.setattr(tf_safe.subprocess, "run", explode)
    assert tf_safe.main(["apply", "-auto-approve"]) == 64
    assert "REFUSED" in capsys.readouterr().err


def test_cloud_credentials_are_stripped_from_the_environment():
    env = tf_safe.clean_environment({
        "PATH": "/usr/bin", "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y",
        "AWS_PROFILE": "prod", "TF_TOKEN_app_terraform_io": "z", "TF_VAR_region": "us-east-1",
        "TF_CLI_CONFIG_FILE": "/etc/terraformrc"})
    assert set(env) == {"PATH", "TF_CLI_CONFIG_FILE", "CHECKPOINT_DISABLE", "TF_IN_AUTOMATION",
                        "TF_INPUT"}


# ---------------------------------------------------------------- run_step


def run_step(tmp_path, check_id, *command):
    return subprocess.run(
        [sys.executable, str(CI_DIR / "run_step.py"), "--id", check_id, "--evidence-dir",
         str(tmp_path / "evidence"), "--", *command],
        capture_output=True, text=True, check=False)


def test_run_step_passes_the_exit_code_through_and_records_it(tmp_path):
    result = run_step(tmp_path, "unit-tests", sys.executable, "-c",
                      "print('boom'); raise SystemExit(3)")
    assert result.returncode == 3
    record = json.loads((tmp_path / "evidence/steps/unit-tests.json").read_text())
    assert (record["status"], record["exit_code"]) == ("failed", 3)
    assert "boom" in (tmp_path / "evidence/logs/unit-tests.log").read_text()


def test_run_step_records_a_missing_tool_as_failed(tmp_path):
    result = run_step(tmp_path, "gitleaks", "definitely-not-a-real-tool")
    assert result.returncode == 127
    assert json.loads((tmp_path / "evidence/steps/gitleaks.json").read_text())["status"] == "failed"


# ---------------------------------------------------------------- evidence packaging

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" skipped="0" tests="2">
<testcase classname="app.tests.test_pricing" name="test_single_item_has_no_discount"/>
<testcase classname="app.tests.test_pricing" name="test_bulk_discount_applies_at_threshold">
<failure message="assert 10000 == 9500">def test_bulk():
&gt;   assert calculate_quote("widget", 10)["total_cents"] == 9500
E   assert 10000 == 9500
E   leaked LEAK
</failure></testcase></testsuite></testsuites>""".replace(
    "LEAK", "api_key=" + "abcdef" + "123456")  # assembled at runtime, see test_boundaries


def write_step(evidence: Path, check_id: str, exit_code: int, log: str = "") -> None:
    (evidence / "steps").mkdir(parents=True, exist_ok=True)
    (evidence / "logs").mkdir(exist_ok=True)
    (evidence / "reports").mkdir(exist_ok=True)
    (evidence / "steps" / f"{check_id}.json").write_text(json.dumps({
        "id": check_id, "exit_code": exit_code, "duration_seconds": 1.5,
        "status": "passed" if exit_code == 0 else "failed"}))
    (evidence / "logs" / f"{check_id}.log").write_text(log)


def package(tmp_path, result: str) -> EvidencePackage:
    completed = subprocess.run(
        [sys.executable, str(CI_DIR / "package_evidence.py"), "--result", result,
         "--started-at-ms", "1767261600000", "--scenario", "demo"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
        env={"JOB_NAME": "devsecops-pipeline", "BUILD_NUMBER": "12", "PATH": "",
             "BUILD_URL": "http://localhost:8080/job/devsecops-pipeline/12/",
             "PYTHONPATH": str(ROOT), "SYSTEMROOT": "C:\\Windows"})
    assert completed.returncode == 0, completed.stderr
    return EvidencePackage.model_validate_json(
        (tmp_path / "evidence" / "evidence.json").read_text("utf-8"))


def test_package_from_a_failed_test_run_is_valid_bounded_and_redacted(tmp_path):
    evidence = tmp_path / "evidence"
    for check_id in ("source-preparation", "dependency-install", "static-checks"):
        write_step(evidence, check_id, 0)
    write_step(evidence, "unit-tests", 1, "\n".join(f"line {n}" for n in range(500)))
    (evidence / "reports" / "junit.xml").write_text(JUNIT)
    write_step(evidence, "bandit", 0)
    (evidence / "reports" / "bandit.json").write_text(json.dumps({"results": []}))

    result = package(tmp_path, "FAILURE")
    assert result.build.id == "devsecops-pipeline-12"
    assert result.build.result == "FAILURE"
    statuses = {stage.name: stage.status for stage in result.stages}
    assert statuses["Unit Tests"] == "failed"
    assert statuses["Static Checks"] == "passed"
    assert statuses["Terraform Validation"] == "not_run"
    ids = result.item_ids()
    assert {"test-summary", "test-001", "bandit-summary", "log-unit-tests"} <= ids

    log = next(i for i in result.items if i.id == "log-unit-tests")
    assert "line 499" in log.content and "line 100" not in log.content  # tail only
    failure = next(i for i in result.items if i.id == "test-001")
    assert "abcdef123456" not in failure.content and "[REDACTED]" in failure.content

    analysis = analyzer.analyze(result, Settings())
    assert analysis.failure_category == "unit_test_failure"
    assert analysis.grounding.status == "verified"


def test_package_reads_scanner_and_terraform_reports(tmp_path):
    evidence = tmp_path / "evidence"
    write_step(evidence, "bandit", 1, "bandit exit 1")
    (evidence / "reports" / "bandit.json").write_text(json.dumps({"results": [{
        "test_id": "B602", "test_name": "subprocess_popen_with_shell_equals_true",
        "issue_severity": "HIGH", "issue_confidence": "HIGH", "line_number": 12,
        "filename": "./app/legacy_export.py", "issue_text": "shell=True", "code": "12 call()",
        "issue_cwe": {"id": 78}}]}))
    write_step(evidence, "pip-audit", 1)
    (evidence / "reports" / "pip-audit.json").write_text(json.dumps({"dependencies": [
        {"name": "examplepkg", "version": "1.0", "vulns": [
            {"id": "PYSEC-0000-1", "fix_versions": ["1.1"], "description": "example"}]},
        {"name": "clean", "version": "2.0", "vulns": []}]}))
    write_step(evidence, "gitleaks", 1)
    (evidence / "reports" / "gitleaks.json").write_text(json.dumps([{
        "RuleID": "generic-api-key", "File": "app/settings.py", "StartLine": 4,
        "Description": "Generic API Key", "Secret": "REDACTED", "Match": "REDACTED"}]))
    write_step(evidence, "terraform-validate", 1, json.dumps({
        "valid": False, "error_count": 1, "warning_count": 0, "diagnostics": [{
            "severity": "error", "summary": "Unsupported argument",
            "detail": 'An argument named "cidr_blok" is not expected here.',
            "range": {"filename": "main.tf", "start": {"line": 10}},
            "snippet": {"context": 'resource "aws_vpc" "example"', "code": "cidr_blok = 1"}}]}))

    result = package(tmp_path, "FAILURE")
    by_id = {i.id: i for i in result.items}
    assert by_id["bandit-001"].attributes["file"] == "app/legacy_export.py"
    assert by_id["pip-audit-001"].attributes["fix_versions"] == "1.1"
    assert by_id["gitleaks-001"].attributes["rule_id"] == "generic-api-key"
    assert by_id["tf-001"].attributes["file"] == "terraform/main.tf"
    assert by_id["tf-001"].attributes["line"] == 10
    assert analyzer.analyze(result, Settings()).failure_category == "security_finding"


def test_package_reports_missing_reports_instead_of_inventing_them(tmp_path):
    evidence = tmp_path / "evidence"
    write_step(evidence, "unit-tests", 2, "collection error")
    write_step(evidence, "bandit", 1, "crashed")
    result = package(tmp_path, "FAILURE")
    assert not [i for i in result.items if i.kind in ("test_summary", "security_summary")]
    assert any("JUnit" in note for note in result.notes)
    assert any("Bandit report missing" in note for note in result.notes)


def test_pipeline_plan_matches_the_jenkinsfile():
    """Runs from the repository root only. In the Jenkins workspace there is no Jenkinsfile."""
    jenkinsfile = ROOT / "Jenkinsfile"
    if not jenkinsfile.is_file():
        pytest.skip("Jenkinsfile is not part of the pipeline workspace")
    from ci.package_evidence import PIPELINE

    text = jenkinsfile.read_text("utf-8")
    for stage, checks in PIPELINE:
        assert f"stage('{stage}')" in text
        for check in checks:
            assert f"--id {check} " in text or f"$RUN {check} " in text
