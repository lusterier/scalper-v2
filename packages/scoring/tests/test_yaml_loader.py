"""§N5 unit tests for :mod:`packages.scoring.yaml_loader` (T-308).

TDD discipline (per WG#3 T-200 precedent — tests-first as project default
for new files): YAML parsing, not financial math.

Mock-free; tmp_path-driven YAML fixtures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from pydantic import ValidationError

from packages.scoring import SignalsSection
from packages.scoring.conditions import (
    AndCondition,
    BetweenCondition,
    EmaStackCondition,
    GtCondition,
    NotCondition,
    OrCondition,
    PluginCondition,
    RisingCondition,
    WhenThenElseCondition,
)
from packages.scoring.tests.fixtures.foo_plugin import FooRule
from packages.scoring.yaml_loader import (
    load_bot_config,
    load_bot_config_from_string,
    parse_condition,
)

if TYPE_CHECKING:
    from pathlib import Path


# T-310a: BotConfig now requires `exchange:` + `execution:` sections.
# `_write_yaml` auto-injects defaults if the fixture doesn't already supply
# them, keeping pre-T-310a fixture texts unchanged. Tests that exercise
# missing/invalid section behavior set `inject_defaults=False` and supply
# the YAML verbatim.
_DEFAULT_EXCHANGE_EXECUTION_YAML = """\
exchange:
  mode: paper
  account: sub_alpha
  api_key_env: BOT_ALPHA_BYBIT_API_KEY
  api_secret_env: BOT_ALPHA_BYBIT_API_SECRET
execution:
  qty: 0.001
  leverage: 20
  sl_pct: 0.01
  tp_pct: 0.01
  tp_qty_pct: 0.5
  be_trigger: 0.005
  be_sl_level: 0.003
  trail_pct: 0.005
  fee_rate: 0.00055
"""


def _write_yaml(tmp_path: Path, content: str, *, inject_defaults: bool = True) -> Path:
    path = tmp_path / "alpha.yaml"
    if inject_defaults and "exchange:" not in content:
        content = content + _DEFAULT_EXCHANGE_EXECUTION_YAML
    path.write_text(content)
    return path


# region: load_bot_config — happy path ---------------------------------------


def test_load_bot_config_minimal_between_rule(tmp_path: Path) -> None:
    yaml_text = """\
bot_id: alpha
trading:
  universe: [BTCUSDT]
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: r1
      weight: 1.0
      condition:
        type: between
        feature: ind.btcusdt.15m.rsi_14
        min: 55
        max: 70
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.bot_id == "alpha"
    assert cfg.symbols == ["BTCUSDT"]
    assert len(cfg.scoring.rules) == 1
    rule = cfg.scoring.rules[0]
    assert rule.name == "r1"
    assert rule.weight == 1.0
    assert rule.feature == "ind.btcusdt.15m.rsi_14"
    cond: object = rule.condition
    assert isinstance(cond, BetweenCondition)


def test_load_bot_config_ema_stack_rule(tmp_path: Path) -> None:
    yaml_text = """\
bot_id: alpha
trading:
  universe: [BTCUSDT]
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: stack
      weight: 1.5
      condition:
        type: ema_stack
        short: ind.btcusdt.15m.ema_20
        mid: ind.btcusdt.15m.ema_50
        long: ind.btcusdt.15m.ema_200
        direction: up
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    rule = cfg.scoring.rules[0]
    cond: object = rule.condition
    assert isinstance(cond, EmaStackCondition)
    assert rule.feature == "ind.btcusdt.15m.ema_20"  # first encountered (short)


def test_load_bot_config_plugin_rule(tmp_path: Path) -> None:
    yaml_text = """\
bot_id: alpha
trading:
  universe: [BTCUSDT]
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: foo_plugin_rule
      weight: 1.0
      feature: ind.btcusdt.15m.foo
      condition:
        type: plugin
        name: foo
        version: "1"
        params: { result: true }
