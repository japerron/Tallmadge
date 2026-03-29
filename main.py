#!/usr/bin/env python3
"""
main.py — Tallmadge CLI Entry Point  (v1.1)

Run fully interactive (recommended):
  python main.py

Or drive non-interactively with flags:
  python main.py --function test --standard
  python main.py --function test --extended --prompts-file my_prompts.txt
  python main.py --function compare --baseline-file results/baseline.json --results-file results/current.json
  python main.py --function render --results-file results/results_latest.json --format web --style corporate
  python main.py --function render --format xlsx --style nestle

Windows — set encoding before running to avoid console errors:
  CMD:        set PYTHONIOENCODING=utf-8 && python main.py
  PowerShell: $env:PYTHONIOENCODING="utf-8"; python main.py
  Git Bash:   PYTHONIOENCODING=utf-8 python main.py

  python main.py --function compare --results-file results/current.json --baseline-file results/NestleStandard.xlsx

Functions: test | compare | render
"""

import sys
import argparse
from pathlib import Path

# Make sure project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from core import cli
from core.cli import banner, section, select, error, info


# ── Argument parser ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    # Load style keys dynamically so user-added styles are always valid choices
    try:
        from config.settings import STYLES as _STYLES
        _style_choices = list(_STYLES.keys())
    except Exception:
        _style_choices = None   # fall back to no validation; runtime will check

    p = argparse.ArgumentParser(
        prog="tallmadge",
        description="Tallmadge — Agent Testing CLI  (v1.1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--function", choices=["test", "compare", "render"],
                   help="Function to run (interactive if omitted)")
    p.add_argument("--mcp-agent", action="store_true",
                   help="Use MCP-Agent data mode — MCP direct HTTP (faster, fewer fields). Test function only.")
    p.add_argument("--api", action="store_true",
                   help="Use API data mode — REST API (includes SQL, Explanation, "
                        "grid data, rendered images). Test function only.")
    p.add_argument("--format", choices=["pptx", "xlsx", "web"],
                   help="Output format for rendering (interactive if omitted)")
    p.add_argument("--style", choices=_style_choices, metavar="STYLE",
                   help="Presentation style key from config/settings.py "
                        f"(available: {', '.join(_style_choices) if _style_choices else 'see settings.py'}). "
                        "Interactive if omitted.")
    p.add_argument("--prompts-file",
                   help="Path to a plain text file of prompts (one per line). "
                        "Prefix a line with '<Follow-up>' to chain it to the previous prompt. "
                        "Test function only.")
    p.add_argument("--results-file",
                   help="Path to a results JSON file (Compare / Render functions)")
    p.add_argument("--baseline-file",
                   help="Path to a baseline results JSON or Excel gold standard file "
                        "(Test / Compare functions — Excel triggers gold standard scoring)")
    p.add_argument("--profile", metavar="PATH",
                   help="Path to a YAML run profile for unattended execution "
                        "(e.g. profiles/weekly.yaml). "
                        "Profile values become defaults; missing keys prompt interactively.")
    return p


# ── Function selection ─────────────────────────────────────────────────────────

FUNCTION_LABELS = [
    "Test     — Run prompts → save results  (optionally compare + render)",
    "Compare  — Compare current results vs baseline JSON or Excel gold standard  (optionally render)",
    "Render   — Load results file → render output  (pptx / xlsx / web)",
]
FUNCTION_KEYS = ["test", "compare", "render"]


def select_function() -> str:
    idx = select("Select task:", FUNCTION_LABELS)
    return FUNCTION_KEYS[idx]


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args   = parser.parse_args()

    banner()

    # Load profile if supplied
    if args.profile:
        from core import profile as _profile
        try:
            _profile.load(args.profile)
            cli.info(f"Profile loaded: {Path(args.profile).name}")
            for _var in _profile.missing_env_vars():
                cli.warn(f"{_var} not set — terminate this run, set the variable, then restart.")
                _ps_val = '"your_value"'
                print(f"       {cli.grey('CMD / Anaconda Prompt:  set ' + _var + '=your_value')}")
                print(f"       {cli.grey('PowerShell:             $env:' + _var + '=' + _ps_val)}")
        except FileNotFoundError:
            cli.error(f"Profile file not found: {args.profile}")
            sys.exit(1)
        except Exception as e:
            cli.error(f"Could not load profile: {e}")
            sys.exit(1)

    # Validate conflicting flags
    if args.mcp_agent and args.api:
        error("Cannot use --mcp-agent and --api together.")
        sys.exit(1)

    # Determine function — flag → profile → interactive menu
    mode = args.function
    if mode is None and args.profile:
        from core import profile as _profile
        mode = _profile.get("function")   # None if key absent; falls through to menu
    if mode is None:
        mode = select_function()

    try:
        if mode == "test":
            from functions.functions import function_test
            function_test(args)

        elif mode == "compare":
            from functions.functions import function_compare
            function_compare(args)

        elif mode == "render":
            from functions.functions import function_render
            function_render(args)

    except KeyboardInterrupt:
        print()
        cli.warn("Interrupted by user.")
        sys.exit(0)
    except FileNotFoundError as e:
        cli.error(str(e))
        sys.exit(1)
    except RuntimeError as e:
        cli.error(str(e))
        sys.exit(1)
    except Exception as e:
        cli.error(f"Unexpected error: {e}")
        raise  # show full traceback for unexpected errors


if __name__ == "__main__":
    main()
