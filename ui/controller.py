from db import CourseGrade, Standing, Student
# routes
def get_most_recent_grades(grades: dict) -> list[CourseGrade]:
    """
    Flips the dictionary, so the most recent grade batch is at the end
    Filters out non-numeric grades
    Returns a list of CourseGrade objects
    """
    if len(grades) == 0: return []
    raw_recent_grades: dict = list(grades.items())[-1][1]
    return [CourseGrade(k, v) for k, v in raw_recent_grades.items() if isinstance(v, float)]

def get_second_most_recent_grades(grades: dict) -> list[CourseGrade]:
    """
    Flips the dictionary, so the most recent grade batch is at the end
    Filters out non-numeric grades
    Returns a list of CourseGrade objects
    """
    if len(grades) <= 1: return []
    raw_recent_grades: dict = list(grades.items())[-2][1]
    return [CourseGrade(k, v) for k, v in raw_recent_grades.items() if isinstance(v, float)]

def compute_grade_changes(grades: list[CourseGrade], prev_grades: list[CourseGrade]):
    """
    Computes the change in grade between two batches of grades
    Modifies the grades in place
    """
    for grade, prev_grade in zip(grades, prev_grades):
        grade.change = '+' if grade.grade > prev_grade.grade else '-' if grade.grade < prev_grade.grade else None
    # return '+' if grade > prev_grade else '-' if grade < prev_grade else None

def compute_student_report(student: Student) -> Student:
    """
    Fills computed fields for a student 
    e.g. low_scores, high_scores, standing
    """
    most_recent_grades = get_most_recent_grades(student.grades)
    second_most_recent_grades = get_second_most_recent_grades(student.grades)
    compute_grade_changes(most_recent_grades, second_most_recent_grades)
    student.grades_snapshot = most_recent_grades

    if len(most_recent_grades) == 0: return student

    # sort the grades, lowest to highest
    sorted_grades = sorted(most_recent_grades, key=lambda x: x.grade)

    # highest/lowest 3 grades
    student.low_grades = sorted_grades[0:3]
    student.high_grades = sorted_grades[-2:]

    # compute score standing
    min_score_threshold = 70
    # lowest grade in the batch
    min_score = sorted_grades[0].grade
    if min_score < min_score_threshold:
        student.standing = Standing.Poor # i.e. x < 70
    elif min_score < min_score_threshold + 10:
        student.standing = Standing.Fair # 70 > x < 80
    else:
        student.standing = Standing.Good # all grades >= 80

    return student

# # jobs
# def franchise_from_job_id(job_id: str) -> int:
#     return int(job_id.split('_')[0])
# def student_from_job_id(job_id: str) -> int | None:
#     parts = job_id.split('_')
#     if len(parts) == 1:
#         return None
#     return int(parts[1])