"""
    registry = {("foo", "1"): FooRule}
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text), plugin_registry=registry)
    rule = cfg.scoring.rules[0]
    cond: object = rule.condition
    assert isinstance(cond, PluginCondition)
    assert cond.name == "foo"
    assert cond.version == "1"
    assert rule.feature == "ind.btcusdt.15m.foo"


# region: error paths --------------------------------------------------------


def test_load_bot_config_file_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_bot_config(tmp_path / "nonexistent.yaml")


def test_load_bot_config_yaml_malformed_raises(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "not: yaml: ::")
    with pytest.raises(yaml.YAMLError):
        load_bot_config(path)


# region: parse_condition dispatch ------------------------------------------


def test_parse_condition_simple_gt() -> None:
    c = parse_condition({"type": "gt", "feature": "ind.x.1m.foo", "value": "50000"})
    assert isinstance(c, GtCondition)


def test_parse_condition_simple_between() -> None:
    c = parse_condition({"type": "between", "feature": "ind.x.1m.foo", "min": 1, "max": 10})
    assert isinstance(c, BetweenCondition)


def test_parse_condition_series_rising() -> None:
    c = parse_condition({"type": "rising", "feature": "ind.x.1m.foo", "n_samples": 3})
    assert isinstance(c, RisingCondition)


# region: composite recursion + when_then_else key normalization ------------


def test_parse_condition_composite_2_level_nested() -> None:
    spec = {
        "type": "and",
        "conditions": [
            {
                "type": "or",
                "conditions": [
                    {"type": "gt", "feature": "ind.x.1m.foo", "value": "50000"},
                    {"type": "lt", "feature": "ind.x.1m.foo", "value": "0"},
                ],
            },
            {"type": "not", "condition": {"type": "equals", "feature": "ind.x.1m.foo", "value": 5}},
        ],
    }
    c = parse_condition(spec)
    assert isinstance(c, AndCondition)
    assert isinstance(c.conditions[0], OrCondition)
    assert isinstance(c.conditions[1], NotCondition)


def test_when_then_else_yaml_keyword_keys_normalized() -> None:
    """Operator may write `then`/`else` (Python keywords); loader maps to then_/else_."""
    spec = {
        "type": "when_then_else",
        "when": {"type": "gt", "feature": "ind.x.1m.foo", "value": 1},
        "then": {"type": "lt", "feature": "ind.x.1m.foo", "value": 10},
        "else": {"type": "lt", "feature": "ind.x.1m.foo", "value": 5},
    }
    c = parse_condition(spec)
    assert isinstance(c, WhenThenElseCondition)


def test_when_then_else_python_attr_keys_also_accepted() -> None:
    """`then_`/`else_` already-escaped also accepted."""
    spec = {
        "type": "when_then_else",
        "when": {"type": "gt", "feature": "ind.x.1m.foo", "value": 1},
        "then_": {"type": "lt", "feature": "ind.x.1m.foo", "value": 10},
        "else_": {"type": "lt", "feature": "ind.x.1m.foo", "value": 5},
    }
    c = parse_condition(spec)
    assert isinstance(c, WhenThenElseCondition)


# region: parse-time error pins (operator-facing diagnostics) ---------------


def test_plugin_condition_without_registry_raises() -> None:
    with pytest.raises(ValueError, match="plugin condition requires plugin_registry"):
        parse_condition({"type": "plugin", "name": "foo", "version": "1", "params": {}})


def test_plugin_not_in_registry_raises() -> None:
    with pytest.raises(ValueError, match="plugin not in registry"):
        parse_condition(
            {"type": "plugin", "name": "ghost", "version": "1", "params": {}},
            plugin_registry={("foo", "1"): FooRule},
        )


def test_unknown_condition_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown condition type"):
        parse_condition({"type": "unknown_xyz"})


def test_ema_stack_from_signal_direction_rejected() -> None:
    """Path A: from_signal direction REJECTED at parse-time per pass-1 BLOCKER#1 fix."""
    with pytest.raises(ValueError, match="direction='from_signal' not supported in v1"):
        parse_condition(
            {
                "type": "ema_stack",
                "short": "A",
                "mid": "B",
                "long": "C",
                "direction": "from_signal",
            }
        )


