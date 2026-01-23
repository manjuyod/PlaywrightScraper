import db
import json
from app import app, get_students_from_session, store_students_in_session
from flask import render_template, redirect, url_for, request, flash, session, Response
import db
from db import filter_group, Student
from controller import *
from pprint import pprint, pformat
from ext_jobs import start_grade_fetch_job, jobs, get_status, is_running
from ui.ext_jobs import franchise_from_job_id


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        session['franchise_id'] = int(request.form['franchise_id'])
        return redirect(url_for('franchise_view', franchise_id=int(request.form['franchise_id'])))
    session.clear()
    return render_template('index.html')
    
    
@app.route('/franchise/<int:franchise_id>', methods=['GET', 'POST'])
async def franchise_view(franchise_id: int):
    """Here we show a list of students for the given franchise.
        Student data is fetched from the database.
        Comprised of the students' first/last name, portal links, most recent grades"""
    session['franchise_id'] = franchise_id
    
    students = get_students_from_session(franchise_id)
    if students is None:
        students = db.get_students(franchise_id)
        store_students_in_session(franchise_id, students)
        
    print(f"Session active keys: {session.keys()}")
    student_reports = [compute_student_report(student) for student in students]
    job_id = f"{franchise_id}"
    if request.method == 'POST': # handle db updates
        # update franchise grades
        if 'run_scraper' in request.form:
            if is_running(job_id):
                print(f"Job {job_id} already running here, or elsewhere.")
                flash("A job is already running for this franchise. Wait for it to finish, then try again.")
            else:
                print("Running scraper")
                flash("Starting grade collection. This may take a few minutes.")
                start_grade_fetch_job(job_id=job_id, total=len(students))
        # delete
        elif 'delete_students' in request.form:
            student_ids = request.form.getlist('student_id')
            if student_ids:
                print(f"Deleting students: {student_ids}")
                db.delete_students([int(sid) for sid in student_ids])
                flash(f"Deleted {len(student_ids)} students.")
            else:
                flash("No students selected for deletion.")
            return redirect(url_for('index'))
        else:
            # For Add/Edit, we create a student object from the form
            student_id = request.args.get('student_id')
            db_student = {
                'id': int(student_id) if student_id else -1,
                'firstname': request.form['first_name'],
                'lastname': request.form['last_name'],
                'grade': int(request.form['grade']),
                'portal1': request.form['portal_url'],
                'p1username': request.form['portal_username'],
                'p1password': request.form['portal_password'],
                'portal2': request.form.get('alt_portal_url'),
                'p2username': request.form.get('alt_portal_username'),
                'p2password': request.form.get('alt_portal_password'),
                'status': 'never',
            }
            pprint(db_student)
            student = Student.create(db_student)
            # add
            if 'add_student' in request.form:
                print(f"Adding student {student.first_name}")
                new_student = db.add_student(franchise_id, student)
                flash(f"Added student {new_student.first_name}")
                return redirect(url_for('student_view', student_id=new_student.id, franchise_id=franchise_id))
            # edit
            elif 'edit_student' in request.form:
                print(f"Updating student {student_id}, {student.first_name}")
                db.update_student(student_id=int(student_id), student=student)
                flash(f"Updated student {student.first_name}")
                return redirect(url_for('franchise_view', franchise_id=franchise_id))
            else:
                return "Invalid form submission", 400

    return render_template('franchise.html', student_reports=student_reports, franchise_id=franchise_id, job_id=job_id)

@app.route('/franchise/<int:franchise_id>/student/<int:student_id>', methods=['GET', 'POST'])
async def student_view(franchise_id: int, student_id: int):
    """
    Here is a single student's page.
    Contains a full report of their grades and agenda.
    """
    job_id = f'{franchise_id}_{student_id}'
    students: list[Student] = get_students_from_session(franchise_id)
    
    if students is None:
        print("Fetching student from db")
        student = db.get_student(student_id=student_id)
        if not is_running(job_id):
            jobs.pop(job_id, None)
    else:
        student = filter_group(students, 'id', student_id)[0]
    if not student:
        return "Student not found", 404

    
    if request.method == 'POST': # handle db updates
        # update franchise grades
        if 'run_scraper' in request.form:            
            if is_running(job_id):
                print(f"Job {job_id} already running.")
                flash("A job is already running for this franchise. Wait for it to finish, then try again.")
            else:
                print("Running scraper")
                flash("Starting grade collection. This may take a few minutes.")
                start_grade_fetch_job(job_id, total=1)
            return redirect(url_for('student_view', student_id=student_id, franchise_id=franchise_id))

    student_report = compute_student_report(student)
    return render_template('student.html', student=student_report, job_id=job_id, franchise_id=franchise_id)

@app.get('/status/<job_id>')
def status(job_id: str):
    state = get_status(job_id) 
    pprint(f"Status for job {job_id}: {state}")
    if state:
        if state.step == state.steps:
            session.pop(f'students_{franchise_from_job_id(job_id)}')
        data = {
            "total": state.total,
            "step": state.step,
            "steps": state.steps,
            "pct": state.pct
        }
        return Response(json.dumps(data), mimetype='application/json')
    return Response(json.dumps({"status": "not_found"}), status=404, mimetype='application/json')

    
    