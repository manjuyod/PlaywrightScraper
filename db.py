import os
import pprint
from asyncio import Task
from scraper.portals.utils import get_portal_key_from_url
import psycopg2 as pg
from psycopg2.extensions import connection
from psycopg2.extras import DictCursor, DictRow
from dataclasses import dataclass, asdict
from typing import Literal
from enum import StrEnum
import json

def db_conn() -> connection:
    print("db_conn(): creating connection...", flush=True)
    return pg.connect(
        host=os.getenv("PGHOST"),
        database=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
        port=os.getenv("PGPORT"),
        sslmode='require'
    )

def fetch(query: str, one=False) -> list | dict | None:
    with db_conn() as conn:
        cur = conn.cursor(cursor_factory=DictCursor)
        cur.execute(query)
        return cur.fetchone() if one else cur.fetchall()
        
# # Data Types used within the app
class Standing(StrEnum):
    Good = 'Good'
    Fair = 'Fair'
    Poor = 'Poor' 
@dataclass
class AppObject:
    pass
@dataclass
class CourseGrade(AppObject):
    course: str
    grade: float
    change: Literal[None, '+', '-'] = None

@dataclass
class Student(AppObject):
    # Student inherits fields from the database plus a few computed fields
    id: int
    grade_level: int 
    first_name: str
    last_name: str
    grades: dict # weeklydata
    status: str
    portal: str
    portal_link: str
    portal_username: str
    portal_password: str
    alt_portal_link: str | None = None
    alt_portal_username: str | None = None
    alt_portal_password: str | None = None
    grades_snapshot: list[CourseGrade] | None = None
    low_grades: list[CourseGrade] | None = None
    high_grades: list[CourseGrade] | None = None
    standing: Standing | None = None
    agenda: dict | None = None
    @staticmethod
    def create(db_student: dict):
        # pprint.pprint(db_student)
        grades_json = db_student.get('weeklydata', '{}')
        grades = json.loads(grades_json)
        
        grades = {k: v for k, v in grades.items() if v != {}} # only rows with grades
        return Student(
            id=db_student['id'],
            first_name=db_student['firstname'],
            last_name=db_student['lastname'],
            grade_level=db_student['grade'],
            portal_link=db_student['portal1'],
            portal=db_student.get('portal'),
            portal_username=db_student['p1username'],
            portal_password=db_student['p1password'],
            alt_portal_link=db_student.get('portal2'),
            alt_portal_username=db_student.get('p2username'),
            alt_portal_password=db_student.get('p2password'),
            status=db_student.get('status', 'never'),
            grades=grades,
            agenda=None
        )
def get_student(student_id: int) -> Student:
    query = f"SELECT * FROM student WHERE id = {student_id}"
    db_student = fetch(query, one=True)
    student = Student.create(db_student)
    return student

def get_students(franchise_id: int | None = None) -> list[Student]:
    print("fetching students for franchise ID:", franchise_id)
    query = "SELECT * FROM student"
    students = fetch(query)
    if franchise_id is not None:
        students = filter_group(students, 'franchiseid', franchise_id)
    return [Student.create(student) for student in students]

def add_student(fid: int, student: Student):
    # INSERT
    weeklydata = {"2025-08-04":{},"2025-08-11":{},"2025-08-18":{},"2025-08-25":{},"2025-09-01":{},"2025-09-08":{},"2025-09-15":{},"2025-09-22":{},"2025-09-29":{},"2025-10-06":{},"2025-10-13":{},"2025-10-20":{},"2025-10-27":{},"2025-11-03":{},"2025-11-10":{},"2025-11-17":{},"2025-11-24":{},"2025-12-01":{},"2025-12-08":{},"2025-12-15":{},"2025-12-22":{},"2025-12-29":{},"2026-01-05":{},"2026-01-12":{},"2026-01-19":{},"2026-01-26":{},"2026-02-02":{},"2026-02-09":{},"2026-02-16":{},"2026-02-23":{},"2026-03-02":{},"2026-03-09":{},"2026-03-16":{},"2026-03-23":{},"2026-03-30":{},"2026-04-06":{},"2026-04-13":{},"2026-04-20":{},"2026-04-27":{},"2026-05-04":{},"2026-05-11":{},"2026-05-18":{},"2026-05-25":{},"2026-06-01":{},"2026-06-08":{},"2026-06-15":{},"2026-06-22":{},"2026-06-29":{}}
    portal = get_portal_key_from_url(student.portal_link)
    
    query = """
            INSERT INTO Student(
                franchiseid, firstname, lastname, grade,
                portal1, p1username, p1password, portal, weeklydata,
                portal2, p2username, p2password 
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """
                 
    params = (
        fid,
        student.first_name,
        student.last_name,
        student.grade_level,
        student.portal_link,
        student.portal_username,
        student.portal_password,
        portal,
        json.dumps(weeklydata),
        # optionals
        student.alt_portal_link,
        student.alt_portal_username,
        student.alt_portal_password
    )
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(query, params,)
        conn.commit()
        
    # get the last rowid and return the new student
    db_id = cur.fetchone()[0]
    return get_student(db_id)

def update_student(student_id: int, student: Student):
    portal = get_portal_key_from_url(student.portal_link)
    query = """
            UPDATE Student SET 
                firstname = %s, lastname = %s, grade = %s,
                portal1 = %s, p1username = %s, p1password = %s, portal = %s,
                portal2 = %s, p2username = %s, p2password = %s
            WHERE id = %s
            """
    params = (
        student.first_name,
        student.last_name,
        student.grade_level,
        student.portal_link,
        student.portal_username,
        student.portal_password,
        portal,
        student.alt_portal_link,
        student.alt_portal_username,
        student.alt_portal_password,
        student_id
    )
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()

def delete_students(student_ids: list[int]):
    if not student_ids:
        return
    with db_conn() as conn:
        cur = conn.cursor()
        format_strings = ','.join(['%s'] * len(student_ids))
        cur.execute(f"DELETE FROM student WHERE id IN ({format_strings})", tuple(student_ids))
        conn.commit()
        print(f"Deleted students {student_ids}")
        
import bcrypt
# # Passwords and encryption
# To make students' passwords secure and visible
# 1. We encrypt using AES them using a master password as the key
# 2. We store the encrypted passwords in the database
# 3. We hash and store the master password in the database as well (per franchise?)
def encrypt_password(password: str) -> str:
    pass
def decrypt_password(encrypted_password: str) -> str:
    pass
def hash_master_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
def check_master_password(password: str, franchise_id: int) -> bool:
    master_password_hash = "" # get from db
    return bcrypt.checkpw(password.encode('utf-8'), master_password_hash.encode('utf-8'))

from typing import Any, List, Dict
def filter_group(group: list[Any], key: str | None, value, include=True) -> list[dict[str, str]]:
    """
    Filters a list of dictionaries by a particular key - value pair.
    Args:
        group: dictionary to be filtered
        key: key to match
        value: value to match
        include: tells whether to include or exclude entries that match the criteria
    Returns:
        The filtered group
    """
    if key is not None:
        key_check = lambda elem: key in elem.keys()
    else: key_check = lambda elem: True
    
    if include:
        value_check = lambda elem: value in elem.values()
    else: value_check = lambda elem: value not in elem.values()
    
    filtered = []
    for obj in group:
        if isinstance(obj, AppObject): # we can filter app objects by treating them as dictionaries
            _obj = asdict(obj)
        else: _obj = obj
        if key_check(_obj) and value_check(_obj):
            filtered.append(obj)
        
    return filtered
