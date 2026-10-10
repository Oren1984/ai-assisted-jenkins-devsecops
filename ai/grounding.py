"""Grounding checks applied to every provider draft, mock or LLM.

A draft is only a proposal. This module decides which of its claims survive:
claims must cite evidence that exists in the submitted package, and the draft may
not contradict the result Jenkins reported.
"""

import re

from ai.schemas import AnalysisDraft, EvidencePackage, GroundingReport, UntrustedContentFlag

# Which pipeline check backs which failure category.
CHECK_CATEGORY = {
    "source-preparation": "pipeline_error",
    "dependency-install": "dependency_failure",
    "static-checks": "static_check_failure",
    "unit-tests": "unit_test_failure",
    "bandit": "security_finding",
    "pip-audit": "security_finding",
    "gitleaks": "security_finding",
    "terraform-fmt": "terraform_validation_failure",
    "terraform-init": "terraform_validation_failure",
    "terraform-validate": "terraform_validation_failure",
}

_INSTRUCTION_PATTERNS = [
    re.compile(
        r"(?i)\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}"
        r"\b(?:instructions?|prompts?|rules|guidelines)\b"
    ),
    re.compile(r"(?i)\byou are now\b|\bnew instructions?\b|\bsystem prompt\b"),
    re.compile(r"(?im)^\s*(?:system|assistant|developer)\s*:"),
    re.compile(
        r"(?i)\b(?:mark|report|treat|classify|declare|set)\b[^.\n]{0,40}"
        r"\b(?:build|pipeline|job|tests?|result|status)\b[^.\n]{0,30}"
        r"\b(?:passed|passing|success(?:ful)?|green)\b"
    ),
    re.compile(r"(?i)\bautonomous_execution\b|\brequires_human_approval\b"),
    re.compile(r"(?i)\b(?:without|skip|bypass)\b[^.\n]{0,30}\b(?:human|approval|review)\b"),
]


def looks_like_instructions(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INSTRUCTION_PATTERNS)


def detect_untrusted_instructions(package: EvidencePackage) -> list[UntrustedContentFlag]:
    """Find evidence items whose text reads like instructions aimed at the analyst."""
    flags = []
    for item in package.items:
        attributes = " ".join(str(value) for value in item.attributes.values())
        if looks_like_instructions(f"{item.title}\n{attributes}\n{item.content}"):
            flags.append(
                UntrustedContentFlag(
                    evidence_reference=item.id,
                    reason="Contains instruction-like text. Treated as data and not followed.",
                )
            )
    return flags


def failed_checks(package: EvidencePackage) -> list[str]:
    return [check.id for check in package.checks() if check.status == "failed"]


def validate_draft(
    draft: AnalysisDraft, package: EvidencePackage
) -> tuple[AnalysisDraft, GroundingReport]:
    """Return the draft with unsupported claims removed, and a report of what was found."""
    known = package.item_ids()
    result = package.build.result
    failed = failed_checks(package)
    issues: list[str] = []
    rejections: list[str] = []
    draft = draft.model_copy(deep=True)

    # 1. The draft may not contradict Jenkins.
    if not draft.inconclusive:
        if result != "SUCCESS" and draft.failure_category == "none":
            rejections.append(
                f"Draft reports no failure but Jenkins reported {result}. Jenkins is authoritative."
            )
        if result == "SUCCESS" and draft.failure_category != "none":
            rejections.append(
                f"Draft reports '{draft.failure_category}' but Jenkins reported SUCCESS "
                "and no check failed."
            )
        backed = {CHECK_CATEGORY.get(check) for check in failed}
        if draft.failure_category in set(CHECK_CATEGORY.values()) and (
            draft.failure_category not in backed
        ):
            rejections.append(
                f"Draft category '{draft.failure_category}' is not backed by any failed check."
            )

    # 2. Non-autonomy is not negotiable.
    if draft.autonomous_execution:
        issues.append("Draft claimed autonomous execution. Forced to false.")
        draft.autonomous_execution = False
    for action in draft.recommended_actions:
        if not action.requires_human_approval:
            issues.append(
                "Draft marked an action as not needing human approval. Approval was enforced."
            )
            action.requires_human_approval = True

    # 3. Every claim must cite evidence that exists.
    facts = []
    for fact in draft.observed_facts:
        if fact.evidence_reference in known:
            facts.append(fact)
        else:
            issues.append(
                f"Removed unsupported fact citing unknown evidence '{fact.evidence_reference}'."
            )
    draft.observed_facts = facts

    if draft.root_cause_hypothesis is not None:
        refs = [ref for ref in draft.root_cause_hypothesis.evidence_references if ref in known]
        if not refs:
            issues.append("Removed root-cause hypothesis: it cited no existing evidence.")
            draft.root_cause_hypothesis = None
        else:
            if len(refs) != len(draft.root_cause_hypothesis.evidence_references):
                issues.append("Removed unknown evidence references from the hypothesis.")
            draft.root_cause_hypothesis.evidence_references = refs

    actions = []
    for action in draft.recommended_actions:
        refs = [ref for ref in action.evidence_references if ref in known]
        if result == "SUCCESS":
            issues.append("Removed a remediation recommended for a successful build.")
        elif not refs:
            issues.append("Removed a recommended action that cited no existing evidence.")
        else:
            action.evidence_references = refs
            actions.append(action)
    draft.recommended_actions = actions

    if result != "SUCCESS" and not draft.inconclusive and not draft.observed_facts:
        rejections.append("No observed fact in the draft is supported by the evidence package.")

    if rejections:
        return draft, GroundingReport(status="rejected", issues=rejections + issues)
    return draft, GroundingReport(status="flagged" if issues else "verified", issues=issues)
