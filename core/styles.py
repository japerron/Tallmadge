"""
core/styles.py — Style configuration resolver

Supports three style sources:
  1. Pick from saved styles in config/settings.py  (existing behaviour)
  2. Extract from a URL — fetch the page and parse hex colors + font-family
     declarations from its CSS/HTML, then let the user assign roles interactively.
  3. Load from a JSON file — expects: {name, primary, secondary, accent, font}

After sources 2 or 3, optionally saves the new style back to config/settings.py
so it appears in the saved-styles list on future runs.

Last-used choices (style source, saved-style key, output format, xlsx layout)
are persisted via core.last_run so they become the defaults on the next run.
"""

import re
import json
import requests
from pathlib import Path

from core import cli
from core import last_run
from core import profile


# ── URL extraction helpers ────────────────────────────────────────────────────

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT":             "1",
}


def _fetch_style_from_url(url: str) -> dict:
    """
    Fetch a URL and extract hex colors and font-family names from its content.
    Returns {"hex_colors": [...], "fonts": [...]}.
    Raises RuntimeError if the page cannot be fetched.
    Sends browser-like headers to avoid 403 blocks on bot user-agents.
    """
    cli.info(f"Fetching: {url}")
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers=_BROWSER_HEADERS,
            allow_redirects=True,
        )
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        raise RuntimeError(f"Could not fetch URL: {e}")

    # All 6-digit hex colors (deduplicated, order-preserving)
    hex_colors = list(dict.fromkeys(re.findall(r"#[0-9A-Fa-f]{6}\b", text)))

    # font-family declarations — take the first named family from each rule
    raw_fonts = re.findall(r"font-family\s*:\s*([^;\"'}{]+)", text)
    fonts = []
    for raw in raw_fonts:
        name = raw.strip().strip("'\"").split(",")[0].strip().strip("'\"")
        if name and len(name) < 60 and name not in fonts:
            fonts.append(name)

    return {"hex_colors": hex_colors[:20], "fonts": fonts[:10]}


def _build_style_from_extraction(extracted: dict) -> dict:
    """
    Present extracted colors and fonts to the user and let them assign roles.
    Returns a complete style dict {name, primary, secondary, accent, font}.
    """
    colors = extracted["hex_colors"]
    fonts  = extracted["fonts"]

    if not colors:
        raise RuntimeError("No hex colors found on that page.")

    cli.info(f"Found {len(colors)} color(s): {', '.join(colors[:12])}")

    def _pick_color(role: str, default_idx: int = 0) -> str:
        safe_idx = min(default_idx, len(colors) - 1)
        idx = cli.select(f"Pick {role} color:", colors, default=safe_idx)
        return colors[idx]

    primary   = _pick_color("primary",   0)
    secondary = _pick_color("secondary", min(1, len(colors) - 1))
    accent    = _pick_color("accent",    min(2, len(colors) - 1))

    if fonts:
        font_idx = cli.select("Pick font family:", fonts)
        font = fonts[font_idx]
    else:
        font = cli.text_input("No fonts detected. Enter font name", default="Calibri")

    name = cli.text_input("Style name", default="Custom")
    return {
        "name":      name,
        "primary":   primary,
        "secondary": secondary,
        "accent":    accent,
        "font":      font,
    }


# ── File loader ───────────────────────────────────────────────────────────────

