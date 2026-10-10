"""Mock-mode analysis: classification, grounding, honesty about missing evidence."""

import pytest

from ai import analyzer
from ai.config import Settings
from ai.grounding import detect_untrusted_instructions, validate_draft
from ai.schemas import AnalysisDraft, AnalysisResult, DraftAction, ObservedFact, RiskAssessment
from ai.tests import samples

SETTINGS = Settings(provider="mock")


def analyze(package):
    return analyzer.analyze(package, SETTINGS)


def comparable(result: AnalysisResult) -> dict:
    data = result.model_dump()
    data.pop("created_at")
    data["provider"].pop("duration_ms")
    return data


# ---------------------------------------------------------------- classification


@pytest.mark.parametrize(
    ("builder", "category", "risk"),
    [
        (samples.success, "none", "low"),
        (samples.test_failure, "unit_test_failure", "medium"),
        (samples.security_finding, "security_finding", "high"),
        (samples.terraform_failure, "terraform_validation_failure", "low"),
    ],
)
def test_classification_and_risk(builder, category, risk):
    result = analyze(builder())
    assert result.analysis_status == "completed"
    assert result.failure_category == category
    assert result.risk_assessment.level == risk
    assert result.grounding.status == "verified"
    assert result.autonomous_execution is False


def test_success_invents_no_risk_or_remediation():
    result = analyze(samples.success())
    assert result.jenkins_result == "SUCCESS"
    assert result.recommended_actions == []
    assert result.root_cause_hypothesis is None
    assert result.risk_assessment.observed_severity == "none"


def test_unit_test_failure_identifies_the_failed_test():
    result = analyze(samples.test_failure())
    facts = " ".join(f.fact for f in result.observed_facts)
    assert "test_bulk_discount_applies_at_threshold" in facts
    assert "10000" in result.root_cause_hypothesis.description
    assert result.root_cause_hypothesis.confidence == "medium"
    assert result.recommended_actions and all(
        a.requires_human_approval for a in result.recommended_actions
    )


def test_security_finding_separates_observed_severity_from_estimated_risk():
    result = analyze(samples.security_finding())
    assert result.risk_assessment.observed_severity == "high"
    assert "estimated" in result.risk_assessment.reason
    assert "not established" in result.risk_assessment.reason
    assert any("B602" in f.fact for f in result.observed_facts)


def test_terraform_failure_suggests_correction_and_states_no_deployment():
    result = analyze(samples.terraform_failure())
    actions = " ".join(a.action for a in result.recommended_actions)
    assert "cidr_block" in actions
    assert "subnet_cidr_block" in actions
    assert any("No plan or apply" in text for text in result.limitations)


def test_classification_does_not_depend_on_the_scenario_label():
    honest = samples.make_package("FAILURE", ["unit-tests"], items=samples.TEST_FAILURE,
                                  scenario="test-failure")
    mislabelled = samples.make_package("FAILURE", ["unit-tests"], items=samples.TEST_FAILURE,
                                       scenario="success")
    assert comparable(analyze(honest)) == comparable(analyze(mislabelled))


def test_mock_mode_is_deterministic():
    for builder in (samples.success, samples.test_failure, samples.security_finding,
                    samples.terraform_failure, samples.injection):
        assert comparable(analyze(builder())) == comparable(analyze(builder()))


def test_failed_build_is_never_reported_as_passed():
    for builder in (samples.test_failure, samples.security_finding, samples.terraform_failure):
        result = analyze(builder())
        assert result.jenkins_result == "FAILURE"
        assert result.failure_category != "none"


# ---------------------------------------------------------------- grounding


def test_every_claim_cites_existing_evidence():
    for builder in (samples.success, samples.test_failure, samples.security_finding,
                    samples.terraform_failure):
        package = builder()
        result = analyze(package)
        known = package.item_ids()
        cited = [f.evidence_reference for f in result.observed_facts]
        cited += [r for a in result.recommended_actions for r in a.evidence_references]
        if result.root_cause_hypothesis:
            cited += result.root_cause_hypothesis.evidence_references
        assert cited and set(cited) <= known
        if result.jenkins_result != "SUCCESS":
            assert all(a.evidence_references for a in result.recommended_actions)


def _draft(**overrides) -> AnalysisDraft:
    values = {
        "failure_category": "unit_test_failure",
        "summary": "A unit test failed.",
        "observed_facts": [ObservedFact(fact="A test failed.", evidence_reference="test-001")],
        "risk_assessment": RiskAssessment(level="medium", reason="Blocks delivery."),
    }
    values.update(overrides)
    return AnalysisDraft(**values)


