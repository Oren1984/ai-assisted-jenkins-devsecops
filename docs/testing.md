# Testing

Three levels, kept separate. Only the third involves the real Jenkins.

| Level | Where it runs | What it proves | Count | Result |
|---|---|---|---|---|
| Unit | `tests` container (no network) and again inside every Jenkins build | Application, analysis engine, grounding, LLM client logic, review API, CI helper scripts | 122 | passed |
| Repository integration | Host, needs the `docker` CLI | Compose configuration, Jenkinsfile structure, no hardcoded secrets, configured boundaries | 27 | passed |
| End-to-end | Host, against the running stack | Real Jenkins builds, real evidence, analysis, review, isolation | 22 | passed |

Last full run: clean stack (`docker compose down -v` then `up --build -d`), Windows 11,
Docker Engine 29.8, Jenkins 2.580.1 LTS, AI provider `mock`. No test is skipped, disabled
or marked expected-to-fail. One unit test skips itself inside the Jenkins workspace,
where there is no `Jenkinsfile` to compare against; it runs in the container.

## Commands

```bash
# Unit tests, in a container
docker compose --profile tools run --rm tests

# Repository integration tests (stdlib + pytest on the host)
python -m venv .venv && .venv/bin/pip install pytest      # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest tests

# End-to-end: needs the stack up. Runs 7 real Jenkins builds, about 5 minutes
docker compose up --build -d
.venv/bin/python -m pytest tests/test_e2e.py -m e2e -v

# Lint
ruff check .
```

Unit tests run in a container because the development host blocks unsigned native
Python extensions. The host-side suites use the standard library only.

## What is covered

**Application** (`app/tests`): health, readiness, the quote endpoint, invalid requests,
pricing rules.

**AI** (`ai/tests`)

- Classification and risk level for each failure type; determinism of mock mode
- Classification does not depend on the scenario label
- Every claim cites existing evidence; unsupported claims are removed and flagged
- Drafts contradicting Jenkins are rejected
- Missing evidence yields `inconclusive`, with no hypothesis and no recommendation
- LLM client against a simulated endpoint: valid answer, timeout with retries, 5xx
  recovery, 4xx without retry, malformed output, missing credentials, fallback
- Prompt injection: hostile text is flagged; a simulated model that obeys it is rejected

**Human review** (`ai/tests/test_service.py`): accept, reject, changed decision, audit
events, invalid requests, unknown build or recommendation, and that accepting changes
nothing except the audit trail.

**Security boundaries** (`ai/tests/test_boundaries.py`, `tests/test_repository.py`)

- The service's routes are exactly the nine expected; remediation paths return 404/405
- AI code imports nothing that starts processes and reads no Jenkins setting
- Redaction of seven secret shapes; no secret-shaped literal in the repository
- Ports bound to localhost; no Docker socket, privilege or cloud credential in Compose
- The Jenkinsfile has no retry, deployment, result override or direct Terraform call

**CI scripts** (`ci/tests`): the Terraform allowlist refuses 16 forms of prohibited
command, credential stripping, exit-code pass-through, and evidence packaging from
sample reports including missing ones.

**End-to-end** (`tests/test_e2e.py`)

- Containers healthy, Jenkins requires login, the controller has 0 executors and the
  agent is online, Jenkins itself validates the Jenkinsfile
- Each of the five scenarios: Jenkins result, failed check, evidence artifact, analysis
  category, grounding
- The test failure is confirmed through Jenkins' own test report API
- The evidence stored by the AI service matches the artifact Jenkins archived
- Recording decisions starts no build, leaves the result failed and leaves a hash of
  the source tree unchanged
- No container has Docker access; the agent cannot see `.env` or write to the source
- With the AI service stopped, a green build stays green and a red build stays red

## Scenario results

Builds 8 to 12 of the last run, started with `python scripts/demo.py run-all`.

| Build | Scenario | Jenkins | Failed check | AI category | Evidence cited |
|---|---|---|---|---|---|
| #8 | `success` | SUCCESS | none | `none` | 5 summary items |
| #9 | `test-failure` | FAILURE | `unit-tests` | `unit_test_failure` | `test-summary`, `test-001` |
| #10 | `security-finding` | FAILURE | `bandit` | `security_finding` | `bandit-summary`, `bandit-001`, `bandit-002` |
| #11 | `terraform-failure` | FAILURE | `terraform-validate` | `terraform_validation_failure` | `terraform-summary`, `tf-001` |
| #12 | `prompt-injection` | FAILURE | `unit-tests` | `unit_test_failure` | `test-summary`, `test-001` (flagged untrusted) |

Builds take about 20 seconds once dependencies are cached. Mock analysis takes under
10 ms per build.

For build #9, two decisions were recorded through the review UI (accept `rec-1`, reject
`rec-2`). Jenkins' last build number was 12 before and after.

## Reproduce

```bash
cp .env.example .env                      # set your own values
docker compose up --build -d
python scripts/demo.py status
python scripts/demo.py lint               # Jenkins validates the Jenkinsfile
python scripts/demo.py run test-failure   # prints the result and a review link
```

Open the printed review link, read the analysis, record a decision, and check the audit
table at the bottom of the page. In Jenkins, the build page shows the stages, the test
report and the archived `evidence/` artifacts.

Screenshots in `assets/` were captured from the running stack with
`scripts/capture_screenshots.mjs`, a Playwright page script.

## Not tested

- The LLM provider against a real model (no credentials available).
- Hosts other than Windows 11 with Docker Desktop.
- Load, concurrency, long-running operation, upgrades.
