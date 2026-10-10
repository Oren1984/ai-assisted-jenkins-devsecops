# Demo scenarios

Each scenario is a real Jenkins build. The `SCENARIO` build parameter selects a small
overlay under `scenarios/<name>/overlay/`, which the *Source Preparation* stage copies
over the pipeline's own workspace copy of the source. The repository working tree is
never modified, and every overlaid file is printed in the build log and listed in the
evidence package.

| Scenario | Overlay | Expected Jenkins result |
|---|---|---|
| `success` | none, the source is built as-is | SUCCESS |
| `test-failure` | `app/pricing.py` with an off-by-one in the bulk discount (`>` instead of `>=`) | FAILURE in *Unit Tests* |
| `security-finding` | adds `app/legacy_export.py`, synthetic code with `shell=True` and MD5 | FAILURE in *Security Scanning* (Bandit) |
| `terraform-failure` | `terraform/main.tf` with the misspelled argument `cidr_blok` | FAILURE in *Terraform Validation* |
| `prompt-injection` | adds a failing test whose assertion message tries to instruct the AI | FAILURE in *Unit Tests* |

## Trigger

In Jenkins: open `devsecops-pipeline`, choose **Build with Parameters**, pick a scenario.

From a terminal:

```bash
python scripts/demo.py run test-failure   # one scenario, waits for the result
python scripts/demo.py run-all            # all five in turn
```

## Reset

There is nothing to reset. The workspace is deleted at the start of every build, so the
next build starts from the unmodified source. Run `success` to get a green build again.

The fixtures contain no real secrets and no vulnerable dependencies. The insecure code
in `security-finding` is never imported or executed.
