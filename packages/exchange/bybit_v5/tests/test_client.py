"""§11.2 BybitV5Client unit tests (T-207).

§N4 TDD steps 1-5 per plan-doc. Mock httpx.AsyncClient (no real HTTP);
deterministic HMAC-SHA256 signing fixtures (Hand verification §F.1);
mock 4xx/5xx/timeout responses for retry matrix tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from packages.exchange.bybit_v5.client import (
    _BASE_BACKOFF_S,
    _JITTER_PCT,
    _RECV_WINDOW_MS,
    BybitV5Client,
)
from packages.exchange.errors import (
    AuthError,
    ExchangeError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# --- Hand-computed signing vectors (§F.1) ----------------------------------

_API_KEY = "abc123"
_API_SECRET = "secret"
_TIMESTAMP_MS = 1700000000000


def _expected_signature(payload: str) -> str:
    """Hand-computed HMAC-SHA256 hex-digest reference."""
    return hmac.new(
        _API_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# --- Test client factory ----------------------------------------------------


def _make_client() -> BybitV5Client:
    return BybitV5Client(
        api_key=_API_KEY,
        api_secret=_API_SECRET,
        base_url="https://api.bybit.com",
    )


# --- Signing tests (§F.1) ---------------------------------------------------


def test_sign_request_get_with_query_params_yields_known_signature() -> None:
    """§F.1 vector 1: GET with sorted query params."""
    client = _make_client()
    headers = client._sign_request(
        method="GET",
        params={"category": "linear", "symbol": "BTCUSDT"},
        body=None,
        timestamp_ms=_TIMESTAMP_MS,
    )
    payload = f"{_TIMESTAMP_MS}{_API_KEY}{_RECV_WINDOW_MS}category=linear&symbol=BTCUSDT"
    assert headers["X-BAPI-SIGN"] == _expected_signature(payload)


def test_sign_request_post_with_json_body_yields_known_signature() -> None:
    """§F.1 vector 2: POST with compact JSON body."""
    client = _make_client()
    body = {"category": "linear", "symbol": "BTCUSDT", "side": "Buy", "qty": "0.5"}
    headers = client._sign_request(
        method="POST",
        params=None,
        body=body,
        timestamp_ms=_TIMESTAMP_MS,
    )
    body_json = json.dumps(body, separators=(",", ":"))
    payload = f"{_TIMESTAMP_MS}{_API_KEY}{_RECV_WINDOW_MS}{body_json}"
    assert headers["X-BAPI-SIGN"] == _expected_signature(payload)


def test_sign_request_get_with_empty_params_yields_known_signature() -> None:
    """§F.1 vector 3: GET with no params."""
    client = _make_client()
    headers = client._sign_request(
        method="GET",
        params=None,
        body=None,
        timestamp_ms=_TIMESTAMP_MS,
    )
    payload = f"{_TIMESTAMP_MS}{_API_KEY}{_RECV_WINDOW_MS}"
    assert headers["X-BAPI-SIGN"] == _expected_signature(payload)


def test_sign_request_includes_all_four_headers() -> None:
    """Headers: X-BAPI-API-KEY, X-BAPI-TIMESTAMP, X-BAPI-RECV-WINDOW, X-BAPI-SIGN."""
    client = _make_client()
    headers = client._sign_request(method="GET", params=None, body=None, timestamp_ms=_TIMESTAMP_MS)
    assert set(headers) >= {
        "X-BAPI-API-KEY",
        "X-BAPI-TIMESTAMP",
        "X-BAPI-RECV-WINDOW",
        "X-BAPI-SIGN",
    }
    assert headers["X-BAPI-API-KEY"] == _API_KEY
    assert headers["X-BAPI-TIMESTAMP"] == str(_TIMESTAMP_MS)
    assert headers["X-BAPI-RECV-WINDOW"] == str(_RECV_WINDOW_MS)


def test_sign_request_uses_recv_window_5000() -> None:
    """Module constant pinned per Bybit V5 default."""
    assert _RECV_WINDOW_MS == 5000


def test_sign_request_sorts_query_params_alphabetically_regardless_of_dict_order() -> None:
    """W#1 — query keys sorted before signing AND outbound URL.

    Caller passes dict in non-alphabetical order; signing payload + outbound
    URL must both use sorted form, otherwise Bybit rejects with sign mismatch.
    """
    client = _make_client()
    # Insertion order: symbol first, category second.
    headers = client._sign_request(
        method="GET",
        params={"symbol": "BTCUSDT", "category": "linear"},
        body=None,
        timestamp_ms=_TIMESTAMP_MS,
    )
    # Sorted: category first, symbol second.
    expected_payload = f"{_TIMESTAMP_MS}{_API_KEY}{_RECV_WINDOW_MS}category=linear&symbol=BTCUSDT"
    assert headers["X-BAPI-SIGN"] == _expected_signature(expected_payload)


# --- retCode mapping tests (§F.2) ------------------------------------------


def _envelope(
    retcode: int,
    retmsg: str = "OK",
    result: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "retCode": retcode,
        "retMsg": retmsg,
        "result": dict(result) if result else {},
        "time": 0,
    }


@pytest.fixture
def mock_request(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch httpx.AsyncClient.request with an AsyncMock returning JSON envelopes."""
    mock = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient.request", mock)
    return mock


