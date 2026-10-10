#!/usr/bin/env python3
"""Run one pipeline check, keep its log, and record its outcome for evidence packaging.

The exit code of the wrapped command is passed through unchanged, so Jenkins still
decides the stage result.

    python3 ci/run_step.py --id unit-tests -- pytest app/tests
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--evidence-dir", default="evidence")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given")

    evidence = Path(args.evidence_dir)
    (evidence / "logs").mkdir(parents=True, exist_ok=True)
    (evidence / "steps").mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"[run_step] {args.id}: {' '.join(command)}", flush=True)
    with open(evidence / "logs" / f"{args.id}.log", "w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            message = f"[run_step] could not start command: {exc}\n"
            log.write(message)
            sys.stdout.write(message)
            exit_code = 127
        else:
            for line in process.stdout:
                sys.stdout.write(line)
                log.write(line)
            exit_code = process.wait()

    record = {
        "id": args.id,
        "command": " ".join(command),
        "exit_code": exit_code,
        "status": "passed" if exit_code == 0 else "failed",
        "started_at": started_at,
        "duration_seconds": round(time.monotonic() - started, 2),
    }
    (evidence / "steps" / f"{args.id}.json").write_text(json.dumps(record, indent=2), "utf-8")
    print(f"[run_step] {args.id}: {record['status']} (exit {exit_code})", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