def _load_style_from_file(path: str) -> dict:
    """
    Load a style from a JSON file.
    Required fields: name, primary, secondary, accent, font.
    Raises ValueError if any field is missing.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"name", "primary", "secondary", "accent", "font"}
    missing  = required - set(data.keys())
    if missing:
        raise ValueError(f"Style file is missing required fields: {', '.join(sorted(missing))}")
    return {k: data[k] for k in required}


# ── Save to settings ──────────────────────────────────────────────────────────

def _save_style_to_settings(style: dict) -> None:
    """
    Insert or update a style entry in the STYLES dict in config/settings.py.
    The dict key is derived from the style name (lowercased, non-alphanumeric → _).
    """
    settings_path = Path(__file__).parent.parent / "config" / "settings.py"
    content = settings_path.read_text(encoding="utf-8")

    key = re.sub(r"[^a-z0-9_]", "_", style["name"].lower()).strip("_")
    new_entry = (
        f'    "{key}": {{'
        f'"name": "{style["name"]}", '
        f'"primary": "{style["primary"]}", '
        f'"secondary": "{style["secondary"]}", '
        f'"accent": "{style["accent"]}", '
        f'"font": "{style["font"]}"}},'
    )

    # Insert before the closing brace of the STYLES = { ... } block.
    # Match up to \n} so we land on the outer dict's closing brace (on its own
    # line), not the first } found inside an entry's inner dict.
    new_content = re.sub(
        r"(STYLES\s*=\s*\{.*?)(\n\})",
        lambda m: m.group(1) + "\n" + new_entry + m.group(2),
        content,
        flags=re.DOTALL,
    )
    settings_path.write_text(new_content, encoding="utf-8")
    cli.success(f"Style '{key}' saved to config/settings.py.")


# ── Public interface ──────────────────────────────────────────────────────────

# Maps last_run "style_source" strings → base option indices
_SOURCE_TO_IDX = {"saved": 0, "url": 1, "json": 2}
_IDX_TO_SOURCE = {0: "saved", 1: "url", 2: "json"}


def select_style(args=None, inherited_style: dict | None = None) -> dict:
    """
    Let the user pick or create a presentation style.

    Sources offered (always):
      1. Pick from saved styles (config/settings.py STYLES dict)
      2. Extract from a URL (auto-detect hex colors + fonts)
      3. Load from a JSON file

    When inherited_style is provided (e.g. the comparison style chosen earlier),
    it is prepended as option 0 and made the default, so the user can accept it
    with a single Enter press.

    After sources URL or JSON, the user is offered the option to save the new
    style to config/settings.py for future runs.

    Fast-path: if --style flag matches a saved style key, return it immediately.
    Last-used source and saved-style key are restored as defaults via last_run.
    """
    from config.settings import STYLES

    # Fast path: command-line --style flag
    if args and getattr(args, "style", None):
        key = args.style.lower()
        if key in STYLES:
            return STYLES[key]
        cli.warn(f"Unknown style '{key}', prompting interactively.")

    BASE_OPTIONS = [
        "Pick from saved styles",
        "Extract from a URL  (auto-detect colors + fonts)",
        "Load from a JSON file",
    ]

    # Prepend inherited style as the default (index 0) when one is available
    if inherited_style:
        source_options = [
            f"Use current style  ({inherited_style['name']})",
        ] + BASE_OPTIONS
        # Don't restore last_run source when an inherited style is on offer —
        # the user's most likely intent is to reuse it (index 0 stays default).
        default_source_idx = 0
    else:
        source_options = BASE_OPTIONS
        _prof_src          = profile.get("style.source")
        last_source        = _prof_src or last_run.get("style_source", "saved")
        default_source_idx = _SOURCE_TO_IDX.get(last_source, 0)

    source_idx = cli.select("Style source:", source_options, default=default_source_idx)

    # Handle inherited style selection
    if inherited_style and source_idx == 0:
        cli.success(f"Style: {inherited_style['name']}")
        return inherited_style

    # Adjust index to BASE_OPTIONS when inherited option was prepended
    base_idx = source_idx - (1 if inherited_style else 0)
    last_run.set("style_source", _IDX_TO_SOURCE.get(base_idx, "saved"))

    style = None

    if base_idx == 0:
        # Saved styles — default to last-used key
        keys    = list(STYLES.keys())
        options = [f"{v['name']}  ({v['primary']})" for v in STYLES.values()]
        _prof_key   = profile.get("style.key", "")
        last_key    = _prof_key or last_run.get("style_key", "")
        default_idx = keys.index(last_key) if last_key in keys else 0
        idx   = cli.select("Presentation style:", options, default=default_idx)
        style = STYLES[keys[idx]]
        last_run.set("style_key", keys[idx])
        last_run.save()
        cli.success(f"Style: {style['name']}")
        return style

    elif base_idx == 1:
        url       = cli.text_input("Enter URL to extract style from")
        extracted = _fetch_style_from_url(url)
        style     = _build_style_from_extraction(extracted)

    elif base_idx == 2:
        path  = cli.file_input("Path to style JSON file")
        style = _load_style_from_file(path)
        cli.success(f"Loaded style: {style['name']}")

    # Offer to save the new style
    if style and cli.confirm("Save this style to config/settings.py?", default=True):
        try:
            _save_style_to_settings(style)
        except Exception as e:
            cli.warn(f"Could not save style: {e}")

    last_run.save()
    return style


def _select_xlsx_layout() -> str:
    """Prompt for Excel layout immediately after the user selects xlsx."""
    _prof_layout = profile.get("render.xlsx_layout", "")
    last_layout  = _prof_layout or last_run.get("xlsx_layout", "detail")
    default_idx  = {"detail": 0, "wide": 1, "both": 2}.get(last_layout, 0)
    idx = cli.select(
        "Excel layout:",
        [
            "Detail  — one sheet per prompt  (default)",
            "Wide    — one row per prompt, all fields as columns",
            "Both    — Wide View sheet + individual detail sheets",
        ],
        default=default_idx,
    )
    layout = ["detail", "wide", "both"][idx]
    last_run.set("xlsx_layout", layout)
    last_run.save()
    return layout


def select_output_format(args=None) -> tuple:
    """
    Let the user pick an output format: pptx, xlsx, or web.
    For xlsx, immediately prompts for layout (detail / wide / both).
    Returns: (fmt, layout)  — layout is None for pptx and web.
    Last-used format and xlsx layout are restored as defaults via last_run.
    """
    if args and getattr(args, "format", None):
        fmt = args.format.lower()
        if fmt in ("pptx", "xlsx", "web"):
            layout = _select_xlsx_layout() if fmt == "xlsx" else None
            return fmt, layout

    _prof_fmt   = profile.get("render.format", "")
    last_fmt    = _prof_fmt or last_run.get("output_format", "pptx")
    default_idx = {"pptx": 0, "xlsx": 1, "web": 2}.get(last_fmt, 0)

    idx = cli.select(
        "Output format:",
        [
            "PowerPoint (.pptx)",
            "Excel (.xlsx)",
            "Web pages  (HTML site)",
        ],
        default=default_idx,
    )
    fmt = ["pptx", "xlsx", "web"][idx]
    last_run.set("output_format", fmt)
    last_run.save()

    layout = _select_xlsx_layout() if fmt == "xlsx" else None
    return fmt, layout
