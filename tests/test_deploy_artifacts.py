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


def test_nginx_uses_server_tls_without_client_auth():
    nginx = _text("api/nginx/grades-api.conf")

    assert "listen 443 ssl;" in nginx
    assert "listen [::]:443 ssl;" in nginx
    assert (
        "server_name grades-api.tutoringclub.com grades-api-dev.tutoringclub.com;"
        in nginx
    )
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in nginx
    for forbidden in (
        "ssl_verify_client",
        "ssl_client_certificate",
        "ssl_crl",
        "$ssl_client_s_dn",
        "$ssl_client_serial",
        "$mtls_role",
        "$mtls_route_allowed",
        "OU=frontend",
        "OU=worker",
        "OU=scheduler",
        "OU=operator",
    ):
        assert forbidden not in nginx


def test_nginx_forwards_but_never_logs_authorization():
    nginx = _text("api/nginx/grades-api.conf")
    log_format = nginx.split("log_format", 1)[1].split(";", 1)[0]

    assert "proxy_set_header Authorization $http_authorization;" in nginx
    for forbidden in (
        "$http_authorization",
        "$request_body",
        "$args",
        "$query_string",
        "$ssl_client",
        "portal_url",
        "p2username",
        "p2password",
    ):
        assert forbidden not in log_format


def test_nginx_applies_source_ip_limit():
    nginx = _text("api/nginx/grades-api.conf")

    assert (
        "limit_req_zone $binary_remote_addr zone=grade_api_per_ip:10m rate=600r/m;"
        in nginx
    )
    assert "limit_req zone=grade_api_per_ip burst=100 nodelay;" in nginx
    assert "limit_req_status 429;" in nginx


def test_nginx_rejects_bodies_over_one_megabyte():
    assert "client_max_body_size 1m;" in _text("api/nginx/grades-api.conf")


def test_nginx_has_no_unknown_path_static_fallback():
    nginx = _text("api/nginx/grades-api.conf")

    assert "proxy_pass http://127.0.0.1:3000;" in nginx
    assert "try_files" not in nginx
    assert "root " not in nginx
    assert "alias " not in nginx
    assert "error_page 404" not in nginx


def test_nginx_body_limit_and_unknown_paths_are_fail_closed():
    nginx = _text("api/nginx/grades-api.conf")
    assert "client_max_body_size 1m;" in nginx
    assert "proxy_pass http://127.0.0.1:3000;" in nginx
    assert "try_files" not in nginx
    assert "root " not in nginx
    assert "alias " not in nginx
    assert "error_page 404" not in nginx


def test_role_env_requires_keyrings_not_client_certificates():
    api_env = _text("api/api.env.example")
    frontend_env = _text("frontend/frontend.env.example")
    windows_env = _text("windows/windows.env.example")
    validator = _text("bin/validate-role-env")

    keyrings = (
        "WORKER_API_KEYRING_JSON",
        "SCHEDULER_API_KEYRING_JSON",
        "OPERATOR_API_KEYRING_JSON",
        "READINESS_API_KEYRING_JSON",
        "DEFAULT_WORKER_ID",
    )
    for name in keyrings:
        assert name in api_env
        assert name in validator
    for obsolete in (
        "WORKER_API_TOKENS_JSON",
        "SCHEDULER_API_TOKENS_JSON",
        "OPERATOR_API_TOKENS_JSON",
        "READINESS_API_TOKEN",
    ):
        assert obsolete not in api_env
        assert obsolete not in validator
    assert "require_file" not in validator
    for role_env in (frontend_env, windows_env):
        active_lines = [
            line.strip()
            for line in role_env.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert all("CLIENT_CERT_FILE" not in line for line in active_lines)
        assert all("CLIENT_KEY_FILE" not in line for line in active_lines)
    assert "SCHEDULER_ID=" not in windows_env
    assert "WINDOWS_TARGET_WORKER_ID=" in windows_env


def test_release_installation_verifies_checksums_and_supports_independent_rollback():
    installer = _text("bin/install-release")
    rollback = _text("bin/rollback-release")

    assert "sha256sum --check" in installer
    assert "/releases/" in installer
    assert "current" in installer
    assert "role" in installer.lower()
    assert "previous" in rollback
    assert "systemctl restart" in rollback


def test_role_install_wrappers_delegate_to_the_hardened_installer():
    api_installer = _text("bin/install-api")
    frontend_installer = _text("bin/install-frontend")

    assert 'exec "$script_dir/install-release" api "$@"' in api_installer
    assert 'exec "$script_dir/install-release" frontend "$@"' in frontend_installer


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
