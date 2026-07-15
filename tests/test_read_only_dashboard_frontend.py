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


def test_replit_proxy_does_not_forward_retired_auth_headers() -> None:
    nginx = (ROOT / "ui" / "nginx.conf").read_text(encoding="utf-8").lower()

    assert "x-franchise" not in nginx
    assert "x-internal-key" not in nginx
