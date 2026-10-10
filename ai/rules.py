"""Deterministic, rule-based analysis of an evidence package (mock mode).

Every statement is derived from an evidence item and cites it. Nothing here reads the
scenario label, so classification depends only on what Jenkins actually reported.
"""

import re

from ai.grounding import CHECK_CATEGORY, failed_checks, looks_like_instructions
from ai.schemas import (
    AnalysisDraft,
    DraftAction,
    EvidenceItem,
    EvidencePackage,
    ObservedFact,
    RiskAssessment,
    RootCauseHypothesis,
)

MAX_FACTS_PER_KIND = 5
NO_SOURCE_LIMITATION = (
    "The analysis is based only on the evidence package. Source code, change history and "
    "runtime behaviour were not inspected, so the root cause is not confirmed."
)

BANDIT_KNOWLEDGE = {
    "B602": (
        "A subprocess is started through a shell. If any part of the command comes from "
        "untrusted input, this allows command injection.",
        "Replace the shell invocation with an argument list (shell=False) and validate inputs.",
    ),
    "B324": (
        "A weak hash function (MD5/SHA1) is used. It is unsuitable for integrity or "
        "security purposes.",
        "Use SHA-256 or stronger, or mark the call usedforsecurity=False if it is not "
        "security-relevant.",
    ),
    "B105": (
        "A string literal looks like a hardcoded password.",
        "Move the value to a secret store or environment variable and rotate it if it was real.",
    ),
    "B608": (
        "A SQL statement is built with string formatting, which can allow SQL injection.",
        "Use parameterised queries.",
    ),
    "B301": (
        "pickle is used to deserialise data. Untrusted pickle data can execute code.",
        "Use a safe serialisation format such as JSON for untrusted data.",
    ),
}

_SEVERITY_ORDER = ["none", "low", "medium", "high", "critical"]
_ASSERT_COMPARE = re.compile(r"assert\s+(.{1,80}?)\s*(==|!=|<=|>=|<|>)\s*(.{1,80})")


def _items(package: EvidencePackage, kind: str, check: str | None = None) -> list[EvidenceItem]:
    return [
        item
        for item in package.items
        if item.kind == kind and (check is None or item.check == check)
    ]


def _first_line(text: str, limit: int = 240) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:limit]


def _log_facts(package: EvidencePackage, checks: list[str]) -> list[ObservedFact]:
    facts = []
    for item in _items(package, "log_excerpt"):
        if item.check in checks:
            facts.append(
                ObservedFact(
                    fact=f"Check '{item.check}' failed. Its log excerpt is attached.",
                    evidence_reference=item.id,
                )
            )
    return facts


def _inconclusive(package: EvidencePackage, reason: str, missing: list[str]) -> AnalysisDraft:
    failed = failed_checks(package)
    return AnalysisDraft(
        failure_category="unknown",
        inconclusive=True,
        summary=f"Inconclusive: {reason} Jenkins reported {package.build.result}.",
        observed_facts=_log_facts(package, failed),
        risk_assessment=RiskAssessment(
            observed_severity="unknown",
            level="medium",
            reason="The failure could not be characterised from the evidence, so its impact "
            "is unknown. Treat as unresolved.",
        ),
        missing_evidence=missing,
        limitations=["No root cause or remediation is proposed without supporting evidence."],
    )


