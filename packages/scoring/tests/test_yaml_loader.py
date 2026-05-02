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
from packages.scoring.yaml_loader import load_bot_config, parse_condition

if TYPE_CHECKING:
    from pathlib import Path


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "alpha.yaml"
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


def test_b1_extras_ignored_via_extra_ignore(tmp_path: Path) -> None:
    """§B.1 extras (exchange/signals/execution/display/etc.) parse without error."""
    yaml_text = """\
bot_id: alpha
display_name: "Alpha — RSI div passthrough"
created_at: "2026-04-25T10:00:00+00:00"
status: active
exchange:
  mode: testnet
  account: sub_alpha
  api_key_env: BOT_ALPHA_BYBIT_API_KEY
signals:
  source_filter: ["tv_rsi_divergence_v3"]
  ttl_seconds: 120
trading:
  universe: [BTCUSDT, ETHUSDT]
  primary_interval: 15m
execution:
  leverage: 20
  sl_pct: 0.01
scoring:
  mode: passthrough
  trigger_threshold: 4.0
  rules: []
"""
    cfg = load_bot_config(_write_yaml(tmp_path, yaml_text))
    assert cfg.bot_id == "alpha"
    assert cfg.symbols == ["BTCUSDT", "ETHUSDT"]
    assert cfg.scoring.mode == "passthrough"


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
