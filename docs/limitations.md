# Limitations

## Scope of the proof of concept

- Local only. Nothing is deployed, published or released.
- The AI never acts. It explains and recommends; a person decides and implements.
- One small sample service and one pipeline. Real pipelines produce larger, noisier
  evidence than this one does.
- Five scripted scenarios. They show that the workflow functions, not that the analysis
  is accurate across real-world failures. No accuracy or time-saving figure is claimed.

## Not verified

| Capability | Status |
|---|---|
| Real LLM analysis | **Not run.** No API credentials were available. The client, retries, timeout, schema validation and fallback are tested against a simulated endpoint only. Output quality, cost and latency with a real model are unknown. |
| Token usage reporting | Implemented and unit-tested with simulated usage data. Not observed from a real provider. |
| Operation on Linux or macOS hosts | Developed and run on Windows 11 with Docker Desktop. Expected to work elsewhere, not tested. |
| Fully offline pipeline run | Not possible as built. See below. |
| Behaviour under concurrent builds | Concurrent builds are disabled. |

## Technical limitations

- **The pipeline needs internet access.** *Dependency Installation* downloads from PyPI
  and pip-audit queries a vulnerability database. The AI engine in mock mode needs no
  network (its unit tests run in a container with networking disabled). Terraform
  validation is configured to use only the provider baked into the image, but an
  offline run of it was not tested.
- **pip-audit results change over time.** A newly published advisory against a pinned
  dependency will fail the `success` scenario until the pin is updated. That is the
  scanner working, but it means "success" is not guaranteed forever.
- **Jenkins plugins and the agent base image are not version-pinned.** `plugins.txt`
  installs the latest compatible versions at image build time. The Jenkins LTS version,
  Terraform, Gitleaks, the AWS provider and the direct Python dependencies are pinned.
  Transitive Python dependencies are not.
- **Jenkinsfile changes need a controller restart.** The job is created from the
  mounted file at startup: `docker compose restart jenkins`.
- **Mock analysis is narrow.** Deterministic rules, a small knowledge table, and one
  failure analysed per build. It is not equivalent to a real LLM and does not reason.
- **Injection detection is a pattern heuristic.** It will miss rephrased attacks and can
  flag innocent text. The structural checks do not depend on it.
- **Redaction is pattern-based.** A secret in an unusual shape can get through.
- **Evidence only exists if the pipeline reaches the packaging stage.** A build that is
  aborted or times out produces no analysis.
- **Review identity and audit integrity are not enforced.** See
  [security-and-human-review.md](security-and-human-review.md).

## AWS and Terraform

- No AWS account, credentials or resources are involved anywhere.
- Only `terraform fmt -check`, `terraform init -backend=false` and `terraform validate`
  are ever run. `plan`, `apply` and `destroy` are refused by `ci/tf_safe.py`.
- `terraform init -backend=false` ran once at image build time to download the pinned
  AWS provider from the public registry. It used no credentials and created no state.
- **Terraform configuration validation does not prove that AWS infrastructure can be
  successfully provisioned.** It checks syntax and provider schema. It says nothing about
  permissions, quotas, naming conflicts, or whether the design is sound.

## Cost

- Local Docker workloads only. No cloud spend.
- Mock mode costs nothing.
- LLM mode sends one request of up to roughly 24,000 characters of evidence plus the
  system prompt per build, with up to two retries. Cost depends on the provider and
  model. It was not measured, because no real call was made.

## Possible next steps (not implemented)

- Run the LLM path against a real model and compare its analyses with the rule-based
  ones on the same evidence.
- Check out source from Git and build per-commit, with ephemeral agents.
- Authenticate reviewers and sign audit events.
- Pin plugin versions and the agent base image by digest.
- Add evidence from more check types and from multi-failure builds.
