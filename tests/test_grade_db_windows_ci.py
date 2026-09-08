from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_ci_formats_lints_tests_and_builds_release() -> None:
    workflow = (ROOT / ".github" / "workflows" / "grade-db-windows.yml").read_text(
        encoding="utf-8"
    ).lower()

    assert "runs-on: windows-latest" in workflow
    assert "cargo fmt" in workflow and "--check" in workflow
    assert "cargo clippy" in workflow and "-d warnings" in workflow
    assert "cargo test" in workflow
    assert "cargo build" in workflow and "--release" in workflow
    assert "x86_64-pc-windows-msvc" in workflow
