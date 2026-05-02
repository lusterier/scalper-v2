"""Tests for :func:`services.execution.app.main.create_app`.

Verifies that:

* The factory returns a :class:`FastAPI` instance with `/health`,
  `/ready`, `/metrics` registered.
* Sync state (settings, logger) attaches before the lifespan runs.
* Async lifespan state (pool, bus) attaches after lifespan entry —
  verified by exercising the lifespan via :class:`TestClient`.
* T-214 lifespan ordering: pool → bus → state attach; reverse on
  shutdown (``bus.close → pool.close``). Order matters per T-200 Q2
  publish-after-persist contract — the pool must outlive the bus so
  any in-flight publish that touches the pool finishes against an
  open pool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from fastapi import FastAPI


def test_create_app_returns_fastapi_instance(app_with_mocks: FastAPI) -> None:
    from fastapi import FastAPI as _FastAPI

    assert isinstance(app_with_mocks, _FastAPI)


def test_routes_registered(app_with_mocks: FastAPI) -> None:
    """`/health`, `/ready`, `/metrics` are all reachable."""
    paths = {route.path for route in app_with_mocks.routes}  # type: ignore[attr-defined]
    assert "/health" in paths
    assert "/ready" in paths
    # `/metrics` is mounted as a sub-app; appears as a Mount in routes.
    mounts = {
        route.path  # type: ignore[attr-defined]
        for route in app_with_mocks.routes
        if route.__class__.__name__ == "Mount"
    }
    assert "/metrics" in mounts


def test_metrics_endpoint_serves_default_collectors(client: TestClient) -> None:
    """`/metrics` returns 200 with default Prometheus collectors in the body."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert b"python_info" in response.content


def test_sync_state_attached_before_lifespan(app_with_mocks: FastAPI) -> None:
    """Settings + logger land on app.state in create_app body, not in lifespan."""
    assert app_with_mocks.state.settings is not None
    assert app_with_mocks.state.logger is not None


def test_lifespan_attaches_pool_and_bus_and_closes_in_reverse_order(
    app_with_mocks: FastAPI,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
) -> None:
    """pool / bus land on app.state inside lifespan; teardown reverse-order.

    Order ``bus.close → pool.close`` is load-bearing for the future
    T-216 publish-after-persist flow (T-200 Q2): pool stays open until
    the bus has fully drained.
    """
    call_order: list[str] = []
    mock_bus.close.side_effect = lambda: call_order.append("bus")
    mock_pool.close.side_effect = lambda: call_order.append("pool")

    with TestClient(app_with_mocks):
        assert app_with_mocks.state.pool is mock_pool
        assert app_with_mocks.state.bus is mock_bus

    mock_bus.close.assert_awaited_once()
    mock_pool.close.assert_awaited_once()
    assert call_order == ["bus", "pool"]


def test_lifespan_attaches_adapters_rate_limiter_and_task_lists(
    app_with_mocks: FastAPI,
    mock_rate_limiter: MagicMock,
    mock_adapter_pool_result: MagicMock,
) -> None:
    """T-215: app.state carries adapters / rate_limiter / ws_tasks / paper_consumer_tasks."""
    with TestClient(app_with_mocks):
        assert app_with_mocks.state.rate_limiter is mock_rate_limiter
        assert app_with_mocks.state.adapters is mock_adapter_pool_result.adapters
        assert app_with_mocks.state.ws_tasks is mock_adapter_pool_result.ws_tasks
        assert (
            app_with_mocks.state.paper_consumer_tasks
            is mock_adapter_pool_result.paper_consumer_tasks
        )