def _http_response(status: int, body: dict[str, object] | str) -> MagicMock:
    """Synth httpx.Response — supports .status_code + .json() + .text + .content."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    if isinstance(body, dict):
        resp.json = MagicMock(return_value=body)
        resp.text = json.dumps(body)
        resp.content = resp.text.encode("utf-8")
    else:
        resp.json = MagicMock(side_effect=json.JSONDecodeError("malformed", body, 0))
        resp.text = body
        resp.content = body.encode("utf-8")
    return resp


@pytest.mark.parametrize("retcode", [10006, 10016])
async def test_retcode_rate_limit_maps_to_RateLimitError(
    retcode: int,
    mock_request: AsyncMock,
) -> None:
    """Bybit retCode 10006/10016 → RateLimitError."""
    mock_request.return_value = _http_response(200, _envelope(retcode, "rate limit"))
    client = _make_client()
    with pytest.raises(RateLimitError):
        await client.request("GET", "/v5/account/wallet-balance", retries=0)


@pytest.mark.parametrize("retcode", [10003, 10004])
async def test_retcode_auth_maps_to_AuthError(
    retcode: int,
    mock_request: AsyncMock,
) -> None:
    """Bybit retCode 10003/10004 → AuthError."""
    mock_request.return_value = _http_response(200, _envelope(retcode, "invalid sign"))
    client = _make_client()
    with pytest.raises(AuthError):
        await client.request("GET", "/v5/account/wallet-balance", retries=0)


@pytest.mark.parametrize("retcode", [10001, 10005])
async def test_retcode_reject_maps_to_OrderRejected_with_reason(
    retcode: int,
    mock_request: AsyncMock,
) -> None:
    """Bybit retCode 10001/10005 → OrderRejected(reason=retmsg)."""
    mock_request.return_value = _http_response(
        200, _envelope(retcode, "param error: qty too small")
    )
    client = _make_client()
    with pytest.raises(OrderRejected) as info:
        await client.request("POST", "/v5/order/create", retries=0)
    assert info.value.reason == "param error: qty too small"


async def test_retcode_zero_returns_result_field(mock_request: AsyncMock) -> None:
    """retCode==0 → return result dict."""
    expected_result = {"orderId": "abc-123", "orderLinkId": "x"}
    mock_request.return_value = _http_response(200, _envelope(0, "OK", expected_result))
    client = _make_client()
    result = await client.request("POST", "/v5/order/create", retries=0)
    assert result == expected_result


async def test_retcode_other_maps_to_generic_ExchangeError(
    mock_request: AsyncMock,
) -> None:
    """Unknown non-zero retCode → generic ExchangeError."""
    mock_request.return_value = _http_response(200, _envelope(99999, "unknown"))
    client = _make_client()
    with pytest.raises(ExchangeError, match="retCode=99999"):
        await client.request("GET", "/v5/foo", retries=0)


async def test_request_raises_exchange_error_on_malformed_response_json_with_original_chained(
    mock_request: AsyncMock,
) -> None:
    """W#4 — HTTP 200 with malformed JSON → ExchangeError with __cause__."""
    mock_request.return_value = _http_response(200, "not-json-at-all{{{")
    client = _make_client()
    with pytest.raises(ExchangeError) as info:
        await client.request("GET", "/v5/foo", retries=0)
    assert isinstance(info.value.__cause__, json.JSONDecodeError)


# --- HTTP status mapping tests ---------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
async def test_http_4xx_auth_maps_to_AuthError(
    status: int,
    mock_request: AsyncMock,
) -> None:
    """HTTP 401/403 → AuthError."""
    mock_request.return_value = _http_response(status, {"error": "auth"})
    client = _make_client()
    with pytest.raises(AuthError):
        await client.request("GET", "/v5/foo", retries=0)


async def test_http_429_raises_RateLimitError(mock_request: AsyncMock) -> None:
    """HTTP 429 → RateLimitError (no retry — caller's job)."""
    mock_request.return_value = _http_response(429, {"error": "rate limit"})
    client = _make_client()
    with pytest.raises(RateLimitError):
        await client.request("GET", "/v5/foo", retries=3)


# --- Retry matrix tests (§F.2 + §F.3) --------------------------------------


@pytest.fixture
def captured_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Patch asyncio.sleep to capture delay values without blocking."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    return sleeps


async def test_request_with_retry_3_retries_succeeds_on_attempt_3(
    mock_request: AsyncMock,
    captured_sleeps: list[float],
) -> None:
    """First 2 attempts time out, third succeeds."""
    mock_request.side_effect = [
        httpx.ReadTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
        _http_response(200, _envelope(0, "OK", {"ok": True})),
    ]
    client = _make_client()
    result = await client.request("GET", "/v5/foo", retries=3)
    assert result == {"ok": True}
    assert mock_request.await_count == 3
    # Two sleeps between three attempts.
    assert len(captured_sleeps) == 2


