"""HMAC-SHA256 verification for signal-gateway webhook auth (§16.3).

Pure function — takes raw body bytes + inbound signature + shared secret
and returns a boolean. FastAPI-dependency wrapping (read
``request.body()``, unwrap :class:`~pydantic.SecretStr`, raise
``HTTPException(401)`` on failure) lives in the T-015b2 handler where
the ``Request`` is in scope. Keeping this layer framework-free makes it
trivially testable without Starlette/Request fixtures.

Uses :func:`hmac.compare_digest` for constant-time comparison per §16.3:
a variable-time ``==`` on an attacker-controlled signature leaks bits
of the secret through timing side-channels.
"""

from __future__ import annotations

import hashlib
import hmac

__all__ = ["verify_hmac"]


def verify_hmac(body: bytes, signature: str, secret: str) -> bool:
    """Return ``True`` iff ``signature`` is a valid HMAC-SHA256 of ``body`` under ``secret``.

    Signature format: lowercase hex digest of HMAC-SHA256, matching
    :meth:`hmac.HMAC.hexdigest`. A malformed, truncated, or empty
    ``signature`` compares byte-by-byte via :func:`hmac.compare_digest`
    and returns ``False`` without raising — callers map that to a
    ``401`` response.

    The ``secret`` parameter is a plain :class:`str`; the caller unwraps
    :class:`pydantic.SecretStr` via
    ``settings.signal_gateway_hmac_secret.get_secret_value()`` before
    invocation. Keeping the signature `str`-in / `bool`-out isolates
    this layer from Pydantic and FastAPI for unit-test fidelity.
    """
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)
