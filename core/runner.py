"""
core/runner.py — Prompt execution orchestrator
Both MCP-Agent and API modes use the same MSTRSession (MicroStrategy login).
MCP-Agent  → MCP JSON-RPC  → connector URL (configurable, see MCP_CONNECTORS in settings)
API        → REST API      → /api/questions + poll (base URL, Agent ID, Project ID configurable)
"""

import re

from core import cli
from core import last_run
from core import profile
from core.results import make_envelope, save
from config.settings import MAX_RESULT_BACKUPS, LOGIN_MODE


def select_run_mode(args=None) -> str:
    """API / MCP-Agent / MCP-Mosaic — flag or interactive."""
    if args and getattr(args, "api", False):
        return "api"
    if args and getattr(args, "mcp_agent", False):
        return "mcp-agent"
    if profile.get("run_mode") == "api":
        return "api"

    _options = [
        "API          — REST API              (includes SQL + Explanation + images)",
        "MCP-Agent    — Access Agents through MCP  [Under Development — authorization issues]",
        "MCP-Mosaic   — Access USL through MCP     [Under Development — not yet available]",
    ]
    _disabled = {
        1: "MCP-Agent is under development and currently has authorization issues.",
        2: "MCP-Mosaic is under development and not yet available.",
    }

    while True:
        idx = cli.select("Data collection mode:", _options)
        if idx == 0:
            return "api"
        cli.warn(_disabled[idx] + " Please select a different mode.")


_ID_RE = re.compile(r"^[A-Fa-f0-9]{32}$")   # 32 hex chars (MSTR GUIDs)
_MAX_ID_ATTEMPTS = 3


def _prompt_mstr_id(label: str, default: str) -> str:
    """
    Prompt for a MicroStrategy GUID (32 hex characters).
    Re-prompts up to _MAX_ID_ATTEMPTS times on invalid input.
    Raises RuntimeError if all attempts are exhausted.
    """
    for attempt in range(1, _MAX_ID_ATTEMPTS + 1):
        value = cli.text_input(label, default=default).strip().upper()
        if _ID_RE.match(value):
            return value
        remaining = _MAX_ID_ATTEMPTS - attempt
        if remaining > 0:
            cli.warn(
                f"Invalid {label}: must be exactly 32 hexadecimal characters "
                f"(0-9, A-F).  {remaining} attempt(s) remaining."
            )
        else:
            raise RuntimeError(
                f"Invalid {label} after {_MAX_ID_ATTEMPTS} attempts. Aborting."
            )


_LOGIN_MODE_OPTIONS = [
    "Standard   (loginMode=1)    — username / password",
    "LDAP       (loginMode=16)   — directory authentication",
    "API Token  (loginMode=4096) — token passed as username, no password",
]
_LOGIN_MODE_VALUES = [1, 16, 4096]


def prompt_extended_config() -> tuple:
    """
    Prompt the user for API mode connection parameters.
    Returns (base_url, agent_id, project_id, login_mode) with defaults from
    last run (falling back to settings values if no prior run exists).
    Press Enter at any prompt to accept the default shown in brackets.
    Agent ID and Project ID are validated as 32-char hex GUIDs before
    being accepted or persisted.
    """
    cli.section("API Mode Configuration")
    cli.info("Press Enter to accept the defaults shown in brackets.")

    base_url   = cli.text_input("MicroStrategy Base URL",
                                default=profile.get("mstr.base_url")
                                        or last_run.get("base_url",   ""))
    agent_id   = _prompt_mstr_id("Agent ID",
                                 default=profile.get("mstr.agent_id")
                                         or last_run.get("agent_id",   ""))
    project_id = _prompt_mstr_id("Project ID",
                                 default=profile.get("mstr.project_id")
                                         or last_run.get("project_id", ""))

    _profile_mode = profile.get("mstr.login_mode")
    last_mode_val = (_profile_mode if _profile_mode is not None
                     else last_run.get("login_mode", LOGIN_MODE))
    default_mode_idx = _LOGIN_MODE_VALUES.index(last_mode_val) \
        if last_mode_val in _LOGIN_MODE_VALUES else 0
    mode_idx   = cli.select("Authentication mode:", _LOGIN_MODE_OPTIONS,
                            default=default_mode_idx)
    login_mode = _LOGIN_MODE_VALUES[mode_idx]

    last_run.set("base_url",   base_url)
    last_run.set("agent_id",   agent_id)
    last_run.set("project_id", project_id)
    last_run.set("login_mode", login_mode)
    last_run.save()

    return base_url, agent_id, project_id, login_mode


