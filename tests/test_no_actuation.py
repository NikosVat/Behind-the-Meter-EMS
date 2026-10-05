"""Advisory-only guard: no production code may write to a device.

Scans every .py file outside tests/ and docs/ for Modbus write calls,
Shelly control RPC methods and Shelly Gen1 relay commands.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"tests", "docs", ".git", ".venv", "venv", "__pycache__", "build", "dist"}

FORBIDDEN = {
    "modbus write (FC 5/6/15/16)": re.compile(r"\bwrite_(register|coil)s?\b"),
    "Shelly Switch.* RPC": re.compile(r"\bSwitch\."),
    "Shelly *.Set* RPC": re.compile(r"\b[A-Z][A-Za-z0-9]*\.Set[A-Za-z]*\b"),
    "Shelly Gen1 relay command": re.compile(r"/relay/\d|\bturn=(on|off|toggle)\b"),
}


def _source_files() -> list[Path]:
    return [
        p
        for p in ROOT.rglob("*.py")
        if not SKIP_DIRS.intersection(p.relative_to(ROOT).parts[:-1])
        and not any(part.endswith(".egg-info") for part in p.parts)
    ]


def _violations(text: str) -> list[str]:
    return [name for name, rx in FORBIDDEN.items() if rx.search(text)]


def test_patterns_catch_known_writes() -> None:
    for sample in (
        "client.write_register(1, 2)",
        "client.write_coils(0, [True])",
        '{"method": "Switch.Set"}',
        '{"method": "Sys.SetConfig"}',
        "http://shelly/relay/0?turn=on",
    ):
        assert _violations(sample), sample
    assert not _violations("client.read_holding_registers(0, 2)")
    assert not _violations('{"method": "EM.GetStatus"}')


def test_no_device_writes_outside_tests() -> None:
    files = _source_files()
    assert files, "scanner found no source files"
    hits = [
        f"{p.relative_to(ROOT)}:{n}: {name}: {line.strip()}"
        for p in files
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        for name in _violations(line)
    ]
    assert not hits, "device-write code found:\n" + "\n".join(hits)
