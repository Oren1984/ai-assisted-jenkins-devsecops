#!/usr/bin/env python3
"""Build the bounded, redacted evidence package that Jenkins sends to the AI service.

Input:  evidence/steps/*.json, evidence/logs/*.log, evidence/reports/* (written by the
        pipeline stages).
Output: evidence/evidence.json (validated by the AI service against ai/schemas.py).

Only excerpts and structured findings are packaged. Whole logs and the workspace never
leave Jenkins.
"""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai.redaction import redact_value  # noqa: E402

# Stage name in the Jenkinsfile -> checks recorded by ci/run_step.py, in pipeline order.
PIPELINE = [
    ("Source Preparation", ["source-preparation"]),
    ("Dependency Installation", ["dependency-install"]),
    ("Static Checks", ["static-checks"]),
    ("Unit Tests", ["unit-tests"]),
    ("Security Scanning", ["bandit", "pip-audit", "gitleaks"]),
    ("Terraform Validation", ["terraform-fmt", "terraform-init", "terraform-validate"]),
]

MAX_ITEMS = 60
MAX_PER_REPORT = 10
MAX_CONTENT = 3500
MAX_LOG_LINES = 60
MAX_ATTRIBUTE = 400


def clip(text, limit: int = MAX_CONTENT) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[: limit - 15] + "\n...[truncated]"


def attributes(**values) -> dict:
    return {k: clip(v, MAX_ATTRIBUTE) if isinstance(v, str) else v for k, v in values.items()}


