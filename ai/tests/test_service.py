"""HTTP API: evidence ingestion, input validation, human review and the audit trail."""

import pytest
from fastapi.testclient import TestClient

from ai.config import Settings
from ai.service import create_app
from ai.tests import samples

TOKEN = {"X-Ingest-Token": "test-ingest-token"}
BUILD = "devsecops-pipeline-7"
REVIEW_URL = f"/api/analyses/{BUILD}/recommendations/rec-1/review"


@pytest.fixture
def client(tmp_path):
    settings = Settings(provider="mock", ingest_token="test-ingest-token",
                        database_path=str(tmp_path / "review.db"))
    return TestClient(create_app(settings))


def submit(client, package):
    return client.post("/api/analyses", json=package.model_dump(), headers=TOKEN)


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["provider"] == "mock"


def test_ui_is_served(client):
    assert "Jenkins executes. AI investigates. Humans decide." in client.get("/").text


def test_submit_evidence_returns_validated_analysis(client):
    response = submit(client, samples.test_failure())
    assert response.status_code == 200
    body = response.json()
    assert body["build_id"] == BUILD
    assert body["jenkins_result"] == "FAILURE"
    assert body["failure_category"] == "unit_test_failure"
    assert body["autonomous_execution"] is False
    listed = client.get("/api/analyses").json()
    assert [(b["build_id"], b["jenkins_result"]) for b in listed] == [(BUILD, "FAILURE")]


def test_ingest_requires_the_token(client):
    package = samples.success().model_dump()
    assert client.post("/api/analyses", json=package).status_code == 401
    wrong = {"X-Ingest-Token": "wrong"}
    assert client.post("/api/analyses", json=package, headers=wrong).status_code == 401
    assert client.get("/api/analyses").json() == []


def test_ingest_fails_closed_when_no_token_is_configured(tmp_path):
    app = create_app(Settings(ingest_token="", database_path=str(tmp_path / "r.db")))
    response = TestClient(app).post(
        "/api/analyses", json=samples.success().model_dump(), headers={"X-Ingest-Token": ""})
    assert response.status_code == 401


@pytest.mark.parametrize("mutate", [
    lambda p: p["build"].update(result="PASSED"),
    lambda p: p["build"].update(id="../../etc/passwd"),
    lambda p: p["build"].update(url="javascript:alert(1)"),
    lambda p: p.update(unexpected="field"),
    lambda p: p.update(items=[{"id": "x", "kind": "log_excerpt", "title": "t",
                               "content": "y" * 5000}]),
    lambda p: p.update(items=[{"id": f"i{n}", "kind": "log_excerpt", "title": "t"}
                              for n in range(81)]),
    lambda p: p.pop("stages"),
])
def test_invalid_evidence_is_rejected(client, mutate):
    package = samples.success().model_dump()
    mutate(package)
    assert client.post("/api/analyses", json=package, headers=TOKEN).status_code == 422


def test_oversized_request_is_rejected(client):
    response = client.post("/api/analyses", content=b"x" * (600 * 1024),
                           headers={**TOKEN, "Content-Type": "application/json"})
    assert response.status_code == 413


def test_secrets_are_redacted_before_storage(client):
    leaky = samples.item("log-unit-tests", "log_excerpt", "unit-tests", "log",
                         "connecting with password=hunter2secret to db\n"
                         "Authorization: Bearer abcdefghijklmnop1234")
    package = samples.make_package("FAILURE", ["unit-tests"], items=[leaky])
    assert submit(client, package).status_code == 200
    stored = client.get(f"/api/analyses/{BUILD}").text
    assert "hunter2secret" not in stored
    assert "abcdefghijklmnop1234" not in stored
    assert "[REDACTED]" in stored


# ---------------------------------------------------------------- human review


def test_accept_and_reject_are_recorded_with_timestamp(client):
    submit(client, samples.test_failure())
    accept = client.post(REVIEW_URL, json={"decision": "accept", "reviewer": "oren",
                                           "comment": "will look at it"})
    assert accept.status_code == 200
    event = accept.json()["event"]
    assert event["decision"] == "accept"
    assert event["reviewer"] == "oren"
    assert event["created_at"]
    assert event["action_executed"] is False
    assert "executes nothing" in accept.json()["meaning"]

    reject_url = REVIEW_URL.replace("rec-1", "rec-2")
    rejected = client.post(reject_url, json={"decision": "reject", "reviewer": "oren"})
    assert rejected.status_code == 200

    reviews = client.get(f"/api/analyses/{BUILD}").json()["reviews"]
    assert reviews["rec-1"]["decision"] == "accept"
    assert reviews["rec-2"]["decision"] == "reject"

    audit = client.get(f"/api/audit?build_id={BUILD}").json()
    assert [e["event_type"] for e in audit] == [
        "analysis_recorded", "review_recorded", "review_recorded"]
    assert all(e["action_executed"] is False for e in audit)


def test_changed_decision_keeps_the_earlier_one_in_the_audit_trail(client):
    submit(client, samples.test_failure())
    client.post(REVIEW_URL, json={"decision": "accept", "reviewer": "a"})
    client.post(REVIEW_URL, json={"decision": "reject", "reviewer": "b"})
    assert client.get(f"/api/analyses/{BUILD}").json()["reviews"]["rec-1"]["decision"] == "reject"
    decisions = [e["decision"] for e in client.get("/api/audit").json() if e["decision"]]
    assert decisions == ["accept", "reject"]


@pytest.mark.parametrize("body", [
    {"decision": "approve-and-apply", "reviewer": "oren"},
    {"decision": "accept"},
    {"decision": "accept", "reviewer": "   "},
    {"decision": "accept", "reviewer": "oren", "execute": True},
    {"decision": "accept", "reviewer": "oren", "comment": "c" * 501},
])
def test_invalid_review_requests_are_rejected(client, body):
    submit(client, samples.test_failure())
    assert client.post(REVIEW_URL, json=body).status_code == 422
    assert client.get("/api/analyses/" + BUILD).json()["reviews"] == {}


def test_review_of_unknown_build_or_recommendation_is_rejected(client):
    body = {"decision": "accept", "reviewer": "oren"}
    assert client.post(REVIEW_URL, json=body).status_code == 404
    submit(client, samples.test_failure())
    assert client.post(REVIEW_URL.replace("rec-1", "rec-99"), json=body).status_code == 404


def test_accepting_a_recommendation_changes_nothing_but_the_audit_trail(client):
    submit(client, samples.test_failure())
    before = client.get(f"/api/analyses/{BUILD}").json()
    client.post(REVIEW_URL, json={"decision": "accept", "reviewer": "oren"})
    after = client.get(f"/api/analyses/{BUILD}").json()
    assert after["analysis"] == before["analysis"]
    assert after["evidence"] == before["evidence"]
    assert after["evidence"]["build"]["result"] == "FAILURE"
    assert client.get("/api/stats").json()["by_decision"] == {"accept": 1}
