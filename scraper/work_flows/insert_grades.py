# scraper/work_flows/insert_grades.py
import json
from scraper.runner import db_conn, DictCursor
import psycopg2
import pathlib
from datetime import date, timedelta

PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "config/students.db"
JSONL_PATH = PROJECT_ROOT / "output/phase1totuples/grades.jsonl"

def get_monday_anchor() -> str:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")

def safe_load_json(s: str):
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}

def clear_grades_jsonl(path: pathlib.Path = JSONL_PATH) -> None:
    """
    Delete the grades.jsonl file if it exists.
    Never raises; logs outcome for visibility.
    """
    try:
        if path.exists():
            path.unlink()
            print(f"Deleted input file: {path}")
        else:
            print(f"No input file to delete at: {path}")
    except Exception as e:
        print(f"Warning: could not delete {path}: {e}")

def insert_grades():
    monday_anchor = get_monday_anchor()
    print(f"Using Monday anchor date: {monday_anchor}")
    print(f"DB: {DB_PATH}")
    print(f"Input: {JSONL_PATH}")

    try:
        with db_conn() as conn , open(JSONL_PATH, "r", encoding="utf-8") as f:
            cur = conn.cursor(cursor_factory=DictCursor)

            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"Skipping non-JSON line: {raw[:120]}…")
                    continue

                # Skip error payloads
                if "error" in data:
                    print(f"Skipping error entry for {data.get('id')}: {data.get('error')}")
                    continue

                # Accept both NEW and OLD shapes:
                # NEW: {"db_id": 1, "id": "...", "parsed_grades": {...}}
                # OLD: {"db_id": 1, "id": "...", "grades": {"parsed_grades": {...}}}
                student_id = data.get("db_id")
                grades = data.get("parsed_grades")
                if grades is None and isinstance(data.get("grades"), dict):
                    grades = data["grades"].get("parsed_grades")

                if not student_id or not isinstance(grades, dict) or not grades:
                    print(f"Skipping line (missing student_id or parsed_grades): {raw[:120]}…")
                    continue

                # Fetch existing WeeklyData
                cur.execute("SELECT WeeklyData FROM Student WHERE ID = %s", (student_id,))
                row = cur.fetchone()
                if not row:
                    print(f"Student with ID '{student_id}' not found.")
                    continue

                weekly_data = safe_load_json(row["weeklydata"])

                # Update current week's bucket
                weekly_data[monday_anchor] = grades

                # Persist
                cur.execute(
                    "UPDATE Student SET WeeklyData = %s WHERE ID = %s",
                    (json.dumps(weekly_data, ensure_ascii=False), student_id)
                )
                print(f"Updated student ID {student_id} for week {monday_anchor} with {len(grades)} courses.")

            conn.commit()

    except FileNotFoundError:
        print(f"Error: Output file not found at {JSONL_PATH}")
    except psycopg2.Error as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    try:
        insert_grades()
    finally:
        clear_grades_jsonl()