def load_json(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


class Packager:
    def __init__(self, evidence_dir: Path, workspace: Path):
        self.dir = evidence_dir
        self.workspace = workspace
        self.items: list[dict] = []
        self.notes: list[str] = []
        self.steps = {
            path.stem: record
            for path in sorted((evidence_dir / "steps").glob("*.json"))
            if (record := load_json(path)) is not None
        }

    def add(self, item_id, kind, check, title, content="", **attrs) -> None:
        self.items.append(
            {
                "id": item_id,
                "kind": kind,
                "check": check,
                "title": clip(title, 300),
                "content": clip(content),
                "attributes": attributes(**attrs),
            }
        )

    def ran(self, check: str) -> bool:
        return check in self.steps

    def relative(self, path: str) -> str:
        try:
            return Path(path).resolve().relative_to(self.workspace.resolve()).as_posix()
        except ValueError:
            return str(path).replace("\\", "/").removeprefix("./")

    # ------------------------------------------------------------ stages

    def stages(self) -> list[dict]:
        stages = []
        for name, check_ids in PIPELINE:
            checks = []
            for check_id in check_ids:
                step = self.steps.get(check_id)
                checks.append(
                    {
                        "id": check_id,
                        "status": step["status"] if step else "not_run",
                        "exit_code": step["exit_code"] if step else None,
                        "duration_seconds": step["duration_seconds"] if step else None,
                    }
                )
            statuses = {check["status"] for check in checks}
            status = "failed" if "failed" in statuses else (
                "not_run" if "not_run" in statuses else "passed")
            stages.append({"name": name, "status": status, "checks": checks})
        return stages

    # ------------------------------------------------------------ reports

    def logs_of_failed_checks(self) -> None:
        for check_id, step in self.steps.items():
            if step["status"] != "failed":
                continue
            try:
                lines = (self.dir / "logs" / f"{check_id}.log").read_text(
                    "utf-8", errors="replace").splitlines()
            except OSError:
                self.notes.append(f"Log for failed check '{check_id}' is missing.")
                continue
            self.add(
                f"log-{check_id}",
                "log_excerpt",
                check_id,
                f"Last {min(len(lines), MAX_LOG_LINES)} log lines of failed check '{check_id}' "
                f"(exit code {step['exit_code']})",
                "\n".join(lines[-MAX_LOG_LINES:])[-MAX_CONTENT:],
                exit_code=step["exit_code"],
            )

    def junit(self) -> None:
        if not self.ran("unit-tests"):
            return
        path = self.dir / "reports" / "junit.xml"
        try:
            # defusedxml lives in the project virtualenv; test output is untrusted XML.
            from defusedxml import ElementTree

            root = ElementTree.parse(path).getroot()
        except ImportError:
            self.notes.append("JUnit report not parsed: defusedxml is unavailable.")
            return
        except Exception as exc:  # noqa: BLE001 - any parse problem means "no report"
            self.notes.append(f"JUnit report missing or unreadable ({type(exc).__name__}).")
            return

        cases = list(root.iter("testcase"))
        failed = []
        skipped = 0
        for case in cases:
            problem = case.find("failure")
            kind = "failure"
            if problem is None:
                problem, kind = case.find("error"), "error"
            if problem is not None:
                failed.append((case, problem, kind))
            elif case.find("skipped") is not None:
                skipped += 1
        failures = sum(1 for _, _, kind in failed if kind == "failure")
        errors = len(failed) - failures
        passed = len(cases) - len(failed) - skipped
        self.add(
            "test-summary",
            "test_summary",
            "unit-tests",
            f"Unit tests: {len(cases)} run, {passed} passed, {failures} failed, "
            f"{errors} errors, {skipped} skipped",
            tests=len(cases), passed=passed, failures=failures, errors=errors, skipped=skipped,
        )
        for index, (case, problem, kind) in enumerate(failed[:MAX_PER_REPORT], start=1):
            classname = case.get("classname", "")
            name = case.get("name", "")
            self.add(
                f"test-{index:03d}",
                "test_failure",
                "unit-tests",
                f"Test {kind}: {classname}::{name}",
                problem.text or "",
                name=f"{classname}::{name}",
                classname=classname,
                file=classname.replace(".", "/") + ".py",
                type=kind,
                message=problem.get("message", ""),
            )
        if len(failed) > MAX_PER_REPORT:
            self.notes.append(f"{len(failed) - MAX_PER_REPORT} further test failures omitted.")

    def bandit(self) -> None:
        if not self.ran("bandit"):
            return
        report = load_json(self.dir / "reports" / "bandit.json")
        if report is None:
            self.notes.append("Bandit report missing or unreadable.")
            return
        results = report.get("results", [])
        self.add(
            "bandit-summary", "security_summary", "bandit",
            f"Bandit (static security analysis): {len(results)} finding(s) at medium severity "
            "or above",
            tool="bandit", findings=len(results),
        )
        for index, result in enumerate(results[:MAX_PER_REPORT], start=1):
            path = self.relative(result.get("filename", ""))
            self.add(
                f"bandit-{index:03d}",
                "security_finding",
                "bandit",
                f"Bandit {result.get('test_id')} {result.get('test_name')} at "
                f"{path}:{result.get('line_number')}",
                f"{result.get('issue_text', '')}\n\n{result.get('code', '')}",
                tool="bandit",
                rule_id=result.get("test_id"),
                rule_name=result.get("test_name"),
                severity=result.get("issue_severity"),
                confidence=result.get("issue_confidence"),
                file=path,
                line=result.get("line_number"),
                cwe=(result.get("issue_cwe") or {}).get("id"),
            )

    def pip_audit(self) -> None:
        if not self.ran("pip-audit"):
            return
        report = load_json(self.dir / "reports" / "pip-audit.json")
        if report is None:
            self.notes.append("pip-audit report missing or unreadable.")
            return
        dependencies = report.get("dependencies", [])
        vulnerable = [(d, v) for d in dependencies for v in d.get("vulns", [])]
        self.add(
            "pip-audit-summary", "security_summary", "pip-audit",
            f"pip-audit (dependency vulnerabilities): {len(vulnerable)} advisory(ies) across "
            f"{len(dependencies)} installed packages",
            tool="pip-audit", findings=len(vulnerable), packages=len(dependencies),
        )
        for index, (dependency, vuln) in enumerate(vulnerable[:MAX_PER_REPORT], start=1):
            self.add(
                f"pip-audit-{index:03d}",
                "security_finding",
                "pip-audit",
                f"pip-audit {vuln.get('id')} in {dependency.get('name')} "
                f"{dependency.get('version')}",
                vuln.get("description", ""),
                tool="pip-audit",
                package=dependency.get("name"),
                version=dependency.get("version"),
                advisory=vuln.get("id"),
                fix_versions=", ".join(vuln.get("fix_versions", [])),
            )

    def gitleaks(self) -> None:
        if not self.ran("gitleaks"):
            return
        report = load_json(self.dir / "reports" / "gitleaks.json")
        if not isinstance(report, list):
            self.notes.append("Gitleaks report missing or unreadable.")
            return
        self.add(
            "gitleaks-summary", "security_summary", "gitleaks",
            f"Gitleaks (secret detection): {len(report)} potential secret(s)",
            tool="gitleaks", findings=len(report),
        )
        for index, leak in enumerate(report[:MAX_PER_REPORT], start=1):
            path = self.relative(leak.get("File", ""))
            self.add(
                f"gitleaks-{index:03d}",
                "security_finding",
                "gitleaks",
                f"Gitleaks {leak.get('RuleID')} at {path}:{leak.get('StartLine')}",
                leak.get("Description", ""),  # the matched secret itself is never packaged
                tool="gitleaks",
                rule_id=leak.get("RuleID"),
                file=path,
                line=leak.get("StartLine"),
            )

    def terraform(self) -> None:
        if not self.ran("terraform-validate"):
            return
        try:
            raw = (self.dir / "logs" / "terraform-validate.log").read_text("utf-8")
            report = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        except (OSError, ValueError):
            self.notes.append("terraform validate produced no machine-readable output.")
            return
        diagnostics = report.get("diagnostics", [])
        self.add(
            "terraform-summary", "terraform_summary", "terraform-validate",
            f"terraform validate: valid={str(report.get('valid')).lower()}, "
            f"{report.get('error_count', 0)} error(s), {report.get('warning_count', 0)} "
            "warning(s). Validation only, nothing was planned or applied",
            valid=bool(report.get("valid")),
            error_count=report.get("error_count", 0),
            warning_count=report.get("warning_count", 0),
        )
        for index, diagnostic in enumerate(diagnostics[:MAX_PER_REPORT], start=1):
            where = diagnostic.get("range") or {}
            snippet = diagnostic.get("snippet") or {}
            self.add(
                f"tf-{index:03d}",
                "terraform_diagnostic",
                "terraform-validate",
                f"Terraform {diagnostic.get('severity')}: {diagnostic.get('summary')}",
                f"{diagnostic.get('detail', '')}\n\n{snippet.get('context') or ''}\n"
                f"{snippet.get('code') or ''}".strip(),
                severity=diagnostic.get("severity"),
                summary=diagnostic.get("summary"),
                file="terraform/" + str(where.get("filename", "")),
                line=(where.get("start") or {}).get("line"),
            )

    def source_notes(self) -> None:
        source = load_json(self.dir / "source.json") or {}
        for entry in source.get("overlay", []):
            self.notes.append(f"Demo scenario overlay applied to the workspace copy: {entry}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", default="evidence", type=Path)
    parser.add_argument("--result", required=True, help="Jenkins currentBuild.currentResult")
    parser.add_argument("--started-at-ms", type=int, default=0)
    parser.add_argument("--scenario", default="")
    args = parser.parse_args()

    packager = Packager(args.evidence_dir, Path.cwd())
    packager.junit()
    packager.bandit()
    packager.pip_audit()
    packager.gitleaks()
    packager.terraform()
    packager.logs_of_failed_checks()
    packager.source_notes()
    if len(packager.items) > MAX_ITEMS:
        packager.notes.append(f"{len(packager.items) - MAX_ITEMS} evidence items omitted.")
        packager.items = packager.items[:MAX_ITEMS]

    job = os.environ.get("JOB_NAME", "local")
    number = int(os.environ.get("BUILD_NUMBER", "0"))
    started = args.started_at_ms / 1000 if args.started_at_ms else None
    package = {
        "schema_version": "1",
        "build": {
            "id": f"{job}-{number}",
            "job": job,
            "number": number,
            "url": os.environ.get("BUILD_URL", ""),
            "result": args.result,
            "started_at": (
                datetime.fromtimestamp(started, UTC).isoformat(timespec="seconds")
                if started else ""
            ),
            "duration_seconds": round(time.time() - started, 1) if started else None,
            "scenario": args.scenario,
        },
        "stages": packager.stages(),
        "items": redact_value(packager.items),
        "notes": [clip(note, 300) for note in redact_value(packager.notes)][:20],
    }
    output = args.evidence_dir / "evidence.json"
    output.write_text(json.dumps(package, indent=2), "utf-8")
    print(f"Evidence package: {output} ({output.stat().st_size} bytes, "
          f"{len(package['items'])} items, build result {args.result})")
    for stage in package["stages"]:
        print(f"  {stage['status']:8} {stage['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
