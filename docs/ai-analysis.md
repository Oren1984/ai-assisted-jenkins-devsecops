# AI analysis

## Evidence package (input)

Produced by `ci/package_evidence.py`, validated by `EvidencePackage` in `ai/schemas.py`.

```json
{
  "schema_version": "1",
  "build": {"id": "devsecops-pipeline-3", "result": "FAILURE", "number": 3, "...": "..."},
  "stages": [
    {"name": "Unit Tests", "status": "failed",
     "checks": [{"id": "unit-tests", "status": "failed", "exit_code": 1, "duration_seconds": 2.3}]}
  ],
  "items": [
    {"id": "test-001", "kind": "test_failure", "check": "unit-tests",
     "title": "Test failure: app.tests.test_pricing::test_bulk_discount_applies_at_threshold",
     "content": "... assert 10000 == 9500 ...",
     "attributes": {"file": "app/tests/test_pricing.py", "message": "assert 10000 == 9500"}}
  ],
  "notes": ["Demo scenario overlay applied to the workspace copy: app/pricing.py (replaced)"]
}
```

Item kinds: `test_summary`, `test_failure`, `security_summary`, `security_finding`,
`terraform_summary`, `terraform_diagnostic`, `log_excerpt`. Each item has an `id`; those
ids are the only things an analysis may cite.

Bounds: at most 60 items, 10 per report, 3,500 characters per item, the last 60 lines of
a failed check's log. The service rejects more than 80 items, items over 4,000
characters, unknown fields, and request bodies over 512 KB.

Redaction happens twice with the same code (`ai/redaction.py`): once in Jenkins before
the package leaves the agent, and again in the service on receipt. It covers key/value
assignments with secret-like names, bearer and basic auth headers, AWS key ids, GitHub
and `sk-` style tokens, JWTs, URL credentials and PEM private keys.

## Analysis flow

```
evidence → redact → flag instruction-like text → provider draft → grounding checks → result
```

A provider (mock or LLM) only authors a *draft*. The engine (`ai/analyzer.py`) sets the
fields a provider must not control: the build id, the Jenkins result,
`requires_human_approval: true` and `autonomous_execution: false`.

## Output schema

`AnalysisResult` in `ai/schemas.py`. It keeps these apart:

| Field | Meaning |
|---|---|
| `jenkins_result` | Copied from the evidence. Never authored by a provider. |
| `observed_facts[]` | What an evidence item shows. Each cites one `evidence_reference`. |
| `root_cause_hypothesis` | A possible cause, with `confidence` and its evidence. Not a fact. |
| `risk_assessment.observed_severity` | What the tool itself reported. |
| `risk_assessment.level` | Estimated operational risk. An interpretation. |
| `recommended_actions[]` | Bounded next steps, each with rationale and evidence. |
| `limitations[]`, `missing_evidence[]` | What the analysis could not establish. |
| `untrusted_content[]` | Evidence items containing instruction-like text. |
| `grounding` | `verified`, `flagged` or `rejected`, with the issues found. |
| `provider` | Mode, model, duration, attempts, token usage, whether fallback was used. |
| `analysis_status` | `completed`, `inconclusive` or `unavailable`. |

## Grounding rules

Applied to every draft in `ai/grounding.py`:

1. **Jenkins is authoritative.** A draft that reports no failure for a failed build,
   reports a failure for a successful build, or names a failure category that no failed
   check backs is rejected whole.
2. **Claims need evidence.** Facts, hypotheses and actions citing ids that are not in the
   package are removed and listed as grounding issues. A failed build whose draft has no
   supported fact left is rejected.
3. **No remediation for a green build.** Recommendations on a successful build are removed.
4. **Non-autonomy is enforced, not requested.** A draft claiming autonomous execution or
   waiving human approval is overridden and flagged.

A rejected draft never reaches the reviewer. The result is `unavailable`, or the
deterministic analysis if fallback is enabled.

## Prompt injection

Logs, test messages, file names and scanner output are attacker-controllable. Defences:

- Instruction-like text in evidence is detected by pattern and reported in
  `untrusted_content`. Detection is a heuristic and can be evaded. It is a signal for
  the reviewer, not the defence.
- The defence is structural. The model has no tools, the fields that matter are set by
  the engine, and the grounding rules above reject an answer that obeys the injected
  text. `test_llm_that_obeys_injected_instructions_is_rejected` simulates a model that
  follows the hostile log completely and checks that the build stays failed, nothing is
  recommended and nothing is marked autonomous.
- The LLM prompt puts evidence in a delimited block and tells the model it is data.

## Mock mode and real LLM mode

| | Mock (`AI_PROVIDER=mock`, default) | LLM (`AI_PROVIDER=llm`) |
|---|---|---|
| Engine | Rules in `ai/rules.py` over the actual evidence | Any OpenAI-compatible `/chat/completions` endpoint |
| Network | None | One HTTPS call per analysis |
| Output | Deterministic | Varies. `temperature: 0`, JSON response format |
| Knowledge | A small table of Bandit rules and Terraform diagnostics | The model's |
| Grounding checks | Same | Same |

LLM settings: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS` (30),
`LLM_MAX_RETRIES` (2, on timeouts, 408, 429 and 5xx). The prompt is capped at about
24,000 characters of evidence and omits the scenario label.

If the LLM fails or its answer is rejected:

- `AI_FALLBACK_TO_MOCK=false` (default): the result is `unavailable` with the reason.
- `AI_FALLBACK_TO_MOCK=true`: the rule-based analysis is returned with
  `provider.fallback_used: true`, the reason, and a limitation saying so.

Either way the Jenkins result is untouched.

**The LLM path has not been run against a real model.** No API credentials were
available. It is tested against a simulated endpoint only (`ai/tests/test_llm_provider.py`).

## Confidence and limits

- Mock-mode confidence is assigned by rule, not measured. `medium` for a parsed
  assertion mismatch means "the evidence shows a mismatch, not which side is wrong".
- The engine never reads source code or history. Every analysis says so.
- The mock recognises five Bandit rules and three Terraform diagnostics by name.
  Anything else gets a generic, log-only analysis without a hypothesis.
- When several checks fail, the first in pipeline order is analysed and the others are
  disclosed as a limitation.
- If the evidence does not support a conclusion, the result is `inconclusive` and lists
  what is missing.