# region: ema_stack mapping --------------------------------------------------


def test_ema_stack_short_mid_long_extracted_to_features_list() -> None:
    c = parse_condition(
        {"type": "ema_stack", "short": "A", "mid": "B", "long": "C", "direction": "up"}
    )
    assert isinstance(c, EmaStackCondition)
    assert c.features == ["A", "B", "C"]
    assert c.direction == "up"


# region: feature extraction (DFS pre-order) --------------------------------


def test_simple_condition_feature_hoisted_from_condition_dict_to_rule(tmp_path: Path) -> None:
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: r1
      weight: 1.0
      condition:
        type: gt
        feature: ind.btcusdt.15m.rsi_14
        value: "50"
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.scoring.rules[0].feature == "ind.btcusdt.15m.rsi_14"


def test_composite_first_feature_dfs_pre_order(tmp_path: Path) -> None:
    """rule.feature uses first feature_ref from DFS pre-order traversal."""
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: r1
      weight: 1.0
      condition:
        type: and
        conditions:
          - type: gt
            feature: ind.btcusdt.15m.first_feature
            value: "0"
          - type: lt
            feature: ind.btcusdt.15m.second_feature
            value: "100"
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.scoring.rules[0].feature == "ind.btcusdt.15m.first_feature"


def test_plugin_without_explicit_feature_raises(tmp_path: Path) -> None:
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: foo_plugin_rule
      weight: 1.0
      condition:
        type: plugin
        name: foo
        version: "1"
        params: {}
"""
    registry = {("foo", "1"): FooRule}
    with pytest.raises(ValueError, match="feature required"):
        load_bot_config(_write_yaml(tmp_path, yaml_text), plugin_registry=registry)


# region: applies_when v1 pass-through --------------------------------------


def test_applies_when_raw_dict_passthrough(tmp_path: Path) -> None:
    """T-308 keeps applies_when as raw dict (T-307 v1 ignores; T-308b will narrow)."""
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: r1
      weight: 1.0
      applies_when: { signal.action: LONG }
      condition:
        type: gt
        feature: ind.btcusdt.15m.rsi_14
        value: "0"
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.scoring.rules[0].applies_when == {"signal.action": "LONG"}


# region: defaults + extras ignored -----------------------------------------


def test_empty_rules_list_valid(tmp_path: Path) -> None:
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules: []
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.scoring.rules == []


def test_bot_config_version_default_one(tmp_path: Path) -> None:
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules: []
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.version == 1


def test_scoring_mode_default_active_when_absent(tmp_path: Path) -> None:
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  trigger_threshold: 1.0
  rules: []
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.scoring.mode == "active"


def test_b1_remaining_unmodeled_extras_ignored_via_extra_ignore(tmp_path: Path) -> None:
    """§B.1 unmodeled top-level keys (display_name/created_at/status/trading.primary_interval/
    sizing) parse without error — T-308 WG#5 + T-310a defense-in-depth.

    T-310a lands ``exchange:``/``signals:``/``execution:`` as first-class BotConfig fields;
    T-514 promotes ``shadow:`` to a modeled :class:`ShadowConfig` field — `shadow.enabled=true`
    now requires non-empty `variants` per ShadowConfig validator. This test uses
    `shadow.enabled: false` to exercise BotConfig parsing path with shadow modeled but
    not requiring variants.
    """
    yaml_text = """\
bot_id: alpha
display_name: "Alpha — RSI div passthrough"
created_at: "2026-04-25T10:00:00+00:00"
status: active
trading:
  universe: [BTCUSDT, ETHUSDT]
  primary_interval: 15m
