"""
core/prompts.py — Prompt loading and Claude API categorization

Supports loading prompts from a plain text file (one per line).
The <Follow-up> prefix is preserved exactly.
Optionally calls the Claude API (claude-haiku-4-5 model) to group prompts
into logical semantic categories.
Optionally rewrites the PROMPTS block in config/settings.py.
"""

import re
import json
import requests
from pathlib import Path

from core import cli
from core import last_run
from core import profile


def load_prompts_file(path: str) -> list:
    """
    Read a text file and return non-empty lines as raw prompt strings.
    Blank lines and lines containing only whitespace are ignored.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def categorize_with_claude(raw_prompts: list, api_key: str) -> list:
    """
    Send raw prompt strings to Claude haiku via direct HTTP POST.
    Returns a list of dicts: [{"id": int, "category": str, "prompt": str}, ...]
    The <Follow-up> prefix is preserved verbatim in each prompt field.
    """
    prompt_list_text = "\n".join(f"{i+1}. {p}" for i, p in enumerate(raw_prompts))

    system = (
        "You are a data analyst assistant. You will receive a numbered list of "
        "analytics questions. Return ONLY a valid JSON array where each element has: "
        '{"id": <number>, "category": "<short category name>", "prompt": "<original question text>"}. '
        "Group similar questions under the same category name. "
        "Preserve any <Follow-up> prefix exactly as-is in the prompt field. "
        "Output nothing except the JSON array."
    )
    user = f"Categorize these prompts:\n{prompt_list_text}"

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-haiku-4-5",
            "max_tokens": 2048,
            "system":     system,
            "messages":   [{"role": "user", "content": user}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    text = body["content"][0]["text"].strip()

    # Strip markdown fences if Claude wrapped the JSON
    text = re.sub(r"^```(?:json)?\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return json.loads(text)


def save_prompts_to_settings(prompts: list, settings_path: str) -> None:
    """
    Rewrite the PROMPTS = [...] block in settings.py with the provided prompts list.
    Uses regex to locate and replace the existing block in-place.
    """
    content = Path(settings_path).read_text(encoding="utf-8")

    lines = ["PROMPTS = ["]
    for p in prompts:
        prompt_esc = p["prompt"].replace("\\", "\\\\").replace('"', '\\"')
        cat_esc    = p["category"].replace("\\", "\\\\").replace('"', '\\"')
        lines.append(
            f'    {{"id": {p["id"]},  "category": "{cat_esc}",  "prompt": "{prompt_esc}"}},'
        )
    lines.append("]")
    new_block = "\n".join(lines)

    new_content = re.sub(
        r"PROMPTS\s*=\s*\[.*?\]",
        new_block,
        content,
        flags=re.DOTALL,
    )
    Path(settings_path).write_text(new_content, encoding="utf-8")


def select_prompts_source(args=None) -> list:
    """
    Return the prompts list to use for this run.

    Decision tree:
    1. If --prompts-file flag was given, load from that file.
    2. Else ask interactively whether to load from a file.
    3. If a file is loaded:
       a. Offer to categorize with Claude API.
       b. Offer to save the result back to config/settings.py.
    4. If no file, return PROMPTS from settings unchanged.
    """
    from config.settings import PROMPTS, ANTHROPIC_API_KEY

    # Determine source
    prompts_file = None
    last_file    = last_run.get("prompts_file", "")
    if args and getattr(args, "prompts_file", None):
        prompts_file = args.prompts_file
        last_run.set("prompts_source", "file")
        last_run.save()
    else:
        # ── Profile fast-path ─────────────────────────────────────────────────
        _prof_src  = profile.get("prompts_source")
        _prof_file = profile.get("prompts_file", "")
        if _prof_src == "settings" and PROMPTS:
            last_run.set("prompts_source", "settings")
            last_run.save()
            return PROMPTS
        if _prof_src == "file" and _prof_file:
            prompts_file = _prof_file
            last_run.set("prompts_source", "file")
            last_run.set("prompts_file", prompts_file)
            last_run.save()
            # fall through to file-loading code below

        if prompts_file is None:
            settings_note  = (f"{len(PROMPTS)} prompt(s)"
                              if PROMPTS else "(none configured)")
            prev_file_note = last_file if last_file else "(no previous file)"
            _SOURCE_OPTIONS = [
                f"Prompts stored in config/settings.py         {settings_note}",
                f"Prompts file from previous run               {prev_file_note}",
                "New Prompts file, enter path to file",
            ]
        while prompts_file is None:
            choice = cli.select("Prompts list read from:", _SOURCE_OPTIONS)

            if choice == 0:                        # settings.py
                if not PROMPTS:
                    cli.warn("No prompts configured in config/settings.py.")
                    continue
                last_run.set("prompts_source", "settings")
                last_run.save()
                return PROMPTS                     # use settings unchanged

            if choice == 1:                        # previous file
                if not last_file:
                    cli.warn("No previous prompts file on record.")
                    continue
                prompts_file = last_file
                last_run.set("prompts_source", "file")
                last_run.save()
                break

            # choice == 2: new file — prompt for path
            while True:
                path = cli.text_input("Path to Prompts text file:",
                                      default=_prof_file or last_file or "")
                if not path:
                    cli.warn("Please enter a file path.")
                    continue
                try:
                    Path(path).read_text(encoding="utf-8")
                except FileNotFoundError:
                    cli.warn(f"File not found: {path}")
                    continue
                except PermissionError:
                    cli.warn(f"Permission denied: {path}")
                    continue
                except IsADirectoryError:
                    cli.warn(f"Path is a directory, not a file: {path}")
                    continue
                except UnicodeDecodeError as e:
                    cli.warn(f"File is not readable as text (encoding error): {e}")
                    continue
                except OSError as e:
                    cli.warn(f"Cannot read file: {e}")
                    continue
                # File is readable — persist immediately
                prompts_file = path
                last_run.set("prompts_file",   prompts_file)
                last_run.set("prompts_source", "file")
                last_run.save()
                break
            break  # exit outer while after new-file path is resolved

    if prompts_file is None:
        return PROMPTS  # reached via --prompts-file=None arg path (fallback)

    # Load from file
    cli.info(f"Loading prompts from: {prompts_file}")
    raw = load_prompts_file(prompts_file)
    if not raw:
        cli.warn("No prompts found in file. Falling back to settings.py prompts.")
        return PROMPTS
    cli.success(f"Loaded {len(raw)} prompt(s).")

    # Categorization
    _cat_default = profile.get("categorize_prompts")
    _cat_default = _cat_default if isinstance(_cat_default, bool) else True
    if cli.confirm("Categorize prompts with Claude API?", default=_cat_default):
        api_key = cli.resolve_secret("Anthropic API Key", "ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)

        cli.info("Calling Claude API to categorize prompts (haiku model)...")
        try:
            categorized = categorize_with_claude(raw, api_key)
            unique_cats = len(set(p["category"] for p in categorized))
            cli.success(f"Categorized into {unique_cats} group(s).")
        except Exception as e:
            cli.warn(f"Categorization failed: {e}. Assigning 'General' category.")
            categorized = [
                {"id": i + 1, "category": "General", "prompt": p}
                for i, p in enumerate(raw)
            ]
    else:
        # No categorization — sequential IDs, "General" category
        categorized = [
            {"id": i + 1, "category": "General", "prompt": p}
            for i, p in enumerate(raw)
        ]

    # Optionally save back to settings.py
    _save_default = profile.get("save_prompts_to_settings")
    _save_default = _save_default if isinstance(_save_default, bool) else False
    if cli.confirm("Save these prompts to config/settings.py?", default=_save_default):
        settings_path = str(Path(__file__).parent.parent / "config" / "settings.py")
        try:
            save_prompts_to_settings(categorized, settings_path)
            cli.success("PROMPTS block updated in config/settings.py.")
        except Exception as e:
            cli.warn(f"Could not save to settings.py: {e}")

    return categorized