def analyze(package: EvidencePackage) -> AnalysisDraft:
    result = package.build.result
    failed = failed_checks(package)

    if result == "SUCCESS" and not failed:
        return _success(package)
    if result == "SUCCESS":
        return _inconclusive(
            package,
            f"checks {failed} are recorded as failed although the build result is SUCCESS.",
            ["A consistent record of check outcomes."],
        )
    if not failed:
        return _inconclusive(
            package,
            "no failed check is recorded in the evidence package.",
            ["The outcome and log of the stage that caused the build result."],
        )

    primary = failed[0]
    category = CHECK_CATEGORY.get(primary)
    builders = {
        "unit_test_failure": _unit_tests,
        "security_finding": _security,
        "terraform_validation_failure": _terraform,
    }
    if category in builders:
        draft = builders[category](package, failed)
    elif category is not None:
        draft = _generic(package, primary, category)
    else:
        draft = None
    if draft is None:
        return _inconclusive(
            package,
            f"check '{primary}' failed but no report or log for it is in the evidence package.",
            [f"Report or log output for check '{primary}'."],
        )

    other = sorted({CHECK_CATEGORY.get(c, "unknown") for c in failed} - {category})
    if other:
        draft.observed_facts.extend(
            _log_facts(package, [c for c in failed if CHECK_CATEGORY.get(c) != category])
        )
        draft.limitations.append(
            "Other checks also failed (" + ", ".join(other) + "). This analysis covers the "
            "first failing check in pipeline order. The others need separate review."
        )
    return draft


def _success(package: EvidencePackage) -> AnalysisDraft:
    facts = []
    for kind in ("test_summary", "security_summary", "terraform_summary"):
        for item in _items(package, kind):
            facts.append(ObservedFact(fact=item.title, evidence_reference=item.id))
    passed = [c for c in package.checks() if c.status == "passed"]
    not_run = [c.id for c in package.checks() if c.status == "not_run"]
    limitations = [
        "Passing checks show that these specific checks passed. They do not prove the "
        "absence of defects or vulnerabilities."
    ]
    if not_run:
        limitations.append("Checks not run in this build: " + ", ".join(not_run) + ".")
    return AnalysisDraft(
        failure_category="none",
        summary=f"Jenkins reported SUCCESS. All {len(passed)} executed checks passed. "
        "No failure to investigate.",
        observed_facts=facts,
        risk_assessment=RiskAssessment(
            observed_severity="none",
            level="low",
            reason="No check failed and no finding was reported in this build.",
        ),
        limitations=limitations,
    )


