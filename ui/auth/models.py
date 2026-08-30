from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


Role = Literal["2", "3"]


@dataclass(frozen=True)
class AuthClaims:
    sub: str
    jti: str
    iss: str
    aud: str
    grant_id: str
    crm_role: Role
    franchise_id: int
    permissions: tuple[str, ...]
    iat: int
    nbf: int
    exp: int

    @property
    def is_valid(self) -> bool:
        now = int(datetime.utcnow().timestamp())
        return self.nbf <= now <= self.exp


@dataclass(frozen=True)
class AuthTransaction:
    state: str
    code_verifier: str
    state_exp: datetime
    return_path: str


@dataclass(frozen=True)
class GrantIntrospection:
    active: bool
    grant_id: str | None = None
    device_id: str | None = None
    crm_role: str | None = None
    franchise_id: int | None = None
    permissions: tuple[str, ...] = ()
    expires_at: int | None = None
