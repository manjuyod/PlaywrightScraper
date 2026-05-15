import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Literal, Optional

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from db_core import get_connection
from scraper.portals.utils import get_portal_key_from_url


DictRow = Mapping[str, Any]
connection = Connection


def db_conn() -> connection:
    print("db_conn(): creating connection...", flush=True)
    print("PGHOST:", os.getenv("PGHOST"), flush=True)
    print("PGDATABASE:", os.getenv("PGDATABASE"), flush=True)
    return get_connection()


def _rows_as_dicts(result) -> list[dict]:
    return [dict(row) for row in result.mappings().all()]


def fetch(query: str, params: tuple = ()) -> Optional[list[DictRow]]:
    with db_conn() as conn:
        rows = conn.exec_driver_sql(query, params)
        data = _rows_as_dicts(rows)
        return data if data else None


def fetchone(query: str, params: tuple = ()) -> Optional[DictRow]:
    with db_conn() as conn:
        row = conn.exec_driver_sql(query, params).mappings().fetchone()
        return dict(row) if row is not None else None


class Standing(StrEnum):
    Good = "Good"
    Fair = "Fair"
    Poor = "Poor"


@dataclass
class AppObject:
    pass


@dataclass
class CourseGrade(AppObject):
    course: str
    grade: float
    change: Literal[None, "+", "-"] = None


@dataclass
class Student(AppObject):
    # Student inherits fields from the database plus a few computed fields
    id: int
    grade_level: int
    first_name: str
    last_name: str
    grades: dict[str, dict]
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
    def check_status(db_student: DictRow) -> bool:
        return db_student.get("status") == "synced"

    @staticmethod
    def check_error(db_student: DictRow) -> str:
        err = db_student.get("error_msg", "")
        return err if err else ""

    @staticmethod
    def create(db_student: dict | DictRow):
        grades_json = db_student.get("weeklydata", "{}")
        if isinstance(grades_json, dict):
            grades = grades_json
        elif isinstance(grades_json, str):
            grades = json.loads(grades_json)
        else:
            grades = {}

        agenda_json = db_student.get("weekly_agenda", "{}")
        if isinstance(agenda_json, dict):
            agenda = agenda_json
        elif isinstance(agenda_json, str):
            agenda = json.loads(agenda_json)
        else:
            agenda = {}

        grades = {k: v for k, v in grades.items() if v != {}}
        return Student(
            id=db_student["id"],
            first_name=db_student["firstname"],
            last_name=db_student["lastname"],
            grade_level=db_student["grade"],
            portal_url=db_student["portal1"],
            portal=db_student["portal"],
            portal_username=db_student["p1username"],
            portal_password=db_student["p1password"],
            alt_portal_url=db_student.get("portal2"),
            alt_portal_username=db_student.get("p2username"),
            alt_portal_password=db_student.get("p2password"),
            status=db_student.get("status", "never"),
            grades=grades,
            agenda=agenda,
        )


def get_student(student_id: int, *, raw: bool = False) -> Student:
    query = "SELECT * FROM student WHERE id = %s"
    db_student = fetchone(query, (student_id,))
    if db_student is None:
        raise ValueError(f"Student with ID {student_id} not found.")

    student = Student.create(db_student)
    return student


def get_students(
    franchise_id: Optional[int] = None, *, raw: bool = False
) -> list[Student] | list[DictRow]:
    query = "SELECT * FROM student"
    if franchise_id is not None:
        cond = "WHERE franchiseid = %s"
        query += " " + cond
        params = (franchise_id,)
    else:
        params = ()

    db_students = fetch(query, params)
    if db_students is None:
        raise ValueError(f"No students found for franchise ID {franchise_id}.")

    return db_students if raw else [Student.create(db_student) for db_student in db_students]


