#!/usr/bin/env python3
"""Copy the read-only source mount into the Jenkins workspace and apply a demo scenario.

A scenario is a small overlay of files under scenarios/<name>/overlay/. It is applied to
the workspace copy only, and every replaced file is printed and recorded. The mounted
source tree is never modified.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

SOURCE_DIRS = ["app", "ai", "ci", "terraform"]
SOURCE_FILES = ["requirements.txt", "requirements-dev.txt", "pyproject.toml"]
SCENARIOS = ["success", "test-failure", "security-finding", "terraform-failure", "prompt-injection"]
IGNORED = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache", ".terraform*")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--scenario", default="success")
    args = parser.parse_args()

    if args.scenario not in SCENARIOS:
        print(f"Unknown scenario '{args.scenario}'. Expected one of: {', '.join(SCENARIOS)}")
        return 2

    for name in SOURCE_DIRS:
        shutil.copytree(args.source / name, args.dest / name, ignore=IGNORED, dirs_exist_ok=True)
    for name in SOURCE_FILES:
        shutil.copy2(args.source / name, args.dest / name)
    print(f"Copied source: {', '.join(SOURCE_DIRS + SOURCE_FILES)}")

    overlay = args.source / "scenarios" / args.scenario / "overlay"
    applied = []
    if overlay.is_dir():
        for path in sorted(p for p in overlay.rglob("*") if p.is_file()):
            relative = path.relative_to(overlay)
            target = args.dest / relative
            verb = "replaced" if target.exists() else "added"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            applied.append(f"{relative.as_posix()} ({verb})")
            print(f"Scenario '{args.scenario}' overlay: {relative.as_posix()} {verb}")
    else:
        print(f"Scenario '{args.scenario}': no overlay, building the source as-is")

    evidence = args.dest / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "source.json").write_text(
        json.dumps({"scenario": args.scenario, "overlay": applied}, indent=2), "utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
