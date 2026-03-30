# builtins
import json
import pprint

# external
from flask import (
    Response,
    abort,
    flash,
    jsonify,
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
    add_student_to_session,
    app,
    dev_bypass,
    get_students_from_session,
    login_required,
    store_students_in_session,
    # update_student_in_session,
)
from ui.controllers import (
    compute_student_report,
)
from ui.ext_jobs import (
    franchise_from_job_id,
    get_status,
    is_running,
    jobs,
    start_grade_fetch_job,
)


# By default, entry is only allowed from the internal key passed by the header
@app.route("/", methods=["GET", "POST"])
async def index():
    if not dev_bypass:  # normal
        franchise_id = request.headers.get("X-Franchise", type=int)
        key = request.headers.get("X-Internal-Key")

        # direct/manual access is not allowed
        if franchise_id is None or key is None:
            session.clear()
            return abort(403)

        if key != INTERNAL_KEY:
            session.clear()
            abort(403)

        session["franchise_id"] = franchise_id
        session["authorized"] = True

        return redirect(url_for("franchise_view", franchise_id=franchise_id))
    else:  # dev only
        session["authorized"] = True
        if request.method == "POST":
            franchise_id = request.form.get("franchise_id", type=int)
            session["franchise_id"] = franchise_id
            return redirect(url_for("franchise_view", franchise_id=franchise_id))
        return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/franchise/<int:franchise_id>", methods=["GET", "POST"])
@login_required
async def franchise_view(franchise_id: int):
    """Here we show a list of students for the given franchise.
    Student data is fetched from the database.
    Comprised of the students' first/last name, portal links, most recent grades"""

    if not session.get("authorized"):
        return abort(403)

    students = get_students_from_session(franchise_id)

    if students is None:
        students = db.get_students(franchise_id)
        store_students_in_session(franchise_id, students)

    assert students is not None
    print(f"Session active keys: {session.keys()}")
    student_reports = [compute_student_report(student) for student in students]
    print(student_reports[0:1])
    job_id = f"{franchise_id}"
    if request.method == "POST":  # handle db updates
        # update franchise grades
        if "run_scraper" in request.form:
            if is_running(job_id):
                print(f"Job {job_id} already running here, or elsewhere.")
                flash(
                    "A job is already running for this franchise. Wait for it to finish, then try again."
                )
            else:
                print("Running scraper")
                flash("Starting grade collection. This may take a few minutes.")
                start_grade_fetch_job(job_id=job_id, total=len(students))
        # delete
        elif "delete_students" in request.form:
            student_ids = request.form.getlist("student_id")
            if student_ids:
                # print(f"Deleting students: {student_ids}")
                db.delete_students([int(sid) for sid in student_ids])
                flash(f"Deleted {len(student_ids)} students.")
            else:
                flash("No students selected for deletion.")
            return redirect(url_for("index"))
        else:
            # For Add/Edit, we create a student object from the form
            dek = session.get("dek")
            if not dek:
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

            student_id = request.args.get("student_id")
            assert student_id is not None, "student_id is required"
            student: Student | None = (
                filter_group(students, "id", student_id)[0] if student_id else None
            )
            db_student = {
                "id": int(student_id) if student_id else -1,
                "firstname": request.form["first_name"],
                "lastname": request.form["last_name"],
                "grade": int(request.form["grade"]),
                "portal1": request.form["portal_url"],
                "p1username": request.form["portal_username"],
                "p1password": request.form["portal_password"],
                "portal2": request.form.get("alt_portal_url"),
                "p2username": request.form.get("alt_portal_username"),
                "p2password": request.form.get("alt_portal_password"),
                "status": student.status if student else "never",
            }
            pprint.pprint(db_student)
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
    )


@app.route(
    "/franchise/<int:franchise_id>/student/<int:student_id>", methods=["GET", "POST"]
)
async def student_view(franchise_id: int, student_id: int):
    """
    Here is a single student's page.
    Contains a full report of their grades and agenda.
    """
    job_id = f"{franchise_id}_{student_id}"

    # load from session, fallback to db
    students = get_students_from_session(franchise_id)
    if students is None:  # no session, get from the database
        # print("Fetching student from db")
        student = db.get_student(student_id=student_id)
        student_report = compute_student_report(student)
        if not is_running(job_id):
            jobs.pop(job_id, None)
        add_student_to_session(franchise_id, student_report)
    else:  # session exists, get the student from the session
        student_report = filter_group(students, "id", student_id)[0]
    if not student_report:  # still no report, failure
        return "Student not found", 404

    pprint.pprint(f"Student Snapshot:{student_report.grades_snapshot}")
    pprint.pprint(f"Student Grades: {student_report.grades}")
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
    return render_template(
        "student.html", student=student_report, job_id=job_id, franchise_id=franchise_id
    )


@app.get("/status/<job_id>")
def status(job_id: str):
    state = get_status(job_id)
    # pprint.pprint(f"Status for job {job_id}: {state}")
    if state:
        if state.step == state.steps:
            session.pop(f"students_{franchise_from_job_id(job_id)}")
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
