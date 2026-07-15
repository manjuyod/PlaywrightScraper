from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_google_sheets_and_jsonl_workflows_are_retired() -> None:
    removed = (
        "run_pipeline_to_spreadsheet.bat",
        "batches/update_students_all.bat",
        "scraper/work_flows/update_students.py",
        "scraper/work_flows/update_sheets.py",
        "scraper/work_flows/insert_grades.py",
        "scraper/work_flows/verify_bad_logins.py",
        "scraper/post_processing.py",
        "scraper/to_excel.py",
        "tests/test_franchise19_loginmaster_vs_db.py",
        "tests/test_insert_grades.py",
    )

    assert all(not (ROOT / path).exists() for path in removed)


def test_google_client_dependencies_are_absent() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()

    assert "gspread" not in project
    assert "google-auth" not in project
    assert "from spreadsheets" not in (ROOT / "db.py").read_text(
        encoding="utf-8"
    ).lower()


def test_franchise_batch_invokes_only_the_rust_backed_runner() -> None:
    batch = (ROOT / "batches" / "pipeline_franchise.bat").read_text(
        encoding="utf-8"
    ).lower()

    assert "scraper.runner" in batch
    assert "insert_grades" not in batch
    assert "update_sheets" not in batch
    assert "update_students" not in batch
    all_batches = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (ROOT / "batches").glob("*.bat")
    )
    assert "sheet_mod_grades" not in all_batches
