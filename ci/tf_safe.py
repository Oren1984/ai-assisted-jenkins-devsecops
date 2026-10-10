#!/usr/bin/env python3
"""The only way this pipeline calls Terraform: an allowlist of validation-only commands.

Allowed:  fmt -check, init -backend=false, validate, version
Refused:  everything else, including plan, apply, destroy, import and state.

Cloud credentials are stripped from the environment before Terraform starts.
"""

import os
import subprocess
import sys

REQUIRED_FLAGS = {
    "fmt": ["-check"],
    "init": ["-backend=false"],
    "validate": [],
    "version": [],
}
CREDENTIAL_PREFIXES = ("AWS_", "TF_TOKEN_", "TF_VAR_", "ARM_", "GOOGLE_")


def check(args: list[str]) -> str | None:
    """Return a refusal message, or None when the command is allowed."""
    if not args:
        return "no terraform subcommand given"
    subcommand = args[0]
    if subcommand not in REQUIRED_FLAGS:
        return f"terraform {subcommand} is not allowed in this project (validation only)"
    for flag in REQUIRED_FLAGS[subcommand]:
        if flag not in args[1:]:
            return f"terraform {subcommand} requires {flag} in this project"
    if subcommand == "init" and any(a.startswith("-backend-config") for a in args):
        return "terraform init must not configure a backend in this project"
    return None


def clean_environment(env: dict[str, str]) -> dict[str, str]:
    cleaned = {k: v for k, v in env.items() if not k.startswith(CREDENTIAL_PREFIXES)}
    cleaned["CHECKPOINT_DISABLE"] = "1"
    cleaned["TF_IN_AUTOMATION"] = "1"
    cleaned["TF_INPUT"] = "0"
    return cleaned


def main(argv: list[str]) -> int:
    chdir, args = "terraform", argv
    if args and args[0].startswith("--chdir="):
        chdir, args = args[0].split("=", 1)[1], args[1:]
    refusal = check(args)
    if refusal:
        print(f"[tf_safe] REFUSED: {refusal}", file=sys.stderr)
        return 64
    command = ["terraform", f"-chdir={chdir}", *args]
    return subprocess.run(command, env=clean_environment(dict(os.environ)), check=False).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