def select_connector() -> str:
    """
    Prompt the user to select an MCP connector URL for Standard mode.
    Reads MCP_CONNECTORS from settings; falls back to a custom URL entry.
    Defaults to the connector chosen on the last run.
    Returns the chosen connector URL string.
    """
    from config.settings import MCP_CONNECTORS

    names  = list(MCP_CONNECTORS.keys())
    labels = [
        f"{name}  ({url})" if url else f"{name}  (enter URL)"
        for name, url in MCP_CONNECTORS.items()
    ]
    labels.append("Enter a custom URL")

    # Default to whichever connector was used last run
    last_name   = last_run.get("connector_name", "")
    default_idx = names.index(last_name) if last_name in names else 0

    cli.section("MCP Connector")
    idx = cli.select("Select MCP connector:", labels, default=default_idx)

    if idx < len(names):
        chosen_name = names[idx]
        url = MCP_CONNECTORS[chosen_name]
        if not url:
            url = cli.text_input("Enter connector URL")
        last_run.set("connector_name", chosen_name)
        last_run.save()
        cli.success(f"Connector: {chosen_name}")
        return url

    # Custom URL
    url = cli.text_input("Enter connector URL")
    last_run.set("connector_name", "custom")
    last_run.save()
    return url


def get_mstr_session(base_url: str | None = None, login_mode: int | None = None):
    """
    Prompt for MicroStrategy credentials and return an authenticated MSTRSession.
    Re-prompts on failure up to MAX_LOGIN_ATTEMPTS times before aborting.
    Defaults the username field to the last successfully used username.
    Uses base_url if provided, otherwise falls back to last_run or empty string.
    login_mode: 1 = Standard (default), 16 = LDAP.  When None, falls back to
    LOGIN_MODE from settings.
    """
    from core.api import MSTRSession

    MAX_LOGIN_ATTEMPTS = 3
    url  = base_url or ""
    mode = login_mode if login_mode is not None else LOGIN_MODE

    cli.section("MicroStrategy Login")
    cli.info("Both MCP-Agent and API modes require your MicroStrategy credentials.")

    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        if mode == 4096:
            username = cli.resolve_secret("API Token", "MSTR_API_TOKEN")
            password = ""
        else:
            username = cli.text_input("Username",
                                      default=profile.get("mstr.username")
                                              or last_run.get("username", ""))
            password = cli.resolve_secret("Password", "MSTR_PASSWORD")

        session = MSTRSession(base_url=url)
        cli.info(f"Logging in to {url}...")
        if session.login(username, password, login_mode=mode):
            if mode != 4096:
                last_run.set("username", username)
                last_run.save()
            cli.success("Logged in successfully.")
            return session

        remaining = MAX_LOGIN_ATTEMPTS - attempt
        if remaining > 0:
            cli.error(f"Authentication failed. {remaining} attempt(s) remaining — please try again.")
        else:
            raise RuntimeError("Authentication failed after 3 attempts. Aborting.")


def try_fast_path(args=None) -> dict | None:
    """
    If a complete run configuration was saved from the previous run, display a
    summary and offer to re-run with those exact settings.

    Returns a completed results envelope if the fast-path is accepted and run,
    or None if it is unavailable, declined, or encounters any setup error
    (caller should fall back to the full interactive flow).
    """
    from config.settings import PROMPTS, MAX_RESULT_BACKUPS
    from core.results import make_envelope, save

    # Fast-path only available for API mode (the only fully implemented mode)
    run_mode = last_run.get("run_mode", "")
    if run_mode != "api":
        return None

    # All fields must be present for a usable fast-path
    base_url      = last_run.get("base_url",      "")
    agent_id      = last_run.get("agent_id",      "")
    project_id    = last_run.get("project_id",    "")
    login_mode    = last_run.get("login_mode")
    on_sorry      = last_run.get("on_sorry",      "")
    sorry_retries = last_run.get("sorry_retries", 0)
    prompts_src   = last_run.get("prompts_source", "")
    prompts_file  = last_run.get("prompts_file",  "")
    username      = last_run.get("username",      "")

    if not all([base_url, agent_id, project_id,
                login_mode is not None, on_sorry, prompts_src]):
        return None
    if prompts_src == "file" and not prompts_file:
        return None

    # ── Build display strings ──────────────────────────────────────────────────
    if prompts_src == "settings":
        n = len(PROMPTS)
        if not n:
            return None
        prompts_desc = f"config/settings.py  ({n} prompt(s))"
    else:
        prompts_desc = prompts_file

    mode_label = {1: "Standard", 16: "LDAP", 4096: "API Token"}.get(
        login_mode, str(login_mode))
    auth_desc  = (mode_label if login_mode == 4096
                  else f"{mode_label}  (username: {username})")

    sorry_label = {
        "stop":     "Stop run and log partial results",
        "continue": "Continue run normally",
        "resubmit": f"Resubmit  ({sorry_retries} retry, 120s delay)",
    }.get(on_sorry, on_sorry)

    # ── Show summary and ask ───────────────────────────────────────────────────
    cli.section("Previous Run Settings")
    cli.info(f"  Mode:             API")
    cli.info(f"  Prompts:          {prompts_desc}")
    cli.info(f"  Base URL:         {base_url}")
    cli.info(f"  Agent ID:         {agent_id}")
    cli.info(f"  Project ID:       {project_id}")
    cli.info(f"  Auth:             {auth_desc}")
    cli.info(f"  On rephrasing:    {sorry_label}")

    if not cli.confirm("Use settings from last run?", default=True):
        return None

    # ── Load prompts ───────────────────────────────────────────────────────────
    if prompts_src == "settings":
        active_prompts = PROMPTS
    else:
        try:
            from core.prompts import load_prompts_file
            raw = load_prompts_file(prompts_file)
            if not raw:
                cli.warn("Prompts file is empty — falling back to full setup.")
                return None
            active_prompts = [{"id": i + 1, "category": "General", "prompt": p}
                              for i, p in enumerate(raw)]
            cli.info(f"Loaded {len(active_prompts)} prompt(s) from {prompts_file}")
        except Exception as e:
            cli.warn(f"Could not load prompts file: {e} — falling back to full setup.")
            return None

    # ── Run ────────────────────────────────────────────────────────────────────
    envelope = make_envelope("api", active_prompts)
    records  = envelope["results"]

    session = get_mstr_session(base_url=base_url, login_mode=login_mode)
    try:
        from core.api import run_extended
        cli.section("Running Prompts")
        run_extended(active_prompts, records, session, agent_id, project_id,
                     on_sorry=on_sorry, sorry_retries=sorry_retries)
    finally:
        session.logout()

    envelope["meta"]["successful"] = sum(1 for r in records if r["status"] == "Success")
    envelope["meta"]["errors"]     = sum(1 for r in records if r["status"] == "Error")

    path = save(envelope, label="results", max_backups=MAX_RESULT_BACKUPS)
    cli.success(f"Results saved to: {path}")
    return envelope


