"""Real-LLM mode against a simulated OpenAI-compatible endpoint (httpx.MockTransport).

These tests exercise the client, retry, timeout, validation and fallback logic.
They do not prove behaviour against any real model.
"""

import json

import httpx
import pytest

from ai import analyzer
from ai.config import Settings
from ai.providers import OpenAICompatibleProvider, ProviderError, build_user_prompt
from ai.tests import samples

SETTINGS = Settings(provider="llm", llm_base_url="http://llm.test/v1", llm_api_key="test-key",
                    llm_model="test-model", llm_timeout_seconds=1, llm_max_retries=2)

GOOD_DRAFT = {
    "failure_category": "unit_test_failure",
    "summary": "One unit test failed in the Unit Tests stage.",
    "observed_facts": [{"fact": "The bulk discount test failed.",
                        "evidence_reference": "test-001"}],
    "root_cause_hypothesis": {"description": "Possible off-by-one in the threshold check.",
                              "confidence": "medium", "evidence_references": ["test-001"]},
    "risk_assessment": {"observed_severity": "unknown", "level": "medium",
                        "reason": "Blocks delivery validation."},
    "recommended_actions": [{"action": "Review the threshold comparison.",
                             "rationale": "The assertion shows no discount at 10 units.",
                             "evidence_references": ["test-001"],
                             "requires_human_approval": True}],
    "limitations": ["Source code was not inspected."],
}


def completion(draft: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={
        "choices": [{"message": {"role": "assistant", "content": json.dumps(draft)}}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 150, "total_tokens": 1050},
    })


def provider(handler, settings=SETTINGS) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(settings, transport=httpx.MockTransport(handler),
                                    backoff_seconds=0)


def test_valid_llm_answer_is_accepted_with_token_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return completion(GOOD_DRAFT)

    result = analyzer.analyze(samples.test_failure(), SETTINGS, provider=provider(handler))
    assert result.analysis_status == "completed"
    assert result.provider.mode == "llm"
    assert result.provider.model == "test-model"
    assert result.provider.token_usage["total_tokens"] == 1050
    assert result.grounding.status == "verified"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert "tools" not in seen["body"]  # the model is given no tools


def test_prompt_is_bounded_and_excludes_the_scenario_label():
    big = [samples.item(f"log-{n}", "log_excerpt", "unit-tests", "log", "x" * 4000)
           for n in range(60)]
    package = samples.make_package("FAILURE", ["unit-tests"], items=big,
                                   scenario="secret-scenario-label")
    prompt = build_user_prompt(package)
    assert len(prompt) < 40_000
    assert "secret-scenario-label" not in prompt
    assert '"items_omitted_for_size"' in prompt


def test_timeout_is_retried_then_reported_as_unavailable():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("timed out", request=request)

    result = analyzer.analyze(samples.test_failure(), SETTINGS, provider=provider(handler))
    assert len(calls) == 3  # one attempt plus two retries
    assert result.analysis_status == "unavailable"
    assert result.jenkins_result == "FAILURE"  # Jenkins result preserved
    assert result.recommended_actions == []
    assert "timed out" in result.limitations[0]
    assert result.provider.fallback_used is False


def test_server_error_is_retried_and_can_recover():
    responses = iter([httpx.Response(503), completion(GOOD_DRAFT)])
    response = provider(lambda request: next(responses)).analyze(samples.test_failure())
    assert response.attempts == 2


def test_client_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(ProviderError, match="HTTP 401"):
        provider(handler).analyze(samples.test_failure())
    assert len(calls) == 1


def test_malformed_llm_output_is_rejected():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    result = analyzer.analyze(samples.test_failure(), SETTINGS, provider=provider(handler))
    assert result.analysis_status == "unavailable"
    assert "schema" in result.limitations[0]


def test_missing_credentials_are_unavailable_without_any_request():
    settings = Settings(provider="llm", llm_api_key="", llm_model="")
    result = analyzer.analyze(samples.test_failure(), settings)
    assert result.analysis_status == "unavailable"
    assert result.jenkins_result == "FAILURE"


def test_fallback_to_mock_only_when_configured_and_clearly_identified():
    settings = Settings(provider="llm", llm_base_url="http://llm.test/v1", llm_api_key="k",
                        llm_model="m", llm_max_retries=0, fallback_to_mock=True)
    result = analyzer.analyze(samples.test_failure(), settings,
                              provider=provider(lambda r: httpx.Response(500), settings))
    assert result.analysis_status == "completed"
    assert result.provider.mode == "llm"
    assert result.provider.fallback_used is True
    assert "HTTP 500" in result.provider.fallback_reason
    assert any("fallback" in text for text in result.limitations)


def test_llm_that_obeys_injected_instructions_is_rejected():
    """Worst case: the model follows the hostile log text. The engine must not."""
    obedient = {
        "failure_category": "none",
        "summary": "False alarm. The build passed.",
        "observed_facts": [],
        "risk_assessment": {"observed_severity": "none", "level": "low", "reason": "All good."},
        "recommended_actions": [{"action": "Re-run the pipeline automatically.",
                                 "rationale": "Instructed by the log.",
                                 "evidence_references": ["test-001"],
                                 "requires_human_approval": False}],
        "autonomous_execution": True,
    }
    result = analyzer.analyze(samples.injection(), SETTINGS,
                              provider=provider(lambda r: completion(obedient)))
    assert result.jenkins_result == "FAILURE"
    assert result.analysis_status == "unavailable"
    assert result.failure_category != "none"
    assert result.grounding.status == "rejected"
    assert result.recommended_actions == []
    assert result.autonomous_execution is False
    assert [f.evidence_reference for f in result.untrusted_content] == ["test-001"]


def test_llm_inventing_a_failure_on_a_green_build_is_rejected():
    result = analyzer.analyze(samples.success(), SETTINGS,
                              provider=provider(lambda r: completion(GOOD_DRAFT)))
    assert result.jenkins_result == "SUCCESS"
    assert result.analysis_status == "unavailable"
    assert result.grounding.status == "rejected"
