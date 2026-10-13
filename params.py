"""The tuned numbers behind the signal rules, loaded from a TOML file.

The rules live in code; the numbers that make them work were found by trial
and error and are kept out of the repository. They are read, in order, from:

1. the file named by the ``SETUP_MONITOR_PARAMS`` environment variable,
2. ``params.toml`` in the project root (git-ignored, the tuned values),
3. ``params.example.toml`` (committed, illustrative values only).

Falling back to the example prints a warning, because alerts built on the
example values are a demonstration, not the tuned monitor.
"""
import os
import sys
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_ENV = "SETUP_MONITOR_PARAMS"


def _source():
    named = os.environ.get(_ENV)
    if named:
        return Path(named)
    tuned = _ROOT / "params.toml"
    if tuned.exists():
        return tuned
    print("[params] params.toml not found; using params.example.toml "
          "(illustrative values, not the tuned ones)", file=sys.stderr)
    return _ROOT / "params.example.toml"


SOURCE = _source()
with open(SOURCE, "rb") as fh:
    _P = tomllib.load(fh)


def get(path):
    """A value by dotted path, e.g. ``get("trend.min_fit")``.

    A missing key raises KeyError naming the path and the file, so an
    incomplete params file fails loudly at import rather than quietly
    running on a guess.
    """
    node = _P
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"{path!r} missing from {SOURCE}")
        node = node[part]
    return node
