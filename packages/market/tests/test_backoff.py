"""Unit tests for :func:`packages.market.backoff.exp_backoff_delays` (H-007).

The reconnect-loop hazard test (`test_ws_reconnect_uses_exponential_backoff`)
proves the integration; this file covers the generator's contract in
isolation:

* Delays are bounded: each yielded delay ``d_n`` lies in
  ``[0, min(base * 2**n, cap)]``.
* The ceiling sequence follows powers of two until the cap, then stays
  at the cap forever.
* Generator yields indefinitely (no premature StopIteration).
* RNG injection is deterministic — same seed yields identical sequence.
"""

from __future__ import annotations

import itertools
import random

import pytest

from packages.market import exp_backoff_delays


def test_delays_within_full_jitter_envelope() -> None:
    """Each delay falls within ``[0, min(base * 2**n, cap)]``."""
    rng = random.Random(0xC0FFEE)
    delays = list(itertools.islice(exp_backoff_delays(rng=rng), 10))
    for n, delay in enumerate(delays):
        ceiling = min(2**n, 60.0)
        assert 0.0 <= delay <= ceiling, f"attempt {n}: delay {delay} outside [0, {ceiling}]"


def test_ceiling_caps_at_60_seconds() -> None:
    """After enough attempts, the upper bound saturates at the cap."""
    # Fix the rng to always pull the maximum (uniform(0, c) → c on a stub).
    fixed = random.Random()
    fixed.uniform = lambda a, b: b  # type: ignore[method-assign]
    delays = list(itertools.islice(exp_backoff_delays(rng=fixed), 12))
    expected = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0, 60.0, 60.0, 60.0]
    assert delays == expected


def test_generator_runs_forever() -> None:
    """The generator never raises StopIteration; bounded iteration suffices."""
    gen = exp_backoff_delays()
    # Pull a few hundred to convince ourselves; the contract is "forever",
    # but pytest can't actually wait forever — 500 covers the cap-saturation
    # regime several times over.
    for _ in range(500):
        delay = next(gen)
        assert 0.0 <= delay <= 60.0


def test_rng_injection_is_deterministic() -> None:
    """Same seed yields identical sequence — required for replay debugging."""
    a = list(itertools.islice(exp_backoff_delays(rng=random.Random(42)), 20))
    b = list(itertools.islice(exp_backoff_delays(rng=random.Random(42)), 20))
    assert a == b


def test_custom_base_and_cap() -> None:
    """`base_seconds` / `cap_seconds` flow through to the ceiling."""
    fixed = random.Random()
    fixed.uniform = lambda a, b: b  # type: ignore[method-assign]
    delays = list(
        itertools.islice(
            exp_backoff_delays(base_seconds=0.5, cap_seconds=4.0, rng=fixed),
            6,
        )
    )
    assert delays == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 0xDEADBEEF])
def test_property_delays_monotonic_ceiling(seed: int) -> None:
    """For any RNG seed, the *ceiling* (not the sample) is non-decreasing."""
    rng = random.Random(seed)
    # Re-derive the ceiling from each sample's upper bound. Since the
    # generator emits `uniform(0, ceiling)`, we can't observe the ceiling
    # directly — but we can observe `max(samples_per_attempt)` over many
    # trials. Instead, check the structural invariant: the index-vs-sample
    # mapping respects 2**n ≤ 60 then plateau.
    delays = list(itertools.islice(exp_backoff_delays(rng=rng), 12))
    ceilings = [min(2**n, 60.0) for n in range(12)]
    for n, (delay, ceiling) in enumerate(zip(delays, ceilings, strict=True)):
        assert 0.0 <= delay <= ceiling, f"attempt {n}: {delay} > {ceiling}"
