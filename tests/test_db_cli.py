from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scraper.db_cli import (
    GradeDbClient,
    GradeDbConflict,
    GradeDbLeaseExpired,
    GradeDbProtocolError,
    GradeDbUnavailable,
    resolve_grade_db_cli,
)


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_resolve_cli_prefers_explicit_environment_path(tmp_path, monkeypatch) -> None:
    executable = _exe(tmp_path / "custom" / "grade-db.exe")
    monkeypatch.setenv("GRADE_DB_CLI_PATH", str(executable))

    assert resolve_grade_db_cli(tmp_path) == executable.resolve()


def test_resolve_cli_falls_back_when_explicit_path_is_stale(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GRADE_DB_CLI_PATH", str(tmp_path / "missing" / "grade-db.exe"))
    release = _exe(tmp_path / "grade_db" / "target" / "release" / "grade-db.exe")

    assert resolve_grade_db_cli(tmp_path) == release.resolve()


def test_resolve_cli_checks_documented_release_then_debug_locations(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("GRADE_DB_CLI_PATH", raising=False)
    debug = _exe(tmp_path / "grade_db" / "target" / "debug" / "grade-db.exe")
    release = _exe(tmp_path / "grade_db" / "target" / "release" / "grade-db.exe")

    assert resolve_grade_db_cli(tmp_path) == release.resolve()
    release.unlink()
    assert resolve_grade_db_cli(tmp_path) == debug.resolve()


@pytest.mark.parametrize(
    ("exit_code", "exception_type"),
    [(3, GradeDbConflict), (4, GradeDbLeaseExpired), (5, GradeDbUnavailable)],
)
def test_exit_codes_map_to_typed_exceptions_without_payload_or_stderr(
    tmp_path, exit_code, exception_type
) -> None:
    executable = _exe(tmp_path / "grade-db.exe")

    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[],
            returncode=exit_code,
            stdout='{"ok":false,"error":"safe_code"}\n',
            stderr="password=do-not-leak",
        )

    client = GradeDbClient(executable=executable, run=run)
    with pytest.raises(exception_type) as raised:
        client.start_job(kind="grade", franchise_id=19)

    message = str(raised.value)
    assert "do-not-leak" not in message
    assert "franchise_id" not in message


def test_invalid_stdout_is_a_protocol_error(tmp_path) -> None:
    executable = _exe(tmp_path / "grade-db.exe")
    client = GradeDbClient(
        executable=executable,
        run=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not-json", stderr=""
        ),
    )

    with pytest.raises(GradeDbProtocolError):
        client.doctor()


def test_result_post_retries_three_times_with_identical_json(tmp_path) -> None:
    executable = _exe(tmp_path / "grade-db.exe")
    calls: list[str] = []

    def run(*_args, **kwargs):
        calls.append(kwargs["input"])
        if len(calls) < 3:
            return subprocess.CompletedProcess(
                args=[],
                returncode=5,
                stdout='{"ok":false,"error":"neon_unavailable"}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"applied":true,"duplicate":false}\n',
            stderr="",
        )

    client = GradeDbClient(executable=executable, run=run, sleep=lambda _: None)
    response = client.post_result(
        job_id="00000000-0000-0000-0000-000000000019",
        lease_token="00000000-0000-0000-0000-000000000042",
        crmstudentid=7,
        outcome={"kind": "grade_success", "parsed_grades": {"2026-07-13": {}}},
    )

    assert response["applied"] is True
    assert len(calls) == 3
    assert calls[0] == calls[1] == calls[2]
    assert json.loads(calls[0])["crmstudentid"] == 7


def test_start_job_uses_json_stdin_and_never_places_scope_on_command_line(tmp_path) -> None:
    executable = _exe(tmp_path / "grade-db.exe")
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["payload"] = json.loads(kwargs["input"])
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"job_id":"j","lease_token":"t","students":[],"progress":{"total":0,"attempted":0,"success":0,"errors":0}}',
            stderr="",
        )

    GradeDbClient(executable=executable, run=run).start_job(
        kind="agenda", franchise_id=19, student_id=42
    )

    assert observed["command"] == [str(executable.resolve()), "job", "start"]
    assert observed["payload"] == {
        "kind": "agenda",
        "franchise_id": 19,
        "student_id": 42,
    }
