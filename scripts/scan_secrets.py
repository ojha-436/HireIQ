"""Gate on secrets before anything is committed.

Checks the staged set against the LIVE values in backend/.env rather than guessing at
patterns, because the only values that matter are the ones this machine actually holds.

Config is not a secret: a model name and a CORS origin list are neither sensitive nor
avoidable — they belong in the committed defaults. Only keys whose NAME marks them
sensitive are treated as blocking.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

SENSITIVE = ("SECRET", "PASSWORD", "PRIVATE")
#: Reaches the browser by design (the client needs it to join a channel), so it is not a
#: secret — but there is no reason to publish it either.
WARN_ONLY = ("AGORA_APP_ID",)
BLOCKING = ("GEMINI_API_KEY", "AGORA_APP_CERTIFICATE", "AGORA_CUSTOMER_ID",
            "AGORA_CUSTOMER_SECRET", "JWT_SECRET", "EMPLOYER_JWT_SECRET", "DIGEST_TOKEN")

PATTERNS = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:gho|ghp|github_pat)_[0-9A-Za-z_]{20,}")),
    ("AWS key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
]


def live_values() -> tuple[dict[str, str], dict[str, str]]:
    env = pathlib.Path("backend/.env")
    block: dict[str, str] = {}
    warn: dict[str, str] = {}
    if not env.is_file():
        return block, warn
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (x.strip() for x in line.split("=", 1))
        if len(value) < 12 or "change-me" in value:
            continue
        if key in BLOCKING or any(m in key for m in SENSITIVE):
            block[key] = value
        elif key in WARN_ONLY:
            warn[key] = value
    return block, warn


def main() -> int:
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                            capture_output=True, text=True).stdout.split()
    block, warn = live_values()
    print(f"  {len(staged)} files staged")
    print(f"  gating on {len(block)} secret value(s) + {len(PATTERNS)} patterns; "
          f"warning on {len(warn)}")

    failures: list[str] = []
    warnings: list[str] = []

    for name in staged:
        if pathlib.Path(name).name in (".env",) or name.endswith("/.env"):
            failures.append(f"{name} is staged — this file must never be committed")
        p = pathlib.Path(name)
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        for key, value in block.items():
            if value in text:
                failures.append(f"{name}: contains the live value of {key}")
        for key, value in warn.items():
            if value in text:
                warnings.append(f"{name}: contains {key} (not secret, but why publish it)")
        for label, rx in PATTERNS:
            if rx.search(text):
                failures.append(f"{name}: matches {label}")

    print()
    for w in warnings:
        print("  WARN ", w)
    if failures:
        print("\n  BLOCKED — nothing will be committed:")
        for f in failures:
            print("   ", f)
        return 1
    print("  CLEAN — no secrets in the staged set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
