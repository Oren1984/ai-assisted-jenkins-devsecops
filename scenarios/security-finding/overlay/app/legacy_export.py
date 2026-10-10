"""SCENARIO FIXTURE (security-finding): synthetic insecure code, never imported or run.

It exists only so that Bandit reports real findings in the security-finding scenario.
"""

import hashlib
import subprocess


def export_orders(destination: str) -> int:
    # Deliberately unsafe: shell command built from a caller-supplied value.
    return subprocess.call(f"tar czf {destination} /var/lib/orders", shell=True)


def fingerprint(payload: bytes) -> str:
    # Deliberately weak: MD5 used as an integrity fingerprint.
    return hashlib.md5(payload).hexdigest()
