from __future__ import annotations

import pytest

from ui.auth.destinations import resolve_landing_path, validate_return_path


@pytest.mark.parametrize("path", [
    "/", "/franchise/6", "/franchise/6/student/32855",
    "/franchise/2147483647/student/9223372036854775807",
])
def test_accepts_canonical_local_destinations(path):
    assert validate_return_path(path) == path


@pytest.mark.parametrize("path", [
    None, True, {}, "", "//evil.example", "https://grades.tutoringclub.com/franchise/6",
    "/franchise/0", "/franchise/06", "/franchise/+6", "/franchise/6/student/-1",
    "/franchise/6/student/0", "/franchise/6/student/01", "/franchise/2147483648",
    "/franchise/6/student/9223372036854775808", "/franchise/6?next=/",
    "/franchise/6#report", "/franchise/6/", "/franchise/6\\student\\1",
    "/franchise/%36", "/franchise/\u0666", "/auth/callback", "/franchise/6\n",
    " /franchise/6", "/franchise/" + "9" * 101,
])
def test_rejects_noncanonical_and_external_destinations(path):
    with pytest.raises(ValueError, match="return path"):
        validate_return_path(path)


@pytest.mark.parametrize("role", ["2", "3"])
@pytest.mark.parametrize("path", ["/franchise/6", "/franchise/6/student/32855"])
def test_explicit_destination_requires_and_preserves_trusted_scope(role, path):
    assert resolve_landing_path(
        path, franchise_id=6, crm_role=role, permissions=("students.read",),
    ) == path
    with pytest.raises(ValueError):
        resolve_landing_path(path, franchise_id=7, crm_role=role, permissions=("students.read",))
    with pytest.raises(ValueError):
        resolve_landing_path(path, franchise_id=6, crm_role=role, permissions=("dashboard.read",))


@pytest.mark.parametrize("role,expected", [("2", "/"), ("3", "/franchise/6")])
def test_generic_sign_in_keeps_role_default(role, expected):
    assert resolve_landing_path(
        "/", franchise_id=6, crm_role=role, permissions=("students.read",),
    ) == expected
