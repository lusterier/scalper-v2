"""Test packages.core.markers — decorator semantics, registry, CI helper."""

from __future__ import annotations

import pytest

from packages.core.markers import (
    UnlabeledMethodError,
    assert_all_methods_labeled,
    idempotent,
    is_idempotent,
    is_non_idempotent,
    non_idempotent,
)


def test_decorator_sets_attribute_and_registers() -> None:
    @idempotent
    def safe_op() -> None:
        pass

    @non_idempotent
    def unsafe_op() -> None:
        pass

    assert getattr(safe_op, "__idempotent__", False) is True
    assert getattr(unsafe_op, "__non_idempotent__", False) is True
    assert is_idempotent(safe_op)
    assert is_non_idempotent(unsafe_op)


def test_unlabeled_function_returns_false_from_both_queries() -> None:
    def plain() -> None:
        pass

    assert is_idempotent(plain) is False
    assert is_non_idempotent(plain) is False


def test_assert_all_methods_labeled_passes_when_every_public_method_labeled() -> None:
    class Adapter:
        @idempotent
        def get_position(self) -> None:
            pass

        @non_idempotent
        def place_order(self) -> None:
            pass

        def _internal(self) -> None:
            pass

    assert_all_methods_labeled(Adapter)


def test_assert_all_methods_labeled_raises_on_unlabeled_public_method() -> None:
    class Adapter:
        @idempotent
        def get_position(self) -> None:
            pass

        def place_order(self) -> None:
            pass

    with pytest.raises(UnlabeledMethodError, match="place_order"):
        assert_all_methods_labeled(Adapter)