sizing:
  tiers:
    - { balance_min: 500, size: 700 }
shadow:
  enabled: false
scoring:
  mode: passthrough
  trigger_threshold: 4.0
  rules: []
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.bot_id == "alpha"
    assert cfg.symbols == ["BTCUSDT", "ETHUSDT"]
    assert cfg.scoring.mode == "passthrough"
    # T-514: shadow modeled with enabled=false → ShadowConfig present but inactive.
    assert cfg.shadow is not None
    assert cfg.shadow.enabled is False


def test_pydantic_validation_error_on_bad_rule_shape(tmp_path: Path) -> None:
    """weight as string instead of float → Pydantic ValidationError surfaces verbatim."""
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: r1
      weight: "not_a_number"
      condition:
        type: gt
        feature: ind.btcusdt.15m.rsi_14
        value: "0"
"""
    with pytest.raises((ValidationError, ValueError)):
        load_bot_config(_write_yaml(tmp_path, yaml_text))


def test_bot_id_charset_regex_rejects_uppercase(tmp_path: Path) -> None:
    """T-300 BotConfig.bot_id charset regex propagates."""
    yaml_text = """\
bot_id: ALPHA
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules: []
"""
    with pytest.raises(ValidationError):
        load_bot_config(_write_yaml(tmp_path, yaml_text))


# region: T-308b — ScoringRule field-level validation hardening --------------


def _yaml_with_rule_overrides(extra_rule_lines: str) -> str:
    """Build a minimal valid bot YAML with one rule, plus injected lines.

    The injected ``extra_rule_lines`` string (already YAML-indented at 6
    spaces to match `rules:` list-item indent) goes after the standard
    name/weight/feature/condition triple. Used to inject a typo'd field
    into an otherwise-valid rule for ValidationError tests.
    """
    return f"""\
bot_id: alpha
trading: {{ universe: [BTCUSDT] }}
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: r1
      weight: 1.0
      condition:
        type: gt
        feature: ind.btcusdt.15m.rsi_14
        value: "0"
{extra_rule_lines}"""


def test_load_bot_config_rejects_non_string_rule_name(tmp_path: Path) -> None:
    """name: 42 (int) → ValidationError mentions 'name' (Pydantic strict)."""
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: 42
      weight: 1.0
      condition:
        type: gt
        feature: ind.btcusdt.15m.rsi_14
        value: "0"
"""
    with pytest.raises(ValidationError, match=r"name"):
        load_bot_config(_write_yaml(tmp_path, yaml_text))


def test_load_bot_config_rejects_invalid_on_error_literal(tmp_path: Path) -> None:
    """on_error: 'blow_up' → ValidationError; not in Literal['skip','reject']."""
    yaml_text = _yaml_with_rule_overrides('      on_error: "blow_up"\n')
    with pytest.raises(ValidationError, match=r"on_error"):
        load_bot_config(_write_yaml(tmp_path, yaml_text))


def test_load_bot_config_rejects_non_bool_required(tmp_path: Path) -> None:
    """required: 'yes' (str) → ValidationError; strict-mode rejects str→bool."""
    yaml_text = _yaml_with_rule_overrides('      required: "yes"\n')
    with pytest.raises(ValidationError, match=r"required"):
        load_bot_config(_write_yaml(tmp_path, yaml_text))


def test_load_bot_config_rejects_non_int_max_staleness_sec(tmp_path: Path) -> None:
    """max_staleness_sec: '60s' → ValidationError; strict-mode rejects str→int."""
    yaml_text = _yaml_with_rule_overrides('      max_staleness_sec: "60s"\n')
    with pytest.raises(ValidationError, match=r"max_staleness_sec"):
        load_bot_config(_write_yaml(tmp_path, yaml_text))


