"""YAML-driven feature registration (§9.3, §B.2). Public port.

Originally shipped in T-111 at ``services/feature_engine/app/yaml_loader.py``;
extracted to :mod:`packages.features` in T-112 to make the loader a public
port reusable across services and offline tools (T-112 backfill CLI).
T-111's shipped path is preserved as a thin re-export shim so existing
T-111-internal callsites (:mod:`services.feature_engine.app.features_registry`)
keep working without changes.

Body verbatim from T-111 shipped ``yaml_loader.py`` — drift checks +
type allow-list + cross-product + lowercase substitution all preserved
unchanged. Only :data:`INDICATORS_YAML_PATH` resolution adjusted from
``parents[3]`` (services-relative) to ``parents[2]`` (packages-relative)
to keep the same repo-root anchor.

Reads ``configs/features/indicators.yaml`` (§B.2 verbatim shape) and
cross-products each indicator with the symbol set
(``FEATURE_ENGINE_SYMBOLS`` env-stopgap mirror of ``MARKET_DATA_SYMBOLS``;
F1+ replaces with ``bots`` JOIN ``bot_configs`` per §9.2 line 1454).

Same ``Mapping[(symbol, interval), list[tuple[str, Feature]]]`` return shape
as T-110c so T-110d composition wiring stays untouched. Substitution happens
here at registration time per T-110c Decision #25 / #26: ``feature_name =
template.format(symbol=symbol.lower(), interval=interval)``.

Plugin discovery (§9.3 line 1486 ``plugin_registry.yaml``) and auto-backfill
on plugin diff (§9.3 line 1518) are F1+ — DEFERRED per T-111 plan Q1.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

import yaml

from packages.features.builtins import (
    AtrFeature,
    BollingerFeature,
    EmaFeature,
    MacdFeature,
    RsiFeature,
    VwapFeature,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from packages.features.protocols import Feature


__all__ = ["INDICATORS_YAML_PATH", "load_indicators_yaml"]


# Repo-root-relative resolution: packages/features/yaml.py → parents[0]=features,
# parents[1]=packages, parents[2]=repo root → configs/features/indicators.yaml.
INDICATORS_YAML_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "features" / "indicators.yaml"
)


# Allow-list mapping YAML ``type:`` literal → Feature class. Validated before
# constructor invocation so a typo in YAML (``builtin.emm``) fails loud at
# startup rather than producing a silent partial registry. Mirrors T-110b
# ``_INTERVAL_TO_TABLE`` allow-list pattern.
_TYPE_TO_FEATURE: Final[Mapping[str, type[Feature]]] = {
    "builtin.ema": EmaFeature,
    "builtin.rsi": RsiFeature,
    "builtin.atr": AtrFeature,
    "builtin.bollinger": BollingerFeature,
    "builtin.macd": MacdFeature,
    "builtin.vwap": VwapFeature,
}


def load_indicators_yaml(
    path: Path,
    symbols: Sequence[str],
) -> Mapping[tuple[str, str], list[tuple[str, Feature]]]:
    """Load ``configs/features/indicators.yaml`` and cross-product with ``symbols``.

    Returns Mapping keyed by ``(symbol, interval)`` where each value is a
    list of ``(feature_name, Feature)`` tuples. ``feature_name`` is the
    pre-substituted ``name_template.format(symbol=symbol.lower(),
    interval=interval)`` (lowercase symbol per §1.7 line 244 / §7.2 line 904
    / §8.4 line 1382 example literals).

    Empty ``symbols`` or missing/empty YAML file → empty mapping
    (composition root tolerates no-op per T-110d Decision #5).
    ``yaml.safe_load("")`` returns ``None``, so the ``if not raw`` guard
    is load-bearing for the empty-file case.

    Raises:
        KeyError: unknown ``type:`` value in YAML; falls through from
            ``_TYPE_TO_FEATURE`` lookup with the offending key in the
            error message.
        ValueError: YAML ``source_version`` or ``name_template`` mismatches
            the corresponding Feature instance attribute (drift check).
            Error message cites both the YAML literal and the Feature
            instance attribute so diagnostics surface the divergence.
    """
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text())
    if not raw or "features" not in raw:
        return {}
    result: dict[tuple[str, str], list[tuple[str, Feature]]] = {}
    for entry in raw["features"]:
        feature_class = _TYPE_TO_FEATURE[entry["type"]]
        interval = entry["interval"]
        params = entry.get("params", {})
        declared_source_version = entry["source_version"]
        declared_template = entry["name_template"]
        for symbol in symbols:
            # Protocol Feature has no __init__ signature; concrete builtins
            # accept **params + interval kwarg. The polymorphic dispatch is
            # intentional — the type-allow-list above guarantees feature_class
            # is one of the 6 builtins, all of which take this shape.
            feature = feature_class(**params, interval=interval)  # type: ignore[call-arg]
            # Drift checks: YAML literals must match Feature instance
            # attributes — catches indicator-class refactor that drifts
            # from §B.2 spec (L-002 control). Error message cites both
            # values so diagnosis is immediate.
            if feature.source_version != declared_source_version:
                msg = (
                    f"YAML source_version {declared_source_version!r} "
                    f"mismatches Feature.source_version "
                    f"{feature.source_version!r} for type {entry['type']!r}"
                )
                raise ValueError(msg)
            # Drift check the YAML literal against Feature.name_template with
            # ``{interval}`` already substituted (§B.2 example literals show
            # ``ind.{symbol}.15m.ema_20`` — interval baked in, only
            # ``{symbol}`` remaining). Feature.name_template has 2 placeholders
            # (``{symbol}`` + ``{interval}``); YAML has 1 (``{symbol}``).
            expected_yaml_template = feature.name_template.replace("{interval}", interval)
            if expected_yaml_template != declared_template:
                msg = (
                    f"YAML name_template {declared_template!r} mismatches "
                    f"Feature.name_template {expected_yaml_template!r} "
                    f"(after substituting interval={interval!r}) "
                    f"for type {entry['type']!r}"
                )
                raise ValueError(msg)
            feature_name = expected_yaml_template.format(symbol=symbol.lower())
            result.setdefault((symbol, interval), []).append((feature_name, feature))
    return result
