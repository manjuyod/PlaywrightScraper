from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from . import config as cfg
from .assertions import validate_assertion
from .models import AuthClaims, GrantIntrospection


@dataclass
class ClientError(RuntimeError):
    code: str


class RustAuthClient:
    def __init__(self, settings: cfg.AuthConfig, session: requests.Session | Any | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def redeem_authorization_code(self, code: str, code_verifier: str) -> AuthClaims:
        response = self._post(
            "/v2/authorization/redeem",
            {"authorization_code": code, "code_verifier": code_verifier},
            "redeem_failed",
        )
        assertion = response.get("assertion")
        if not isinstance(assertion, str) or not assertion:
            raise ClientError("redeem_failed")
        return validate_assertion(assertion, self._jwks(), self.settings)

    def introspect_grant(self, grant_id: str, device_id: str) -> GrantIntrospection:
        response = self._post(
            "/v2/grants/introspect",
            {"grant_id": grant_id, "device_id": device_id},
            "introspection_failed",
        )
        if type(response.get("active")) is not bool:
            raise ClientError("introspection_failed")
        if not response["active"]:
            return GrantIntrospection(active=False)
        required = ("grant_id", "device_id", "crm_role", "franchise_id", "permissions", "expires_at")
        if any(name not in response for name in required):
            raise ClientError("introspection_failed")
        if (
            not all(isinstance(response[name], str) and response[name] for name in ("grant_id", "device_id", "crm_role"))
            or type(response["franchise_id"]) is not int
            or type(response["expires_at"]) is not int
            or not isinstance(response["permissions"], list)
            or not all(isinstance(permission, str) for permission in response["permissions"])
        ):
            raise ClientError("introspection_failed")
        return GrantIntrospection(
            active=True,
            grant_id=response["grant_id"],
            device_id=response["device_id"],
            crm_role=response["crm_role"],
            franchise_id=response["franchise_id"],
            permissions=tuple(response["permissions"]),
            expires_at=response["expires_at"],
        )

    def _post(self, path: str, payload: dict[str, str], error_code: str) -> dict[str, Any]:
        try:
            response = self.session.post(
                f"{self.settings.crm_auth_base_url}{path}",
                json=payload,
                auth=(self.settings.crm_auth_client_id, self.settings.crm_auth_client_secret),
                headers={"Accept": "application/json"},
                timeout=(3, 3),
            )
        except requests.RequestException as exc:
            raise ClientError(error_code) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise ClientError(error_code)
        return self._json(response, error_code)

    def _jwks(self) -> dict[str, Any]:
        try:
            response = self.session.get(self.settings.crm_auth_jwks_url, timeout=(3, 3))
        except requests.RequestException as exc:
            raise ClientError("jwks_failed") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise ClientError("jwks_failed")
        return self._json(response, "jwks_failed")

    @staticmethod
    def _json(response: Any, error_code: str) -> dict[str, Any]:
        try:
            body = response.json()
            if not isinstance(body, dict) or len(json.dumps(body)) > 65_536:
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ClientError(error_code) from exc
        return body
