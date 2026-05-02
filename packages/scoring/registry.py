"""§10.6 plugin registry loader (T-305).

Parses ``plugin_registry.yaml``, resolves each ``entry_point`` via
``importlib.import_module``, and validates each loaded class is a
:class:`packages.scoring.protocol.Rule` subclass with matching
``name`` / ``version`` ClassVar attrs.

YAML schema (BRIEF §10.6:1814-1821 verbatim)::

    rules:
      - name: oi_squeeze
        version: "2"
        entry_point: plugins.rules.oi_squeeze:OISqueezeRule

Returns ``dict[(name, version), type[Rule]]``. Multi-version coexistence
supported (operator may register multiple versions of same plugin).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

import yaml

from .protocol import Rule

if TYPE_CHECKING:
    from pathlib import Path


__all__ = ["load_plugin_registry"]


def _resolve_entry_point(entry_point: str) -> type[Rule]:
    """Parse ``module.path:ClassName`` and return the resolved class.

    Raises:
        ValueError: malformed entry_point (missing or extra ``:``).
        ImportError: module not importable.
        AttributeError: class not in module.
        TypeError: resolved class doesn't inherit Rule.
    """
    if entry_point.count(":") != 1:
        msg = f"invalid entry_point format: {entry_point!r}; expected 'module.path:ClassName'"
        raise ValueError(msg)
    module_path, class_name = entry_point.split(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    if not (isinstance(cls, type) and issubclass(cls, Rule)):
        msg = f"entry_point {entry_point!r} is not a Rule subclass"
        raise TypeError(msg)
    return cls


def load_plugin_registry(path: Path) -> dict[tuple[str, str], type[Rule]]:
    """Load + validate plugin registry from YAML.

    Returns dict keyed on ``(name, version)`` tuple → Rule subclass.
    Empty ``rules`` list returns ``{}``.

    Raises:
        FileNotFoundError: path missing.
        ValueError: YAML structure invalid, duplicate entries, name/version
            ClassVar mismatch with YAML, or malformed entry_point.
        ImportError: entry_point module not importable.
        AttributeError: entry_point class not in module.
        TypeError: entry_point class doesn't inherit Rule.
    """
    with path.open() as f:
        data: Any = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict) or "rules" not in data:
        msg = "plugin_registry.yaml: top-level must be a mapping with 'rules' key"
        raise ValueError(msg)
    rules_list = data["rules"]
    if rules_list is None:
        return {}
    if not isinstance(rules_list, list):
        msg = "plugin_registry.yaml: 'rules' must be a list"
        raise ValueError(msg)

    registry: dict[tuple[str, str], type[Rule]] = {}
    for entry in rules_list:
        if not isinstance(entry, dict):
            msg = f"plugin_registry.yaml: each rule entry must be a mapping; got {entry!r}"
            raise ValueError(msg)
        for required in ("name", "version", "entry_point"):
            if required not in entry:
                msg = f"plugin_registry.yaml: rule entry missing required field {required!r}"
                raise ValueError(msg)
        name = str(entry["name"])
        version = str(entry["version"])
        entry_point = str(entry["entry_point"])

        key = (name, version)
        if key in registry:
            msg = f"duplicate plugin entry: name={name!r} version={version!r}"
            raise ValueError(msg)

        cls = _resolve_entry_point(entry_point)
        if cls.name != name:
            msg = f"plugin {entry_point!r} class.name={cls.name!r} != YAML name={name!r}"
            raise ValueError(msg)
        if cls.version != version:
            msg = (
                f"plugin {entry_point!r} class.version={cls.version!r} != YAML version={version!r}"
            )
            raise ValueError(msg)

        registry[key] = cls
    return registry