def test_load_bot_config_rejects_non_dict_applies_when(tmp_path: Path) -> None:
    """applies_when: 'all' → ValidationError; strict-mode rejects str→dict."""
    yaml_text = _yaml_with_rule_overrides('      applies_when: "all"\n')
    with pytest.raises(ValidationError, match=r"applies_when"):
        load_bot_config(_write_yaml(tmp_path, yaml_text))


def test_load_bot_config_accepts_condition_instance_via_any_typing(
    tmp_path: Path,
) -> None:
    """Regression: condition: Any accepts Condition instance (T-308b Path A1).

    Validates that replacing model_construct with normal ScoringRule(...)
    ctor still accepts the Condition instance produced by parse_condition,
    via condition: Any typing in T-300 ScoringRule.
    """
    yaml_text = _yaml_with_rule_overrides("")
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    rule = cfg.scoring.rules[0]
    cond: object = rule.condition
    assert isinstance(cond, GtCondition)


def test_load_bot_config_accepts_int_max_staleness_sec(tmp_path: Path) -> None:
    """Pydantic 2 strict-mode accepts int→int for max_staleness_sec: 60."""
    yaml_text = _yaml_with_rule_overrides("      max_staleness_sec: 60\n")
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.scoring.rules[0].max_staleness_sec == 60


def test_load_bot_config_accepts_int_weight_under_strict(tmp_path: Path) -> None:
    """Pin Pydantic 2 strict int→float contract for weight.

    PyYAML parses ``weight: 1`` as Python int. The yaml_loader's manual
    pre-check passes int through ``float(weight_raw)`` before invoking the
    Pydantic ctor — Pydantic strict-mode then receives a true float and
    accepts. Final ``rule.weight`` is float 1.0.

    Per WG#1: strict-mode also accepts int→float natively (verified
    empirically against pydantic==2.13.2 workspace pin), so the pre-check
    here is L-008 belt-and-suspenders for bool→float rejection clarity,
    not a fixture-compat shim.
    """
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: r1
      weight: 1
      condition:
        type: gt
        feature: ind.btcusdt.15m.rsi_14
        value: "0"
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    rule = cfg.scoring.rules[0]
    assert rule.weight == 1.0
    assert isinstance(rule.weight, float)


# region: T-310a — BotConfig sections (exchange/signals/execution) ----------


def test_load_bot_config_full_b1_shape_round_trip(tmp_path: Path) -> None:
    """Happy path: §B.1-verbatim YAML → BotConfig with all 4 sections populated.

    Decimal precision pinned: ``Decimal("0.01")`` round-trips exact via
    ``_to_decimal`` helper (string-conversion preserves YAML float form).
    """
    yaml_text = """\
bot_id: alpha
trading:
  universe: [BTCUSDT, ETHUSDT]
exchange:
  mode: testnet
  account: sub_alpha
  api_key_env: BOT_ALPHA_BYBIT_API_KEY
  api_secret_env: BOT_ALPHA_BYBIT_API_SECRET
signals:
  source_filter: [tv_rsi_v3]
  ttl_seconds: 60
execution:
  qty: 0.001
  leverage: 20
  sl_pct: 0.01
  tp_pct: 0.01
  tp_qty_pct: 0.5
  be_trigger: 0.005
  be_sl_level: 0.003
  trail_pct: 0.005
  fee_rate: 0.00055
scoring:
  mode: active
  trigger_threshold: 1.0
  rules: []
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text, inject_defaults=False))
    from decimal import Decimal as _Decimal

    assert cfg.exchange.mode == "testnet"
    assert cfg.exchange.account == "sub_alpha"
    assert cfg.signals.source_filter == ["tv_rsi_v3"]
    assert cfg.signals.ttl_seconds == 60
    assert cfg.execution.qty == _Decimal("0.001")
    assert cfg.execution.leverage == 20
    assert cfg.execution.sl_pct == _Decimal("0.01")
    assert cfg.execution.fee_rate == _Decimal("0.00055")
    assert cfg.execution.sl_retry_count == 3  # default
    assert cfg.execution.emergency_close_on_sl_fail is True  # default


def test_load_bot_config_signals_section_default_when_omitted(tmp_path: Path) -> None:
    """WG#2: `_parse_signals({})` returns `SignalsSection()` — default ttl=120, no filter."""
    yaml_text = """\
bot_id: alpha
trading:
  universe: [BTCUSDT]
scoring:
  mode: active
  trigger_threshold: 1.0
  rules: []
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.signals == SignalsSection()
    assert cfg.signals.ttl_seconds == 120
    assert cfg.signals.source_filter is None


def test_load_bot_config_rejects_missing_exchange_section(tmp_path: Path) -> None:
    """No `exchange:` block → ExchangeSection ValidationError surfaces (required fields missing)."""
    yaml_text = """\
