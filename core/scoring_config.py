"""
core/scoring_config.py — loads config/scoring.yaml once at startup.

Provides a single load() function that returns the parsed config dict,
caching the result after the first call so the file is read only once
per process.

Consumers (expected.py, sql_judge.py, ci_advisor.py) call load() at
module level and pull the values they need from the returned dict.
"""

from pathlib import Path
import yaml

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "scoring.yaml"
_cache: dict | None = None


def load() -> dict:
    """Return the full scoring config dict.

    The YAML file is read from disk on the first call; subsequent calls
    return the cached result.  Raises FileNotFoundError with a clear
    message if config/scoring.yaml is missing.
    """
    global _cache
    if _cache is None:
        if not _CONFIG_PATH.exists():
            raise FileNotFoundError(
                f"Scoring config not found: {_CONFIG_PATH}\n"
                "Ensure config/scoring.yaml is present in the project directory."
            )
        with _CONFIG_PATH.open(encoding="utf-8") as fh:
            _cache = yaml.safe_load(fh)
    return _cache