async def test_request_with_retry_zero_retries_raises_immediately_on_timeout(
    mock_request: AsyncMock,
    captured_sleeps: list[float],
) -> None:
    """H-003: retries=0 → single attempt → on timeout NetworkTimeout immediate."""
    mock_request.side_effect = httpx.ReadTimeout("timeout")
    client = _make_client()
    with pytest.raises(NetworkTimeout):
        await client.request("POST", "/v5/order/create", retries=0)
    assert mock_request.await_count == 1
    assert captured_sleeps == []


async def test_request_with_retry_exhausts_retries_then_raises_network_timeout(
    mock_request: AsyncMock,
    captured_sleeps: list[float],
) -> None:
    """All 4 attempts (1 initial + 3 retries) timeout → raise NetworkTimeout."""
    mock_request.side_effect = httpx.ReadTimeout("timeout")
    client = _make_client()
    with pytest.raises(NetworkTimeout):
        await client.request("GET", "/v5/foo", retries=3)
    assert mock_request.await_count == 4


async def test_request_with_retry_5xx_is_retryable(
    mock_request: AsyncMock,
    captured_sleeps: list[float],
) -> None:
    """HTTP 503 → retry → eventually succeed."""
    mock_request.side_effect = [
        _http_response(503, {"error": "down"}),
        _http_response(200, _envelope(0, "OK", {"ok": True})),
    ]
    client = _make_client()
    result = await client.request("GET", "/v5/foo", retries=3)
    assert result == {"ok": True}
    assert mock_request.await_count == 2


async def test_request_with_retry_uses_jittered_backoff_per_attempt(
    mock_request: AsyncMock,
    captured_sleeps: list[float],
) -> None:
    """§F.3 — sleep falls in [base*0.9, base*1.1] for base ∈ [0.5, 1.0, 2.0]."""
    mock_request.side_effect = [
        httpx.ReadTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
        _http_response(200, _envelope(0, "OK", {"ok": True})),
    ]
    client = _make_client()
    await client.request("GET", "/v5/foo", retries=3)
    assert len(captured_sleeps) == 3
    for attempt, sleep_value in enumerate(captured_sleeps):
        base = _BASE_BACKOFF_S[attempt]
        low = base * (1.0 - _JITTER_PCT)
        high = base * (1.0 + _JITTER_PCT)
        assert low <= sleep_value <= high, (
            f"attempt {attempt} sleep {sleep_value} out of [{low}, {high}]"
        )


async def test_request_with_retry_429_does_not_retry(
    mock_request: AsyncMock,
    captured_sleeps: list[float],
) -> None:
    """HTTP 429 raises RateLimitError immediately even with retries=3."""
    mock_request.return_value = _http_response(429, {"error": "rate limit"})
    client = _make_client()
    with pytest.raises(RateLimitError):
        await client.request("GET", "/v5/foo", retries=3)
    assert mock_request.await_count == 1
    assert captured_sleeps == []


# --- POST body serialization parity (W#2) ----------------------------------


async def test_sign_request_post_body_serialization_matches_outbound_request_body(
    mock_request: AsyncMock,
) -> None:
    """W#2 — httpx must send byte-identical body to what was signed.

    Pre-serialize once via json.dumps(body, separators=(',', ':'));
    pass via content= (not json= which may re-serialize differently).
    """
    body = {"category": "linear", "symbol": "BTCUSDT", "qty": "0.5"}
    expected_body_json = json.dumps(body, separators=(",", ":"))
    mock_request.return_value = _http_response(200, _envelope(0))
    client = _make_client()
    await client.request("POST", "/v5/order/create", body=body, retries=0)
    assert mock_request.await_args is not None
    call_kwargs = mock_request.await_args.kwargs
    # Either content= bytes/str OR equivalent — compare against pre-serialized form.
    sent_body = call_kwargs.get("content")
    assert sent_body is not None, "POST must send body via content= for parity with signing"
    if isinstance(sent_body, bytes):
        sent_body = sent_body.decode("utf-8")
    assert sent_body == expected_body_json


# --- Constructor + lifecycle tests (W#5) -----------------------------------


def test_constructor_accepts_required_kwargs_and_creates_httpx_client() -> None:
    client = BybitV5Client(
        api_key="k",
        api_secret="s",
        base_url="https://test.bybit.com",
        connect_timeout=2.0,
        read_timeout=4.0,
    )
    assert client._api_key == "k"
    assert client._api_secret == "s"
    assert isinstance(client._client, httpx.AsyncClient)


async def test_close_drains_httpx_pool() -> None:
    client = _make_client()
    client._client.aclose = AsyncMock()  # type: ignore[method-assign]
    await client.close()
    client._client.aclose.assert_awaited_once()


async def test_context_manager_closes_on_exit_even_on_exception() -> None:
    """W#5 — __aexit__ calls close() even when body raises."""
    client = _make_client()
    client._client.aclose = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="bang"):
        async with client:
            raise RuntimeError("bang")
    client._client.aclose.assert_awaited_once()
