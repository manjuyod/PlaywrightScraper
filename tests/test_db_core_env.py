from db_core import _connection_url


def _clear_db_env(monkeypatch):
    for name in (
        "PGHOST",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "PGPORT",
        "GRADES_NEON_URL",
        "GRADES_NEON_HOST",
        "GRADES_NEON_DB",
        "GRADES_NEON_DATABASE",
        "GRADES_NEON_USER",
        "GRADES_NEON_PASSWORD",
        "GRADES_NEON_PORT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_connection_url_uses_grades_neon_parts_when_pg_env_is_missing(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("GRADES_NEON_HOST", "ep-test.us-east-2.aws.neon.tech")
    monkeypatch.setenv("GRADES_NEON_DB", "grades")
    monkeypatch.setenv("GRADES_NEON_USER", "grades user")
    monkeypatch.setenv("GRADES_NEON_PASSWORD", "p@ ss/word")
    monkeypatch.setenv("GRADES_NEON_PORT", "5432")

    assert _connection_url() == (
        "postgresql+psycopg://grades+user:p%40+ss%2Fword"
        "@ep-test.us-east-2.aws.neon.tech:5432/grades?sslmode=require"
    )


def test_connection_url_normalizes_grades_neon_url(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv(
        "GRADES_NEON_URL",
        "postgresql://grades_user:secret@ep-test.us-east-2.aws.neon.tech/grades",
    )

    assert _connection_url() == (
        "postgresql+psycopg://grades_user:secret"
        "@ep-test.us-east-2.aws.neon.tech/grades?sslmode=require"
    )
