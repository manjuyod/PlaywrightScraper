from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _is_ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", path],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_manual_replit_files_are_gitignored() -> None:
    assert _is_ignored(".replit")
    assert _is_ignored("replit.nix")


def test_start_script_registers_odbc_before_starting_gunicorn() -> None:
    start_script = (PROJECT_ROOT / "ui" / "start.sh").read_text(encoding="utf-8")

    assert 'ODBCSYSINI="${ODBCSYSINI:-$HOME/.odbc}"' in start_script
    assert "bash setup_odbc.sh" in start_script
    assert "mkdir -p ui/tmp" in start_script
    assert "uv run gunicorn" in start_script
    assert "--bind 127.0.0.1:3000" in start_script
    assert "nginx" in start_script


def test_odbc_setup_registers_bundled_driver_name_used_by_auth() -> None:
    setup_script = (PROJECT_ROOT / "setup_odbc.sh").read_text(encoding="utf-8")

    assert "odbc_driver" in setup_script
    assert "[ODBC Driver 17 for SQL Server]" in setup_script
    assert "Driver=$DRIVER_LIB" in setup_script


def test_odbc_build_script_accepts_existing_complete_bundle() -> None:
    build_script = (PROJECT_ROOT / "setup_odbc_build.sh").read_text(encoding="utf-8")

    assert "ODBC driver bundle already complete" in build_script
    assert "libmsodbcsql-17*.so*" in build_script
    assert "msodbcsqlr17.rll" in build_script
