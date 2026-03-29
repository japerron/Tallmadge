"""
core/cli.py — Interactive CLI helpers
Provides menu-driven prompts with keyboard navigation.
Falls back gracefully to numbered selection if terminal is limited.
"""

import sys
import os


# ── Colour helpers ─────────────────────────────────────────────────────────────

BOLD   = "\033[1m"
DIM    = "\033[2m"
GREY   = "\033[37m"    # light grey — readable on black backgrounds
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RED    = "\033[91m"
RESET  = "\033[0m"


def _detect_ansi_support() -> bool:
    """
    Return True if the current terminal will actually render ANSI escape codes.

    On Windows, CMD and PowerShell ARE tty's but do NOT process ANSI by default.
    We attempt to enable VT100 processing via the Win32 console API
    (ENABLE_VIRTUAL_TERMINAL_PROCESSING, flag 0x0004).  Windows 10 v1511+
    honours this; older Windows silently ignores it and SetConsoleMode returns 0.
    Fall back to plain text if enabling fails or if not a tty at all.
    """
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
            mode   = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                    return True
        except Exception:
            pass
        return False
    return True


_ANSI = _detect_ansi_support()   # evaluated once at import time


def _c(text, code):
    """Wrap text in ANSI colour codes if the terminal supports them."""
    if _ANSI:
        return f"{code}{text}{RESET}"
    return text

def bold(t):   return _c(t, BOLD)
def dim(t):    return _c(t, DIM)
def grey(t):   return _c(t, GREY)
def green(t):  return _c(t, GREEN)
def yellow(t): return _c(t, YELLOW)
def cyan(t):   return _c(t, CYAN)
def red(t):    return _c(t, RED)


# ── Banner ─────────────────────────────────────────────────────────────────────

def banner():
    W = 42  # interior display width in character cells

    # 🔮 is a "wide" Unicode character: 2 display columns but len() counts it as 1.
    # Use a plain reference string (no ANSI codes) to compute the padding correctly.
    _r2_plain = "  🔮  Tallmadge CLI  v1.1"    # Python len = 24, display width = 25
    _r2_pad   = " " * (W - len(_r2_plain) - 1)  # -1 compensates for emoji's extra col
                                                  # = 42 - 24 - 1 = 17 spaces

    print()
    print(bold(cyan("╔" + "═" * W + "╗")))
    print(bold(cyan("║") + "  🔮  " + bold("Tallmadge CLI") + "  v1.1" + _r2_pad + cyan("║")))
    print(bold(cyan("╚" + "═" * W + "╝")))
    print()


# ── Generic selection ──────────────────────────────────────────────────────────

def select(prompt_text: str, options: list[str], default: int = 0) -> int:
    """
    Display a numbered menu and return the chosen index.
    options: list of display strings
    Returns: 0-based index of chosen option
    """
    from core import profile as _profile
    if _profile.is_silent():
        label = options[default] if 0 <= default < len(options) else str(default)
        print(f"  {grey('[profile]')} {prompt_text}: {label}")
        return default

    print(bold(f"\n{prompt_text}"))
    for i, opt in enumerate(options):
        marker = green("▶") if i == default else " "
        print(f"  {marker} {cyan(str(i+1))}. {opt}")
    print()

    while True:
        raw = input(f"  Enter choice [1-{len(options)}] (default={default+1}): ").strip()
        if raw == "":
            return default
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        print(red(f"  Please enter a number between 1 and {len(options)}."))


def multiselect(prompt_text: str, options: list[str], defaults: list[int] = None) -> list[int]:
    """
    Display a numbered menu for multi-selection (comma-separated input).
    Returns: sorted list of 0-based indices
    """
    defaults = defaults or list(range(len(options)))
    print(bold(f"\n{prompt_text}"))
    print(grey("  Enter numbers separated by commas, or 'all' for all options."))
    for i, opt in enumerate(options):
        marker = green("✓") if i in defaults else " "
        print(f"  {marker} {cyan(str(i+1))}. {opt}")
    print()

    while True:
        raw = input(f"  Enter choices (default=all): ").strip()
        if raw == "" or raw.lower() == "all":
            return list(range(len(options)))
        parts = [p.strip() for p in raw.split(",")]
        try:
            indices = [int(p) - 1 for p in parts if p]
            if all(0 <= i < len(options) for i in indices):
                return sorted(set(indices))
        except ValueError:
            pass
        print(red(f"  Please enter comma-separated numbers between 1 and {len(options)}."))


