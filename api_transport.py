from __future__ import annotations

import math
import os
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 120.0


class TransportConfigError(RuntimeError):
    """Raised when a private API transport profile is unsafe or incomplete."""


@dataclass(frozen=True)
class HttpsTransportProfile:
    ca_file: str | None
    client_cert_file: str | None
    client_key_file: str | None
    timeout_seconds: float
    production: bool

    @classmethod
    def from_env(
        cls,
        prefix: str,
        *,
        default_timeout_seconds: float,
        fallback_prefix: str | None = None,
    ) -> HttpsTransportProfile:
        def setting(suffix: str) -> str | None:
            value = os.getenv(f"{prefix}_{suffix}")
            if value is None and fallback_prefix:
                value = os.getenv(f"{fallback_prefix}_{suffix}")
            return value

        ca_file = setting("CA_FILE")
        client_cert_file = setting("CLIENT_CERT_FILE")
        client_key_file = setting("CLIENT_KEY_FILE")
        if any(
            value is not None and (not value or value.strip() != value)
            for value in (ca_file, client_cert_file, client_key_file)
        ):
            raise TransportConfigError(f"{prefix} TLS paths must be nonempty and unpadded")
        if bool(client_cert_file) != bool(client_key_file):
            raise TransportConfigError(
                f"{prefix} client certificate and key must be configured together"
            )

        timeout_value = setting("TIMEOUT_SECONDS")
        try:
            timeout_seconds = (
                default_timeout_seconds
                if timeout_value is None
                else float(timeout_value)
            )
        except ValueError as exc:
            raise TransportConfigError(
                f"{prefix}_TIMEOUT_SECONDS must be a number"
            ) from exc
        if (
            not math.isfinite(timeout_seconds)
            or not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise TransportConfigError(
                f"{prefix}_TIMEOUT_SECONDS must be between "
                f"{MIN_TIMEOUT_SECONDS:g} and {MAX_TIMEOUT_SECONDS:g}"
            )

        deployment_env = os.getenv("DEPLOYMENT_ENV", "development").strip().lower()
        if deployment_env not in {"development", "test", "production"}:
            raise TransportConfigError(
                "DEPLOYMENT_ENV must be production, development, or test"
            )

        return cls(
            ca_file=ca_file,
            client_cert_file=client_cert_file,
            client_key_file=client_key_file,
            timeout_seconds=timeout_seconds,
            production=deployment_env == "production",
        )

    @property
    def mutual_tls_configured(self) -> bool:
        return bool(self.client_cert_file and self.client_key_file)

    def ssl_context(self) -> ssl.SSLContext | None:
        if not any((self.ca_file, self.client_cert_file, self.client_key_file)):
            return None
        context = ssl.create_default_context(cafile=self.ca_file)
        if self.client_cert_file and self.client_key_file:
            context.load_cert_chain(
                certfile=self.client_cert_file,
                keyfile=self.client_key_file,
            )
        return context

    def open(self, request: urllib.request.Request) -> Any:
        scheme = urllib.parse.urlsplit(request.full_url).scheme.lower()
        if self.production and scheme != "https":
            raise TransportConfigError("API requests must use HTTPS in production")
        if any((self.ca_file, self.client_cert_file, self.client_key_file)) and scheme != "https":
            raise TransportConfigError("TLS settings cannot be used with a non-HTTPS URL")

        context = self.ssl_context()
        if context is None:
            return urllib.request.urlopen(request, timeout=self.timeout_seconds)
        return urllib.request.urlopen(
            request,
            timeout=self.timeout_seconds,
            context=context,
        )
