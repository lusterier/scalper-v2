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
from .types import (
    BotConfig,
    ExchangeSection,
    ExecutionSection,
    RiskSection,
    ScoringConfig,
    ScoringRule,
    ShadowConfig,
    ShadowVariant,
    SignalsSection,
    SizingSection,
    SizingTier,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .protocol import Rule


__all__ = ["load_bot_config", "load_bot_config_from_string", "parse_condition"]


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
    return ScoringRule(
        name=rule_yaml["name"],
        weight=float(weight_raw),
        feature=feature,
        applies_when=rule_yaml.get("applies_when"),
        condition=condition,
        on_error=rule_yaml.get("on_error", "skip"),
        required=rule_yaml.get("required", False),
        max_staleness_sec=rule_yaml.get("max_staleness_sec"),
    )


_EXECUTION_DECIMAL_FIELDS: frozenset[str] = frozenset(
    {
        "qty",
        "sl_pct",
        "tp_pct",
        "tp_qty_pct",
        "be_trigger",
        "be_sl_level",
        "trail_pct",
        "fee_rate",
    },
)

# `risk:` block fields needing Decimal coercion (§5.13): T-525a1
# daily_loss_limit_usd (USD money) + T-525b max_drawdown_pct (fraction).
# The 5 T-526/T-524 cooldown/cap knobs stay int (not in the set — no bleed).
_RISK_DECIMAL_FIELDS: frozenset[str] = frozenset({"daily_loss_limit_usd", "max_drawdown_pct"})


def _parse_exchange(spec: dict[str, Any]) -> ExchangeSection:
    """Build ExchangeSection from §B.1 ``exchange:`` block (T-310a)."""
    return ExchangeSection(**spec)


def _parse_signals(spec: dict[str, Any]) -> SignalsSection:
    """Build SignalsSection from §B.1 ``signals:`` block (T-310a).

    Empty-dict input → ``SignalsSection()`` (default ttl=120, source_filter=None)
    so loader can call this unconditionally with ``data.get("signals", {})``
    regardless of YAML presence (per T-310a WG#2).
    """
    return SignalsSection(**spec)


def _parse_execution(spec: dict[str, Any]) -> ExecutionSection:
    """Build ExecutionSection from §B.1 ``execution:`` block + T-310a ``qty``.

    Decimal coercion via shared :func:`_to_decimal` helper for all 8 Decimal
    fields per T-310a WG#4 + §N1 / §5.13.
    """
    coerced = {
        k: (_to_decimal(v) if k in _EXECUTION_DECIMAL_FIELDS else v) for k, v in spec.items()
    }
    return ExecutionSection(**coerced)


def _parse_shadow(spec: dict[str, Any] | None) -> ShadowConfig | None:
    """Parse ``shadow:`` YAML block per BRIEF §13.2; return ``None`` if absent.

    Decimal coercion on per-variant ``overrides`` mirrors :func:`_parse_execution`
    pattern. Pydantic validators on :class:`ShadowConfig` + :class:`ShadowVariant`
    enforce structural rules (unique names + valid override keys + max_duration_hours
    bounds + enabled requires variants). Backward-compat: existing alpha.yaml /
    beta.yaml fixtures have no ``shadow:`` block → returns ``None`` → BotConfig.shadow
    stays None → T-511 worker checks ``if bot.shadow is not None and bot.shadow.enabled``.
    """
    if not spec:
        return None
    raw_variants = spec.get("variants", []) or []
    coerced_variants: list[ShadowVariant] = []
    for v in raw_variants:
        if not isinstance(v, dict):
            continue
        overrides_raw = v.get("overrides", {}) or {}
        overrides: dict[str, Decimal] = {k: _to_decimal(val) for k, val in overrides_raw.items()}
        coerced_variants.append(ShadowVariant(name=v.get("name", ""), overrides=overrides))
    return ShadowConfig(
        enabled=bool(spec.get("enabled", False)),
        variants=coerced_variants,
        max_duration_hours=float(spec.get("max_duration_hours", 4.0)),
    )


def _parse_risk(spec: dict[str, Any] | None) -> RiskSection:
    """Parse ``risk:`` YAML block (T-526 cooldown + T-524 caps + T-525a1 loss-limit).

    Missing block or empty dict → ``RiskSection()`` (all-zero defaults = every
    knob disabled; gates short-circuit before SELECT).
    Pydantic ``extra="forbid"`` on :class:`RiskSection` catches operator typos at
    YAML load (net-new feature; mirror :func:`_parse_shadow` convention).

    T-525a1: ``daily_loss_limit_usd`` is Decimal-coerced via :func:`_to_decimal`
    (mirror :func:`_parse_execution`) so a float-given YAML value (``100.50``)
    becomes ``Decimal("100.50")`` via ``Decimal(str(value))`` — no binary-float
    artefact (§5.13 / §N1). The 5 cooldown/cap knobs are NOT in
    :data:`_RISK_DECIMAL_FIELDS` → stay int (no coercion bleed).
    """
    if not spec:
        return RiskSection()
    coerced = {k: (_to_decimal(v) if k in _RISK_DECIMAL_FIELDS else v) for k, v in spec.items()}
    return RiskSection(**coerced)


def _parse_sizing(spec: dict[str, Any] | None) -> SizingSection | None:
    """Parse ``sizing:`` YAML block per BRIEF §B.1; return ``None`` if absent.

    Absent / empty dict → ``None`` (backward-compat: a bot with no ``sizing:``
    block → ``BotConfig.sizing=None`` → T-527b leaves the static
    ``execution.qty`` path byte-unchanged; T-527a has no consumer). A PRESENT
    block is an explicit operator intent to tier-size and must be well-formed
    (the :class:`SizingSection` / :class:`SizingTier` Pydantic validators
    enforce structure: non-empty + strictly-ascending tiers + ``default`` cap
    key + digit-string multiplier keys; missing required field → loud
    pydantic error, NOT a silent fall-back to static qty).

    **Plan-vs-reality (T-536/T-533a tooling-correction class, L-018)**: the
    T-527a plan said "mirror :func:`_parse_shadow`". `_parse_shadow` reads
    keys explicitly and never ``**spec``-splats, so an unknown ``shadow:``
    key is *silently dropped* (latent gap there). For ``sizing`` that is a
    capital-safety hazard: the operator OQ-2=A-DEFERRED ``tier_promotion`` /
    ``tier_demotion`` (§B.1 alpha.yaml 3146-3149), or any typo, must be
    *loudly rejected* at YAML load — silently ignoring a ``tier_promotion:``
    the operator wrote (believing promotion is configured) is exactly the
    failure WG#2's "extra='forbid' rejects them at YAML load" guards against.
    So this mirrors :func:`_parse_risk`'s ``RiskSection(**coerced)`` splat
    instead (preserve every key → :class:`SizingSection` ``extra="forbid"``
    catches unknowns), adapted for the nested coercion the flat ``risk:``
    block does not need. Decimal coercion via :func:`_to_decimal`
    (``Decimal(str(v))`` — no binary-float artefact, §5.13 / §N1) on every
    tier ``balance_min``/``size`` + each ``score_multipliers`` value + each
    ``max_notional_per_symbol`` value + the T-528a scalar ``risk_pct``
    (REQUIRED — YAML parses ``risk_pct: 0.01`` as a Python ``float``;
    :class:`SizingSection` is non-strict so an un-coerced float would be
    pydantic-coerced via ``Decimal(0.01)`` = the binary-float artefact that
    corrupts the ``total_equity*risk_pct/sl_pct`` capital path); their KEYS
    stay ``str`` (not coerced). ``method`` is an INTENTIONAL non-coerced
    ``str`` passthrough (pydantic validates the ``Literal``). Unknown
    top-level ``sizing:`` keys flow through untouched → rejected.
    """
    if not spec:
        return None
    coerced: dict[str, Any] = dict(spec)  # preserve unknowns → extra="forbid" (OQ-2=A / typos)
    if "tiers" in coerced:
        coerced["tiers"] = [
            SizingTier(
                balance_min=_to_decimal(t.get("balance_min")),
                size=_to_decimal(t.get("size")),
            )
            for t in (coerced["tiers"] or [])
            if isinstance(t, dict)
        ]
    if "score_multipliers" in coerced:
        coerced["score_multipliers"] = {
            k: _to_decimal(v) for k, v in (coerced["score_multipliers"] or {}).items()
        }
    if "max_notional_per_symbol" in coerced:
        coerced["max_notional_per_symbol"] = {
            k: _to_decimal(v) for k, v in (coerced["max_notional_per_symbol"] or {}).items()
        }
    if "risk_pct" in coerced:
        coerced["risk_pct"] = _to_decimal(coerced["risk_pct"])
    return SizingSection(**coerced)


def load_bot_config_from_string(
    yaml_text: str,
    *,
    plugin_registry: Mapping[tuple[str, str], type[Rule]] | None = None,
) -> BotConfig:
    """Parse + validate raw YAML text per BRIEF §B.1.

    Body extracted from :func:`load_bot_config` per T-405 WG#1 — analytics-api
    `/api/configs/validate` + `/api/configs/{bot_id}/apply` endpoints accept
    raw YAML text in JSON request body and need to validate without writing
    to a temp file. Existing path-based callers (T-309 strategy-engine +
    T-310b consumer) continue to use :func:`load_bot_config` which delegates
    here after :meth:`Path.read_text`.

    Returns :class:`BotConfig` with discriminated ``ConditionUnion`` narrowed
    via recursive ``parse_condition`` descent. Raises :class:`ValueError`
    on YAML parse failure or schema violation.
    """
    data: Any = yaml.safe_load(yaml_text)
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
        exchange=_parse_exchange(data.get("exchange", {})),
        signals=_parse_signals(data.get("signals", {})),
        execution=_parse_execution(data.get("execution", {})),
        scoring=scoring,
        shadow=_parse_shadow(data.get("shadow")),
        risk=_parse_risk(data.get("risk")),
        sizing=_parse_sizing(data.get("sizing")),
    )


def load_bot_config(
    path: Path,
    *,
    plugin_registry: Mapping[tuple[str, str], type[Rule]] | None = None,
) -> BotConfig:
    """Parse + validate ``configs/bots/<bot_id>.yaml`` per BRIEF §B.1.

    Public API unchanged for existing callers (T-309 strategy-engine,
    T-310b consumer); delegates to :func:`load_bot_config_from_string`
    after :meth:`Path.read_text` per T-405 WG#1 refactor.
    """
    return load_bot_config_from_string(
        path.read_text(),
        plugin_registry=plugin_registry,
    )
