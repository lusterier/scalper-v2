"""Exception hierarchy: InvalidDsnError ⊂ DatabaseError ⊂ ScalperError (§5.4)."""

from __future__ import annotations

import pytest

from packages.core import ScalperError
from packages.db import DatabaseError, InvalidDsnError


def test_database_error_subclasses_scalper_error() -> None:
    assert issubclass(DatabaseError, ScalperError)


def test_invalid_dsn_error_subclasses_database_error() -> None:
    assert issubclass(InvalidDsnError, DatabaseError)


def test_invalid_dsn_error_reachable_via_scalper_error() -> None:
    with pytest.raises(ScalperError) as exc_info:
        raise InvalidDsnError("bad scheme")
    assert isinstance(exc_info.value, InvalidDsnError)
