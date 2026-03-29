"""
core/ci_advisor.py — Custom Instructions improvement advisor

Sends failing prompt results (those below a score threshold) to Claude and
asks it to produce an improved set of Custom Instructions for the agent.

Inputs:
  ci_text   — current CI as a plain-text string
  scored    — output of core.expected.score_results()
  threshold — prompts with score < threshold are included (e.g. 3.5)

Output:
  Improved CI text saved to output/ci_improved_{stem}_{timestamp}.txt
"""

import requests
from datetime import datetime
from pathlib import Path

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OUTPUT_DIR   = Path(__file__).parent.parent / "output"
_API_URL     = "https://api.anthropic.com/v1/messages"
_MODEL       = "claude-sonnet-4-5"

# CI limits and system prompt loaded from config/scoring.yaml
from core import scoring_config as _sc
_ci_cfg      = _sc.load()["ci_advisor"]
CI_MAX_CHARS: int = _ci_cfg["max_chars"]
_SYSTEM: str      = _ci_cfg["system_prompt"].rstrip()


# ── API call ──────────────────────────────────────────────────────────────────

def _call_claude(system: str, user: str, api_key: str) -> str:
    resp = requests.post(
        _API_URL,
        headers={
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      _MODEL,
            "max_tokens": 8192,
            "system":     system,
            "messages":   [{"role": "user", "content": user}],
        },
        timeout=120,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(ci_text: str, scored: list[dict],
                  threshold: float) -> str | None:
    """
    Build the user message for the CI improvement call.
    Returns None when no prompts fall below the threshold.
    """
    failing = [s for s in scored
               if s["score"] is not None and s["score"] < threshold]
    if not failing:
        return None

    lines = []

    lines.append(f"Below are {len(failing)} analytics prompt(s) that scored "
                 f"below {threshold}/5.0 when tested against expected values. "
                 f"Each entry shows the prompt, its score, and exactly what "
                 f"went wrong (missing/extra WHERE filters, attributes, or metrics).")
    lines.append("")

    for s in failing:
        lines.append(f"--- Prompt #{s['id']}  (Score: {s['score']}) ---")
        lines.append(f"  \"{s['prompt']}\"")
        for d in s["deductions"]:
            lines.append(f"  [{d['field']}]  -{d['deduction']} pt(s)")
            lines.append(f"    Expected : {d['expected']}")
            lines.append(f"    Actual   : {d['actual']}")
            if d.get("missing"):
                lines.append(f"    Missing  : {', '.join(d['missing'])}")
            if d.get("extra"):
                lines.append(f"    Extra    : {', '.join(d['extra'])}")
        lines.append("")

    lines.append("Using the failing results above as a guide, rewrite the "
                 "Custom Instructions so the agent will correctly handle these "
                 "and similar prompts in future runs.")
    lines.append(f"The improved CI must be under {CI_MAX_CHARS} characters.")
    lines.append("Output only the improved CI text — no explanation, "
                 "no preamble, no markdown fencing.")

    return "\n".join(lines)




# ── Public API ────────────────────────────────────────────────────────────────

def suggest_ci(ci_text: str, scored: list[dict],
               threshold: float, api_key: str) -> str | None:
    """
    Call Claude and return improved CI text.
    Returns None if no prompts are below the threshold.
    """
    user = _build_prompt(ci_text, scored, threshold)
    if user is None:
        return None

    # Prepend current CI (or a from-scratch note) before the failing prompts
    if ci_text.strip():
        ci_block = (
            "CURRENT CUSTOM INSTRUCTIONS:\n"
            "---\n"
            f"{ci_text.strip()}\n"
            "---\n\n"
        )
    else:
        ci_block = (
            "NO CURRENT CUSTOM INSTRUCTIONS — "
            "create a new set from scratch based on the failing prompts below.\n\n"
        )
    return _call_claude(_SYSTEM, ci_block + user, api_key)


def save_ci(improved_text: str, source_path: Path) -> Path:
    """Save improved CI to a timestamped file in the output directory."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = OUTPUT_DIR / f"ci_improved_{source_path.stem}_{ts}.txt"
    out_path.write_text(improved_text, encoding="utf-8")
    return out_path