def run_prompts(mode: str, label: str = "results", args=None) -> dict:
    """
    Execute all prompts in the given mode.
    Returns the completed results envelope (also saved to disk).
    """
    from core.prompts import select_prompts_source
    last_run.set("run_mode", mode)
    last_run.save()
    active_prompts = select_prompts_source(args)

    envelope = make_envelope(mode, active_prompts)
    records  = envelope["results"]

    # API mode: prompt for connection config before login
    if mode == "api":
        base_url, agent_id, project_id, login_mode = prompt_extended_config()
    else:
        base_url   = last_run.get("base_url",   "")
        agent_id   = last_run.get("agent_id",   "")
        project_id = last_run.get("project_id", "")
        login_mode = None   # falls back to LOGIN_MODE in settings

    session = get_mstr_session(base_url=base_url, login_mode=login_mode)

    try:
        if mode == "mcp-agent":
            from core.agent import run_standard
            connector_url = select_connector()
            run_standard(active_prompts, records, session, connector_url, agent_id, project_id)

        elif mode == "api":
            from core.api import run_extended
            _SORRY_OPTIONS = [
                "Stop run and log partial results",
                "Continue run normally  (subsequent prompts are likely to error)",
                "Continue run with resubmissions  (resubmissions are offset by 120 seconds)",
            ]
            _SORRY_KEYS = ["stop", "continue", "resubmit"]
            _prof_sorry    = profile.get("on_sorry")
            _saved_sorry   = last_run.get("on_sorry", "stop")
            _sorry_val     = _prof_sorry if _prof_sorry in _SORRY_KEYS else _saved_sorry
            _sorry_default = _SORRY_KEYS.index(_sorry_val) if _sorry_val in _SORRY_KEYS else 0
            sorry_choice = cli.select(
                "What to do if the agent requests rephrasing any prompt:",
                _SORRY_OPTIONS,
                default=_sorry_default,
            )
            on_sorry      = _SORRY_KEYS[sorry_choice]
            sorry_retries = 0
            if on_sorry == "resubmit":
                _retries_default = str(profile.get("sorry_retries")
                                       or last_run.get("sorry_retries", 1))
                raw = cli.text_input(
                    "Retry attempts per rephrasing request [0-3]",
                    default=_retries_default,
                )
                try:
                    sorry_retries = max(0, min(3, int(raw.strip())))
                except ValueError:
                    sorry_retries = 1
            last_run.set("on_sorry",      on_sorry)
            last_run.set("sorry_retries", sorry_retries)
            last_run.save()
            run_extended(active_prompts, records, session, agent_id, project_id,
                         on_sorry=on_sorry, sorry_retries=sorry_retries)

        elif mode == "mcp-mosaic":
            raise RuntimeError("MCP-Mosaic mode is under development and not yet available.")

    finally:
        session.logout()

    # Update meta counts
    envelope["meta"]["successful"] = sum(1 for r in records if r["status"] == "Success")
    envelope["meta"]["errors"]     = sum(1 for r in records if r["status"] == "Error")

    path = save(envelope, label=label, max_backups=MAX_RESULT_BACKUPS)
    cli.success(f"Results saved to: {path}")

    return envelope
