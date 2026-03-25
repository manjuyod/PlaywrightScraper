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
    grades: dict[str, dict]  # weeklydata
    status: str
    portal: str
    portal_url: str
    portal_username: str
    portal_password: str
    alt_portal_url: str | None = None
    alt_portal_username: str | None = None
    alt_portal_password: str | None = None
    agenda: dict | None = None
    # computed fields
    grades_snapshot: list[CourseGrade] | None = None
    low_grades: list[CourseGrade] | None = None
    high_grades: list[CourseGrade] | None = None
    standing: Standing | None = None

    @staticmethod
    def create(db_student: dict):
        # pprint.pprint(db_student)
        grades_json = db_student.get('weeklydata', '{}')
        if isinstance(grades_json, dict):
            grades = grades_json
        elif isinstance(grades_json, str):
            grades = json.loads(grades_json)
        else:
            grades = {}
            
        agenda_json = db_student.get('weekly_agenda', '{}')
        if isinstance(agenda_json, dict):
            agenda = agenda_json
        elif isinstance(agenda_json, str):
            agenda = json.loads(agenda_json)
        else:
            agenda = {}
            
        grades = {k: v for k, v in grades.items() if v != {}} # only rows with grades
        print("Agenda:", agenda)
        return Student(
            id=db_student['id'],
            first_name=db_student['firstname'],
            last_name=db_student['lastname'],
            grade_level=db_student['grade'],
            portal_url=db_student['portal1'],
            portal=db_student.get('portal'),
            portal_username=db_student['p1username'],
            portal_password=db_student['p1password'],
            alt_portal_url=db_student.get('portal2'),
            alt_portal_username=db_student.get('p2username'),
            alt_portal_password=db_student.get('p2password'),
            status=db_student.get('status', 'never'),
            grades=grades,
            agenda=agenda
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

def add_student(fid: int, student: Student, master_key: bytes):
    # INSERT
    weeklydata = {"2025-08-04":{},"2025-08-11":{},"2025-08-18":{},"2025-08-25":{},"2025-09-01":{},"2025-09-08":{},"2025-09-15":{},"2025-09-22":{},"2025-09-29":{},"2025-10-06":{},"2025-10-13":{},"2025-10-20":{},"2025-10-27":{},"2025-11-03":{},"2025-11-10":{},"2025-11-17":{},"2025-11-24":{},"2025-12-01":{},"2025-12-08":{},"2025-12-15":{},"2025-12-22":{},"2025-12-29":{},"2026-01-05":{},"2026-01-12":{},"2026-01-19":{},"2026-01-26":{},"2026-02-02":{},"2026-02-09":{},"2026-02-16":{},"2026-02-23":{},"2026-03-02":{},"2026-03-09":{},"2026-03-16":{},"2026-03-23":{},"2026-03-30":{},"2026-04-06":{},"2026-04-13":{},"2026-04-20":{},"2026-04-27":{},"2026-05-04":{},"2026-05-11":{},"2026-05-18":{},"2026-05-25":{},"2026-06-01":{},"2026-06-08":{},"2026-06-15":{},"2026-06-22":{},"2026-06-29":{}}
    portal = get_portal_key_from_url(student.portal_url)
    
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
        student.portal_url,
        student.portal_username,
        encrypt_field(master_key, student.portal_password),
        portal,
        json.dumps(weeklydata),
        # optionals
        student.alt_portal_url,
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

def update_student(student_id: int, student: Student, master_key: bytes):
    portal = get_portal_key_from_url(student.portal_url)
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
        student.portal_url,
        student.portal_username,
        encrypt_field(master_key, student.portal_password),
        portal,
        student.alt_portal_url,
        student.alt_portal_username,
        student.alt_portal_password,
        student_id
    )
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()

def delete_students(student_ids: list[int], master_key: bytes | None = None):
    if not student_ids:
        return
    with db_conn() as conn:
        cur = conn.cursor()
        format_strings = ','.join(['%s'] * len(student_ids))
        cur.execute(f"DELETE FROM student WHERE id IN ({format_strings})", tuple(student_ids))
        conn.commit()
        print(f"Deleted students {student_ids}")

