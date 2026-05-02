"""§9.4:1530 + §B.1 YAML config loader (T-308).

Parses ``configs/bots/<bot_id>.yaml`` per BRIEF §B.1 alpha.yaml example,
narrows T-300 ``ScoringRule.condition: dict[str, Any]`` placeholder to a
discriminated :class:`~packages.scoring.conditions.base.Condition` union
over the 14-variant §10.2 catalog (T-302 simple 8 + T-303 series 3 +
T-304 composite 4 + T-305 plugin 1).

Recursive descent for composite (and/or/not/when_then_else) sub-conditions;
plugin conditions wired through ``plugin_registry`` per T-305 contract.

§B.1 vs T-300 schema mismatch: §B.1 puts ``feature: ind...`` INSIDE the
condition dict; T-300 ScoringRule has ``feature`` at rule level. T-308
extracts feature from condition dict at parse time + places at rule level.
For multi-feature rules (composite/series), uses the FIRST feature
encountered in DFS pre-order traversal.

Path A operator-approved 2026-05-02:
* ``ema_stack.direction: from_signal`` REJECTED at parse-time (v1 limitation;
  operator duplicates rule with applies_when {LONG, SHORT} + explicit
  direction up/down).
* No ``_multi_feature`` sentinel — first-encountered-feature uniform across
  all condition shapes; plugin without explicit feature → ValueError.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import yaml

from .conditions import (
    AndCondition,
    BetweenCondition,
    EmaStackCondition,
    EqualsCondition,
    FallingCondition,
    GtCondition,
    GteCondition,
    InCondition,
    LtCondition,
    LteCondition,
    NotCondition,
    NotEqualsCondition,
    OrCondition,
    PluginCondition,
    RisingCondition,
    WhenThenElseCondition,
)
from .types import BotConfig, ScoringConfig, ScoringRule

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .protocol import Rule


__all__ = ["load_bot_config", "parse_condition"]


def parse_condition(
    spec: dict[str, Any],
    *,
    plugin_registry: Mapping[tuple[str, str], type[Rule]] | None = None,
) -> Any:
    """Recursive 14-variant condition narrowing.

    Public for test-pin reasons; T-310 strategy-engine consumer uses
    :func:`load_bot_config`. Caller may build Condition trees from YAML
    snippets without full BotConfig load.

    Raises:
        ValueError: unknown ``type``, missing plugin, malformed structure.
        pydantic.ValidationError: bad sub-condition field types.
    """
    cond_type = spec.get("type")
    if cond_type is None:
        msg = f"unknown condition type: missing 'type' key in {spec!r}"
        raise ValueError(msg)
    return (
        _DISPATCH[cond_type](spec, plugin_registry)
        if cond_type in _DISPATCH
        else _unknown(cond_type)
    )


def _unknown(cond_type: str) -> Any:
    msg = f"unknown condition type: {cond_type!r}"
    raise ValueError(msg)


def _to_decimal(value: Any) -> Any:
    """Coerce YAML int/float/numeric-string to Decimal; bool/list/other passthrough."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except (InvalidOperation, ValueError):
            return value
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


def _strip_meta(spec: dict[str, Any]) -> dict[str, Any]:
    """Drop ``type`` + ``feature`` keys; passthrough other fields."""
    return {k: v for k, v in spec.items() if k not in ("type", "feature")}


_DECIMAL_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    "equals": frozenset({"value"}),
    "not_equals": frozenset({"value"}),
    "gt": frozenset({"value"}),
    "gte": frozenset({"value"}),
    "lt": frozenset({"value"}),
    "lte": frozenset({"value"}),
    "between": frozenset({"min", "max"}),
    "in": frozenset({"values"}),
}


def _coerce_decimal_fields(spec: dict[str, Any]) -> dict[str, Any]:
    """Pydantic strict-mode-aware per-condition Decimal coercion."""
    cond_type = spec.get("type")
    decimal_fields = _DECIMAL_FIELDS_BY_TYPE.get(cond_type, frozenset())  # type: ignore[arg-type]
    return {k: (_to_decimal(v) if k in decimal_fields else v) for k, v in spec.items()}


def _build_simple(cls: type, spec: dict[str, Any]) -> Any:
    return cls(**_strip_meta(_coerce_decimal_fields(spec)))


# region: simple (T-302) + series (T-303) — thin lambdas in _DISPATCH below ---