def _unit_tests(package: EvidencePackage, failed: list[str]) -> AnalysisDraft | None:
    failures = _items(package, "test_failure")
    summary_items = _items(package, "test_summary")
    logs = [i for i in _items(package, "log_excerpt") if i.check == "unit-tests"]
    if not failures and not logs:
        return None

    facts = [ObservedFact(fact=i.title, evidence_reference=i.id) for i in summary_items]
    for item in failures[:MAX_FACTS_PER_KIND]:
        message = _first_line(str(item.attributes.get("message") or item.content))
        if looks_like_instructions(message):
            # Do not repeat hostile text as if it were a finding.
            message = "(message withheld: it contains instruction-like text, see the evidence)"
        facts.append(
            ObservedFact(
                fact=f"Test {item.attributes.get('name', item.title)} failed: {message}",
                evidence_reference=item.id,
            )
        )
    if not failures:
        facts.extend(_log_facts(package, ["unit-tests"]))
        return AnalysisDraft(
            failure_category="unit_test_failure",
            summary="The unit test stage failed, but no per-test report was produced.",
            observed_facts=facts,
            risk_assessment=RiskAssessment(
                observed_severity="unknown",
                level="medium",
                reason="Tests did not complete, so the tested behaviour is unverified.",
            ),
            recommended_actions=[
                DraftAction(
                    action="Read the test stage log to find why no test report was produced "
                    "(for example a collection or import error).",
                    rationale="Only the raw log is available for this failure.",
                    evidence_references=[logs[0].id],
                )
            ],
            missing_evidence=["JUnit test report for the unit test stage."],
            limitations=[NO_SOURCE_LIMITATION],
        )

    first = failures[0]
    text = f"{first.attributes.get('message', '')}\n{first.content}"
    refs = [i.id for i in failures[:MAX_FACTS_PER_KIND]]
    compare = _ASSERT_COMPARE.search(text)
    if first.attributes.get("type") == "error" or re.search(r"(Import|ModuleNotFound)Error", text):
        hypothesis = RootCauseHypothesis(
            description="The test raised an error instead of reaching its assertion. This "
            "may be a missing module, a broken import, or an unhandled exception in the "
            "code under test.",
            confidence="low",
            evidence_references=refs,
        )
    elif compare:
        hypothesis = RootCauseHypothesis(
            description=f"The assertion compares '{compare.group(1).strip()}' with "
            f"'{compare.group(3).strip()}' and they differ. Either the implementation "
            "exercised by this test changed its behaviour (a regression), or the expected "
            "value in the test is out of date.",
            confidence="medium",
            evidence_references=refs,
        )
    else:
        hypothesis = RootCauseHypothesis(
            description="The behaviour of the code under test does not match what the test "
            "expects. The evidence does not show which side is wrong.",
            confidence="low",
            evidence_references=refs,
        )

    counts = summary_items[0].attributes if summary_items else {}
    total = int(counts.get("tests") or 0)
    broken = int(counts.get("failures") or 0) + int(counts.get("errors") or 0) or len(failures)
    widespread = total > 0 and broken * 2 > total
    location = first.attributes.get("file") or first.attributes.get("classname") or "the test"
    return AnalysisDraft(
        failure_category="unit_test_failure",
        summary=f"{broken} unit test(s) failed in the Unit Tests stage"
        + (f" out of {total}." if total else "."),
        observed_facts=facts,
        root_cause_hypothesis=hypothesis,
        risk_assessment=RiskAssessment(
            observed_severity="unknown",
            level="high" if widespread else "medium",
            reason=(
                "More than half of the tests fail, which suggests a broad breakage. "
                if widespread
                else "A failing unit test blocks delivery validation. "
            )
            + "The behaviour covered by the failing test is unverified until it is resolved.",
        ),
        recommended_actions=[
            DraftAction(
                action=f"Reproduce locally by running the failing test "
                f"({first.attributes.get('name', 'see evidence')}) and inspect the values "
                "in the assertion.",
                rationale="The test report identifies the exact failing assertion, so a "
                "local run is the cheapest way to confirm the mismatch.",
                evidence_references=[first.id],
            ),
            DraftAction(
                action=f"Compare the expectation in {location} with the implementation and "
                "its recent changes, then decide whether the code or the test is wrong.",
                rationale="The evidence shows a mismatch but not which side is correct. "
                "That is an engineering judgement.",
                evidence_references=refs,
            ),
        ],
        limitations=[NO_SOURCE_LIMITATION],
    )


