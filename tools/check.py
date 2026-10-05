"""Run the project gate: ruff, mypy (billing_core), pytest. Exit non-zero on any failure."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(name: str, *args: str) -> bool:
    print(f"== {name}: {' '.join(args)}", flush=True)
    ok = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, check=False).returncode == 0
    print(f"== {name}: {'PASS' if ok else 'FAIL'}", flush=True)
    return ok


def main() -> int:
    results = [run("ruff", "ruff", "check", ".")]
    if (ROOT / "billing_core").is_dir():
        results.append(run("mypy", "mypy", "billing_core"))
    else:
        print("== mypy: SKIPPED (billing_core does not exist yet)")
    results.append(run("pytest", "pytest", "-q"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