def _parse_ema_stack(spec: dict[str, Any], _r: Mapping[tuple[str, str], type[Rule]] | None) -> Any:
    """§B.1 mapping: short/mid/long/direction → features list + direction Literal."""
    direction = spec.get("direction")
    if direction == "from_signal":
        msg = (
            "direction='from_signal' not supported in v1; "
            "specify 'up' or 'down' explicitly. "
            "To get LONG/SHORT-aware behavior, duplicate the rule with "
            "applies_when {signal.action: LONG} + direction: up + paired SHORT version."
        )
        raise ValueError(msg)
    if direction not in ("up", "down"):
        msg = f"ema_stack direction must be 'up' or 'down'; got {direction!r}"
        raise ValueError(msg)
    short = spec.get("short")
    mid = spec.get("mid")
    long_ = spec.get("long")
    if short is None or mid is None or long_ is None:
        msg = f"ema_stack requires 'short', 'mid', 'long' keys; got {sorted(spec)!r}"
        raise ValueError(msg)
    return EmaStackCondition(features=[short, mid, long_], direction=direction)


# region: composite (T-304) — recursive descent ------------------------------


def _parse_and(spec: dict[str, Any], registry: Mapping[tuple[str, str], type[Rule]] | None) -> Any:
    subs = spec.get("conditions", [])
    if not isinstance(subs, list):
        msg = f"and condition: 'conditions' must be a list; got {type(subs).__name__}"
        raise ValueError(msg)
    return AndCondition(conditions=[parse_condition(s, plugin_registry=registry) for s in subs])


def _parse_or(spec: dict[str, Any], registry: Mapping[tuple[str, str], type[Rule]] | None) -> Any:
    subs = spec.get("conditions", [])
    if not isinstance(subs, list):
        msg = f"or condition: 'conditions' must be a list; got {type(subs).__name__}"
        raise ValueError(msg)
    return OrCondition(conditions=[parse_condition(s, plugin_registry=registry) for s in subs])


def _parse_not(spec: dict[str, Any], registry: Mapping[tuple[str, str], type[Rule]] | None) -> Any:
    sub = spec.get("condition")
    if not isinstance(sub, dict):
        msg = f"not condition: 'condition' must be a dict; got {type(sub).__name__}"
        raise ValueError(msg)
    return NotCondition(condition=parse_condition(sub, plugin_registry=registry))


def _parse_when_then_else(
    spec: dict[str, Any], registry: Mapping[tuple[str, str], type[Rule]] | None
) -> Any:
    """Normalize Python-keyword YAML keys ``then``/``else`` → ``then_``/``else_``."""
    when = spec.get("when")
    then_ = spec.get("then_") if "then_" in spec else spec.get("then")
    else_ = spec.get("else_") if "else_" in spec else spec.get("else")
    if not (isinstance(when, dict) and isinstance(then_, dict) and isinstance(else_, dict)):
        msg = "when_then_else requires 'when' + 'then'/'then_' + 'else'/'else_' as dict subs"
        raise ValueError(msg)
    return WhenThenElseCondition(
        when=parse_condition(when, plugin_registry=registry),
        then_=parse_condition(then_, plugin_registry=registry),
        else_=parse_condition(else_, plugin_registry=registry),
    )


# region: plugin (T-305) -----------------------------------------------------


def _parse_plugin(
    spec: dict[str, Any], registry: Mapping[tuple[str, str], type[Rule]] | None
) -> Any:
    if registry is None:
        msg = "plugin condition requires plugin_registry"
        raise ValueError(msg)
    name = spec.get("name")
    version = str(spec.get("version", ""))
    if not name or not version:
        msg = f"plugin condition requires 'name' and 'version'; got {spec!r}"
        raise ValueError(msg)
    key = (str(name), version)
    if key not in registry:
        msg = f"plugin not in registry: name={name!r} version={version!r}"
        raise ValueError(msg)
    rule_class = registry[key]
    params = spec.get("params", {})
    return PluginCondition(name=str(name), version=version, rule=rule_class(params))


_DISPATCH: dict[str, Any] = {
    "equals": lambda s, _r: _build_simple(EqualsCondition, s),
    "not_equals": lambda s, _r: _build_simple(NotEqualsCondition, s),
    "gt": lambda s, _r: _build_simple(GtCondition, s),
    "gte": lambda s, _r: _build_simple(GteCondition, s),
    "lt": lambda s, _r: _build_simple(LtCondition, s),
    "lte": lambda s, _r: _build_simple(LteCondition, s),
    "between": lambda s, _r: _build_simple(BetweenCondition, s),
    "in": lambda s, _r: _build_simple(InCondition, s),
    "rising": lambda s, _r: _build_simple(RisingCondition, s),
    "falling": lambda s, _r: _build_simple(FallingCondition, s),
    "ema_stack": _parse_ema_stack,
    "and": _parse_and,
    "or": _parse_or,
    "not": _parse_not,
    "when_then_else": _parse_when_then_else,
    "plugin": _parse_plugin,
}


