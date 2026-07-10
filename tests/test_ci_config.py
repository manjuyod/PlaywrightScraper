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
        "001-006",
        "001-003 to 004-006",
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
