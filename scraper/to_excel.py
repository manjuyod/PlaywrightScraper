import json
import pandas as pd
from pathlib import Path
import os

def convert_to_excel(input_path: Path, output_path: Path, sheet_name: str):
    """
    Reads a JSON grade report and converts it into a formatted Excel file,
    then deletes the source JSON file.

    Args:
        input_path (Path): The path to the input grades_report.json file.
        output_path (Path): The path to write the output Excel file.
        sheet_name (str): The name of the sheet in the Excel workbook.
    """
    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        return

    with open(input_path, 'r') as f:
        data = json.load(f)

    # Prepare data for Excel
    excel_rows = []
    # Header row
    excel_rows.append(['', 'Test Grade'])

    for student_id, courses in data.items():
        # Student ID row
        excel_rows.append([student_id, ''])
        
        # Course and grade rows
        for course, grade in courses.items():
            excel_rows.append([course, grade])
            
        # Two blank rows after each student
        excel_rows.append(['', ''])
        excel_rows.append(['', ''])

    # Create DataFrame
    df = pd.DataFrame(excel_rows)

    # Ensure the output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to Excel
    df.to_excel(output_path, index=False, header=False, sheet_name=sheet_name)
    print(f"Successfully created Excel report at: {output_path}")

    # Delete the source JSON file
    #os.remove(input_path)
    #print(f"Deleted source file: {input_path}")

if __name__ == '__main__':
    project_root = Path(__file__).parent.parent
    input_file = project_root / "output" / "phase2todf" / "grades_report.json"
    output_file = project_root / "output" / "phase21toexcelfortest" / "grade_test.xlsx"
    sheet = "Grades"
    convert_to_excel(input_file, output_file, sheet)