# region: feature extraction (DFS pre-order) ---------------------------------


def _extract_feature(spec: dict[str, Any]) -> str | None:
    """DFS pre-order traversal returning first encountered ``feature`` string.

    Pre-order: visit current node's `feature` field first; if absent,
    descend into sub-conditions in declaration order. For ema_stack:
    `short` (positional [0]) is first. For and/or: `conditions[0]` first.
    For not: only child. For when_then_else: when → then(_) → else(_).
    Plugin: returns its own `feature` field if present, else None.
    """
    feat_raw = spec.get("feature")
    if isinstance(feat_raw, str):
        return feat_raw
    cond_type = spec.get("type")
    short = spec.get("short")
    if cond_type == "ema_stack" and isinstance(short, str):
        return short
    if cond_type in ("and", "or"):
        for sub in spec.get("conditions", []) or []:
            if isinstance(sub, dict):
                feat = _extract_feature(sub)
                if feat is not None:
                    return feat
    if cond_type == "not":
        sub = spec.get("condition")
        if isinstance(sub, dict):
            return _extract_feature(sub)
    if cond_type == "when_then_else":
        for key in ("when", "then_", "then", "else_", "else"):
            sub = spec.get(key)
            if isinstance(sub, dict):
                feat = _extract_feature(sub)
                if feat is not None:
                    return feat
    return None


# region: top-level loader ---------------------------------------------------


def _build_rule(
    rule_yaml: dict[str, Any],
    plugin_registry: Mapping[tuple[str, str], type[Rule]] | None,
) -> ScoringRule:
    cond_spec = rule_yaml.get("condition")
    if not isinstance(cond_spec, dict):
        msg = f"rule {rule_yaml.get('name')!r}: 'condition' must be a dict"
        raise ValueError(msg)
    feature = rule_yaml.get("feature") or _extract_feature(cond_spec)
    if feature is None:
        cond_type = cond_spec.get("type")
        if cond_type == "plugin":
            msg = (
                f"plugin rule {rule_yaml.get('name')!r}: feature required for resolver path; "
                "specify feature explicitly at rule or condition level"
            )
        else:
            msg = (
                f"rule {rule_yaml.get('name')!r}: feature required at composite root or "
                "in at least one sub-condition"
            )
        raise ValueError(msg)
    condition = parse_condition(cond_spec, plugin_registry=plugin_registry)
    weight_raw = rule_yaml.get("weight")
    if not isinstance(weight_raw, (int, float)) or isinstance(weight_raw, bool):
        msg = f"rule {rule_yaml.get('name')!r}: weight must be a number; got {weight_raw!r}"
        raise ValueError(msg)
    return ScoringRule.model_construct(
        name=rule_yaml["name"],
        weight=float(weight_raw),
        feature=feature,
        applies_when=rule_yaml.get("applies_when"),
        condition=condition,
        on_error=rule_yaml.get("on_error", "skip"),
        required=rule_yaml.get("required", False),
        max_staleness_sec=rule_yaml.get("max_staleness_sec"),
    )


def load_bot_config(
    path: Path,
    *,
    plugin_registry: Mapping[tuple[str, str], type[Rule]] | None = None,
) -> BotConfig:
    """Parse + validate ``configs/bots/<bot_id>.yaml`` per BRIEF §B.1.

    Returns :class:`BotConfig` with discriminated `ConditionUnion` narrowed
    via recursive ``parse_condition`` descent.
    """
    with path.open() as f:
        data: Any = yaml.safe_load(f)
    if not isinstance(data, dict):
        msg = f"yaml top-level must be a mapping; got {type(data).__name__}"
        raise ValueError(msg)
    scoring_yaml = data.get("scoring", {})
    if not isinstance(scoring_yaml, dict):
        msg = "scoring section must be a mapping"
        raise ValueError(msg)
    rules_yaml = scoring_yaml.get("rules", []) or []
    rules = [_build_rule(r, plugin_registry) for r in rules_yaml if isinstance(r, dict)]
    scoring = ScoringConfig(
        mode=scoring_yaml.get("mode", "active"),
        trigger_threshold=float(scoring_yaml.get("trigger_threshold", 0.0)),
        rules=rules,
    )
    symbols = (data.get("trading", {}) or {}).get("universe", []) or []
    return BotConfig(
        bot_id=data["bot_id"],
        version=data.get("version", 1),  # Pydantic strict-mode rejects non-int
        symbols=list(symbols),
        scoring=scoring,
    )