def _security(package: EvidencePackage, failed: list[str]) -> AnalysisDraft | None:
    findings = _items(package, "security_finding")
    if not findings:
        return None

    facts = [
        ObservedFact(fact=i.title, evidence_reference=i.id)
        for i in _items(package, "security_summary")
        if i.check in failed
    ]
    actions = []
    severities = []
    runtime_code = False
    explanations = []
    for item in findings[:MAX_FACTS_PER_KIND]:
        attrs = item.attributes
        tool = attrs.get("tool")
        if tool == "bandit":
            rule = str(attrs.get("rule_id"))
            severity = str(attrs.get("severity", "unknown")).lower()
            severities.append(severity)
            path = str(attrs.get("file", ""))
            runtime_code = runtime_code or not path.startswith(("ci/", "tests/"))
            facts.append(
                ObservedFact(
                    fact=f"Bandit reported {rule} ({attrs.get('rule_name')}) at "
                    f"{path}:{attrs.get('line')} with severity {severity.upper()} and "
                    f"confidence {str(attrs.get('confidence', 'unknown')).upper()}.",
                    evidence_reference=item.id,
                )
            )
            meaning, fix = BANDIT_KNOWLEDGE.get(
                rule,
                (
                    "Bandit flagged a pattern that is commonly associated with security defects.",
                    "Review the flagged code against the rule documentation.",
                ),
            )
            explanations.append(f"{rule}: {meaning}")
            actions.append(
                DraftAction(
                    action=f"Review {path}:{attrs.get('line')}. {fix}",
                    rationale=meaning,
                    evidence_references=[item.id],
                )
            )
        elif tool == "pip-audit":
            severities.append("unknown")
            runtime_code = True
            fix_versions = attrs.get("fix_versions") or "none listed"
            facts.append(
                ObservedFact(
                    fact=f"pip-audit reported advisory {attrs.get('advisory')} for "
                    f"{attrs.get('package')} {attrs.get('version')} "
                    f"(fix versions: {fix_versions}).",
                    evidence_reference=item.id,
                )
            )
            actions.append(
                DraftAction(
                    action=f"Evaluate upgrading {attrs.get('package')} to a fixed version "
                    f"({fix_versions}) and check whether the advisory affects how the "
                    "package is used here.",
                    rationale="A known advisory applies to the installed version.",
                    evidence_references=[item.id],
                )
            )
        elif tool == "gitleaks":
            severities.append("high")
            runtime_code = True
            facts.append(
                ObservedFact(
                    fact=f"Gitleaks rule {attrs.get('rule_id')} matched at "
                    f"{attrs.get('file')}:{attrs.get('line')}.",
                    evidence_reference=item.id,
                )
            )
            actions.append(
                DraftAction(
                    action=f"Check whether the value at {attrs.get('file')}:"
                    f"{attrs.get('line')} is a real credential. If it is, rotate it and "
                    "remove it from the source.",
                    rationale="A secret-detection rule matched. The scanner cannot tell a "
                    "live credential from a placeholder.",
                    evidence_references=[item.id],
                )
            )

    known = [s for s in severities if s in _SEVERITY_ORDER]
    observed = max(known, key=_SEVERITY_ORDER.index) if known else "unknown"
    if observed in ("high", "critical") and runtime_code:
        level = "high"
    elif observed == "low":
        level = "low"
    else:
        level = "medium"
    refs = [i.id for i in findings[:MAX_FACTS_PER_KIND]]
    return AnalysisDraft(
        failure_category="security_finding",
        summary=f"Security scanning reported {len(findings)} finding(s) and the security "
        "gate failed the build.",
        observed_facts=facts,
        root_cause_hypothesis=RootCauseHypothesis(
            description="The flagged code or dependency was introduced or changed without "
            "meeting the security gate. "
            + " ".join(explanations)
            + " Whether it is exploitable depends on context the scanner does not see.",
            confidence="medium",
            evidence_references=refs,
        ),
        risk_assessment=RiskAssessment(
            observed_severity=observed,
            level=level,
            reason=f"Scanner-reported severity is {observed.upper()}. The operational risk "
            f"is estimated as {level.upper()} from severity and file location only. "
            "Exploitability (whether untrusted input reaches this code, and where it is "
            "deployed) is not established by the evidence.",
        ),
        recommended_actions=actions[:4],
        limitations=[
            NO_SOURCE_LIMITATION,
            "Scanner findings can be false positives. A clean scan would not prove the "
            "application is secure.",
        ],
    )


