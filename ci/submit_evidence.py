#!/usr/bin/env python3
"""Send the evidence package to the AI analysis service and save its answer.

This step is advisory. It always exits 0: an unreachable or failing AI service is
reported as "unavailable" and never changes the Jenkins build result.
"""

import argparse
import json
import os
import sys
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from urllib.parse import urlsplit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", default="evidence", type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    url = os.environ.get("AI_SERVICE_URL", "").rstrip("/") + "/api/analyses"
    output = args.evidence_dir / "ai-analysis.json"
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("AI_SERVICE_URL is not an http(s) URL")
        connection_type = HTTPSConnection if parts.scheme == "https" else HTTPConnection
        connection = connection_type(parts.netloc, timeout=args.timeout)
        connection.request(
            "POST",
            parts.path,
            body=(args.evidence_dir / "evidence.json").read_bytes(),
            headers={
                "Content-Type": "application/json",
                "X-Ingest-Token": os.environ.get("AI_INGEST_TOKEN", ""),
            },
        )
        response = connection.getresponse()
        body = response.read()
        connection.close()
        if response.status == 200:
            analysis = json.loads(body)
        else:
            analysis = _unavailable(f"AI service returned HTTP {response.status}: {body[:300]!r}")
    except Exception as exc:  # noqa: BLE001 - the pipeline must not fail because of the AI step
        analysis = _unavailable(f"AI service not reachable: {type(exc).__name__}: {exc}")

    output.write_text(json.dumps(analysis, indent=2), "utf-8")
    print("=" * 72)
    print(f"AI analysis status : {analysis.get('analysis_status')}")
    print(f"Jenkins result     : {analysis.get('jenkins_result', 'see build status')} "
          "(authoritative, unchanged by this step)")
    print(f"Failure category   : {analysis.get('failure_category')}")
    print(f"Summary            : {analysis.get('summary')}")
    risk = analysis.get("risk_assessment") or {}
    if risk:
        print(f"Estimated risk     : {risk.get('level')} "
              f"(tool-reported severity: {risk.get('observed_severity')})")
    for action in analysis.get("recommended_actions", []):
        print(f"Recommendation {action['id']:6}: {action['action']}")
        print("                     -> needs human review, nothing is executed")
    print("=" * 72)
    return 0


def _unavailable(reason: str) -> dict:
    return {
        "analysis_status": "unavailable",
        "failure_category": "unknown",
        "summary": "AI analysis is unavailable. The Jenkins result stands unchanged.",
        "limitations": [reason],
        "recommended_actions": [],
        "autonomous_execution": False,
    }


if __name__ == "__main__":
    sys.exit(main())
