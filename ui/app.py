import os
from flask import Flask, session, request, abort
from flask_session import Session
from dotenv import load_dotenv
from db import Student, filter_group
load_dotenv()

# To run and test use curl -i -H "X-Franchise: {franchise}" -H "X-Internal-Key: {key}" http://localhost:8080/

app = Flask(__name__,
            static_folder='static',
            template_folder='templates')

INTERNAL_KEY = os.getenv("INTERNAL_KEY")        
app.secret_key = os.getenv("SESSION_SECRET", "dev-secret-key")



# By default, entry is only allowed from the internal key passed by the header
is_deployment = os.getenv("REPLIT_DEPLOYMENT") == 1
print("Is deployment:", is_deployment)
dev_bypass = not is_deployment and int(os.getenv("DEV_BYPASS", 0)) == 1

# Session management
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = 'ui/tmp'
app.config["SESSION_FILE_THRESHOLD"] = 100
app.config["SESSION_PERMANENT"] = False

Session(app)

students_key = lambda f_id: f"students_{f_id}"

# session helpers
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

# route helpers
from functools import wraps
from flask import session, abort

def login_required(view):
    @wraps(view)
    async def wrapped_view(*args, **kwargs):
        print("Checking session")
        if not session.get("authorized"):
            abort(403)
        return await view(*args, **kwargs)
    return wrapped_view

@app.errorhandler(403) # called on forbidden access to routes
async def forbidden(e):
    print("Access forbidden")
    session.clear()
    return {"error": "access forbidden"}, 403