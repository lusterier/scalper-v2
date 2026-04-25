"""Exponential-backoff delay generator with full jitter (§9.2, H-007).

`exp_backoff_delays` is the backoff source for `BinanceWsClient`'s
reconnect loop. The sequence follows the AWS-published "full jitter"
recipe:

    delay_n = uniform(0, min(base * 2**n, cap))

with `base=1.0` second and `cap=60.0` seconds — 1s, 2s, 4s, 8s, 16s,
32s, 60s, 60s, … (each value sampled in [0, ceiling]).

Hand-rolled instead of using `backoff` / `tenacity` per §0.9 — the
single use case (driving an async reconnect loop with structured
per-attempt logging) does not justify a runtime dep, and the loop
shape `async for delay in exp_backoff_delays(): await asyncio.sleep(delay); ...`
is cancellation-friendly out of the box.

The generator yields forever — the caller decides when to stop
(typically: never, since market-data-svc is long-lived and §9.2 alerts
on disconnect > 60s rather than giving up).
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


__all__ = ["exp_backoff_delays"]


_DEFAULT_BASE_SECONDS = 1.0
_DEFAULT_CAP_SECONDS = 60.0


def exp_backoff_delays(
    *,
    base_seconds: float = _DEFAULT_BASE_SECONDS,
    cap_seconds: float = _DEFAULT_CAP_SECONDS,
    rng: random.Random | None = None,
) -> Iterator[float]:
    """Yield exponential-backoff delays with full jitter, forever.

    `rng` accepts an injected :class:`random.Random` for deterministic
    tests. When omitted, the module-global random is used.
    """
    rand = rng if rng is not None else random
    attempt = 0
    while True:
        ceiling = min(base_seconds * (2**attempt), cap_seconds)
        yield rand.uniform(0.0, ceiling)
        attempt += 1
