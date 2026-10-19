"""Configuration loader: defaults.json, then an optional file, then overrides."""

import json
import os
from typing import Any, Dict, Optional

# Absolute path of the kit root (the parent of the `review/` package).
KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS_PATH = os.path.join(KIT_DIR, "config", "defaults.json")


def _read_json_object(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("config file must contain a JSON object: %s" % path)
    return data


def _check_known(defaults: Dict[str, Any], layer: Dict[str, Any]) -> None:
    for key in layer:
        if key not in defaults:
            raise KeyError(key)


def load_config(
    path: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Return a Config dict: defaults, shallow-merged with `path`, then `overrides`.

    Any key that is not present in defaults.json raises KeyError(key).
    """
    config = _read_json_object(DEFAULTS_PATH)
    if path is not None:
        layer = _read_json_object(path)
        _check_known(config, layer)
        config.update(layer)
    if overrides:
        _check_known(config, overrides)
        config.update(overrides)
    return config
