# builtins
import json
from collections.abc import Mapping
from typing import cast
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

import db as db
from db import (
    Student,
    filter_group,
)
from ui.app import (
    INTERNAL_KEY,
    clear_login_failures,
    app,
    DEV_BYPASS,
    get_students_from_session,
    csrf_protect,
    is_login_rate_limited,
    record_login_failure,
    set_session_state,
    login_required,
    store_students_in_session,
    # update_student_in_session,
)
from ui.auth import crm_login
from ui.controllers import (
    compute_student_report,
    check_students_status,
)
from ui.ext_jobs import (
    franchise_from_job_id,
    get_status,
    is_running,
    jobs,
    start_agenda_fetch_job,
    start_grade_fetch_job,
    run_job,
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


# By default, entry is only allowed from the internal key passed by the header
@app.route("/", methods=["GET", "POST"])
async def index():
    if DEV_BYPASS:  # dev only route
        set_session_state(session_type="dev", franchise_id=None)
        return redirect(url_for("health"))

    if not DEV_BYPASS:  # normal
        franchise_id = request.headers.get("X-Franchise", type=int)
        key = request.headers.get("X-Internal-Key")

        # direct/manual access is not allowed
        if franchise_id is None or key is None:
            return redirect(url_for("login"))

        if key != INTERNAL_KEY:
            return redirect(url_for("login"))

        set_session_state(session_type="internal", franchise_id=franchise_id)

        return redirect(url_for("franchise_view", franchise_id=franchise_id))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
@csrf_protect
async def login():
    if request.method == "GET":
        if session.get("authorized"):
            if session.get("session_type") == "crm":
                return redirect(
                    url_for("franchise_view", franchise_id=session.get("franchise_id"))
                )
            return redirect(url_for("health"))
        return render_template("login.html")

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    remote_addr = request.remote_addr

    if is_login_rate_limited(remote_addr, username):
        flash("Too many sign-in attempts. Try again later.")
        return render_template("login.html"), 429

    if username == "test@test.com" and password == "test":
        set_session_state(session_type="health_test", franchise_id=None)
        clear_login_failures(remote_addr, username)
        return redirect(url_for("health"))

    login_result = crm_login(username=username, password=password)

    if not login_result.authenticated or login_result.franchise_id is None:
        record_login_failure(remote_addr, username)
        flash("Invalid username or password.")
        return redirect(url_for("login"))

    clear_login_failures(remote_addr, username)
    set_session_state(
        session_type="crm",
        franchise_id=login_result.franchise_id,
        role=login_result.role,
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
    all_students = db.fetch("select * from student")
    if not all_students:
        return "No students found in the database.", 500
    all_students_ct = len(all_students)

    synced_ct = len(filter_group(all_students, "status", "synced"))
    bad_login_ct = check_students_status(all_students).get("bad_logins", 0)
    active_franchises = db.get_active_franchises()
    health_info: list[dict] = [] # list of dicts per active franchise with keys: id, active_students, synced_students, errors, last_updated

    # count_franchise_students = 0
    for franchise in active_franchises:
        fid = franchise["franchiseid"]
        f_students = filter_group(all_students, "franchiseid", fid)
        f_health = check_students_status(f_students)
        f_health["id"] = fid
        health_info.append(f_health)

    return render_template(
        "health.html",
        health=health_info,
        count_all=all_students_ct,
        count_synced=synced_ct,
        count_bad_logins=bad_login_ct,
        jobs=jobs,
    )



@app.route("/franchise/<int:franchise_id>", methods=["GET", "POST"])
@login_required
@csrf_protect
async def franchise_view(franchise_id: int):
    """Here we show a list of students for the given franchise.
    Student data is fetched from the database.
    Comprised of the students' first/last name, portal links, most recent grades"""
    students = get_students_from_session(franchise_id)

    if students is None:
        students = db.get_students(franchise_id)
        students = cast(list[Student], students)
        store_students_in_session(franchise_id, students)

    assert students is not None
    student_reports = [compute_student_report(student) for student in students]
    # print(student_reports[0:1])
    job_id = f"{franchise_id}"
    agenda_job_id = f"{franchise_id}_agenda"
    if request.method == "POST":  # handle db updates
        # update franchise grades//agenda
        if "run_scraper" in request.form:
            run_job(job_id, len(students), "grade")
        elif "run_agenda" in request.form:
            run_job(job_id, len(students), "agenda")
        # delete
        elif "delete_students" in request.form:
            # this should also probably gate on dek
            allowed_student_ids = {
                _coerce_int(_student_value(student, "id"))
                for student in students
            }
            allowed_student_ids.discard(None)
            student_ids = [
                sid
                for sid in (
                    _coerce_int(raw_sid)
                    for raw_sid in request.form.getlist("student_id")
                )
                if sid is not None
            ]
            if student_ids:
                if any(sid not in allowed_student_ids for sid in student_ids):
                    return {"error": "invalid student selection"}, 403
                db.delete_students(student_ids)
                flash(f"Deleted {len(student_ids)} students.")
            else:
                flash("No students selected for deletion.")
            return redirect(url_for("index"))
        elif "add_student" in request.form or "edit_student" in request.form:
            # For Add/Edit, we create a student object from the form
            dek = session.get("dek")
            if not dek: # gate on master password for any operation that requires the dek
                master_password = request.form.get("master_password")
                if master_password:
                    dek = db.verify_master_password(franchise_id, master_password)
                    if dek:
                        session["dek"] = dek
                    else:
                        flash("Incorrect master password.")
                        return redirect(
                            url_for("franchise_view", franchise_id=franchise_id)
                        )
                else:
                    flash("Master password required.")
                    return redirect(
                        url_for("franchise_view", franchise_id=franchise_id)
                    )

            student_id = request.args.get("student_id", type=int)
            existing_student = _find_student(students, student_id) if student_id else None
            if "edit_student" in request.form and existing_student is None:
                return "Student not found", 404
            db_student = {
                "id": int(student_id) if student_id else -1,
                "firstname": request.form["first_name"],
                "lastname": request.form["last_name"],
                "grade": int(request.form["grade"]),
                "portal1": request.form["portal_url"],
                "portal": _student_value(existing_student, "portal") if existing_student else "",
                "p1username": _form_or_existing(
                    "portal_username", existing_student, "portal_username"
                ),
                "p1password": _form_or_existing(
                    "portal_password", existing_student, "portal_password"
                ),
                "portal2": request.form.get("alt_portal_url"),
                "p2username": _form_or_existing(
                    "alt_portal_username", existing_student, "alt_portal_username"
                ),
                "p2password": _form_or_existing(
                    "alt_portal_password", existing_student, "alt_portal_password"
                ),
                "status": _student_value(existing_student, "status") if existing_student else "never",
            }
            student = Student.create(db_student)
            # add
            if "add_student" in request.form:
                # print(f"Adding student {student.first_name}")
                new_student = db.add_student(franchise_id, student, dek)
                flash(f"Added student {new_student.first_name}")
                return redirect(
                    url_for(
                        "student_view",
                        student_id=new_student.id,
                        franchise_id=franchise_id,
                    )
                )
            # edit
            elif "edit_student" in request.form:
                # print(f"Updating student {student_id}, {student.first_name}")
                db.update_student(
                    student_id=int(student_id), student=student, master_key=dek
                )
                flash(f"Updated student {student.first_name}")
                return redirect(url_for("franchise_view", franchise_id=franchise_id))
            else:
                return "Invalid form submission", 400
    # print("Job id", job_id)
    return render_template(
        "franchise.html",
        student_reports=student_reports,
        franchise_id=franchise_id,
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
    job_id = f"{franchise_id}_{student_id}"
    agenda_job_id = f"{franchise_id}_{student_id}_agenda"

    # load only students within the requested franchise, then select by student id.
    students = get_students_from_session(franchise_id)
    if students is None:
        try:
            students = cast(list[Student], db.get_students(franchise_id))
        except ValueError:
            students = []
        store_students_in_session(franchise_id, students)

    student = _find_student(students, student_id)
    if student is None:
        return "Student not found", 404

    student_report = compute_student_report(cast(Student, student))
    if not is_running(job_id):
        jobs.pop(job_id, None)
    if not student_report:  # still no report, failure
        return "Student not found", 404

    if request.method == "POST":  # handle db updates
        if "run_scraper" in request.form:  # update franchise grades
            if is_running(job_id):
                # print(f"Job {job_id} already running.")
                flash(
                    "A job is already running for this franchise. Wait for it to finish, then try again."
                )
            else:
                # print("Running scraper")
                flash("Starting grade collection. This may take a few minutes.")
                start_grade_fetch_job(job_id, total=1)
            return redirect(
                url_for(
                    "student_view", student_id=student_id, franchise_id=franchise_id
                )
            )
        if "run_agenda" in request.form:  # update student agenda
            if is_running(agenda_job_id):
                flash(
                    "An agenda refresh job is already running for this student. Wait for it to finish, then try again."
                )
            else:
                flash("Starting agenda refresh. This may take a few minutes.")
                start_agenda_fetch_job(agenda_job_id, total=1)
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
    state = get_status(job_id)
    if state:
        if state.step == state.steps:
            session.pop(f"students_{franchise_from_job_id(job_id)}", None)
        data = {
            "total": state.total,
            "step": state.step,
            "steps": state.steps,
            "pct": state.pct,
        }
        return Response(json.dumps(data), mimetype="application/json")
    return Response(
        json.dumps({"status": "not_found"}), status=404, mimetype="application/json"
    )