def add_student(fid: int, student: Student, master_key: bytes):
    weeklydata = {
        "2025-08-04": {},
        "2025-08-11": {},
        "2025-08-18": {},
        "2025-08-25": {},
        "2025-09-01": {},
        "2025-09-08": {},
        "2025-09-15": {},
        "2025-09-22": {},
        "2025-09-29": {},
        "2025-10-06": {},
        "2025-10-13": {},
        "2025-10-20": {},
        "2025-10-27": {},
        "2025-11-03": {},
        "2025-11-10": {},
        "2025-11-17": {},
        "2025-11-24": {},
        "2025-12-01": {},
        "2025-12-08": {},
        "2025-12-15": {},
        "2025-12-22": {},
        "2025-12-29": {},
        "2026-01-05": {},
        "2026-01-12": {},
        "2026-01-19": {},
        "2026-01-26": {},
        "2026-02-02": {},
        "2026-02-09": {},
        "2026-02-16": {},
        "2026-02-23": {},
        "2026-03-02": {},
        "2026-03-09": {},
        "2026-03-16": {},
        "2026-03-23": {},
        "2026-03-30": {},
        "2026-04-06": {},
        "2026-04-13": {},
        "2026-04-20": {},
        "2026-04-27": {},
        "2026-05-04": {},
        "2026-05-11": {},
        "2026-05-18": {},
        "2026-05-25": {},
        "2026-06-01": {},
        "2026-06-08": {},
        "2026-06-15": {},
        "2026-06-22": {},
        "2026-06-29": {},
    }
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
        student.alt_portal_url,
        student.alt_portal_username,
        student.alt_portal_password,
    )
    with db_conn() as conn:
        result = conn.exec_driver_sql(query, params)
        maybe_db_id = result.mappings().fetchone()
        conn.commit()

    if maybe_db_id is None:
        raise ValueError("Failed to create student.")
    db_id = maybe_db_id["id"]
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
        student_id,
    )
    with db_conn() as conn:
        conn.exec_driver_sql(query, params)
        conn.commit()


def delete_students(student_ids: list[int], master_key: bytes | None = None):
    if not master_key:
        return
    if not student_ids:
        return
    with db_conn() as conn:
        conn.exec_driver_sql("DELETE FROM student WHERE id = ANY(%s)", (student_ids,))
        conn.commit()
        print(f"Deleted students {student_ids}")


def get_active_franchises() -> list[DictRow]:
    query = "SELECT franchiseid FROM spreadsheets"
    franchises = fetch(query)
    return franchises if franchises is not None else []


def check_db_connection() -> bool:
    try:
        with db_conn() as _:
            return True
    except SQLAlchemyError as e:
        print("Database connection failed:", e)
        return False


VERSION = b"\x01"
SALT = b"\x17\x19\xce\x12\x8a\x8d\x11\xef\x8a\x0e\x00\x15\x5d\xee\x0b\x00"


def derive_key_from_master(password: str, salt: bytes = SALT) -> bytes:
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=3,
        memory_cost=65536,
        parallelism=1,
        hash_len=32,
        type=Type.ID,
    )


def encrypt_field(key: bytes, plaintext: str, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return VERSION + nonce + ciphertext


def decrypt_field(key: bytes, blob: bytes, aad: bytes = b"") -> bytes | None:
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
    dek = derive_key_from_master(master_password)
    students = get_students(franchise_id, raw=True)

    if not students:
        return dek

    student_pass: bytes | None = None
    student = students[0]
    assert isinstance(student, Student)
    student_pass = student.portal_password

    if student_pass and isinstance(student_pass, (bytes, bytearray)):
        try:
            decrypted = decrypt_field(dek, bytes(student_pass))
            if decrypted is not None:
                return dek
        except (ValueError, InvalidTag):
            return None
    else:
        print("Apparently the student's password has not been encrypted yet.")
    return None


def test_encryption():
    master_password = "thisismymasterpassword"
    salt = os.urandom(16)
    master_key = derive_key_from_master(master_password, salt)
    assert len(master_key) * 8 == 256

    secret = "this is my secret. shhhh"
    test = encrypt_field(master_key, secret)

    print(f"Master PW plaintext: {master_password}")
    print(f"Master Key: {master_key.hex()}")
    print(f"Secret plaintext: {secret}")
    print(f"Encrypted secret: {test.hex()}")

    u_password = input("Enter password:")
    kek = derive_key_from_master(u_password, salt)
    try:
        decrypted = decrypt_field(kek, test)
        if decrypted is not None:
            print(decrypted.decode("utf-8"))
    except InvalidTag:
        print("Incorrect password (or corrupted data)!")
        exit(1)


from typing import Callable, Dict
def filter_group(group: list[Any], key: str | None, value, include=True) -> list[Any]:
    if key is not None:
        key_check: Callable[[Dict[str, Any]], bool] = lambda group: key in group.keys()
    else:
        key_check: Callable[[Dict[str, Any]], bool] = lambda _: True

    if include:
        def value_check(group):
            return group[key] == value
    else:
        def value_check(group):
            return group[key] != value

    filtered = []
    for obj in group:
        if isinstance(obj, AppObject):
            _obj = asdict(obj)
        else:
            _obj = obj
        if key_check(_obj) and value_check(_obj):
            filtered.append(obj)

    return filtered


if __name__ == "__main__":
    test_encryption()
