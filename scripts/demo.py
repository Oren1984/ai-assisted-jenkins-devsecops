#!/usr/bin/env python3
"""Human-operated helper for the local demo. Standard library only.

    python scripts/demo.py status                 # is everything up?
    python scripts/demo.py lint                   # validate the Jenkinsfile in Jenkins
    python scripts/demo.py run test-failure       # start one real Jenkins build and wait
    python scripts/demo.py run-all                # run every scenario in turn

Scenarios never touch the working tree: the pipeline overlays scenarios/<name>/overlay on
its own workspace copy, so there is nothing to reset afterwards. Run "success" to return
to a green build.

This tool is the only thing here that starts builds, and a person runs it. The AI
service has no equivalent capability.
"""

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOB = "devsecops-pipeline"
SCENARIOS = ["success", "test-failure", "security-finding", "terraform-failure", "prompt-injection"]
# 127.0.0.1 rather than localhost: avoids a slow IPv6 attempt on Windows.
JENKINS_URL = "http://127.0.0.1:8080"
AI_URL = "http://127.0.0.1:8001"
APP_URL = "http://127.0.0.1:8000"


def read_env(path: Path = ROOT / ".env") -> dict[str, str]:
    values = {}
    for line in path.read_text("utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def get_json(url: str, timeout: float = 10):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


class Jenkins:
    """Minimal Jenkins REST client using the local admin account from .env."""

    def __init__(self, url: str = JENKINS_URL, env: dict[str, str] | None = None):
        env = env or read_env()
        self.url = url
        token = f"{env['JENKINS_ADMIN_ID']}:{env['JENKINS_ADMIN_PASSWORD']}".encode()
        self.auth = "Basic " + base64.b64encode(token).decode()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self._crumb: dict[str, str] | None = None

    def request(self, path: str, data: bytes | None = None, anonymous: bool = False):
        headers = {} if anonymous else {"Authorization": self.auth}
        if data is not None:
            if self._crumb is None:
                crumb = json.load(self.request("/crumbIssuer/api/json"))
                self._crumb = {crumb["crumbRequestField"]: crumb["crumb"]}
            headers.update(self._crumb)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(self.url + path, data=data, headers=headers)
        return self.opener.open(request, timeout=30)

    def json(self, path: str):
        return json.load(self.request(path))

    def text(self, path: str) -> str:
        return self.request(path).read().decode("utf-8", errors="replace")

    def agent_online(self) -> bool:
        return not self.json("/computer/build-agent/api/json")["offline"]

    def lint(self, jenkinsfile: Path = ROOT / "Jenkinsfile") -> str:
        body = urllib.parse.urlencode({"jenkinsfile": jenkinsfile.read_text("utf-8")}).encode()
        return self.request("/pipeline-model-converter/validate", body).read().decode().strip()

    def run(self, scenario: str, timeout: float = 900) -> dict:
        """Start a build with the given scenario and wait for it to finish."""
        body = urllib.parse.urlencode({"SCENARIO": scenario}).encode()
        response = self.request(f"/job/{JOB}/buildWithParameters", body)
        queue_path = urllib.parse.urlparse(response.headers["Location"]).path
        deadline = time.time() + timeout
        number = None
        while time.time() < deadline:
            if number is None:
                executable = self.json(queue_path + "api/json").get("executable")
                number = executable["number"] if executable else None
            else:
                build = self.json(f"/job/{JOB}/{number}/api/json")
                if not build["building"] and build["result"]:
                    return build
            time.sleep(3)
        raise TimeoutError(f"build for scenario {scenario} did not finish in {timeout}s")

    def artifact(self, number: int, name: str):
        return self.json(f"/job/{JOB}/{number}/artifact/evidence/{name}")


def status() -> int:
    ok = True
    checks = [
        ("Sample app  /health", lambda: get_json(APP_URL + "/health")["status"]),
        ("Sample app  /ready", lambda: get_json(APP_URL + "/ready")["status"]),
        ("AI service  /ready", lambda: "provider=" + get_json(AI_URL + "/ready")["provider"]),
        ("Jenkins     job", lambda: Jenkins().json(f"/job/{JOB}/api/json")["name"]),
        ("Jenkins     agent", lambda: "online" if Jenkins().agent_online() else "OFFLINE"),
    ]
    for label, probe in checks:
        try:
            value = probe()
        except Exception as exc:  # noqa: BLE001 - report every probe, whatever fails
            value, ok = f"FAILED ({type(exc).__name__}: {exc})", False
        ok = ok and value != "OFFLINE"
        print(f"{label:22} {value}")
    return 0 if ok else 1


def run(scenarios: list[str]) -> int:
    jenkins = Jenkins()
    for scenario in scenarios:
        print(f"Starting Jenkins build for scenario '{scenario}'...", flush=True)
        build = jenkins.run(scenario)
        analysis = jenkins.artifact(build["number"], "ai-analysis.json")
        print(f"  build #{build['number']}: Jenkins result {build['result']} "
              f"in {build['duration'] / 1000:.0f}s")
        print(f"  AI: {analysis.get('analysis_status')} / {analysis.get('failure_category')}")
        print(f"  {analysis.get('summary')}")
        print(f"  Review: http://localhost:8001/?build={JOB}-{build['number']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("lint")
    commands.add_parser("run").add_argument("scenario", choices=SCENARIOS)
    commands.add_parser("run-all")
    args = parser.parse_args()
    if args.command == "status":
        return status()
    if args.command == "lint":
        result = Jenkins().lint()
        print(result)
        return 0 if "successfully validated" in result else 1
    return run([args.scenario] if args.command == "run" else SCENARIOS)


if __name__ == "__main__":
    sys.exit(main())
