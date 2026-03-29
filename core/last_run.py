"""
core/last_run.py — Persists last-used interactive choices across runs.

Values are stored in config/last_run.json (gitignore-friendly; non-fatal on
any read/write error). Callers use get() to retrieve defaults and set()/save()
to persist a new choice immediately after the user confirms it.

Keys used across the codebase:
  username         str   — MicroStrategy login (no password ever stored)
  connector_name   str   — MCP_CONNECTORS key, or "custom"
  base_url         str   — API mode MicroStrategy base URL
  agent_id         str   — API mode Agent ID
  project_id       str   — API mode Project ID
  login_mode       int   — authentication mode (1=Standard, 16=LDAP, 4096=API Token)
  run_mode         str   — "api" | "mcp-agent"
  prompts_source   str   — "settings" | "file"
  prompts_file     str   — absolute path to last-used prompts text file
  on_sorry         str   — "stop" | "continue" | "resubmit"
  sorry_retries    int   — retry count for "resubmit" mode (0-3)
  style_source     str   — "saved" | "url" | "json"
  style_key        str   — key in STYLES dict (saved-style runs only)
  output_format    str   — "pptx" | "xlsx" | "web"
  xlsx_layout      str   — "detail" | "wide" | "both"
"""

import json
from pathlib import Path

_PATH = Path(__file__).parent.parent / "config" / "last_run.json"
_data: dict = {}


def _load() -> None:
    global _data
    try:
        if _PATH.exists():
            _data = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        _data = {}


def get(key: str, fallback=None):
    """Return the last-saved value for key, or fallback if not present."""
    return _data.get(key, fallback)


def set(key: str, value) -> None:
    """Stage a value. Call save() to flush to disk."""
    _data[key] = value


def save() -> None:
    """Write all staged values to disk. Silently swallows errors.
    Skipped during profile runs to preserve interactive session state."""
    try:
        from core import profile as _profile
        if _profile.loaded():
            return
    except Exception:
        pass
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(_data, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    except Exception:
        pass  # non-fatal — last_run is a convenience, not critical state


# Load on import so callers can call get() immediately.
_load()
