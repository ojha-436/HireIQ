"""Download the O*NET source file the question bank is derived from.

Kept as a script rather than a vendored 3.4 MB CSV: the derived bank is what the app
needs, and this preserves a reproducible path back to the original public record.
"""
from __future__ import annotations

import pathlib
import sys
import urllib.request

BASE = "https://www.onetcenter.org/dl_files/database/db_31_0_csv"
FILES = ("task_statements.csv", "occupation_data.csv")
HERE = pathlib.Path(__file__).resolve().parent


def main() -> int:
    for name in FILES:
        target = HERE / name
        if target.is_file() and target.stat().st_size > 1000:
            print(f"  {name}: already present ({target.stat().st_size:,} bytes)")
            continue
        url = f"{BASE}/{name}"
        print(f"  fetching {url}")
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                target.write_bytes(r.read())
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}", file=sys.stderr)
            return 1
        print(f"  {name}: {target.stat().st_size:,} bytes")
    print("\nO*NET 31.0 — CC BY 4.0, USDOL/ETA. See ATTRIBUTION.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