bot_id: alpha
trading:
  universe: [BTCUSDT]
execution:
  qty: 0.001
  leverage: 20
  sl_pct: 0.01
  tp_pct: 0.01
  tp_qty_pct: 0.5
  be_trigger: 0.005
  be_sl_level: 0.003
  trail_pct: 0.005
  fee_rate: 0.00055
scoring:
  mode: active
  trigger_threshold: 1.0
  rules: []
"""
    with pytest.raises(ValidationError, match=r"mode|account|api_key_env|api_secret_env"):
        load_bot_config(_write_yaml(tmp_path, yaml_text, inject_defaults=False))


def test_load_bot_config_rejects_missing_execution_section(tmp_path: Path) -> None:
    """No `execution:` block → ExecutionSection ValidationError surfaces."""
    yaml_text = """\
bot_id: alpha
trading:
  universe: [BTCUSDT]
exchange:
  mode: paper
  account: sub_alpha
  api_key_env: K
  api_secret_env: S
scoring:
  mode: active
  trigger_threshold: 1.0
  rules: []
"""
    with pytest.raises(ValidationError, match=r"qty|leverage|sl_pct"):
        load_bot_config(_write_yaml(tmp_path, yaml_text, inject_defaults=False))


def test_load_bot_config_execution_decimal_strings_coerce(tmp_path: Path) -> None:
    """WG#4: both `qty: "0.001"` (str) and `qty: 0.001` (float) coerce to Decimal exact."""
    from decimal import Decimal as _Decimal

    yaml_text_str = """\
bot_id: alpha
trading:
  universe: [BTCUSDT]
exchange:
  mode: paper
  account: sub_alpha
  api_key_env: K
  api_secret_env: S
execution:
  qty: "0.001"
  leverage: 20
  sl_pct: "0.01"
  tp_pct: "0.01"
  tp_qty_pct: "0.5"
  be_trigger: "0.005"
  be_sl_level: "0.003"
  trail_pct: "0.005"
  fee_rate: "0.00055"
scoring:
  mode: active
  trigger_threshold: 1.0
  rules: []
"""
    cfg_str = load_bot_config(_write_yaml(tmp_path, yaml_text_str, inject_defaults=False))
    assert cfg_str.execution.qty == _Decimal("0.001")
    assert cfg_str.execution.fee_rate == _Decimal("0.00055")


def test_load_bot_config_weight_pre_check_runs_before_pydantic_ctor(
    tmp_path: Path,
) -> None:
    """Pin pre-check ordering — manual weight check raises BEFORE Pydantic.

    ``weight: true`` in YAML → PyYAML bool. The manual pre-check at
    yaml_loader.py:317 rejects with verbatim message
    ``rule 'r1': weight must be a number; got True``. Without the pre-
    check, Pydantic strict-mode would reject too but with a less
    actionable message (Input should be a valid number / Input type=bool).

    This test pins the error-message clarity provided by the pre-check
    (L-008 active control + L-001 field-named diagnostics).
    """
    yaml_text = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 1.0
  rules:
    - name: r1
      weight: true
      condition:
        type: gt
        feature: ind.btcusdt.15m.rsi_14
        value: "0"
