"""Analysis engine: redact, ask a provider for a draft, ground it, and finalise.

Fields that must not be influenced by a provider (build id, Jenkins result, human
approval, non-autonomy) are set here, after the draft has been validated.
"""

import logging
import time
from datetime import UTC, datetime

from ai.config import Settings
from ai.grounding import detect_untrusted_instructions, validate_draft
from ai.providers import MockProvider, ProviderError, get_provider
from ai.redaction import redact_value
from ai.schemas import (
    AnalysisDraft,
    AnalysisResult,
    EvidencePackage,
    GroundingReport,
    ProviderInfo,
    RecommendedAction,
    UntrustedContentFlag,
)

log = logging.getLogger("ai.analyzer")


def redact_package(package: EvidencePackage) -> EvidencePackage:
    """Second redaction pass. Jenkins redacts before sending, but is not trusted to."""
    data = package.model_dump()
    data["items"] = redact_value(data["items"])
    data["notes"] = redact_value(data["notes"])
    return EvidencePackage.model_validate(data)


def analyze(package: EvidencePackage, settings: Settings, provider=None) -> AnalysisResult:
    started = time.monotonic()
    package = redact_package(package)
    untrusted = detect_untrusted_instructions(package)
    provider = provider or get_provider(settings)
    info = ProviderInfo(mode=provider.mode, name=provider.name, model=provider.model)

    draft, report, failure = _run(provider, package, info)
    if failure and provider.mode != "mock" and settings.fallback_to_mock:
        info.fallback_used = True
        info.fallback_reason = failure
        draft, report, mock_failure = _run(MockProvider(), package, info)
        failure = mock_failure

    info.duration_ms = int((time.monotonic() - started) * 1000)
    if failure:
        result = _unavailable(package, failure, report, info, untrusted)
    else:
        result = _finalise(package, draft, report, info, untrusted)
    log.info(
        "analysis build=%s jenkins_result=%s provider=%s fallback=%s status=%s category=%s "
        "grounding=%s duration_ms=%d tokens=%s",
        result.build_id,
        result.jenkins_result,
        info.name,
        info.fallback_used,
        result.analysis_status,
        result.failure_category,
        result.grounding.status,
        info.duration_ms,
        info.token_usage,
    )
    return result


def _run(provider, package: EvidencePackage, info: ProviderInfo):
    """Return (draft, grounding report, failure reason or None)."""
    try:
        response = provider.analyze(package)
    except ProviderError as exc:
        return None, GroundingReport(status="not_applicable"), f"Provider error: {exc}"
    info.attempts += response.attempts
    if response.token_usage:
        info.token_usage = response.token_usage
    draft, report = validate_draft(response.draft, package)
    if report.status == "rejected":
        return None, report, "Draft rejected by grounding checks: " + " ".join(report.issues)
    return draft, report, None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _unavailable(
    package: EvidencePackage,
    reason: str,
    report: GroundingReport,
    info: ProviderInfo,
    untrusted: list[UntrustedContentFlag],
) -> AnalysisResult:
    return AnalysisResult(
        build_id=package.build.id,
        jenkins_result=package.build.result,
        analysis_status="unavailable",
        failure_category="unknown",
        summary=f"AI analysis is unavailable. The Jenkins result ({package.build.result}) "
        "stands unchanged and needs manual investigation.",
        limitations=[reason[:1900]],
        untrusted_content=untrusted,
        grounding=report,
        provider=info,
        created_at=_now(),
    )


def _finalise(
    package: EvidencePackage,
    draft: AnalysisDraft,
    report: GroundingReport,
    info: ProviderInfo,
    untrusted: list[UntrustedContentFlag],
) -> AnalysisResult:
    limitations = list(draft.limitations)
    if untrusted:
        limitations.append(
            "Evidence item(s) "
            + ", ".join(flag.evidence_reference for flag in untrusted)
            + " contain instruction-like text. It was treated as untrusted data and not followed."
        )
    if info.fallback_used:
        limitations.append(
            "The configured LLM did not produce a usable analysis. This result comes from "
            "the deterministic rule-based fallback."
        )
    return AnalysisResult(
        build_id=package.build.id,
        jenkins_result=package.build.result,
        analysis_status="inconclusive" if draft.inconclusive else "completed",
        failure_category=draft.failure_category,
        summary=draft.summary,
        observed_facts=draft.observed_facts,
        root_cause_hypothesis=draft.root_cause_hypothesis,
        risk_assessment=draft.risk_assessment,
        recommended_actions=[
            RecommendedAction(
                id=f"rec-{index}",
                action=action.action,
                rationale=action.rationale,
                evidence_references=action.evidence_references,
            )
            for index, action in enumerate(draft.recommended_actions, start=1)
        ],
        limitations=limitations,
        missing_evidence=draft.missing_evidence,
        untrusted_content=untrusted,
        grounding=report,
        provider=info,
        created_at=_now(),
    )
