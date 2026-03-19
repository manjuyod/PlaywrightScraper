"""
Module for tracking the scraper status and info
i.e. Student Status (errored, synced, no grades)
"""
from db import get_students
from rapidfuzz import fuzz
import pprint
def course_names_similar(course_name1: str, course_name2: str) -> bool:
    """
    This function checks if two course names are similar even if there are semantic differences.
    "American History" should not be different from "1: AMERICAN HISTORY" although they contain differences
    """
    course_name1 = course_name1.lower().replace(" ", "")
    course_name2 = course_name2.lower().replace(" ", "")
    similarity = fuzz.token_set_ratio(course_name1, course_name2)
    print(f"{course_name1} vs {course_name2}: {similarity}%")
    close_enough =  similarity > 80
    return close_enough

if __name__ == '__main__':
    all_students = get_students()
    for student in all_students:
        student_grades_json = {}
        grades = student.grades
        for week, grades in grades.items():
            pprint.pprint(week)
            pprint.pprint(grades)
            break
        break        
    print("Are these the same course?", course_names_similar('7: ATHLETIC WEIGHT TRAINING', "Athletic Weight Training"))