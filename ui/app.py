import os
from flask import Flask, session
from flask_session import Session
from dotenv import load_dotenv
from db import Student, filter_group
load_dotenv()

app = Flask(__name__,
            static_folder='static',
            template_folder='templates')
app.secret_key = os.getenv("SESSION_SECRET", "dev-secret-key")

# Session management
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = 'ui/tmp'
app.config["SESSION_FILE_THRESHOLD"] = 100
app.config["SESSION_PERMANENT"] = False

Session(app)

students_key = lambda f_id: f"students_{f_id}"

def get_students_from_session(franchise_id: int) -> list[Student] | None:
    s_key = students_key(franchise_id)
    students: list[Student] = session.get(s_key, None)
    return students

def store_students_in_session(franchise_id: int, students: list[Student]):
    s_key = students_key(franchise_id)
    session[s_key] = students 
    
def add_student_to_session(franchise_id: int, student: Student):
    s_key = students_key(franchise_id)
    session[s_key].append(student)
    
def update_student_in_session(franchise_id: int, student: Student):
    s_key = students_key(franchise_id)
    student_removed = filter_group(session[s_key], "id", student.id, include=False)
    session[s_key] = student_removed.append(student)
    