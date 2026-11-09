"""TileRunner: dry runs skip the command, failures carry the command output.

Real subprocesses are used, with the running Python as the command.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from planetarble.tiling.manager import TileCommandError, TileRunner


def test_dry_run_does_not_execute(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    TileRunner(dry_run=True).run([sys.executable, "-c", f"open({str(marker)!r}, 'w')"], description="touch")
    assert not marker.exists()


def test_successful_command_runs(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    TileRunner().run([sys.executable, "-c", f"open({str(marker)!r}, 'w'); print('out')"], description="touch")
    assert marker.exists()


def test_failure_reports_the_command_and_both_streams() -> None:
    script = "import sys; print('to stdout'); print('to stderr', file=sys.stderr); sys.exit(3)"
    with pytest.raises(TileCommandError) as info:
        TileRunner().run([sys.executable, "-c", script], description="fail")
    message = str(info.value)
    assert message.startswith(f"Command failed: {sys.executable} -c ")
    assert "--- stdout ---\nto stdout" in message
    assert "--- stderr ---\nto stderr" in message


def test_failure_without_output_has_only_the_command() -> None:
    with pytest.raises(TileCommandError) as info:
        TileRunner().run([sys.executable, "-c", "raise SystemExit(1)"], description="fail")
    assert "---" not in str(info.value)
