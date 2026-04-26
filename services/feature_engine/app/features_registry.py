"""Hardcoded feature registry for T-110c demo (T-111 replaces with YAML loader).

:func:`build_features` returns ``Mapping[(symbol, interval), list[tuple[str, Feature]]]``
where the leading ``str`` in each tuple is the **pre-substituted feature_name**.
Substitution happens here at registration time per T-107a docstring
("T-111 substitutes at registration time"); :mod:`pipeline` NEVER
re-substitutes — single source of truth in this layer.

The example feature is EMA-20 on ``BTCUSDT`` 1m so the T-110c live path
(1m subscribe → dispatch → persist+KV+publish) is exercised end-to-end.
Higher-interval features can be added here ahead of T-110d's warmup
wiring; their buffers will be populated at startup but not live-updated
until F1+ multi-interval cagg-trigger.

Symbol case in feature_name = lowercase per §1.7 line 244 / §7.2 line 904
/ §8.4 line 1382 example literals (``ind.btcusdt.15m.ema_20``); routing-
key tuple stays canonical Bybit-shape (e.g., ``BTCUSDT``) since that is
what :class:`packages.bus.schemas.OhlcCandlePayload.symbol` carries.

T-111 will replace this module with a ``configs/features/indicators.yaml``
loader behind the same ``Mapping[tuple[str, str], list[tuple[str, Feature]]]``
signature so T-110c/T-110d wiring stays untouched at the indicator-
registration swap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from packages.features.builtins.ema import EmaFeature

if TYPE_CHECKING:
    from collections.abc import Mapping

    from packages.features.protocols import Feature


__all__ = ["build_features"]


def build_features() -> Mapping[tuple[str, str], list[tuple[str, Feature]]]:
    """Return the demo feature registry with pre-substituted names."""
    symbol = "BTCUSDT"
    interval = "1m"
    feature = EmaFeature(period=20, interval=interval)
    feature_name = feature.name_template.format(symbol=symbol.lower(), interval=interval)
    return {(symbol, interval): [(feature_name, feature)]}
