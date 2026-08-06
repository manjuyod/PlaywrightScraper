from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_react_bundle_is_read_only_and_polls_canonical_jobs() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "/api/jobs" in javascript
    assert "15000" in javascript
    assert "noopener noreferrer" in javascript
    assert 'data.page === "home"' in javascript
    assert 'data.page === "franchise"' in javascript
    assert 'data.page === "student"' in javascript
    for retired in (
        "LoginPage",
        "LogoutForm",
        "StudentDialog",
        "HiddenCsrf",
        "csrfToken",
        "/status/",
        "run_scraper",
        "run_agenda",
        "delete_students",
        "add_student",
        "edit_student",
    ):
        assert retired not in javascript


def test_franchise_and_student_headers_omit_overview_actions() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    franchise_page = javascript.split("function FranchisePage", 1)[1].split(
        "function GradeHistory", 1
    )[0]
    student_page = javascript.split("function StudentPage", 1)[1].split(
        "function App", 1
    )[0]

    for page in (franchise_page, student_page):
        assert "data.homeUrl" not in page
        assert '"Overview"' not in page


def test_franchise_cover_uses_the_name_from_page_data() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    franchise_page = javascript.split("function FranchisePage", 1)[1].split(
        "function GradeHistory", 1
    )[0]

    assert "title: data.franchiseName" in franchise_page
    assert "`Franchise ${data.franchiseId}`" not in franchise_page


def test_header_ambient_texture_omits_floating_square_overlays() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "ui" / "static" / "react-dashboard.css").read_text(
        encoding="utf-8"
    )
    header = javascript.split("function Header", 1)[1].split(
        "function Shell", 1
    )[0]

    assert 'className: "tc-header-squares"' in header
    assert 'className: "tc-header-scanline"' in header
    assert 'className: "tc-header-square ' not in header
    assert ".tc-header-square {" not in css
    assert ".tc-header-square--" not in css
    assert "tc-header-square-pulse" not in css


def test_student_header_back_action_uses_generic_label() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    student_page = javascript.split("function StudentPage", 1)[1].split(
        "function App", 1
    )[0]

    assert 'href: data.backUrl' in student_page
    assert 'icon: "arrowLeft"' in student_page
    assert '"Back"' in student_page
    assert '"Franchise"' not in student_page


def test_franchise_students_render_as_a_read_only_table() -> None:
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )

    assert "function StudentTable" in javascript
    assert re.search(r'h\(\s*"table"', javascript)
    assert re.search(r'h\(\s*"thead"', javascript)
    assert re.search(r'h\(\s*"tbody"', javascript)
    assert "h(StudentTable" in javascript
    assert "overflow-x-auto" in javascript
    assert "gradesSnapshot" in javascript
    assert "lowGrades" in javascript
    assert "highGrades" in javascript
    assert "data.students.map((student) => h(StudentCard" not in javascript


def test_heatmap_course_column_is_bounded_and_adaptive() -> None:
    css = (ROOT / "ui" / "static" / "react-dashboard.css").read_text(
        encoding="utf-8"
    )
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    column_selector = ".tc-heatmap-table th:first-child {"
    label_selector = ".tc-heatmap-course-label {"

    assert column_selector in css
    assert label_selector in css

    course_column = css.split(column_selector, 1)[1].split("}", 1)[0]
    course_label = css.split(label_selector, 1)[1].split("}", 1)[0]

    assert "min-width: 100px;" in course_column
    assert "width: 1%;" in course_column
    assert "padding-inline: 8px;" in course_column
    assert "display: block;" in course_label
    assert "max-width: 244px;" in course_label
    assert "overflow: hidden;" in course_label
    assert "text-overflow: ellipsis;" in course_label
    assert "white-space: nowrap;" in course_label
    assert 'className: "tc-heatmap-course-label"' in javascript
    assert "title: course" in javascript


def test_heatmap_grade_columns_are_individually_compact() -> None:
    css = (ROOT / "ui" / "static" / "react-dashboard.css").read_text(
        encoding="utf-8"
    )
    selector = ".tc-heatmap-table tr > :not(:first-child) {"

    assert selector in css

    grade_columns = css.split(selector, 1)[1].split("}", 1)[0]
    mobile_css = css.split("@media (max-width: 760px) {", 1)[1]

    assert selector in mobile_css

    mobile_grade_columns = mobile_css.split(selector, 1)[1].split("}", 1)[0]

    assert "min-width: 58px;" in grade_columns
    assert "width: 1%;" not in grade_columns
    assert "white-space: nowrap;" in grade_columns
    assert "padding-inline: 8px;" in grade_columns
    assert "min-width: 48px;" in mobile_grade_columns


def test_flask_web_path_does_not_import_legacy_writes_or_executor() -> None:
    routes = (ROOT / "ui" / "routes.py").read_text(encoding="utf-8")
    app = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")

    assert "ui.ext_jobs" not in routes
    assert "import db" not in routes
    assert "flask_session" not in app
    assert "SESSION_SECRET" not in app
    assert "session" not in app.lower()
    assert "csrf" not in app.lower()
    assert "load_dotenv()" in app


def test_retired_auth_templates_and_dependency_are_removed() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()

    assert "flask-session" not in pyproject
    assert not (ROOT / "ui" / "auth.py").exists()
    for template in (
        "login.html",
        "health.html",
        "franchise.html",
        "student.html",
        "student_heatmap.html",
    ):
        assert not (ROOT / "ui" / "templates" / template).exists()


def test_unauthorized_overview_template_is_static_and_actionless() -> None:
    template = (ROOT / "ui" / "templates" / "unauthorized.html").read_text(
        encoding="utf-8"
    )

    assert "Unauthorized" in template
    assert "development environment" in template
    assert "direct franchise URL" in template
    assert "<form" not in template
    assert "tc-page-data" not in template


def test_replit_proxy_does_not_forward_retired_auth_headers() -> None:
    nginx = (ROOT / "ui" / "nginx.conf").read_text(encoding="utf-8").lower()

    assert "x-franchise" not in nginx
    assert "x-internal-key" not in nginx