def test_lifespan_subscribes_to_orders_requests_per_bot(
    settings: object,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_rate_limiter: MagicMock,
    monkeypatch: object,
) -> None:
    """T-216a: per-bot subscription loop — `bus.subscribe(orders.requests.<bot_id>, ...)`
    invoked once per bot in adapter pool. NO wildcard `.>`.
    """
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.execution.app.main import create_app

    def _adapter() -> _MagicMock:
        a = _MagicMock()
        a.close = _AsyncMock()
        return a

    fake_pool_result = _MagicMock()
    fake_pool_result.adapters = {"alpha": _adapter(), "beta": _adapter()}
    fake_pool_result.ws_tasks = []
    fake_pool_result.paper_consumer_tasks = []

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.create_pool",
        _AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.NatsClient",
        _MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.SharedRateLimiter",
        _MagicMock(return_value=mock_rate_limiter),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.build_adapter_pool",
        _AsyncMock(return_value=fake_pool_result),
    )
    app = create_app(settings=settings)  # type: ignore[arg-type]
    with TestClient(app):
        pass
    subscribe_subjects = [call.args[0] for call in mock_bus.subscribe.await_args_list]
    assert "orders.requests.alpha" in subscribe_subjects
    assert "orders.requests.beta" in subscribe_subjects
    assert "orders.requests.>" not in subscribe_subjects
    assert mock_bus.subscribe.await_count == 2


def test_lifespan_subscribes_each_bot_with_handler_wrapped_in_OrderRequestDedupConsumer(
    settings: object,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_rate_limiter: MagicMock,
    monkeypatch: object,
) -> None:
    """T-216b2 — per-bot subscribe handler is :class:`OrderRequestDedupConsumer.consume`."""
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.execution.app.main import create_app
    from services.execution.app.placement_persist import OrderRequestDedupConsumer

    def _adapter() -> _MagicMock:
        a = _MagicMock()
        a.close = _AsyncMock()
        return a

    fake_pool_result = _MagicMock()
    fake_pool_result.adapters = {"alpha": _adapter(), "beta": _adapter()}
    fake_pool_result.ws_tasks = []
    fake_pool_result.paper_consumer_tasks = []

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.create_pool",
        _AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.NatsClient",
        _MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.SharedRateLimiter",
        _MagicMock(return_value=mock_rate_limiter),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.build_adapter_pool",
        _AsyncMock(return_value=fake_pool_result),
    )
    app = create_app(settings=settings)  # type: ignore[arg-type]
    with TestClient(app):
        pass
    # Each subscribe call carries a handler whose __self__ is OrderRequestDedupConsumer.
    handlers = [call.args[1] for call in mock_bus.subscribe.await_args_list]
    assert len(handlers) == 2
    for handler in handlers:
        assert isinstance(handler.__self__, OrderRequestDedupConsumer)
        assert handler.__name__ == "consume"


def test_lifespan_threads_settings_execution_orders_dedup_capacity_to_make_per_bot_handler(
    settings: object,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_rate_limiter: MagicMock,
    monkeypatch: object,
) -> None:
    """T-216b2 — Settings.execution_orders_dedup_capacity propagates to per-bot consumer."""
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.execution.app.main import create_app

    def _adapter() -> _MagicMock:
        a = _MagicMock()
        a.close = _AsyncMock()
        return a

    fake_pool_result = _MagicMock()
    fake_pool_result.adapters = {"alpha": _adapter()}
    fake_pool_result.ws_tasks = []
    fake_pool_result.paper_consumer_tasks = []

    captured_kwargs: list[dict[str, object]] = []

    def _capture_handler_factory(**kwargs: object) -> object:
        captured_kwargs.append(kwargs)

        async def _no_op(_: object) -> None:
            return None

        return _no_op

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.create_pool",
        _AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.NatsClient",
        _MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.SharedRateLimiter",
        _MagicMock(return_value=mock_rate_limiter),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.build_adapter_pool",
        _AsyncMock(return_value=fake_pool_result),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.make_per_bot_handler",
        _capture_handler_factory,
    )
    app = create_app(settings=settings)  # type: ignore[arg-type]
    with TestClient(app):
        pass
    assert len(captured_kwargs) == 1
    kwargs = captured_kwargs[0]
    assert kwargs["dedup_capacity"] == settings.execution_orders_dedup_capacity  # type: ignore[attr-defined]
    assert kwargs["pool"] is mock_pool
    assert callable(kwargs["now_fn"])


