from __future__ import annotations

import os
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from collections.abc import Iterator


@contextmanager
def suppress_portal_output() -> Iterator[None]:
    """Discard untrusted portal-library output at the production worker boundary."""

    with open(os.devnull, "w", encoding="utf-8") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            yield
