import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.unit
def test_network_audio_test_layers_and_runner_exist():
    assert Path("tests/unit").is_dir()
    assert Path("tests/component").is_dir()
    assert Path("tests/e2e").is_dir()
    assert Path("tools/run_network_audio_tests.py").is_file()


@pytest.mark.unit
def test_network_audio_test_runner_list_command_works():
    cmd = [sys.executable, "tools/run_network_audio_tests.py", "--list"]
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert "ut" in completed.stdout.lower()
    assert "ct" in completed.stdout.lower()
    assert "e2e" in completed.stdout.lower()
