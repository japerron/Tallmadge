"""
core/profile.py — Optional YAML run profile for unattended execution.

Load a profile with:
    from core import profile
    profile.load("profiles/my_profile.yaml")

Then inject profile values as defaults ahead of last_run:
    default = profile.get("mstr.base_url") or last_run.get("base_url", "")
    val = cli.text_input("MicroStrategy Base URL", default=default)

Keys use dot notation to address nested YAML dicts:
    "mstr.base_url"  →  _data["mstr"]["base_url"]

Sensitive keys (mstr.password, mstr.api_token, anthropic_api_key) are NEVER
written to profile files. Use environment variables instead:
    mstr:
      password: ${MSTR_PASSWORD}

The get() method resolves ${VARNAME} strings from os.environ automatically.
The module does NOT auto-load on import — call load() explicitly from main.py
when --profile is supplied.
"""

import os
import re
from pathlib import Path

try:
    import yaml          # PyYAML ≥ 5.1
except ImportError:      # pragma: no cover
    yaml = None

_data: dict = {}
_loaded_path: str = ""

_ENV_RE = re.compile(r"^\$\{(\w+)\}$")


def load(path: str) -> None:
    """
    Read a YAML file and populate the profile.
    Raises FileNotFoundError if the file does not exist.
    Raises yaml.YAMLError if the file is not valid YAML.
    Raises ImportError if PyYAML is not installed.
    """
    global _data, _loaded_path
    if yaml is None:
        raise ImportError(
            "PyYAML is required for profile support.  "
            "Install it with:  pip install pyyaml"
        )
    text = Path(path).read_text(encoding="utf-8")   # raises FileNotFoundError
    _data = yaml.safe_load(text) or {}
    _loaded_path = str(Path(path).resolve())


def loaded() -> bool:
    """Return True if a profile has been successfully loaded."""
    return bool(_loaded_path)


def is_silent() -> bool:
    """Return True when a profile is loaded with interactive: false.

    When silent, cli helpers skip all prompts and return their defaults
    immediately.  Credentials must be available as environment variables;
    missing required values raise RuntimeError rather than prompting.
    """
    return loaded() and (get("interactive") is False)


def get(key: str, fallback=None):
    """
    Retrieve a value by dot-notation key (e.g. "mstr.base_url").
    Traverses nested dicts one segment at a time.
    Resolves ${VARNAME} strings from os.environ.
    Returns fallback if the key is absent or the value is None.
    """
    parts = key.split(".")
    node = _data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return fallback
        node = node[part]
    if node is None:
        return fallback
    if isinstance(node, str):
        m = _ENV_RE.match(node)
        if m:
            return os.environ.get(m.group(1), "") or fallback
    return node


def missing_env_vars() -> list:
    """
    Return a sorted list of environment variable names that are either:
      - known credential vars (MSTR_PASSWORD, ANTHROPIC_API_KEY), or
      - explicitly referenced in the profile as ${VAR_NAME}
    …and are not currently set in os.environ.
    Call after load(). Returns [] when no profile is loaded.
    """
    _KNOWN = {"MSTR_PASSWORD", "ANTHROPIC_API_KEY"}

    found: set = set()

    def _scan(node):
        if isinstance(node, dict):
            for v in node.values():
                _scan(v)
        elif isinstance(node, list):
            for v in node:
                _scan(v)
        elif isinstance(node, str):
            m = _ENV_RE.match(node)
            if m:
                found.add(m.group(1))

    _scan(_data)
    all_vars = _KNOWN | found
    return sorted(v for v in all_vars if not os.environ.get(v))


def save(path: str, data: dict) -> None:
    """
    Write data as a YAML profile file with a standard header comment block.
    Creates parent directories if they do not exist.
    Caller is responsible for ensuring no sensitive keys are in data.
    """
    if yaml is None:
        raise ImportError(
            "PyYAML is required for profile support.  "
            "Install it with:  pip install pyyaml"
        )
    from datetime import date
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Tallmadge run profile — generated {date.today().isoformat()}\n"
        f"# Run: python main.py --profile {path}\n"
        f"# Credentials: set MSTR_PASSWORD and ANTHROPIC_API_KEY as environment variables\n"
        f"# Add  interactive: false  to suppress all prompts (fully unattended run)\n\n"
    )
    body = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    dest.write_text(header + body, encoding="utf-8")
