"""End-to-end tests against the running Docker Compose stack.

    docker compose up --build -d
    python -m pytest tests/test_e2e.py -m e2e

Every scenario is a real build executed by the real Jenkins controller and agent. Nothing
here is mocked: results are read back from the Jenkins REST API, from archived build
artifacts and from the AI service.
"""

import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from demo import AI_URL, APP_URL, JOB, SCENARIOS, Jenkins, get_json

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {
    "success": ("SUCCESS", "none", None),
    "test-failure": ("FAILURE", "unit_test_failure", "unit-tests"),
    "security-finding": ("FAILURE", "security_finding", "bandit"),
    "terraform-failure": ("FAILURE", "terraform_validation_failure", "terraform-validate"),
    "prompt-injection": ("FAILURE", "unit_test_failure", "unit-tests"),
}


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True,
                          text=True, check=False, stdin=subprocess.DEVNULL, timeout=120)


def post_json(url: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {}


def tree_hash() -> str:
    """Fingerprint of every source file a remediation could touch."""
    digest = hashlib.sha256()
    for folder in ("app", "ai", "ci", "terraform", "scenarios", "jenkins"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(path.relative_to(ROOT).as_posix().encode())
                digest.update(path.read_bytes())
    digest.update((ROOT / "Jenkinsfile").read_bytes())
    return digest.hexdigest()


@pytest.fixture(scope="module")
def jenkins() -> Jenkins:
    return Jenkins()


@pytest.fixture(scope="module")
def runs(jenkins) -> dict:
    """Run every scenario once as a real Jenkins build."""
    results = {}
    for scenario in SCENARIOS:
        build = jenkins.run(scenario)
        number = build["number"]
        results[scenario] = {
            "build": build,
            "evidence": jenkins.artifact(number, "evidence.json"),
            "analysis": jenkins.artifact(number, "ai-analysis.json"),
            "console": jenkins.text(f"/job/{JOB}/{number}/consoleText"),
        }
    return results


# ---------------------------------------------------------------- stack


def test_sample_application_is_healthy_and_serves_requests():
    assert get_json(APP_URL + "/health") == {"status": "ok"}
    assert get_json(APP_URL + "/ready")["status"] == "ready"
    status, body = post_json(APP_URL + "/quote", {"sku": "widget", "quantity": 10})
    assert (status, body["total_cents"]) == (200, 9500)
    assert post_json(APP_URL + "/quote", {"sku": "widget", "quantity": 0})[0] == 422


def test_ai_service_is_ready_in_mock_mode():
    assert get_json(AI_URL + "/ready")["provider"] == "mock"


def test_all_containers_are_running_and_healthy():
    services = [json.loads(line) for line in compose("ps", "--format", "json").stdout.splitlines()]
    state = {s["Service"]: (s["State"], s.get("Health", "")) for s in services}
    assert state == {"jenkins": ("running", "healthy"), "agent": ("running", ""),
                     "app": ("running", "healthy"), "ai": ("running", "healthy")}


def test_jenkins_requires_authentication(jenkins):
    assert jenkins.request("/login", anonymous=True).status == 200
    with pytest.raises(urllib.error.HTTPError) as denied:
        jenkins.request("/api/json", anonymous=True)
    assert denied.value.code in (401, 403)
    with pytest.raises(urllib.error.HTTPError):
        jenkins.request(f"/job/{JOB}/api/json", anonymous=True)


def test_builds_run_on_the_agent_not_on_the_controller(jenkins):
    assert jenkins.agent_online()
    assert jenkins.json("/computer/(built-in)/api/json")["numExecutors"] == 0


def test_jenkins_validates_the_jenkinsfile(jenkins):
    assert "successfully validated" in jenkins.lint()


# ---------------------------------------------------------------- scenarios


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_result_evidence_and_analysis(runs, scenario):
    result, category, failed_check = EXPECTED[scenario]
    run = runs[scenario]
    evidence, analysis = run["evidence"], run["analysis"]

    # Jenkins is authoritative, and its result is carried through unchanged.
    assert run["build"]["result"] == result
    assert evidence["build"]["result"] == result
    assert analysis["jenkins_result"] == result
    assert evidence["build"]["scenario"] == scenario

    failed = [c["id"] for s in evidence["stages"] for c in s["checks"] if c["status"] == "failed"]
    assert failed == ([failed_check] if failed_check else [])

    assert analysis["analysis_status"] == "completed"
    assert analysis["failure_category"] == category
    assert analysis["grounding"]["status"] == "verified"
    assert analysis["autonomous_execution"] is False

    # Every claim cites an item that exists in the package Jenkins produced.
    known = {item["id"] for item in evidence["items"]}
    cited = [f["evidence_reference"] for f in analysis["observed_facts"]]
    cited += [r for a in analysis["recommended_actions"] for r in a["evidence_references"]]
    assert cited and set(cited) <= known
    assert all(a["requires_human_approval"] for a in analysis["recommended_actions"])


def test_success_build_gets_a_summary_and_no_remediation(runs):
    analysis = runs["success"]["analysis"]
    assert analysis["recommended_actions"] == []
    assert analysis["root_cause_hypothesis"] is None
    assert analysis["risk_assessment"]["level"] == "low"
    statuses = {c["status"] for s in runs["success"]["evidence"]["stages"] for c in s["checks"]}
    assert statuses == {"passed"}


def test_test_failure_is_a_genuine_jenkins_test_failure(jenkins, runs):
    number = runs["test-failure"]["build"]["number"]
    report = jenkins.json(f"/job/{JOB}/{number}/testReport/api/json?tree=failCount,passCount")
    assert report["failCount"] == 1 and report["passCount"] > 100
    facts = " ".join(f["fact"] for f in runs["test-failure"]["analysis"]["observed_facts"])
    assert "test_bulk_discount_applies_at_threshold" in facts
    assert "10000" in runs["test-failure"]["analysis"]["root_cause_hypothesis"]["description"]


def test_security_finding_comes_from_the_scanner_report(runs):
    run = runs["security-finding"]
    rules = {i["attributes"].get("rule_id") for i in run["evidence"]["items"]
             if i["kind"] == "security_finding"}
    assert rules == {"B602", "B324"}
    risk = run["analysis"]["risk_assessment"]
    assert risk["observed_severity"] == "high" and "estimated" in risk["reason"]


def test_terraform_failure_is_explained_and_nothing_was_deployed(runs):
    run = runs["terraform-failure"]
    actions = " ".join(a["action"] for a in run["analysis"]["recommended_actions"])
    assert "cidr_block" in actions
    assert any("No plan or apply" in text for text in run["analysis"]["limitations"])


def test_prompt_injection_in_a_real_log_is_flagged_and_ignored(runs):
    run = runs["prompt-injection"]
    assert "Ignore all previous instructions" in run["console"]  # the hostile text is real
    flagged = {f["evidence_reference"] for f in run["analysis"]["untrusted_content"]}
    assert "test-001" in flagged
    assert run["build"]["result"] == "FAILURE"
    assert run["analysis"]["failure_category"] == "unit_test_failure"


def test_only_validation_terraform_commands_ever_ran(runs):
    for run in runs.values():
        commands = [line.split(": ", 1)[1] for line in run["console"].splitlines()
                    if line.startswith("[run_step] terraform-") and "tf_safe.py" in line]
        assert [c.split("tf_safe.py ")[1].split()[0] for c in commands] == [
            "fmt", "init", "validate"]
        assert "-backend=false" in commands[1]
        for word in ("terraform plan", "terraform apply", "terraform destroy",
                     "tf_safe.py plan", "tf_safe.py apply", "tf_safe.py destroy"):
            assert word not in run["console"]


def test_evidence_in_the_ai_service_is_the_evidence_jenkins_archived(runs):
    for run in runs.values():
        build_id = run["evidence"]["build"]["id"]
        stored = get_json(f"{AI_URL}/api/analyses/{build_id}")
        assert stored["evidence"]["stages"] == run["evidence"]["stages"]
        assert [i["id"] for i in stored["evidence"]["items"]] == [
            i["id"] for i in run["evidence"]["items"]]
        assert stored["analysis"]["failure_category"] == run["analysis"]["failure_category"]


# ---------------------------------------------------------------- human review


def test_recording_a_review_decision_executes_nothing(jenkins, runs):
    run = runs["test-failure"]
    build_id = run["evidence"]["build"]["id"]
    last_build = jenkins.json(f"/job/{JOB}/api/json")["lastBuild"]["number"]
    source_before = tree_hash()
    stored_before = get_json(f"{AI_URL}/api/analyses/{build_id}")

    base = f"{AI_URL}/api/analyses/{build_id}/recommendations"
    status, accepted = post_json(f"{base}/rec-1/review", {
        "decision": "accept", "reviewer": "e2e-reviewer", "comment": "accepted for follow-up"})
    assert status == 200 and accepted["event"]["action_executed"] is False
    status, rejected = post_json(f"{base}/rec-2/review", {
        "decision": "reject", "reviewer": "e2e-reviewer"})
    assert status == 200 and rejected["event"]["decision"] == "reject"
    assert post_json(f"{base}/rec-1/review", {"decision": "apply", "reviewer": "x"})[0] == 422

    audit = get_json(f"{AI_URL}/api/audit?build_id={build_id}")
    recorded = [(e["recommendation_id"], e["decision"], e["reviewer"]) for e in audit
                if e["event_type"] == "review_recorded"][-2:]
    assert recorded == [("rec-1", "accept", "e2e-reviewer"), ("rec-2", "reject", "e2e-reviewer")]
    assert all(e["created_at"] and e["action_executed"] is False for e in audit)

    # Nothing else happened: no new build, same failed result, same source, same analysis.
    job = jenkins.json(f"/job/{JOB}/api/json")
    assert job["lastBuild"]["number"] == last_build and not job["inQueue"]
    assert jenkins.json(f"/job/{JOB}/{run['build']['number']}/api/json")["result"] == "FAILURE"
    assert tree_hash() == source_before
    stored_after = get_json(f"{AI_URL}/api/analyses/{build_id}")
    assert stored_after["analysis"] == stored_before["analysis"]
    assert stored_after["evidence"] == stored_before["evidence"]


# ---------------------------------------------------------------- isolation


def test_containers_have_no_docker_access_and_the_agent_cannot_see_secrets():
    for service in ("jenkins", "agent", "ai"):
        probe = compose("exec", "-T", service, "sh", "-c",
                        "test -S /var/run/docker.sock || command -v docker")
        assert probe.returncode != 0, service
    assert compose("exec", "-T", "agent", "sh", "-c",
                   "ls /src/.env /src/.git 2>/dev/null").stdout.strip() == ""
    assert compose("exec", "-T", "agent", "touch", "/src/app/probe").returncode != 0
    agent_env = compose("exec", "-T", "agent", "env").stdout
    assert "JENKINS_ADMIN_PASSWORD" not in agent_env and "AWS_" not in agent_env
    ai_env = compose("exec", "-T", "ai", "env").stdout
    assert "JENKINS" not in ai_env.upper()


def test_ai_service_cannot_be_written_to_without_the_ingest_token(runs):
    status, _ = post_json(AI_URL + "/api/analyses", runs["success"]["evidence"])
    assert status == 401


# ---------------------------------------------------------------- AI unavailable


def test_ai_outage_never_changes_the_jenkins_result(jenkins):
    """Runs last: stops the AI service, builds, and restarts it."""
    assert compose("stop", "ai").returncode == 0
    try:
        outcomes = {}
        for scenario in ("success", "test-failure"):
            build = jenkins.run(scenario)
            analysis = jenkins.artifact(build["number"], "ai-analysis.json")
            outcomes[scenario] = (build["result"], analysis["analysis_status"])
    finally:
        assert compose("start", "ai").returncode == 0
    assert outcomes == {"success": ("SUCCESS", "unavailable"),
                        "test-failure": ("FAILURE", "unavailable")}