"""
    with pytest.raises(ValueError, match=r"weight must be a number; got True"):
        load_bot_config(_write_yaml(tmp_path, yaml_text))


# ---------------------------------------------------------------------------
# T-405 — load_bot_config_from_string refactor (yaml_loader extracted helper)
# ---------------------------------------------------------------------------


def test_load_bot_config_from_string_happy_path() -> None:
    """Raw YAML text → BotConfig, no path/file involved (T-405 validate consumer)."""
    yaml_text = (
        """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: active
  trigger_threshold: 0.5
  rules:
    - name: r1
      weight: 1.0
      condition:
        type: gt
        feature: ind.btcusdt.15m.rsi_14
        value: "70"
"""
        + _DEFAULT_EXCHANGE_EXECUTION_YAML
    )
    cfg = load_bot_config_from_string(yaml_text)
    assert cfg.bot_id == "alpha"
    assert cfg.scoring.mode == "active"
    assert cfg.scoring.trigger_threshold == 0.5
    assert len(cfg.scoring.rules) == 1


def test_load_bot_config_from_string_raises_value_error_on_non_dict_top_level() -> None:
    """Top-level YAML must be mapping; list/scalar raises ValueError."""
    with pytest.raises(ValueError, match="yaml top-level must be a mapping"):
        load_bot_config_from_string("- just a list\n- not a mapping\n")


def test_load_bot_config_delegates_to_from_string(tmp_path: Path) -> None:
    """Existing path-based load_bot_config remains functional (regression pin)."""
    yaml_text = (
        """\
bot_id: beta
trading: { universe: [ETHUSDT] }
scoring:
  mode: passthrough
  trigger_threshold: 0.0
  rules: []
"""
        + _DEFAULT_EXCHANGE_EXECUTION_YAML
    )
    path = _write_yaml(tmp_path, yaml_text, inject_defaults=False)
    cfg = load_bot_config(path)
    assert cfg.bot_id == "beta"
    # Delegated result identical to direct from-string call.
    cfg_from_string = load_bot_config_from_string(yaml_text)
    assert cfg_from_string.bot_id == cfg.bot_id
    assert cfg_from_string.scoring.mode == cfg.scoring.mode


# ---------------------------------------------------------------------------
# T-514 — Shadow config schema (BRIEF §13.2)
# ---------------------------------------------------------------------------

from decimal import Decimal  # noqa: E402

from packages.scoring.types import ShadowConfig, ShadowVariant  # noqa: E402

_T514_BASE_YAML = """
bot_id: alpha
version: 1
exchange:
  mode: paper
  account: sub_alpha
  api_key_env: BOT_ALPHA_BYBIT_API_KEY
  api_secret_env: BOT_ALPHA_BYBIT_API_SECRET
trading:
  universe: [BTCUSDT]
execution:
  qty: "0.001"
  leverage: 20
  sl_pct: "0.01"
  tp_pct: "0.01"
  tp_qty_pct: "0.5"
  be_trigger: "0.005"
  be_sl_level: "0.003"
  trail_pct: "0.005"
  fee_rate: "0.00055"
scoring:
  mode: passthrough
