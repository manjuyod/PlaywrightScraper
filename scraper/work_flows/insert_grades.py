# scraper/work_flows/insert_grades.py
import json
import sqlite3
import pathlib
from datetime import date, timedelta

# Correctly navigate up three levels to the project root
PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "config/students.db"
JSONL_PATH = PROJECT_ROOT / "output/phase1totuples/grades.jsonl"

def get_monday_anchor():
    """Returns the date of the most recent Monday."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")

def insert_grades():
    monday_anchor = get_monday_anchor()
    print(f"Using Monday anchor date: {monday_anchor}")

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            with open(JSONL_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    data = json.loads(line)
                    student_id = data.get("db_id")
                    grades = data.get("grades", {}).get("parsed_grades")

                    if not student_id or not isinstance(grades, dict):
                        print(f"Skipping line due to missing data or incorrect format: {line.strip()}")
                        continue

                    # First, get the student's current WeeklyData
                    cursor.execute("SELECT WeeklyData FROM Student WHERE ID = ?", (student_id,))
                    row = cursor.fetchone()

                    if not row:
                        print(f"Student with ID '{student_id}' not found in the database.")
                        continue

                    weekly_data_json = row["WeeklyData"]
                    weekly_data = json.loads(weekly_data_json)

                    # Update the dictionary for the current week
                    weekly_data[monday_anchor] = grades

                    # Write the updated JSON back to the database
                    cursor.execute(
                        "UPDATE Student SET WeeklyData = ? WHERE ID = ?",
                        (json.dumps(weekly_data), student_id)
                    )
                    print(f"Successfully updated grades for student ID {student_id} for the week of {monday_anchor}.")
            
            conn.commit()

    except FileNotFoundError:
        print(f"Error: Output file not found at {JSONL_PATH}")
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {JSONL_PATH}")
    except sqlite3.Error as e:
        print(f"Database error: {e}")

if __name__ == "__main__":
    insert_grades()