def test_lifespan_spawns_one_dispatcher_task_per_bot_named_dispatcher_botid(
    settings: object,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_rate_limiter: MagicMock,
    monkeypatch: object,
) -> None:
    """T-218a WG#4 — one asyncio.Task per bot named ``dispatcher_<bot_id>``."""
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.execution.app.main import create_app

    def _adapter() -> _MagicMock:
        a = _MagicMock()
        a.close = _AsyncMock()

        async def _empty_stream() -> object:
            for _ in ():  # empty iter — never yields but marks function as async generator
                yield _

        a.stream_executions = _MagicMock(return_value=_empty_stream())
        return a

    fake_pool_result = _MagicMock()
    fake_pool_result.adapters = {"alpha": _adapter(), "beta": _adapter()}
    fake_pool_result.ws_tasks = []
    fake_pool_result.paper_consumer_tasks = []

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.create_pool",
        _AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.NatsClient",
        _MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.SharedRateLimiter",
        _MagicMock(return_value=mock_rate_limiter),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.build_adapter_pool",
        _AsyncMock(return_value=fake_pool_result),
    )
    app = create_app(settings=settings)  # type: ignore[arg-type]
    with TestClient(app):
        # During lifespan: app.state.dispatcher_tasks holds 2 tasks named
        # ``dispatcher_alpha`` + ``dispatcher_beta``.
        task_names = sorted(t.get_name() for t in app.state.dispatcher_tasks)
        assert task_names == ["dispatcher_alpha", "dispatcher_beta"]


def test_lifespan_cancels_dispatcher_tasks_before_adapter_close(
    settings: object,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_rate_limiter: MagicMock,
    monkeypatch: object,
) -> None:
    """T-218a WG#5 — dispatcher_tasks cancel BEFORE adapter.close (graceful stop signal)."""
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.execution.app.main import create_app

    shutdown_sequence: list[str] = []

    def _adapter(label: str) -> _MagicMock:
        import asyncio as _asyncio

        a = _MagicMock()

        async def _close() -> None:
            shutdown_sequence.append(f"adapter_close_{label}")

        a.close = _close

        async def _hanging_stream() -> object:
            # Hang indefinitely so the dispatcher task is alive at shutdown
            # and goes through the cancel path. The unreachable `yield`
            # below is the async-generator marker for this function.
            for _ in ():  # never iterates
                yield _
            await _asyncio.Event().wait()

        a.stream_executions = _MagicMock(return_value=_hanging_stream())
        return a

    fake_pool_result = _MagicMock()
    fake_pool_result.adapters = {"alpha": _adapter("alpha")}
    fake_pool_result.ws_tasks = []
    fake_pool_result.paper_consumer_tasks = []

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.create_pool",
        _AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.NatsClient",
        _MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.SharedRateLimiter",
        _MagicMock(return_value=mock_rate_limiter),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.build_adapter_pool",
        _AsyncMock(return_value=fake_pool_result),
    )

    # Wrap run_dispatcher_for_bot so it logs cancellation timing.
    real_run = __import__(
        "services.execution.app.main", fromlist=["run_dispatcher_for_bot"]
    ).run_dispatcher_for_bot

    async def _spy_run_dispatcher_for_bot(*args: object, **kwargs: object) -> None:
        try:
            await real_run(*args, **kwargs)
        except __import__("asyncio").CancelledError:
            shutdown_sequence.append("dispatcher_cancel")
            raise

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.run_dispatcher_for_bot",
        _spy_run_dispatcher_for_bot,
    )

    app = create_app(settings=settings)  # type: ignore[arg-type]
    with TestClient(app):
        pass

    # Order pin: dispatcher_cancel BEFORE adapter_close_alpha.
    assert "dispatcher_cancel" in shutdown_sequence
    assert "adapter_close_alpha" in shutdown_sequence
    assert shutdown_sequence.index("dispatcher_cancel") < shutdown_sequence.index(
        "adapter_close_alpha"
    )


