"""§N5 unit tests for :mod:`packages.scoring.registry` (T-305).

TDD discipline (§N4 spirit per WG#3 T-200 precedent).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from packages.scoring.registry import load_plugin_registry
from packages.scoring.tests.fixtures.foo_plugin import FooRule

if TYPE_CHECKING:
    from pathlib import Path


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "plugin_registry.yaml"
    path.write_text(content)
    return path


def test_load_registry_happy_path(tmp_path: Path) -> None:
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: packages.scoring.tests.fixtures.foo_plugin:FooRule
"""
    path = _write_yaml(tmp_path, yaml_text)
    registry = load_plugin_registry(path)
    assert registry == {("foo", "1"): FooRule}


def test_load_registry_returns_dict_keyed_on_tuple_name_version(tmp_path: Path) -> None:
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: packages.scoring.tests.fixtures.foo_plugin:FooRule
"""
    path = _write_yaml(tmp_path, yaml_text)
    registry = load_plugin_registry(path)
    assert ("foo", "1") in registry


def test_load_registry_empty_rules(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "rules: []\n")
    assert load_plugin_registry(path) == {}


def test_load_registry_missing_rules_key_raises(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "other: value\n")
    with pytest.raises(ValueError, match="must be a mapping with 'rules' key"):
        load_plugin_registry(path)


def test_load_registry_file_missing(tmp_path: Path) -> None:
    path = tmp_path / "nonexistent.yaml"
    with pytest.raises(FileNotFoundError):
        load_plugin_registry(path)


def test_load_registry_malformed_entry_point_no_colon(tmp_path: Path) -> None:
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: no_colon_here
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValueError, match="invalid entry_point format"):
        load_plugin_registry(path)


def test_load_registry_malformed_entry_point_two_colons(tmp_path: Path) -> None:
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: a:b:c
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValueError, match="invalid entry_point format"):
        load_plugin_registry(path)


def test_load_registry_module_not_importable(tmp_path: Path) -> None:
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: nonexistent.module:Class
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ImportError):
        load_plugin_registry(path)


def test_load_registry_class_not_in_module(tmp_path: Path) -> None:
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: packages.scoring.tests.fixtures.foo_plugin:NoSuchClass
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(AttributeError):
        load_plugin_registry(path)


def test_load_registry_class_not_rule_subclass(tmp_path: Path) -> None:
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: packages.scoring.tests.fixtures.foo_plugin:NotARule
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(TypeError, match="not a Rule subclass"):
        load_plugin_registry(path)


def test_load_registry_duplicate_name_version_raises(tmp_path: Path) -> None:
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: packages.scoring.tests.fixtures.foo_plugin:FooRule
  - name: foo
    version: "1"
    entry_point: packages.scoring.tests.fixtures.foo_plugin:FooRule
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValueError, match="duplicate plugin entry"):
        load_plugin_registry(path)


def test_load_registry_name_classvar_mismatch_raises(tmp_path: Path) -> None:
    """WG#4: class.name='actually_bar' but YAML name='foo' → ValueError mentioning both."""
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: packages.scoring.tests.fixtures.foo_plugin:WrongNameRule
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValueError, match=r"class.name='actually_bar'.*YAML name='foo'"):
        load_plugin_registry(path)


def test_load_registry_version_classvar_mismatch_raises(tmp_path: Path) -> None:
    """WG#4: class.version='999' but YAML version='1' → ValueError mentioning both."""
    yaml_text = """\
rules:
  - name: foo
    version: "1"
    entry_point: packages.scoring.tests.fixtures.foo_plugin:WrongVersionRule
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValueError, match=r"class.version='999'.*YAML version='1'"):
        load_plugin_registry(path)


def test_load_registry_missing_required_field(tmp_path: Path) -> None:
    yaml_text = """\
rules:
  - name: foo
    version: "1"
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(ValueError, match="missing required field 'entry_point'"):
        load_plugin_registry(path)
