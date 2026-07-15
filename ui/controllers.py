from db import CourseGrade, Standing, Student, DictRow
import pandas as pd
import plotly.express as px

# # Route helpers
def get_grades_x_weeks_back(raw_grades: dict, weeks_back: int) -> list[CourseGrade]:
    if len(raw_grades.values()) < weeks_back:
        return []
    grades_x_weeks_ago: dict[str, str] = list(raw_grades.values())[-weeks_back] # gets the grades from x weeks back. dict[course, grade] 
    return [CourseGrade(k, v) for k, v in grades_x_weeks_ago.items() if isinstance(v, float)]
    
def compute_grade_changes(grades: list[CourseGrade], prev_grades: list[CourseGrade]):
    """
    Computes the change in grade between two batches of grades
    Modifies the grades in place
    """
    for grade, prev_grade in zip(grades, prev_grades):
        grade.change = '+' if grade.grade > prev_grade.grade else '-' if grade.grade < prev_grade.grade else None
        
def get_student_standing(sorted_grades: list[CourseGrade]) -> Standing:
    min_score_threshold = 70
    # lowest grade in the batch
    min_score = sorted_grades[0].grade
    if min_score < min_score_threshold:
        return Standing.Poor # i.e. x < 70
    elif min_score < min_score_threshold + 10:
        return Standing.Fair # 70 > x < 80
    else:
        return Standing.Good # all grades >= 80
        
def compute_student_report(student: Student) -> Student:
    """
    Fills computed fields for a student 
    e.g. low_scores, high_scores, standing
    """
    most_recent_grades = get_grades_x_weeks_back(student.grades, 1)
    second_most_recent_grades = get_grades_x_weeks_back(student.grades, 2)
    compute_grade_changes(most_recent_grades, second_most_recent_grades)
    if len(most_recent_grades) == 0:
        return student
    
    student.grades_snapshot = most_recent_grades
    
    # sort the grades, lowest to highest
    sorted_grades = sorted(most_recent_grades, key=lambda x: x.grade)

    # highest/lowest 3 grades
    student.low_grades = sorted_grades[0:3]
    student.high_grades = sorted_grades[-2:]

    # compute current score standing
    student.standing = get_student_standing(sorted_grades)
    return student

# health helper

"""
Accepts a list of active students from a franchise franchise and returns a report of the update/sync status, 
including error counts and common error types.
    This is used to monitor the health of the scraper and identify common issues.
"""
def check_students_status(students: list[DictRow]) -> dict:
    synced = 0
    total = len(students)
    errors = []
    error_groups: dict[str, int] = {}
    malformed_inputs = 0
    nonconfigured_portals = 0
    bad_logins = 0
    for student in students:
        # check if the student is synced
        if Student.check_status(student):
            synced += 1
        # check for errors
        error = Student.check_error(student)
        assert isinstance(error, str)
        if len(error) > 0:
            errors.append(error)
            error_groups[error] = error_groups.get(error, 0) + 1
        # check for bad logins
        if student.get('passwordgood') == 0:
            bad_logins += 1
        # check for malformed inputs (missing portal or credentials)
        if (
            not student.get('portal1') 
            or not student.get('p1username') 
            or not student.get('p1password')
        ):
            malformed_inputs += 1
        # check for nonconfigured portals (portal specified but not configured in scraper)
        if (
            student.get('portal1') is not None and
            student.get('portal') is None
        ):
            nonconfigured_portals += 1

    grouped_errors = sorted(
        (
            {"label": label, "count": count}
            for label, count in error_groups.items()
        ),
        key=lambda entry: entry["count"],
        reverse=True,
    )


    return {
        "synced": synced,
        "total": total,
        "errors": errors,
        "error_count": len(errors),
        "error_groups": grouped_errors,
        "malformed_inputs": malformed_inputs,
        "nonconfigured_portals": nonconfigured_portals,
        "last_updated": 'n/a',
        "bad_logins": bad_logins,
    }
    
"""unused as of now"""
def create_grade_line_graph(student: Student):
    name = student.first_name + ' ' + student.last_name
    df = pd.DataFrame(student.grades).T
    # debug
    pd.set_option('display.max_rows', None)
    pd.set_option('display.max_columns', None)
    print(df.head())
    # Wide DataFrame like:
    #                            Course 1   Course 2  Course 3  Course 4
    #       2025-08-25              83.28      94.08     53.23     82.16   
    #       2025-09-01              87.94      95.21     53.23     82.61   
    #       2025-09-08              84.39      94.61     62.82     82.90   
    #       2025-09-15              91.98      92.00     61.05     74.88   
    #       2025-09-22              82.06      92.00     64.78     91.05   
    #       ...

    # convert from wide to long format
    df.index = pd.to_datetime(df.index)
    df = df.reset_index().melt( # this section might be unnecessarily computationally heavy
        id_vars='index',        # could be improved by storing data in a more compatible format
        var_name='course',      # i.e., list of entries per course per week
        value_name='grade'      # the downside is data repetition among weeks and course storage
    ).rename(columns={'index': 'week'})
    print(df.tail())
    # Long DataFrame like:
    #       idx       week         course  grade
    #       135 2025-12-08  3: ENGLISH 11   62.3
    #       136 2025-12-15  3: ENGLISH 11   62.3
    #       137 2026-01-12  3: ENGLISH 11   62.3
    #       138 2026-01-19  3: ENGLISH 11   24.5
    #       139 2026-01-26  3: ENGLISH 11   24.5
    #       ...
    
    df = df.sort_index().ffill() # fills NaN values with the previous value   
    # Note: this graph looks bad due to courses that are the same but are semantically different
    # TODO: normalize equivalent course titles before comparison.
    
    y_max = max(df['grade']) + 10
    # create graph
    fig = px.line(
        df,
        x="week",
        y="grade",
        color="course",
        markers=False,
        title=f"{name} Grade Timeline"
    )
    fig.update_layout(
        yaxis_title="Grade",
        xaxis_title="Time",
        hovermode="x unified",
        legend_title="",
        margin=dict(l=40, r=20, t=80, b=40)
    )
    fig.update_traces(
        line=dict(width=3), # thicker line,
        hovertemplate="%{fullData.name}: %{y:.1f}<extra></extra>" # format hover
    )
    # shade grade thresholds
    bands = [
        (0, 70,  "rgba(255, 0, 0, 0.12)"),
        (70, 80,  "rgba(255, 165, 0, 0.12)"),
        (80, 90, "rgba(255, 200, 0, 0.12)"),
        (90, y_max,"rgba(0, 200, 0, 0.12)"),
    ]
    
    for y0, y1, fill in bands:
        fig.add_hrect(
            y0=y0, y1=y1,
            fillcolor=fill,
            line_width=0, # no line border
            layer="below"
        )
        
    fig.update_yaxes(
        range=[0, y_max],
        gridcolor="rgba(0,0,0,0.08)",
        title=None
    )
    fig.update_xaxes(
        rangeslider=dict(visible=True),
        rangeselector=dict(
            buttons=[
                dict(count=1, label="1m", step="month", stepmode="backward"),
                dict(count=3, label="3m", step="month", stepmode="backward"),
                dict(count=6, label="6m", step="month", stepmode="backward"),
                dict(step="all", label="All"),
            ]
        ),
        showgrid=False,
        title=None
    )

    fig.show()
