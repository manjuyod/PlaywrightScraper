from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _text(path: str) -> str:
    return (DEPLOY / path).read_text(encoding="utf-8")


def test_role_specific_services_are_nonroot_loopback_and_hardened():
    api = _text("api/systemd/grade-api.service")
    frontend = _text("frontend/systemd/grade-frontend.service")

    assert "User=grade-api" in api
    assert "EnvironmentFile=/etc/grade-api/api.env" in api
    assert "127.0.0.1:3000" in _text("api/api.env.example")
    assert "User=grade-frontend" in frontend
    assert "EnvironmentFile=/etc/grade-frontend/frontend.env" in frontend
    assert "--bind 127.0.0.1:8080" in frontend

    for unit in (api, frontend):
        assert "NoNewPrivileges=true" in unit
        assert "ProtectSystem=strict" in unit
        assert "ProtectHome=true" in unit
        assert "PrivateTmp=true" in unit
        assert "Restart=on-failure" in unit
        assert "UMask=0077" in unit


def test_role_environment_inventories_do_not_cross_the_host_boundary():
    api_env = _text("api/api.env.example")
    frontend_env = _text("frontend/frontend.env.example")

    for forbidden in ("SESSION_SECRET", "FRONTEND_API_CLIENT_KEY_FILE"):
        assert forbidden not in api_env
    for forbidden in (
        "NEON_DATABASE_URL",
        "CRM_DATABASE_URL",
        "WORKER_API_TOKENS_JSON",
        "READINESS_API_TOKEN",
    ):
        assert forbidden not in frontend_env

    validator = _text("bin/validate-role-env")
    assert "NEON_DATABASE_URL" in validator
    assert "SESSION_SECRET" in validator
    assert "DEPLOYMENT_ENV" in validator


def test_private_api_nginx_enforces_mtls_roles_limits_and_safe_logging():
    nginx = _text("api/nginx/grades-api.conf")

    for required in (
        "ssl_protocols TLSv1.2 TLSv1.3",
        "ssl_verify_client on",
        "ssl_client_certificate",
        "ssl_crl",
        "client_max_body_size 1m",
        "proxy_pass http://127.0.0.1:3000",
        "$ssl_client_s_dn",
        "$ssl_client_serial",
        "$uri",
    ):
        assert required in nginx
    for role in ("frontend", "worker", "scheduler", "operator"):
        assert f"OU={role}" in nginx
        assert f"{role}:" in nginx
    assert "$http_authorization" not in nginx.split("log_format", 1)[1].split(";", 1)[0]
    assert "$request_body" not in nginx


def test_release_installation_verifies_checksums_and_supports_independent_rollback():
    installer = _text("bin/install-release")
    rollback = _text("bin/rollback-release")

    assert "sha256sum --check" in installer
    assert "/releases/" in installer
    assert "current" in installer
    assert "role" in installer.lower()
    assert "previous" in rollback
    assert "systemctl restart" in rollback


def test_private_ca_tooling_uses_encrypted_keys_short_leaves_and_crls():
    initialize = _text("pki/init-offline-ca")
    issue = _text("pki/issue-client")
    revoke = _text("pki/revoke-client")
    expiry = _text("pki/check-expiry")

    assert "umask 077" in initialize
    assert "aes-256-cbc" in initialize
    assert "intermediate" in initialize
    assert "-days 30" in issue
    for role in ("frontend", "worker", "scheduler", "operator"):
        assert role in issue
    assert "openssl ca" in revoke
    assert "-gencrl" in revoke
    assert "-checkend" in expiry


def test_log_rotation_exists_for_both_ubuntu_roles():
    api = _text("api/logrotate/grade-api")
    frontend = _text("frontend/logrotate/grade-frontend")
    for config in (api, frontend):
        assert "rotate 14" in config
        assert "daily" in config
        assert "compress" in config
        assert "su " in config
