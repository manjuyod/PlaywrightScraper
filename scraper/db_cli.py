from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping


class GradeDbError(RuntimeError):
    """Safe base error for the local database boundary."""


class GradeDbProtocolError(GradeDbError):
    pass


class GradeDbValidationError(GradeDbError):
    pass


class GradeDbConflict(GradeDbError):
    pass


class GradeDbLeaseExpired(GradeDbError):
    pass


class GradeDbUnavailable(GradeDbError):
    pass


class GradeDbInternalError(GradeDbError):
    pass


_SAFE_CODE = re.compile(r"^[a-z0-9_-]{1,64}$")
_EXIT_ERRORS = {
    1: GradeDbInternalError,
    2: GradeDbValidationError,
    3: GradeDbConflict,
    4: GradeDbLeaseExpired,
    5: GradeDbUnavailable,
}


def resolve_grade_db_cli(project_root: Path | None = None) -> Path:
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    configured = os.getenv("GRADE_DB_CLI_PATH", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    target = root / "grade_db" / "target"
    candidates.extend(
        [
            target / "x86_64-pc-windows-msvc" / "release" / "grade-db.exe",
            target / "release" / "grade-db.exe",
            target / "x86_64-pc-windows-msvc" / "debug" / "grade-db.exe",
            target / "debug" / "grade-db.exe",
        ]
    )

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    raise GradeDbUnavailable("grade-db executable is unavailable")


class GradeDbClient:
    def __init__(
        self,
        executable: Path | None = None,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 120.0,
    ) -> None:
        self.executable = (executable or resolve_grade_db_cli()).resolve()
        if not self.executable.is_file():
            raise GradeDbUnavailable("grade-db executable is unavailable")
        self._run = run
        self._sleep = sleep
        self._timeout = timeout

    def start_job(
        self,
        *,
        kind: str,
        franchise_id: int | None = None,
        student_id: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "kind": kind,
            "franchise_id": franchise_id,
            "student_id": student_id,
        }
        return self._invoke(("job", "start"), payload)

    def heartbeat(
        self,
        *,
        job_id: str,
        lease_token: str,
        progress: Mapping[str, int],
    ) -> dict[str, Any]:
        return self._invoke(
            ("job", "heartbeat"),
            {"job_id": job_id, "lease_token": lease_token, "progress": dict(progress)},
        )

    def post_result(
        self,
        *,
        job_id: str,
        lease_token: str,
        crmstudentid: int,
        outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "job_id": job_id,
            "lease_token": lease_token,
            "crmstudentid": crmstudentid,
            "outcome": dict(outcome),
        }
        for attempt in range(3):
            try:
                return self._invoke(("result", "post"), payload)
            except GradeDbUnavailable:
                if attempt == 2:
                    raise
                self._sleep(0.25 * (2**attempt))
        raise GradeDbUnavailable("grade-db dependency is unavailable")

    def complete_job(
        self,
        *,
        job_id: str,
        lease_token: str,
        progress: Mapping[str, int],
    ) -> dict[str, Any]:
        return self._invoke(
            ("job", "complete"),
            {"job_id": job_id, "lease_token": lease_token, "progress": dict(progress)},
        )

    def fail_job(
        self, *, job_id: str, lease_token: str, code: str
    ) -> dict[str, Any]:
        return self._invoke(
            ("job", "fail"),
            {"job_id": job_id, "lease_token": lease_token, "code": code},
        )

    def doctor(self) -> dict[str, Any]:
        return self._invoke(("doctor",), None)

    def _invoke(
        self, command: tuple[str, ...], payload: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        encoded = "" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"))
        try:
            completed = self._run(
                [str(self.executable), *command],
                input=encoded,
                text=True,
                capture_output=True,
                check=False,
                timeout=self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GradeDbUnavailable("grade-db process is unavailable") from error

        response = _decode_response(completed.stdout)
        if completed.returncode != 0:
            code = response.get("error")
            safe_code = code if isinstance(code, str) and _SAFE_CODE.fullmatch(code) else "command_failed"
            error_type = _EXIT_ERRORS.get(completed.returncode, GradeDbInternalError)
            raise error_type(f"grade-db command failed: {safe_code}")
        return response


def _decode_response(stdout: str) -> dict[str, Any]:
    try:
        response = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise GradeDbProtocolError("grade-db returned invalid JSON") from error
    if not isinstance(response, dict):
        raise GradeDbProtocolError("grade-db returned an invalid response")
    return response
