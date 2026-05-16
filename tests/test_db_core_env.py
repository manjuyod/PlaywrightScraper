from urllib.parse import parse_qsl, urlsplit

import pytest

from db_core import _connection_url


GRADES_NEON_ENV = (
    "GRADES_NEON_URL",
    "GRADES_NEON_DB",
    "GRADES_NEON_HOST",
    "GRADES_NEON_PORT",
    "GRADES_NEON_USER",
    "GRADES_NEON_PASSWORD",
)
PG_ENV = ("PGHOST", "PGDATABASE", "PGPORT", "PGUSER", "PGPASSWORD")


def _clear_db_env(monkeypatch):
    for name in GRADES_NEON_ENV + PG_ENV:
        monkeypatch.delenv(name, raising=False)


def test_connection_url_uses_grades_neon_url_before_legacy_pg(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv(
        "GRADES_NEON_URL",
        "postgres://grades_user:grades_pass@grades-neon.example/grades_db",
    )
    monkeypatch.setenv("PGHOST", "wrong-host.example")
    monkeypatch.setenv("PGDATABASE", "wrong_db")
    monkeypatch.setenv("PGUSER", "wrong_user")
    monkeypatch.setenv("PGPASSWORD", "wrong_pass")
    monkeypatch.setenv("PGPORT", "9999")

    url = _connection_url()

    assert url == (
        "postgresql+psycopg://grades_user:grades_pass@"
        "grades-neon.example/grades_db?sslmode=require"
    )


def test_connection_url_composes_from_grades_neon_components(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("GRADES_NEON_HOST", "ep-example.neon.tech")
    monkeypatch.setenv("GRADES_NEON_DB", "grades_db")
    monkeypatch.setenv("GRADES_NEON_USER", "grades user")
    monkeypatch.setenv("GRADES_NEON_PASSWORD", "pa@ss/word")
    monkeypatch.setenv("GRADES_NEON_PORT", "6543")

    url = _connection_url()

    assert url == (
        "postgresql+psycopg://grades+user:pa%40ss%2Fword@"
        "ep-example.neon.tech:6543/grades_db?sslmode=require"
    )


def test_connection_url_requires_grades_neon_configuration_and_ignores_pg(
    monkeypatch,
):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("PGHOST", "wrong-host.example")
    monkeypatch.setenv("PGDATABASE", "wrong_db")
    monkeypatch.setenv("PGUSER", "wrong_user")
    monkeypatch.setenv("PGPASSWORD", "wrong_pass")
    monkeypatch.setenv("PGPORT", "9999")

    with pytest.raises(ValueError, match="GRADES_NEON"):
        _connection_url()


def test_connection_url_adds_sslmode_to_url_without_losing_query(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv(
        "GRADES_NEON_URL",
        "postgresql://grades_user:grades_pass@grades-neon.example/grades_db"
        "?connect_timeout=10",
    )

    url = _connection_url()
    parsed = urlsplit(url)

    assert parsed.scheme == "postgresql+psycopg"
    assert parsed.netloc == "grades_user:grades_pass@grades-neon.example"
    assert parsed.path == "/grades_db"
    assert dict(parse_qsl(parsed.query)) == {
        "connect_timeout": "10",
        "sslmode": "require",
    }
