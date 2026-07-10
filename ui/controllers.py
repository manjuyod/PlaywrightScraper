from ui.report_models import CourseGrade, Standing, Student


def get_grades_x_weeks_back(
    raw_grades: dict, weeks_back: int
) -> list[CourseGrade]:
    if len(raw_grades.values()) < weeks_back:
        return []
    grades_x_weeks_ago: dict[str, str] = list(raw_grades.values())[-weeks_back]
    return [
        CourseGrade(course, grade)
        for course, grade in grades_x_weeks_ago.items()
        if isinstance(grade, float)
    ]


def compute_grade_changes(
    grades: list[CourseGrade], prev_grades: list[CourseGrade]
) -> None:
    for grade, prev_grade in zip(grades, prev_grades):
        grade.change = (
            "+"
            if grade.grade > prev_grade.grade
            else "-"
            if grade.grade < prev_grade.grade
            else None
        )


def get_student_standing(sorted_grades: list[CourseGrade]) -> Standing:
    min_score = sorted_grades[0].grade
    if min_score < 70:
        return Standing.Poor
    if min_score < 80:
        return Standing.Fair
    return Standing.Good


def compute_student_report(student: Student) -> Student:
    most_recent_grades = get_grades_x_weeks_back(student.grades, 1)
    second_most_recent_grades = get_grades_x_weeks_back(student.grades, 2)
    compute_grade_changes(most_recent_grades, second_most_recent_grades)
    if not most_recent_grades:
        return student

    student.grades_snapshot = most_recent_grades
    sorted_grades = sorted(most_recent_grades, key=lambda grade: grade.grade)
    student.low_grades = sorted_grades[:3]
    student.high_grades = sorted_grades[-2:]
    student.standing = get_student_standing(sorted_grades)
    return student