def test_lifespan_threads_settings_dispatch_dedup_capacity_to_dispatcher(
    settings: object,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_rate_limiter: MagicMock,
    monkeypatch: object,
) -> None:
    """T-218a — Settings.dispatch_dedup_capacity propagates to ExecutionDispatcher ctor."""
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.execution.app.main import create_app

    def _adapter() -> _MagicMock:
        a = _MagicMock()
        a.close = _AsyncMock()

        async def _empty_stream() -> object:
            for _ in ():  # empty iter — never yields but marks function as async generator
                yield _

        a.stream_executions = _MagicMock(return_value=_empty_stream())
        return a

    fake_pool_result = _MagicMock()
    fake_pool_result.adapters = {"alpha": _adapter()}
    fake_pool_result.ws_tasks = []
    fake_pool_result.paper_consumer_tasks = []

    captured_ctor_kwargs: list[dict[str, object]] = []

    def _capture_dispatcher(**kwargs: object) -> object:
        captured_ctor_kwargs.append(kwargs)
        # Return a dummy with the methods main.py + run_dispatcher_for_bot need.
        d = _MagicMock()
        d.bot_id = kwargs["bot_id"]

        async def _consume(*_args: object, **_kwargs: object) -> None:
            return None

        d.consume = _consume
        return d

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.create_pool",
        _AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.NatsClient",
        _MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.SharedRateLimiter",
        _MagicMock(return_value=mock_rate_limiter),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.build_adapter_pool",
        _AsyncMock(return_value=fake_pool_result),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.ExecutionDispatcher",
        _capture_dispatcher,
    )
    app = create_app(settings=settings)  # type: ignore[arg-type]
    with TestClient(app):
        pass
    assert len(captured_ctor_kwargs) == 1
    kwargs = captured_ctor_kwargs[0]
    assert kwargs["capacity"] == settings.dispatch_dedup_capacity  # type: ignore[attr-defined]
    assert kwargs["pool"] is mock_pool
    assert kwargs["bus"] is mock_bus
    assert callable(kwargs["now_fn"])


# ---------------------------------------------------------------------------
# T-217a — PositionLifecycle lifespan integration tests
# ---------------------------------------------------------------------------


def test_lifespan_initializes_empty_position_lifecycle_tasks_dict(
    app_with_mocks: FastAPI,
) -> None:
    """T-217a — app.state.position_lifecycle_tasks is an empty dict at lifespan start."""
    with TestClient(app_with_mocks):
        assert app_with_mocks.state.position_lifecycle_tasks == {}
        assert isinstance(app_with_mocks.state.position_lifecycle_tasks, dict)


def test_lifespan_threads_settings_position_poll_to_make_per_bot_handler(
    settings: object,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_rate_limiter: MagicMock,
    monkeypatch: object,
) -> None:
    """T-217a — Settings position_poll fields threaded into make_per_bot_handler ctor."""
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import MagicMock as _MagicMock

    from services.execution.app.main import create_app

    def _adapter() -> _MagicMock:
        a = _MagicMock()
        a.close = _AsyncMock()
        return a

    fake_pool_result = _MagicMock()
    fake_pool_result.adapters = {"alpha": _adapter()}
    fake_pool_result.ws_tasks = []
    fake_pool_result.paper_consumer_tasks = []

    captured_kwargs: list[dict[str, object]] = []

    def _capture_handler(**kwargs: object) -> object:
        captured_kwargs.append(kwargs)

        async def _handler(_envelope: object) -> None:
            return None

        return _handler

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.create_pool",
        _AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.NatsClient",
        _MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.SharedRateLimiter",
        _MagicMock(return_value=mock_rate_limiter),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.build_adapter_pool",
        _AsyncMock(return_value=fake_pool_result),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "services.execution.app.main.make_per_bot_handler",
        _capture_handler,
    )
    app = create_app(settings=settings)  # type: ignore[arg-type]
    with TestClient(app):
        pass
    assert len(captured_kwargs) == 1
    kwargs = captured_kwargs[0]
    assert kwargs["position_poll_interval_s"] == settings.position_poll_interval_s  # type: ignore[attr-defined]
    assert kwargs["position_poll_stale_ticks"] == settings.position_poll_stale_ticks  # type: ignore[attr-defined]
    assert kwargs["position_lifecycle_tasks"] is app.state.position_lifecycle_tasks
