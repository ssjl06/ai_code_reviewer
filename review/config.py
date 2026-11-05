"""Configuration loader: defaults.json, then an optional file, then overrides."""

import json
import os
import sys
from typing import Any, Dict, Optional

# Absolute path of the kit root (the parent of the `review/` package).
KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS_PATH = os.path.join(KIT_DIR, "config", "defaults.json")
# Turns past the explore budget: the denied overrun calls plus the answer itself.
TURN_MARGIN = 4


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


# Keys removed from defaults.json: still accepted in an old config file or override, ignored with a warning.
_RETIRED_KEYS = {"deny_limit": "deny_repeat_limit / deny_total_limit"}


def _drop_retired(layer: Dict[str, Any]) -> Dict[str, Any]:
    kept = {}
    for key, value in layer.items():
        if key in _RETIRED_KEYS:
            print("ai-review: warning: config key %s is no longer used and is ignored; use %s"
                  % (key, _RETIRED_KEYS[key]), file=sys.stderr, flush=True)
        else:
            kept[key] = value
    return kept


def load_config(
    path: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Return a Config dict: defaults, shallow-merged with `path`, then `overrides`.

    Any key that is not present in defaults.json raises KeyError(key).
    """
    config = _read_json_object(DEFAULTS_PATH)
    if path is not None:
        layer = _drop_retired(_read_json_object(path))
        _check_known(config, layer)
        config.update(layer)
    if overrides:
        overrides = _drop_retired(overrides)
        _check_known(config, overrides)
        config.update(overrides)
    return _fit_turns(config)


def _fit_turns(config: Dict[str, Any]) -> Dict[str, Any]:
    """Raise max_turns to explore_budget + TURN_MARGIN (with a warning) so the answer fits."""
    need = config["explore_budget"] + TURN_MARGIN
    if config["max_turns"] < need:
        print("ai-review: warning: max_turns %s < explore_budget + %d; using %d"
              % (config["max_turns"], TURN_MARGIN, need), file=sys.stderr, flush=True)
        config["max_turns"] = need
    return config
