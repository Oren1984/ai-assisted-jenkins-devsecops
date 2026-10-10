"""Contracts between Jenkins, the analysis engine, and the review UI."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]
ShortText = Annotated[str, StringConstraints(max_length=300)]
LongText = Annotated[str, StringConstraints(max_length=2000)]
# Rendered as a link in the review UI, so only http(s) is accepted.
HttpUrlText = Annotated[str, StringConstraints(max_length=300, pattern=r"^(https?://\S*)?$")]

# ---------------------------------------------------------------- evidence (input)

CheckStatus = Literal["passed", "failed", "not_run"]
JenkinsResult = Literal["SUCCESS", "UNSTABLE", "FAILURE", "ABORTED", "NOT_BUILT"]
EvidenceKind = Literal[
    "build_metadata",
    "log_excerpt",
    "test_summary",
    "test_failure",
    "security_summary",
    "security_finding",
    "terraform_summary",
    "terraform_diagnostic",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(Strict):
    id: Identifier
    status: CheckStatus
    exit_code: int | None = None
    duration_seconds: float | None = Field(default=None, ge=0)


class Stage(Strict):
    name: ShortText
    status: CheckStatus
    checks: list[Check] = Field(default_factory=list, max_length=20)


class Build(Strict):
    id: Identifier
    job: ShortText
    number: int = Field(ge=0)
    url: HttpUrlText = ""
    result: JenkinsResult
    started_at: ShortText = ""
    duration_seconds: float | None = Field(default=None, ge=0)
    # Display label only. The analysis engine never reads it.
    scenario: ShortText = ""


class EvidenceItem(Strict):
    id: Identifier
    kind: EvidenceKind
    check: Identifier | None = None
    title: ShortText
    content: Annotated[str, StringConstraints(max_length=4000)] = ""
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class EvidencePackage(Strict):
    schema_version: Literal["1"]
    build: Build
    stages: list[Stage] = Field(max_length=30)
    items: list[EvidenceItem] = Field(max_length=80)
    notes: list[ShortText] = Field(default_factory=list, max_length=20)

    def checks(self) -> list[Check]:
        return [check for stage in self.stages for check in stage.checks]

    def item_ids(self) -> set[str]:
        return {item.id for item in self.items}


# ---------------------------------------------------------------- analysis (output)

FailureCategory = Literal[
    "none",
    "dependency_failure",
    "static_check_failure",
    "unit_test_failure",
    "security_finding",
    "terraform_validation_failure",
    "pipeline_error",
    "unknown",
]
Level = Literal["low", "medium", "high"]
Severity = Literal["none", "low", "medium", "high", "critical", "unknown"]


class ObservedFact(BaseModel):
    fact: LongText
    evidence_reference: str


class RootCauseHypothesis(BaseModel):
    description: LongText
    confidence: Level
    evidence_references: list[str] = Field(default_factory=list, max_length=20)


class RiskAssessment(BaseModel):
    # What the tooling itself reported, kept apart from the interpretation below.
    observed_severity: Severity = "unknown"
    # Estimated operational risk: an interpretation, not a measurement.
    level: Level
    reason: LongText


class DraftAction(BaseModel):
    action: LongText
    rationale: LongText
    evidence_references: list[str] = Field(default_factory=list, max_length=20)
    # Providers may send this; the engine forces it to True and flags any other value.
    requires_human_approval: bool = True


class AnalysisDraft(BaseModel):
    """The only fields a provider (mock or LLM) is allowed to author."""

    failure_category: FailureCategory
    summary: LongText
    observed_facts: list[ObservedFact] = Field(default_factory=list, max_length=20)
    root_cause_hypothesis: RootCauseHypothesis | None = None
    risk_assessment: RiskAssessment
    recommended_actions: list[DraftAction] = Field(default_factory=list, max_length=8)
    limitations: list[LongText] = Field(default_factory=list, max_length=10)
    missing_evidence: list[LongText] = Field(default_factory=list, max_length=10)
    inconclusive: bool = False
    autonomous_execution: bool = False


class RecommendedAction(BaseModel):
    id: Identifier
    action: LongText
    rationale: LongText
    evidence_references: list[str]
    requires_human_approval: Literal[True] = True


class UntrustedContentFlag(BaseModel):
    evidence_reference: str
    reason: ShortText


class GroundingReport(BaseModel):
    status: Literal["verified", "flagged", "rejected", "not_applicable"]
    issues: list[LongText] = Field(default_factory=list)


class ProviderInfo(BaseModel):
    mode: Literal["mock", "llm"]
    name: ShortText
    model: ShortText | None = None
    fallback_used: bool = False
    fallback_reason: LongText | None = None
    duration_ms: int = 0
    attempts: int = 0
    token_usage: dict[str, int] | None = None


class AnalysisResult(BaseModel):
    build_id: str
    # Copied from the Jenkins evidence, never authored by a provider.
    jenkins_result: JenkinsResult
    analysis_status: Literal["completed", "inconclusive", "unavailable"]
    failure_category: FailureCategory
    summary: LongText
    observed_facts: list[ObservedFact] = Field(default_factory=list)
    root_cause_hypothesis: RootCauseHypothesis | None = None
    risk_assessment: RiskAssessment | None = None
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    limitations: list[LongText] = Field(default_factory=list)
    missing_evidence: list[LongText] = Field(default_factory=list)
    untrusted_content: list[UntrustedContentFlag] = Field(default_factory=list)
    grounding: GroundingReport
    provider: ProviderInfo
    autonomous_execution: Literal[False] = False
    created_at: str


# ---------------------------------------------------------------- human review


class ReviewRequest(Strict):
    decision: Literal["accept", "reject"]
    reviewer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class AuditEvent(BaseModel):
    id: int
    created_at: str
    event_type: Literal["analysis_recorded", "review_recorded"]
    build_id: str
    recommendation_id: str | None = None
    decision: str | None = None
    reviewer: str | None = None
    comment: str | None = None
    # Constant: recording a decision never triggers an action.
    action_executed: Literal[False] = False


def to_jsonable(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