def _terraform(package: EvidencePackage, failed: list[str]) -> AnalysisDraft | None:
    diagnostics = [
        i
        for i in _items(package, "terraform_diagnostic")
        if i.attributes.get("severity") == "error"
    ]
    logs = [i for i in _items(package, "log_excerpt") if i.check in failed]
    no_deploy = (
        "Only terraform fmt/validate run in this pipeline. No plan or apply is executed, so "
        "no infrastructure was created or changed. Passing validation would not prove that "
        "AWS infrastructure can be provisioned."
    )
    if not diagnostics:
        fmt_logs = [i for i in logs if i.check == "terraform-fmt"]
        if not fmt_logs:
            return None
        return AnalysisDraft(
            failure_category="terraform_validation_failure",
            summary="terraform fmt -check failed: at least one file is not in canonical format.",
            observed_facts=_log_facts(package, ["terraform-fmt"]),
            root_cause_hypothesis=RootCauseHypothesis(
                description="A Terraform file was edited without running terraform fmt.",
                confidence="high",
                evidence_references=[fmt_logs[0].id],
            ),
            risk_assessment=RiskAssessment(
                observed_severity="low",
                level="low",
                reason="Formatting does not change behaviour. " + no_deploy,
            ),
            recommended_actions=[
                DraftAction(
                    action="Run terraform fmt on the listed files and review the diff.",
                    rationale="The format check lists the files that differ.",
                    evidence_references=[fmt_logs[0].id],
                )
            ],
            limitations=[no_deploy],
        )

    facts = [ObservedFact(fact=i.title, evidence_reference=i.id) for i in _items(
        package, "terraform_summary")]
    actions = []
    causes = []
    for item in diagnostics[:MAX_FACTS_PER_KIND]:
        attrs = item.attributes
        summary = str(attrs.get("summary", ""))
        where = f"{attrs.get('file')}:{attrs.get('line')}"
        facts.append(
            ObservedFact(
                fact=f"terraform validate reported '{summary}' at {where}.",
                evidence_reference=item.id,
            )
        )
        hint = re.search(r'Did you mean "([^"]+)"\?', item.content)
        if "undeclared input variable" in summary:
            name = re.search(r'"([^"]+)"', item.content)
            variable = name.group(1) if name else "the referenced variable"
            causes.append(f"The configuration references variable {variable}, which is not "
                          "declared (a typo or a missing variable block).")
            fix = f"Declare variable {variable} or correct the reference at {where}."
        elif "Unsupported argument" in summary:
            causes.append("An argument name is not part of the resource schema"
                          + (f" (Terraform suggests '{hint.group(1)}')." if hint else "."))
            fix = (f"Rename the argument at {where} to '{hint.group(1)}' if that was intended."
                   if hint else f"Check the argument name at {where} against the provider schema.")
        elif "Missing required argument" in summary:
            causes.append("A required argument is absent from a block.")
            fix = f"Add the required argument named in the diagnostic at {where}."
        else:
            causes.append(f"Terraform rejected the configuration: {summary}.")
            fix = f"Correct the configuration at {where} as described by the diagnostic."
        actions.append(
            DraftAction(
                action=fix,
                rationale=f"Diagnostic from terraform validate: {summary}.",
                evidence_references=[item.id],
            )
        )
    return AnalysisDraft(
        failure_category="terraform_validation_failure",
        summary=f"terraform validate reported {len(diagnostics)} error(s). The Terraform "
        "configuration is invalid.",
        observed_facts=facts,
        root_cause_hypothesis=RootCauseHypothesis(
            description=" ".join(dict.fromkeys(causes)),
            confidence="high" if len(diagnostics) == 1 else "medium",
            evidence_references=[i.id for i in diagnostics[:MAX_FACTS_PER_KIND]],
        ),
        risk_assessment=RiskAssessment(
            observed_severity="unknown",
            level="low",
            reason="The invalid configuration blocks infrastructure-as-code delivery but "
            "has no operational effect. " + no_deploy,
        ),
        recommended_actions=actions[:4],
        limitations=[
            no_deploy,
            "The suggested corrections restore validity. Whether they match the intended "
            "design needs human confirmation.",
        ],
    )


def _generic(package: EvidencePackage, check: str, category: str) -> AnalysisDraft | None:
    logs = [i for i in _items(package, "log_excerpt") if i.check == check]
    if not logs:
        return None
    return AnalysisDraft(
        failure_category=category,
        summary=f"Check '{check}' failed. Only its log output is available.",
        observed_facts=_log_facts(package, [check]),
        risk_assessment=RiskAssessment(
            observed_severity="unknown",
            level="medium",
            reason="The pipeline cannot validate the change while this check fails.",
        ),
        recommended_actions=[
            DraftAction(
                action=f"Read the log excerpt for '{check}' and reproduce the command locally.",
                rationale="No structured report exists for this check, so the log is the "
                "only evidence.",
                evidence_references=[logs[0].id],
            )
        ],
        limitations=[
            NO_SOURCE_LIMITATION,
            "No rule-based root-cause hypothesis is available for this failure type.",
        ],
    )
