# builtins
import json
import re
import uuid
from collections.abc import Mapping
from typing import Literal, cast
# external
from flask import (
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ui import api_client as grade_api
from ui.report_models import Student
from ui.app import (
    clear_login_failures,
    app,
    csrf_protect,
    is_login_rate_limited,
    record_login_failure,
    set_session_state,
    login_required,
)
from ui.auth import crm_login
from ui.controllers import (
    compute_student_report,
)


def _coerce_int(value: object) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _student_value(student: Student | Mapping, key: str):
    if isinstance(student, Student):
        return getattr(student, key, None)
    return student.get(key)


def _find_student(students: list[Student] | list[Mapping], student_id: int) -> Student | Mapping | None:
    for student in students:
        if _coerce_int(_student_value(student, "id")) == student_id:
            return student
    return None


def _form_or_existing(field_name: str, existing_student: Student | Mapping | None, attr_name: str) -> str:
    submitted = request.form.get(field_name)
    if submitted:
        return submitted
    if existing_student is not None:
        existing = _student_value(existing_student, attr_name)
        if existing is not None:
            return str(existing)
    return ""


GRADE_FILTER_LEVELS = {
    "middle_school": {6, 7, 8},
    "high_school": {9, 10, 11, 12},
}


def _normalize_grade_filter(raw_filter: str | None) -> str:
    if raw_filter in GRADE_FILTER_LEVELS:
        return raw_filter
    return "all"


def _grade_level_int(grade_level: object) -> int | None:
    if grade_level is None or isinstance(grade_level, bool):
        return None
    if isinstance(grade_level, int):
        return grade_level
    if isinstance(grade_level, float) and grade_level.is_integer():
        return int(grade_level)

    match = re.search(r"\d+", str(grade_level))
    if match is None:
        return None
    return int(match.group())


def _filter_students_by_grade(students: list[Student], grade_filter: str) -> list[Student]:
    grade_levels = GRADE_FILTER_LEVELS.get(grade_filter)
    if grade_levels is None:
        return students
    return [
        student
        for student in students
        if _grade_level_int(student.grade_level) in grade_levels
    ]


def _api_scope(franchise_id: int | None = None) -> grade_api.ApiScope:
    session_franchise_id = _coerce_int(session.get("franchise_id"))
    session_role: int | str | None = _coerce_int(session.get("role"))
    if session.get("session_type") == "health_test":
        session_role = "health"
    return grade_api.ApiScope(
        franchise_id=franchise_id if franchise_id is not None else session_franchise_id,
        role=session_role,
        user=str(session.get("user_fingerprint") or ""),
    )


def _students_from_api_payload(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        students = payload.get("students", [])
        if isinstance(students, list):
            return [row for row in students if isinstance(row, dict)]
    return []


def _student_from_api(row: Mapping) -> Student:
    return Student.from_api(row)


def get_dashboard_students(franchise_id: int) -> list[Student]:
    payload = grade_api.request_json(
        "GET",
        "/api/students",
        scope=_api_scope(franchise_id),
        query={"franchise_id": franchise_id},
    )
    return [_student_from_api(row) for row in _students_from_api_payload(payload)]


def create_manual_pull(
    franchise_id: int,
    kind: Literal["grade", "agenda"] = "grade",
    student_id: int | None = None,
):
    payload: dict[str, object] = {"kind": kind}
    if student_id is not None:
        payload["student_id"] = student_id
    return grade_api.request_json(
        "POST",
        "/api/jobs/manual-pull",
        scope=_api_scope(franchise_id),
        payload=payload,
    )


def _active_jobs() -> dict[str, str]:
    stored = session.get("active_job_ids")
    if not isinstance(stored, dict):
        return {}
    active: dict[str, str] = {}
    for kind in ("grade", "agenda"):
        job_id = stored.get(kind)
        if not isinstance(job_id, str):
            continue
        try:
            active[kind] = str(uuid.UUID(job_id))
        except ValueError:
            continue
    return active


def _remember_active_job(kind: Literal["grade", "agenda"], payload: object) -> str | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("job_id"), str):
        return None
    try:
        job_id = str(uuid.UUID(payload["job_id"]))
    except ValueError:
        return None
    active = _active_jobs()
    active[kind] = job_id
    session["active_job_ids"] = active
    return job_id


def _forget_active_job(job_id: str) -> None:
    active = {
        kind: active_id
        for kind, active_id in _active_jobs().items()
        if active_id != job_id
    }
    session["active_job_ids"] = active


def get_dashboard_health() -> dict[str, object]:
    payload = grade_api.request_json(
        "GET", "/api/dashboard/health", scope=_api_scope()
    )
    return payload if isinstance(payload, dict) else {}


def get_dashboard_job(job_id: str) -> dict[str, object] | None:
    try:
        payload = grade_api.request_json(
            "GET",
            f"/api/jobs/{job_id}",
            scope=_api_scope(),
        )
    except grade_api.ApiClientError as exc:
        if exc.status == 404:
            return None
        raise
    return payload if isinstance(payload, dict) else None


@app.route("/", methods=["GET", "POST"])
async def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
@csrf_protect
async def login():
    if request.method == "GET":
        if session.get("session_type"):
            session_type = session.get("session_type")
            franchise_id = _coerce_int(session.get("franchise_id"))
            if session_type == "crm" and franchise_id and franchise_id != 1:
                return redirect(url_for("franchise_view", franchise_id=franchise_id))
            if session_type == "health_test":
                return redirect(url_for("health"))
            session.clear()
        return render_template("login.html")

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    remote_addr = request.remote_addr

    if is_login_rate_limited(remote_addr, username):
        flash("Too many sign-in attempts. Try again later.")
        return render_template("login.html"), 429

    login_result = crm_login(username=username, password=password)

    if not login_result.authenticated or login_result.franchise_id is None:
        record_login_failure(remote_addr, username)
        flash("Invalid username or password.")
        return redirect(url_for("login"))

    clear_login_failures(remote_addr, username)
    if login_result.franchise_id == 1:
        set_session_state(
            session_type="health_test", franchise_id=None, username=username
        )
        return redirect(url_for("health"))

    set_session_state(
        session_type="crm",
        franchise_id=login_result.franchise_id,
        role=login_result.role,
        username=username,
    )
    return redirect(url_for("franchise_view", franchise_id=login_result.franchise_id))


@app.route("/logout", methods=["POST"])
@csrf_protect
@login_required
async def logout():
    session.clear()
    return redirect(url_for("login"))


# A simple health page to show the health of active franchise pages and status of background jobs, and provide a landing page for dev access
@app.route("/health")
@login_required
async def health():
    try:
        health_payload = get_dashboard_health()
    except grade_api.ApiClientError:
        return "Dashboard API unavailable.", 503

    jobs_payload = health_payload.get("jobs", [])
    jobs_for_template = [job for job in jobs_payload if isinstance(job, dict)]
    franchises_payload = health_payload.get("franchises", [])
    franchises = [row for row in franchises_payload if isinstance(row, dict)]

    return render_template(
        "health.html",
        health=franchises,
        count_all=sum(int(row.get("total_students", 0)) for row in franchises),
        count_synced=sum(int(row.get("synced_students", 0)) for row in franchises),
        count_bad_logins=0,
        jobs=jobs_for_template,
    )



@app.route("/franchise/<int:franchise_id>", methods=["GET", "POST"])
@login_required
@csrf_protect
async def franchise_view(franchise_id: int):
    """Here we show a list of students for the given franchise.
    Student data is fetched from the database.
    Comprised of the students' first/last name, portal links, most recent grades"""
    grade_filter = _normalize_grade_filter(request.args.get("grade_filter"))
    students = get_dashboard_students(franchise_id)
    visible_students = _filter_students_by_grade(students, grade_filter)
    student_reports = [compute_student_report(student) for student in visible_students]
    # print(student_reports[0:1])
    active_jobs = _active_jobs()
    job_id = active_jobs.get("grade")
    agenda_job_id = active_jobs.get("agenda")
    if request.method == "POST":
        if "run_scraper" in request.form:
            payload = create_manual_pull(franchise_id, "grade")
            job_id = _remember_active_job("grade", payload)
            flash("Grade collection queued. This may take a few minutes.")
        elif "run_agenda" in request.form:
            payload = create_manual_pull(franchise_id, "agenda")
            agenda_job_id = _remember_active_job("agenda", payload)
            flash("Agenda collection queued. This may take a few minutes.")
        else:
            return {"error": "unsupported dashboard action"}, 400
    # print("Job id", job_id)
    return render_template(
        "franchise.html",
        student_reports=student_reports,
        franchise_id=franchise_id,
        grade_filter=grade_filter,
        job_id=job_id,
        agenda_job_id=agenda_job_id,
    )


@app.route(
    "/franchise/<int:franchise_id>/student/<int:student_id>", methods=["GET", "POST"]
)
@login_required
@csrf_protect
async def student_view(franchise_id: int, student_id: int):
    """
    Here is a single student's page.
    Contains a full report of their grades and agenda.
    """
    active_jobs = _active_jobs()
    job_id = active_jobs.get("grade")
    agenda_job_id = active_jobs.get("agenda")

    # Load only students within the requested franchise, then select by student id.
    try:
        students = get_dashboard_students(franchise_id)
    except grade_api.ApiClientError:
        students = []

    student = _find_student(students, student_id)
    if student is None:
        return "Student not found", 404

    student_report = compute_student_report(cast(Student, student))
    if not student_report:  # still no report, failure
        return "Student not found", 404

    if request.method == "POST":  # handle db updates
        if "run_scraper" in request.form:  # update franchise grades
            payload = create_manual_pull(franchise_id, "grade", student_id)
            _remember_active_job("grade", payload)
            flash("Grade collection queued. This may take a few minutes.")
            return redirect(
                url_for(
                    "student_view", student_id=student_id, franchise_id=franchise_id
                )
            )
        if "run_agenda" in request.form:  # update student agenda
            payload = create_manual_pull(franchise_id, "agenda", student_id)
            _remember_active_job("agenda", payload)
            flash("Agenda refresh queued. This may take a few minutes.")
            return redirect(
                url_for(
                    "student_view", student_id=student_id, franchise_id=franchise_id
                )
            )
    return render_template(
        "student.html",
        student=student_report,
        job_id=job_id,
        agenda_job_id=agenda_job_id,
        franchise_id=franchise_id,
    )


@app.get("/status/<job_id>")
@login_required
async def status(job_id: str):
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        return Response(
            json.dumps({"status": "not_found"}),
            status=404,
            mimetype="application/json",
        )
    state = get_dashboard_job(job_id)
    if state:
        scope = state.get("scope")
        if not isinstance(scope, dict) or _coerce_int(scope.get("franchise_id")) != _coerce_int(
            session.get("franchise_id")
        ):
            return Response(
                json.dumps({"status": "not_found"}),
                status=404,
                mimetype="application/json",
            )
        progress = state.get("progress")
        if not isinstance(progress, dict):
            progress = {}
        total = max(_coerce_int(progress.get("total")) or 0, 0)
        attempted = min(max(_coerce_int(progress.get("attempted")) or 0, 0), total)
        steps = max(total, 1)
        terminal = state.get("status") in {"complete", "failed", "cancelled"}
        step = steps if terminal else attempted
        data = {
            "total": total,
            "step": step,
            "steps": steps,
            "pct": step / steps,
        }
        if terminal:
            _forget_active_job(job_id)
        return Response(json.dumps(data), mimetype="application/json")
    return Response(
        json.dumps({"status": "not_found"}), status=404, mimetype="application/json"
    )