# # Passwords, hashing and encryption
import os

from argon2.low_level import hash_secret_raw, Type

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

VERSION = b"\x01" # denotes the version of the encryption scheme
# To make students' passwords secure and visible
# 1. We encrypt using AES them using a master password as the key
# 2. We store the encrypted passwords in the database
# 3. We hash and store the master password in the database as well (per franchise?)


SALT = b'\x17\x19\xce\x12\x8a\x8d\x11\xef\x8a\x0e\x00\x15\x5d\xee\x0b\x00' # temp, should be in db

def derive_key_from_master(password: str, salt: bytes = SALT) -> bytes:
    return hash_secret_raw(
        secret=password.encode('utf-8'),
        salt=salt,
        time_cost=3,
        memory_cost=65536, # KiB
        parallelism=1,
        hash_len=32, # 256 bits for AES-256
        type=Type.ID
    )
def encrypt_field(key: bytes, plaintext: str, aad: bytes = b"") -> bytes:
    """
    Encrypts a string using AES-GCM with random nonce.
    Follows form:
        version (1 byte) || nonce (12 bytes) || ciphertext+tag
    """
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode('utf-8'), aad)
    return VERSION + nonce + ciphertext
def decrypt_field(key: bytes, blob: bytes, aad: bytes = b"") -> bytes | None:
    if not blob:
        return None
    if len(blob) < 1 + 12 + 16:
        raise ValueError("Ciphertext blob too short")
    version = blob[:1]
    if version != VERSION:
        raise ValueError(f"Unsupported version {version!r}")
    nonce = blob[1:13]
    cyphertext = blob[13:]
    try:
        return AESGCM(key).decrypt(nonce, cyphertext, aad)
    except InvalidTag:
        print("Incorrect password (or corrupted data)!")
        return None

def verify_master_password(franchise_id: int, master_password: str) -> bytes | None:
    """
    Verifies the master password by attempting to decrypt a field of an existing student
    in the franchise. Returns the DEK if successful, else None.
    """
    dek = derive_key_from_master(master_password)
    students = get_students(franchise_id)
    if not students: return dek
    
    student_pass = None
    while student_pass is None:
        # Try to decrypt the first student's password
        student = students[0]
        student_pass = student.portal_password
        
    if student_pass and isinstance(student_pass, (bytes, bytearray)):
        try:
            decrypted = decrypt_field(dek, student_pass)
            if decrypted is not None:
                return dek
        except (ValueError, InvalidTag):
            return None
    else:
        print("Apparently the student's password has not been encrypted yet.")
    
    # If we tried all students and couldn't decrypt anything, it's probably the wrong password
    # (or they all have empty passwords, which is unlikely)
    return None

def test_encryption():
    master_password = "thisismymasterpassword" # just for test
    salt = os.urandom(16) # STORED per key
    master_key = derive_key_from_master(master_password, salt) # STORED per franchise
    assert len(master_key) * 8 == 256
    
    secret = "this is my secret. shhhh" # to be encrypted
    test = encrypt_field(master_key, secret) # STORED
    
    print(f"Master PW plaintext: {master_password}")
    print(f"Master Key: {master_key.hex()}")
    print(f"Secret plaintext: {secret}")
    print(f"Encrypted secret: {test.hex()}")
    
    u_password = input("Enter password:")
    kek = derive_key_from_master(u_password, salt)
    try:
        decrypted = decrypt_field(kek, test)
    except InvalidTag:
        print("Incorrect password (or corrupted data)!")
        exit(1)
    print(decrypted.decode('utf-8'))
    
    
from typing import Any, List, Dict
def filter_group(group: list[Any], key: str | None, value, include=True) -> list[Any]:
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
    else: key_check = lambda _: True
    
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

if __name__ == "__main__":
    test_encryption()
    
    
    
    
    
