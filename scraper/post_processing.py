import json
import os
from pathlib import Path

def process_grades(input_path: Path, output_path: Path):
    """
    Reads raw grade data from a JSONL file, processes it into a unified
    JSON report, and deletes the source file.

    Args:
        input_path (Path): The path to the input grades.jsonl file.
        output_path (Path): The path to write the processed JSON report.
    """
    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        return

    processed_data = {}
    with open(input_path, 'r') as f:
        for line in f:
            try:
                data = json.loads(line)
                student_id = data.get("db_id")
                grades = data.get("grades", {}).get("parsed_grades", [])
                if not student_id or not grades:
                    continue

                student_grades = {}
                for grade_info in grades:
                    course_name = grade_info.get("course_name")
                    quarter_grade = grade_info.get("quarter_grade", {})
                    percentage = quarter_grade.get("percentage")
                    letter_grade = quarter_grade.get("letter_grade")

                    if course_name:
                        grade_value = None
                        if percentage is not None:
                            if isinstance(percentage, str):
                                try:
                                    grade_value = float(percentage.replace('%', '').strip())
                                except ValueError:
                                    grade_value = letter_grade # Fallback if conversion fails
                            else:
                                grade_value = float(percentage)
                        elif letter_grade:
                            grade_value = letter_grade
                        
                        if grade_value is not None:
                            student_grades[course_name] = grade_value

                if student_grades:
                    processed_data[student_id] = student_grades
            except json.JSONDecodeError:
                print(f"Warning: Could not decode JSON from line: {line.strip()}")
            except (AttributeError, TypeError) as e:
                print(f"Warning: Could not process line due to unexpected structure: {line.strip()} - Error: {e}")

    # Ensure the output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write the processed data to the output file
    with open(output_path, 'w') as f:
        json.dump(processed_data, f, indent=2)

    # Delete the original file
    #os.remove(input_path)
    print(f"Successfully processed grades and saved to {output_path}")
    print(f"Deleted source file: {input_path}")

if __name__ == '__main__':
    # For manual testing
    project_root = Path(__file__).parent.parent
    input_file = project_root / "output" / "phase1totuples" / "grades.jsonl"
    output_file = project_root / "output" / "phase2todf" / "grades_report.json"
    process_grades(input_file, output_file)
