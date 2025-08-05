# config/dbCreate/verify_student.py
import sqlite3
import pathlib
import json

DB_PATH = pathlib.Path(__file__).parent.parent / "students.db"

def verify_student(db_path, student_id):
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM Student WHERE ID = ?", (student_id,))
            row = cursor.fetchone()
            
            if row:
                print(f"WeeklyData for student ID {student_id}:")
                # Parse and pretty-print the JSON
                weekly_data = json.loads(row["WeeklyData"])
                print(json.dumps(weekly_data, indent=2))
            else:
                print(f"No student found with ID {student_id}")

    except sqlite3.Error as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    verify_student(DB_PATH, 1)
