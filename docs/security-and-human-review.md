# Security and human review

Proportionate controls for a local proof of concept. This is not a hardened deployment.

## Trust boundaries

| Boundary | Trusted side | Untrusted side | Control |
|---|---|---|---|
| Host ↔ stack | Local user | Network | Every port bound to `127.0.0.1` |
| User ↔ Jenkins | Authenticated admin | Anonymous | Login required, no sign-up, no anonymous read |
| Controller ↔ build | Controller | Build workload | 0 executors on the controller; builds run on the agent only |
| Build ↔ host | Host | Build workload | No Docker socket, no Docker client, source mounted read-only, `.env` and `.git` not mounted |
| Jenkins ↔ AI | Pipeline | AI service | One-way push. The AI holds no Jenkins credentials |
| Evidence ↔ analysis | Engine | Log, test and scanner text | Schema validation, size limits, redaction, grounding checks |
| Analysis ↔ action | Human reviewer | AI output | Review records a decision only. Nothing executes |

## Controls

- **Jenkins authentication.** Local admin from `.env`, created by Configuration as Code.
- **Credentials.** The ingest token is a Jenkins secret-text credential, bound with
  `withCredentials` and masked in logs. The agent connects with a per-node secret, not a
  user account. `.env` is git-ignored and `.env.example` holds placeholders.
- **Ingest token.** `POST /api/analyses` requires it and fails closed if none is
  configured. It grants one thing: submitting evidence.
- **Input validation.** Pydantic models with `extra="forbid"`, bounded lengths and
  counts, a strict identifier pattern, and a 512 KB body limit.
- **Safe parsing.** JUnit XML is parsed with `defusedxml`. The UI builds the page with
  `textContent` only, so evidence text cannot become markup.
- **Redaction.** Twice, before sending and on receipt. Gitleaks runs with `--redact`,
  and the matched secret is never packaged.
- **Bounded LLM requests.** Capped prompt, timeout, limited retries, no tools.
- **Terraform allowlist.** The pipeline calls Terraform only through `ci/tf_safe.py`,
  which permits `fmt -check`, `init -backend=false`, `validate` and `version`, refuses
  everything else, and strips `AWS_*`, `TF_TOKEN_*`, `TF_VAR_*`, `ARM_*` and `GOOGLE_*`
  from the environment. The provider is baked into the agent image and Terraform is
  configured to use only that copy.
- **Containers.** The two Python services run as a non-root user on a read-only root
  filesystem.

## Human review

For each recommendation the UI shows the proposed action, why it was suggested, the
evidence it cites (click a reference to open the item), and the hypothesis confidence
and limitations. The reviewer enters a name, optionally a comment, and chooses Accept or
Reject. The decision, reviewer, comment and UTC timestamp are stored as an audit event.
Changing a decision adds a new event; earlier ones stay.

**Accept means:** the reviewer accepts this recommendation for consideration or
subsequent manual implementation.

**Accept does not** edit source code, trigger Jenkins jobs, deploy applications, alter
infrastructure, install dependencies or run remediation commands. Implementing a
recommendation is a separate change made by a person through the normal workflow.

## Prohibited actions and how each is prevented

| Action | Prevention |
|---|---|
| AI triggers or reruns a build | No Jenkins URL or credentials in the AI service. No process or network code outside the LLM client. |
| AI changes code or infrastructure | The AI container has no source mount and no cloud credentials. |
| AI turns a failed build green | The Jenkins result is established before the AI stage, which cannot set it. A contradicting analysis is rejected. |
| Review decision executes a fix | The review endpoint writes one database row. Every audit event carries `action_executed: false`. |
| `terraform plan`, `apply`, `destroy` | Refused by `ci/tf_safe.py`. No pipeline stage calls Terraform any other way. |
| Cloud provisioning | No AWS credentials exist anywhere in the stack. |
| Automatic retry or deployment | The Jenkinsfile has no `retry`, no downstream trigger and no deploy stage. |

Each row is covered by a test in `ai/tests/test_boundaries.py`,
`ci/tests/test_ci_scripts.py`, `tests/test_repository.py` or `tests/test_e2e.py`.

## What this does not provide

- **Reviewer identity.** The reviewer name is typed, not authenticated. Anyone who can
  reach `localhost:8001` can record a decision under any name.
- **Tamper-proof audit.** The audit trail is rows in a SQLite file. The API offers no
  update or delete, but someone with access to the volume can edit it.
- **Role separation in Jenkins.** One admin account.
- **Transport security.** Plain HTTP between containers and on localhost.
- **Proof of a secure application.** The scanners report what they find. A clean scan
  is not evidence that the code is secure.
- **Build isolation between runs.** One long-lived agent. A malicious build could
  affect later builds on it.
