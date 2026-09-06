"""Runs the one Node-executed test in this repo: frontend/js/interview's mic VAD has
no browser-testable harness (it's an AudioWorkletProcessor), so its regression test is
a standalone Node script rather than a pytest-native one. See that file's own
docstring for what bug it guards against (a candidate's mic getting permanently stuck
on "speaking" in a room whose noise floor never reads as quiet).

Skips if `node` is not on PATH rather than failing the suite — this project has no
build step and does not otherwise depend on Node being installed.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent.parent
          / "frontend" / "js" / "interview" / "mic-worklet.node-test.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_mic_vad_safety_valve():
    result = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f"mic-worklet VAD regression test failed:\n{result.stdout}\n{result.stderr}"
    )