def confirm(prompt_text: str, default: bool = True) -> bool:
    """Yes/No confirmation prompt."""
    from core import profile as _profile
    if _profile.is_silent():
        label = "Yes" if default else "No"
        print(f"  {grey('[profile]')} {prompt_text}: {label}")
        return default

    hint = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"  {bold(prompt_text)} {grey(hint)}: ").strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print(red("  Please enter y or n."))


def text_input(prompt_text: str, default: str = "", secret: bool = False) -> str:
    """Free-text input, optionally masked for passwords."""
    from core import profile as _profile
    if _profile.is_silent():
        if not secret:
            print(f"  {grey('[profile]')} {prompt_text}: {default or '(empty)'}")
        return default

    hint = f" [{grey(default)}]" if default else ""
    if secret:
        import getpass
        val = getpass.getpass(f"  {bold(prompt_text)}{hint}: ")
        return val if val else default
    val = input(f"  {bold(prompt_text)}{hint}: ").strip()
    return val if val else default


def resolve_secret(label: str, env_var: str, settings_val: str = "") -> str:
    """Resolve a secret credential (API key, password).

    Resolution order:
      1. settings_val (value from config/settings.py, passed by caller)
      2. environment variable named env_var
      3. interactive masked prompt (if neither is available)

    When a stored credential is found, the prompt shows the last 3 characters
    as a hint (e.g. '…abc') and lets the user press Enter to accept it or type
    a replacement. The full stored value is never echoed to the terminal.

    In silent mode (interactive: false): returns the stored value immediately
    without prompting. Raises RuntimeError if no stored value is available —
    credentials must be set as environment variables for unattended runs.
    """
    stored = (settings_val or "").strip() or os.environ.get(env_var, "").strip()

    from core import profile as _profile
    if _profile.is_silent():
        if not stored:
            raise RuntimeError(
                f"{env_var} is not set. "
                f"Set it as an environment variable before running with interactive: false.\n"
                f"  CMD / Anaconda Prompt:  set {env_var}=your_value\n"
                f"  PowerShell:             $env:{env_var}='your_value'"
            )
        return stored

    if stored:
        hint_label = f"{label}  [stored, ends ...{stored[-3:]} — Enter to use]"
        val = text_input(hint_label, secret=True).strip()
        return val if val else stored
    return text_input(label, secret=True).strip()


def file_input(prompt_text: str, must_exist: bool = True, default: str = "") -> str:
    """Prompt for a file path with existence validation.
    If default is provided and the user presses Enter, the default is returned
    (after verifying it still exists when must_exist is True).

    In silent mode: returns the default immediately.  Raises RuntimeError if
    no default is available, or if must_exist and the file is not found.
    """
    from core import profile as _profile
    if _profile.is_silent():
        if not default:
            raise RuntimeError(
                f"Profile field required for '{prompt_text}' but no value was provided."
            )
        if must_exist and not os.path.isfile(default):
            raise RuntimeError(
                f"Profile specifies '{default}' for '{prompt_text}' but the file does not exist."
            )
        print(f"  {grey('[profile]')} {prompt_text}: {default}")
        return default

    hint = f" [{grey(default)}]" if default else ""
    while True:
        path = input(f"  {bold(prompt_text)}{hint}: ").strip().strip('"').strip("'")
        if not path:
            if default:
                if must_exist and not os.path.isfile(default):
                    print(red(f"  Default path no longer exists: {default}"))
                    continue
                return default
            print(red("  Path cannot be empty."))
            continue
        if must_exist and not os.path.isfile(path):
            print(red(f"  File not found: {path}"))
            continue
        return path


def progress(current: int, total: int, label: str = ""):
    """Simple inline progress indicator."""
    pct = int((current / total) * 20)
    bar = green("█" * pct) + dim("░" * (20 - pct))
    suffix = f" {label}" if label else ""
    print(f"\r  [{bar}] {cyan(str(current))}/{total}{suffix}   ", end="", flush=True)
    if current == total:
        print()


def section(title: str):
    """Print a section divider."""
    print()
    print(bold(yellow(f"── {title} "  + "─" * max(0, 44 - len(title)))))
    print()


def success(msg: str):
    print(f"  {green('✓')} {msg}")

def warn(msg: str):
    print(f"  {yellow('⚠')} {msg}")

def error(msg: str):
    print(f"  {red('✗')} {msg}")

def info(msg: str):
    print(f"  {cyan('ℹ')} {msg}")
