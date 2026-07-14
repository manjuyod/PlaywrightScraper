from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import windows_pipeline


def test_direct_script_help_runs_from_repository_root():
    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/windows_pipeline.py", "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--target-worker" in result.stdout


def test_cli_target_overrides_environment(monkeypatch):
    captured = {}

    async def fake_run_pipeline(franchises, kinds, **options):
        captured.update(franchises=franchises, kinds=kinds, **options)

    monkeypatch.setenv("WINDOWS_TARGET_WORKER_ID", "worker-from-environment")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "windows_pipeline.py",
            "--franchise-id",
            "11",
            "--enqueue",
            "--target-worker",
            "worker-from-cli",
        ],
    )
    monkeypatch.setattr(windows_pipeline, "run_pipeline", fake_run_pipeline)

    assert windows_pipeline.main() == 0
    assert captured["target_worker_id"] == "worker-from-cli"
    assert captured["enqueue"] is True


def test_enqueue_requires_explicit_target(monkeypatch, capsys):
    async def fake_run_pipeline(*_args, **_kwargs):
        return None

    monkeypatch.delenv("WINDOWS_TARGET_WORKER_ID", raising=False)
    monkeypatch.setenv("SCHEDULER_ID", "must-not-be-used-as-a-target")
    monkeypatch.setattr(
        sys,
        "argv",
        ["windows_pipeline.py", "--franchise-id", "11", "--enqueue"],
    )
    monkeypatch.setattr(windows_pipeline, "run_pipeline", fake_run_pipeline)

    with pytest.raises(SystemExit) as exc_info:
        windows_pipeline.main()

    assert exc_info.value.code == 2
    assert "--target-worker or WINDOWS_TARGET_WORKER_ID is required" in capsys.readouterr().err


def test_reconcile_and_drain_modes_are_unchanged(monkeypatch):
    events = []

    def fake_reconcile():
        events.append("reconcile")
        return {"status": "ok"}

    async def fake_drain():
        events.append("drain")
        return 2

    monkeypatch.setattr(windows_pipeline.scheduler_client, "reconcile_students", fake_reconcile)
    monkeypatch.setattr(windows_pipeline, "drain_worker", fake_drain)

    asyncio.run(
        windows_pipeline.run_pipeline(
            [11],
            ["grade"],
            reconcile=True,
            enqueue=False,
            drain=True,
            target_worker_id=None,
        )
    )

    assert events == ["reconcile", "drain"]
