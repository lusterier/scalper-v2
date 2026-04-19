"""Function-scope autouse fixture: reset the test clock after every test.

Tests that swap the clock via `_testing_set_clock` don't need to clean
up — this fixture's teardown calls `_testing_reset_clock` after every
test, preventing state leakage across the test session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from packages.core.time import _testing_reset_clock

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def reset_clock() -> Iterator[None]:
    yield
    _testing_reset_clock()
