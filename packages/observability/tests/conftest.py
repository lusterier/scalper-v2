"""Autouse fixtures for packages/observability tests.

Resets global state between tests: structlog config + contextvars, the
redactor pattern list, and the stdlib root logger's handlers. Without
this, state from one test leaks into the next.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
import structlog

from packages.observability.redact import _reset_redacted_keys

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_observability_state() -> Iterator[None]:
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    _reset_redacted_keys()
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
