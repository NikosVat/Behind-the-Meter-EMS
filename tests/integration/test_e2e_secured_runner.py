import os
from pathlib import Path
import subprocess
import sys


def test_documented_e2e_runner_works_with_api_authentication():
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, API_KEY="verification-test-secret", PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "tools/run_e2e_verification.py", "--profile", "bakery"],
                            cwd=root, env=env, capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verification-test-secret" not in result.stdout + result.stderr