def test_unsupported_claims_are_removed_and_flagged():
    draft = _draft(
        observed_facts=[
            ObservedFact(fact="A test failed.", evidence_reference="test-001"),
            ObservedFact(fact="The database was down.", evidence_reference="db-log-404"),
        ],
        recommended_actions=[
            DraftAction(action="Restart the database.", rationale="It was down.",
                        evidence_references=["db-log-404"]),
        ],
    )
    cleaned, report = validate_draft(draft, samples.test_failure())
    assert report.status == "flagged"
    assert [f.evidence_reference for f in cleaned.observed_facts] == ["test-001"]
    assert cleaned.recommended_actions == []
    assert any("db-log-404" in issue for issue in report.issues)


def test_draft_with_only_fabricated_evidence_is_rejected():
    draft = _draft(observed_facts=[ObservedFact(fact="Made up.", evidence_reference="nope-1")])
    _, report = validate_draft(draft, samples.test_failure())
    assert report.status == "rejected"


def test_draft_contradicting_jenkins_is_rejected():
    passed = _draft(failure_category="none", observed_facts=[])
    assert validate_draft(passed, samples.test_failure())[1].status == "rejected"
    invented = _draft()
    assert validate_draft(invented, samples.success())[1].status == "rejected"
    wrong_stage = _draft(failure_category="security_finding")
    assert validate_draft(wrong_stage, samples.test_failure())[1].status == "rejected"


def test_autonomy_and_missing_approval_are_overridden_and_flagged():
    draft = _draft(
        autonomous_execution=True,
        recommended_actions=[
            DraftAction(action="Fix it.", rationale="Because.", evidence_references=["test-001"],
                        requires_human_approval=False),
        ],
    )
    cleaned, report = validate_draft(draft, samples.test_failure())
    assert report.status == "flagged"
    assert cleaned.autonomous_execution is False
    assert cleaned.recommended_actions[0].requires_human_approval is True


# ---------------------------------------------------------------- missing evidence


def test_failure_without_supporting_evidence_is_inconclusive():
    package = samples.make_package("FAILURE", failed=["unit-tests"], items=[])
    result = analyze(package)
    assert result.analysis_status == "inconclusive"
    assert result.failure_category == "unknown"
    assert result.root_cause_hypothesis is None
    assert result.recommended_actions == []
    assert result.missing_evidence


def test_failed_result_without_failed_check_is_inconclusive():
    result = analyze(samples.make_package("FAILURE", items=samples.SUMMARIES))
    assert result.analysis_status == "inconclusive"
    assert result.missing_evidence


def test_unknown_failure_type_uses_log_only_and_offers_no_hypothesis():
    items = [samples.item("log-dependency-install", "log_excerpt", "dependency-install",
                          "Last 2 log lines", "ERROR: No matching distribution", exit_code=1)]
    package = samples.make_package(
        "FAILURE", failed=["dependency-install"],
        not_run=["static-checks", "unit-tests", "bandit", "pip-audit", "gitleaks"], items=items)
    result = analyze(package)
    assert result.failure_category == "dependency_failure"
    assert result.root_cause_hypothesis is None
    assert [a.evidence_references for a in result.recommended_actions] == [
        ["log-dependency-install"]]


def test_multiple_failures_are_disclosed():
    package = samples.make_package(
        "FAILURE", failed=["unit-tests", "terraform-validate"],
        items=samples.TEST_FAILURE + samples.TERRAFORM_FAILURE[1:])
    result = analyze(package)
    assert result.failure_category == "unit_test_failure"
    assert any("terraform_validation_failure" in text for text in result.limitations)


# ---------------------------------------------------------------- prompt injection


def test_hostile_text_in_evidence_is_flagged_and_not_followed():
    package = samples.injection()
    assert [f.evidence_reference for f in detect_untrusted_instructions(package)] == ["test-001"]
    result = analyze(package)
    assert result.jenkins_result == "FAILURE"
    assert result.failure_category == "unit_test_failure"
    assert result.autonomous_execution is False
    assert [f.evidence_reference for f in result.untrusted_content] == ["test-001"]
    assert any("untrusted data" in text for text in result.limitations)
    stated = result.summary + " ".join(f.fact for f in result.observed_facts)
    assert "Ignore all previous instructions" not in stated
    assert all(a.requires_human_approval for a in result.recommended_actions)


def test_ordinary_evidence_is_not_flagged_as_hostile():
    for builder in (samples.success, samples.test_failure, samples.security_finding,
                    samples.terraform_failure):
        assert detect_untrusted_instructions(builder()) == []
