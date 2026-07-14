from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ci_covers_frozen_dependencies_audits_migrations_and_role_artifacts():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    for required in (
        "uv sync --frozen",
        "cargo clippy --locked",
        "cargo test --locked",
        "npm ci",
        "npm test",
        "pip-audit",
        "rustsec/audit-check",
        "gitleaks",
        "001-007",
        "001-003 to 004-007",
        "build-release-artifacts",
        "sha256sum --check",
    ):
        assert required in workflow

    assert "NEON_DATABASE_URL" not in workflow
    assert "CRM_DATABASE_URL" not in workflow


def test_release_builder_creates_separate_api_and_frontend_archives():
    builder = (ROOT / "deploy" / "bin" / "build-release-artifacts").read_text(
        encoding="utf-8"
    )

    assert "cargo build" in builder and "--locked" in builder
    assert "uv sync --frozen --no-dev" in builder
    assert "npm ci" in builder
    assert 'stage/api' in builder
    assert 'stage/frontend' in builder
    assert "sha256sum" in builder


def test_rust_ci_runs_targeted_postgres_lifecycle_tests():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    rust_job = workflow.split("  rust:", 1)[1].split("\n  frontend:", 1)[0]

    assert "services:" in rust_job
    assert "image: postgres:16-alpine" in rust_job
    assert (
        "DATABASE_URL: postgres://postgres:postgres@127.0.0.1:5432/postgres"
        in rust_job
    )
    assert "cargo test --test targeted_jobs_postgres --locked" in rust_job
