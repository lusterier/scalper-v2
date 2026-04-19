"""Tests for the key-based secret redactor (§16.1)."""

from __future__ import annotations

from typing import Any

from packages.observability.redact import (
    _MAX_DEPTH,
    add_redacted_keys,
    redactor,
)


def _call(event_dict: dict[str, Any]) -> dict[str, Any]:
    return dict(redactor(None, "info", dict(event_dict)))


def test_top_level_api_key_redacted() -> None:
    out = _call({"event": "order_placed", "api_key": "shhh"})
    assert out["api_key"] == "***"
    assert out["event"] == "order_placed"


def test_case_insensitive_header_style_match() -> None:
    out = _call({"headers": {"X-API-Key": "shhh", "Authorization": "Bearer xyz"}})
    assert out["headers"]["X-API-Key"] == "***"
    assert out["headers"]["Authorization"] == "***"


def test_nested_dict_password_redacted() -> None:
    out = _call({"event": "login", "payload": {"user": {"name": "alice", "password": "hunter2"}}})
    assert out["payload"]["user"]["password"] == "***"
    assert out["payload"]["user"]["name"] == "alice"


def test_list_of_dicts_redacted() -> None:
    out = _call({"items": [{"secret": "a"}, {"ok": "b"}]})
    assert out["items"][0]["secret"] == "***"
    assert out["items"][1]["ok"] == "b"


def test_tuple_values_recursed() -> None:
    out = _call({"tags": ("a", {"token": "t"})})
    assert out["tags"][0] == "a"
    assert out["tags"][1]["token"] == "***"


def test_non_secret_keys_preserved() -> None:
    out = _call({"event": "heartbeat", "count": 42, "ok": True, "ratio": 1.5})
    assert out["count"] == 42
    assert out["ok"] is True
    assert out["ratio"] == 1.5


def test_add_redacted_keys_extends_list() -> None:
    add_redacted_keys("session_id")
    out = _call({"session_id": "ABC123"})
    assert out["session_id"] == "***"


def test_add_redacted_keys_normalizes_hyphen_to_underscore() -> None:
    add_redacted_keys("X-Custom-Auth")
    out = _call({"x_custom_auth": "v", "X-Custom-Auth": "w"})
    assert out["x_custom_auth"] == "***"
    assert out["X-Custom-Auth"] == "***"


def test_depth_cap_stops_recursion() -> None:
    # Build a nested chain well past the depth cap, with a 'token' key
    # at the very bottom. At depth _MAX_DEPTH we stop descending, so the
    # deepest 'token' remains unredacted.
    deep: dict[str, Any] = {"token": "shhh"}
    for _ in range(_MAX_DEPTH + 2):
        deep = {"nested": deep}
    out = _call({"deep": deep})
    node: Any = out["deep"]
    while isinstance(node, dict) and "nested" in node:
        node = node["nested"]
    assert node == {"token": "shhh"}


def test_default_keys_cover_common_credential_fields() -> None:
    out = _call(
        {
            "api_secret": "a",
            "password": "b",
            "bearer_token": "c",  # 'bearer' and 'token' both hit
            "private_key": "d",
            "cookie": "e",
            "hmac_signature": "f",
        },
    )
    expected = ("api_secret", "password", "bearer_token", "private_key", "cookie", "hmac_signature")
    for key in expected:
        assert out[key] == "***"


def test_non_string_keys_passed_through() -> None:
    out = _call({"numeric_map": {1: "a", 2: "b"}})
    assert out["numeric_map"] == {1: "a", 2: "b"}