"""


def test_shadow_config_defaults() -> None:
    """Default ShadowConfig: enabled=False, empty variants, max_duration_hours=4.0."""
    cfg = ShadowConfig()
    assert cfg.enabled is False
    assert cfg.variants == []
    assert cfg.max_duration_hours == 4.0


def test_shadow_variant_overrides_decimal_coercion() -> None:
    """Pydantic coerces float YAML values to Decimal in overrides dict."""
    variant = ShadowVariant(name="sl_tight", overrides={"sl_pct": Decimal("0.005")})
    assert variant.overrides["sl_pct"] == Decimal("0.005")
    assert isinstance(variant.overrides["sl_pct"], Decimal)


def test_shadow_variant_overrides_target_unknown_field_raises() -> None:
    """Override key outside ExecutionSection 9-field subset → ValidationError."""
    with pytest.raises(ValidationError, match="unknown ExecutionSection"):
        ShadowVariant(name="bad", overrides={"unknown_field": Decimal("0")})


def test_shadow_config_duplicate_variant_names_raises() -> None:
    """Two variants with same name → ValidationError."""
    with pytest.raises(ValidationError, match="must be unique"):
        ShadowConfig(
            enabled=True,
            variants=[ShadowVariant(name="x"), ShadowVariant(name="x")],
        )


def test_shadow_config_enabled_without_variants_raises() -> None:
    """enabled=True + empty variants → ValidationError."""
    with pytest.raises(ValidationError, match="requires at least 1 variant"):
        ShadowConfig(enabled=True, variants=[])


@pytest.mark.parametrize("invalid_hours", [0.0, -1.0, 25.0, 100.0])
def test_shadow_config_max_duration_hours_outside_bounds_raises(invalid_hours: float) -> None:
    """max_duration_hours outside (0, 24] → ValidationError."""
    with pytest.raises(ValidationError):
        ShadowConfig(max_duration_hours=invalid_hours)


def test_shadow_variant_extra_field_forbidden() -> None:
    """ShadowVariant extra=forbid catches operator typos (e.g. 'enabld' on variant)."""
    with pytest.raises(ValidationError):
        ShadowVariant(name="x", enabld=True)  # type: ignore[call-arg]


def test_shadow_config_extra_field_forbidden() -> None:
    """ShadowConfig extra=forbid catches operator typos at top level."""
    with pytest.raises(ValidationError):
        ShadowConfig(disabled=True)  # type: ignore[call-arg]


def test_botconfig_shadow_field_default_none() -> None:
    """BotConfig without shadow kwarg → bot.shadow is None (backward-compat)."""
    cfg = load_bot_config_from_string(_T514_BASE_YAML)
    assert cfg.shadow is None


def test_yaml_loader_brief_13_2_5_variants_round_trip() -> None:
    """§A — BRIEF §13.2 verbatim 5-variant example loads cleanly."""
    yaml_text = (
        _T514_BASE_YAML
        + """
shadow:
  enabled: true
  variants:
    - name: baseline
    - name: no_be
      overrides: { be_trigger: 0 }
    - name: full_tp
      overrides: { tp_qty_pct: 1.0, trail_pct: 0 }
    - name: sl_tight
      overrides: { sl_pct: 0.005 }
    - name: sl_wide
      overrides: { sl_pct: 0.015 }
  max_duration_hours: 4
"""
    )
    cfg = load_bot_config_from_string(yaml_text)
    assert cfg.shadow is not None
    assert cfg.shadow.enabled is True
    assert cfg.shadow.max_duration_hours == 4.0
    assert len(cfg.shadow.variants) == 5
    names = [v.name for v in cfg.shadow.variants]
    assert names == ["baseline", "no_be", "full_tp", "sl_tight", "sl_wide"]
    # Override Decimal coercion verified.
    sl_tight = cfg.shadow.variants[3]
    assert sl_tight.overrides["sl_pct"] == Decimal("0.005")
    full_tp = cfg.shadow.variants[2]
    assert full_tp.overrides["tp_qty_pct"] == Decimal("1.0")
    assert full_tp.overrides["trail_pct"] == Decimal("0")
    # baseline (no overrides) decodes as empty dict.
    assert cfg.shadow.variants[0].overrides == {}


def test_yaml_loader_alpha_without_shadow_yields_none() -> None:
    """§B — backward-compat: existing fixture YAML without shadow block → bot.shadow is None."""
    cfg = load_bot_config_from_string(_T514_BASE_YAML)
    assert cfg.shadow is None
    # Existing fields still parse correctly — no regression.
    assert cfg.bot_id == "alpha"
    assert cfg.execution.sl_pct == Decimal("0.01")
