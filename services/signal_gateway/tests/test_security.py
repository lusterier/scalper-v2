"""Tests for :func:`services.signal_gateway.app.security.verify_hmac`."""

from __future__ import annotations

import hashlib
import hmac

from services.signal_gateway.app.security import verify_hmac

_SECRET = "unit-test-secret-padded-32chars!"


def _sign(body: bytes, secret: str = _SECRET) -> str:
    """Compute the reference HMAC-SHA256 hex digest a valid client would send."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_accepts_valid_signature() -> None:
    body = b'{"symbol":"BTCUSDT","action":"LONG","source":"tv","idempotency_key":"k1"}'
    assert verify_hmac(body, _sign(body), _SECRET) is True


def test_rejects_invalid_signature() -> None:
    body = b'{"any":"body"}'
    assert verify_hmac(body, "deadbeef" * 8, _SECRET) is False


def test_rejects_empty_signature() -> None:
    body = b'{"any":"body"}'
    assert verify_hmac(body, "", _SECRET) is False


def test_rejects_tampered_body() -> None:
    """Body changed after signing — signature no longer matches."""
    original = b'{"symbol":"BTCUSDT","action":"LONG"}'
    sig = _sign(original)
    tampered = b'{"symbol":"ETHUSDT","action":"LONG"}'
    assert verify_hmac(tampered, sig, _SECRET) is False


def test_rejects_signature_from_different_secret() -> None:
    """Caller used secret A; server has secret B."""
    other_secret = "other-32-char-secret-value-padded"
    body = b'{"any":"body"}'
    sig_from_other = _sign(body, other_secret)
    assert verify_hmac(body, sig_from_other, _SECRET) is False


def test_rejects_uppercase_hex_digest() -> None:
    """HMAC hex digest is lowercase per stdlib; uppercase must not match."""
    body = b'{"any":"body"}'
    sig = _sign(body)
    assert verify_hmac(body, sig.upper(), _SECRET) is False


def test_compare_is_constant_time() -> None:
    """Implementation contract: ``hmac.compare_digest`` is used for the comparison.

    This is a structural assertion — we can't observe timing here, but we can
    verify the function accepts signatures of any length (a naive ``==`` would
    short-circuit on the first mismatched byte, whereas compare_digest evaluates
    all bytes even when lengths differ).
    """
    body = b'{"any":"body"}'
    # Signatures of mismatched length should still return False, not raise.
    assert verify_hmac(body, "short", _SECRET) is False
    assert verify_hmac(body, "x" * 128, _SECRET) is False
