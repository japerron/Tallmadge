"""
functions/ — The three Tallmadge functions.

Each function follows a progressive disclosure pattern: collect parameters first,
then ask about optional steps (comparison, rendering) in a logical order.

  Test    — Run prompts → save results
              └ optionally compare to a baseline (saves comparison Excel)
              └ optionally render results (pptx / xlsx / web)

  Compare — Compare two existing results files → save comparison Excel
              └ optionally render the current results (pptx / xlsx / web)

  Render  — Load an existing results file → render output
"""

from pathlib import Path

from core import cli
from core import profile as _profile
from core import last_run
from core.runner import run_prompts, select_run_mode, try_fast_path
from core.styles import select_style, select_output_format
from core.results import load, load_latest, list_files, compare


# ── Shared helpers ──────────────────────────────────────────────────────────────

def _render_output(envelope: dict, fmt: str, style: dict, layout: str | None = None) -> None:
    """Dispatch to the correct renderer."""
    if fmt == "pptx":
        from renderers.pptx import render
        path = render(envelope, style)
        cli.success(f"PowerPoint saved: {path}")
    elif fmt == "xlsx":
        from renderers.xlsx import render
        path = render(envelope, style, layout=layout or "detail")
        cli.success(f"Excel saved: {path}")
    elif fmt == "web":
        from renderers.web import render
        path = render(envelope, style)
        cli.success(f"Web site saved: {path}")
        cli.info(f"Open: {path}/index.html")


def _pick_comparison_file(label: str = "Select comparison target") -> Path:
    """
    Let the user pick a comparison target from the results directory.
    JSON files  → comparison to baseline
    Excel files → comparison to gold standard
    """
    from core.results import RESULTS_DIR
    json_files  = sorted(
        [p for p in RESULTS_DIR.iterdir() if p.suffix.lower() == ".json"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    xlsx_files  = sorted(
        [p for p in RESULTS_DIR.iterdir() if p.suffix.lower() in (".xlsx", ".xls")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    all_files = json_files + xlsx_files
    if not all_files:
        raise FileNotFoundError("No results or Excel files found in results/ directory.")

    cli.section(label)
    cli.info("JSON  files → comparison to baseline")
    cli.info("Excel files → comparison to gold standard")
    options = [
        f"{'[JSON] ' if f.suffix == '.json' else '[Excel]'}  {f.name}  ({f.stat().st_size // 1024}KB)"
        for f in all_files
    ]
    idx = cli.select("Choose file:", options)
    chosen = all_files[idx]
    last_run.set("compare_file", str(chosen))
    last_run.save()
    return chosen


def _pick_results_file(label: str = "Select a results file") -> dict:
    """Let the user pick from available results files."""
    from core.results import RESULTS_DIR
    files = sorted(
        [p for p in RESULTS_DIR.iterdir() if p.suffix.lower() == ".json"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError("No results files found in results/ directory.")

    cli.section(label)
    options = [f"{f.name}  ({f.stat().st_size // 1024}KB)" for f in files]
    idx = cli.select("Choose file:", options)
    chosen = files[idx]
    last_run.set("results_file", str(chosen))
    last_run.save()
    return load(str(chosen))


def _do_comparison(baseline: dict, current: dict, style: dict) -> None:
    """
    Diff baseline vs current and save a comparison Excel report.
    Called from both Test and Compare modes.
    """
    diffs         = compare(baseline, current)
    total_changes = len(diffs)

    if total_changes == 0:
        cli.success("No differences found between the two result sets.")
    else:
        affected = len(set(d["id"] for d in diffs))
        cli.warn(f"{total_changes} difference(s) found across {affected} prompt(s).")

    from renderers.comparison import render as render_comparison
    path = render_comparison(baseline, current, diffs, style)
    cli.success(f"Comparison report saved: {path}")


def _do_expected_comparison(current: dict, standard_path: Path,
                            style: dict) -> tuple[list[dict], str]:
    """Score current results against an Excel standard and save the report.
    Returns (scored, ci_used) — scored list for downstream use (e.g. CI advisor)
    and the CI file path loaded during SQL comparison (empty string if none).
    Optionally enriches scored records with LLM-based SQL comparison before saving.
    """
    from core.expected import load_standard, score_results
    from renderers.expected_report import render as render_expected

    standard = load_standard(standard_path)
    cli.info(f"Standard loaded: {len(standard)} expected prompt(s).")

    scored    = score_results(current, standard)
    matched   = [s for s in scored if s["matched"]]
    unmatched = [s for s in scored if not s["matched"]]
    perfect   = sum(1 for s in matched if s["score"] == 5.0)
    avg       = (sum(s["score"] for s in matched) / len(matched)) if matched else 0.0

    cli.success(f"Matched: {len(matched)}  |  Unmatched: {len(unmatched)}")
    cli.success(f"Perfect (5.0): {perfect}  |  Average score: {avg:.2f}")
    if unmatched:
        cli.warn(f"{len(unmatched)} prompt(s) had no match in the standard.")

    # ── Optional SQL comparison ────────────────────────────────────────────────
    ci_used = ""
    has_gold_sql = any(r.get("sql") for r in standard)
    _sql_default = _profile.get("sql_comparison_enabled")
    _sql_default = _sql_default if isinstance(_sql_default, bool) else False
    _do_sql = has_gold_sql and cli.confirm(
            "Run SQL comparison against gold-standard queries? (uses Claude API tokens)",
            default=_sql_default)
    last_run.set("sql_comparison_enabled", _do_sql); last_run.save()
    if _do_sql:
        cli.section("SQL Comparison")
        ci_used = _do_sql_enrichment(scored, current, standard)
        # Field score is unchanged; show SQL score average separately
        sql_scored = [s for s in matched if s.get("sql_score") is not None]
        if sql_scored:
            sql_avg = sum(s["sql_score"] for s in sql_scored) / len(sql_scored)
            cli.success(f"SQL Score  — Average: {sql_avg:.2f}  ({len(sql_scored)} prompt(s) compared)")
        cli.success(f"Field Score — Perfect (5.0): {perfect}  |  Average: {avg:.2f}")

    path = render_expected(scored, current, style, str(standard_path),
                           standard=standard)
    cli.success(f"Expected-values report saved: {path}")
    return scored, ci_used


def _do_sql_enrichment(scored: list[dict], current: dict,
                       standard: list[dict]) -> str:
    """
    Resolve API key and call enrich_with_sql() to add sql_judgment to scored records.
    Modifies scored in-place.
    Returns the CI file path that was successfully loaded, or "" if none was used.
    """
    from core import last_run
    from core.sql_judge import enrich_with_sql

    # ── API key ────────────────────────────────────────────────────────────────
    try:
        from config.settings import ANTHROPIC_API_KEY as _AK
        _settings_key = (_AK or "").strip()
    except Exception:
        _settings_key = ""
    api_key = cli.resolve_secret("Anthropic API key", "ANTHROPIC_API_KEY", _settings_key)

    # ── Agent instructions (CI file) — optional ────────────────────────────────
    default_ci         = _profile.get("ci_file") or last_run.get("ci_file", "")
    agent_instructions = ""
    ci_loaded          = ""   # path of the CI file actually loaded this session
    while True:
        ci_path_str = cli.text_input(
            "Path to agent instructions file (CI)  (enter -1 to skip)",
            default=default_ci,
        ).strip()
        if not ci_path_str or ci_path_str == "-1":
            break
        try:
            agent_instructions = Path(ci_path_str).read_text(encoding="utf-8").strip()
            cli.info("Agent instructions loaded.")
            last_run.set("ci_file", ci_path_str)
            last_run.save()
            ci_loaded = ci_path_str
            break
        except FileNotFoundError:
            cli.warn(f"File not found: {ci_path_str} — please try again.")
            default_ci = ""
        except Exception as exc:
            cli.warn(f"Could not read CI file: {exc} — please try again.")
            default_ci = ""

    # ── Run ────────────────────────────────────────────────────────────────────
    cli.info("Comparing SQL for matched prompts with gold-standard queries…")
    try:
        n = enrich_with_sql(scored, current, standard, api_key,
                            agent_instructions=agent_instructions)
    except Exception as exc:
        cli.error(f"SQL comparison failed: {exc}")
        return ci_loaded

    if n == 0:
        cli.warn("No prompts had gold SQL in the standard — SQL comparison skipped.")
    else:
        cli.success(f"SQL comparison complete: {n} prompt(s) compared.")

    return ci_loaded


def _do_ci_improvement(scored: list[dict], ci_hint: str = "") -> None:
    """
    Ask Claude to produce improved Custom Instructions based on failing prompts.

    ci_hint: CI file path used in the preceding SQL comparison step (if any).
             Takes precedence over last_run["ci_file"] as the default.

    Steps:
      1. Prompt for score threshold (default 3.5, one decimal).
      2. Prompt for path to the current CI file (press Enter to generate from scratch).
      3. Resolve Anthropic API key (settings → runtime prompt).
      4. Call suggest_ci(); print result size and save timestamped file.
    """
    from core import last_run
    from core.ci_advisor import suggest_ci, save_ci

    # ── threshold ──────────────────────────────────────────────────────────────
    _threshold_default = str(_profile.get("ci_score_threshold") or "3.5")
    raw = cli.text_input(
        "Score threshold  (prompts below this score are sent to Claude)",
        default=_threshold_default,
    ).strip()
    try:
        threshold = round(float(raw), 1)
    except ValueError:
        cli.warn("Invalid threshold — defaulting to 3.5.")
        threshold = 3.5

    # ── CI file path (optional — -1 = generate from scratch) ─────────────────
    default_ci = ci_hint or _profile.get("ci_file") or last_run.get("ci_file", "")
    ci_text    = ""
    ci_path    = Path("new_ci")
    while True:
        ci_path_str = cli.text_input(
            "Path to Custom Instructions file  (enter -1 to generate from scratch)",
            default=default_ci,
        ).strip()
        if not ci_path_str or ci_path_str == "-1":
            cli.info("No CI file provided — will generate Custom Instructions from scratch.")
            break
        ci_path = Path(ci_path_str)
        if not ci_path.exists():
            cli.warn(f"File not found: {ci_path} — please try again.")
            default_ci = ""
            continue
        try:
            ci_text = ci_path.read_text(encoding="utf-8")
            last_run.set("ci_file", str(ci_path))
            last_run.save()
            break
        except Exception as exc:
            cli.warn(f"Could not read CI file: {exc} — please try again.")
            default_ci = ""

    # ── API key ────────────────────────────────────────────────────────────────
    try:
        from config.settings import ANTHROPIC_API_KEY as _AK
        _settings_key = (_AK or "").strip()
    except Exception:
        _settings_key = ""
    api_key = cli.resolve_secret("Anthropic API key", "ANTHROPIC_API_KEY", _settings_key)

    # ── Call Claude ────────────────────────────────────────────────────────────
    cli.info(f"Sending prompts with score < {threshold} to Claude for CI rewrite…")
    try:
        improved = suggest_ci(ci_text, scored, threshold, api_key)
    except Exception as exc:
        cli.error(f"CI improvement failed: {exc}")
        return

    if improved is None:
        cli.success(
            f"No prompts scored below {threshold} — CI improvement not needed."
        )
        return

    cli.success(f"Improved CI received ({len(improved):,} characters).")
    out_path = save_ci(improved, ci_path)
    cli.success(f"Improved CI saved: {out_path}")


# ── Profile save helper ────────────────────────────────────────────────────────

def _prompt_save_profile(function_name: str) -> None:
    """
    Offer to save the current session as a reusable YAML profile.
    Only offered when no profile was loaded (this was a fresh interactive run).
    Builds the profile dict from last_run values (freshly saved at this point).
    Never writes sensitive keys (password, api_token, anthropic_api_key).
    """
    if _profile.loaded():
        return                          # already running from a profile — skip

    if not cli.confirm("Save this session as a reusable profile?", default=False):
        return

    name = cli.text_input("Profile name", default="my_profile").strip() or "my_profile"
    import re as _re
    safe_name = _re.sub(r"[^\w\-]", "_", name).strip("_") or "my_profile"
    _silent = cli.confirm(
        "Run without any prompts when using this profile? (adds interactive: false)",
        default=False,
    )

    def _strip_nones(d):
        if isinstance(d, dict):
            return {k: _strip_nones(v) for k, v in d.items() if v is not None}
        return d

    _style = {"source": last_run.get("style_source"), "key": last_run.get("style_key")}
    _render = {"format": last_run.get("output_format"), "xlsx_layout": last_run.get("xlsx_layout")}

    if function_name == "render":
        data = _strip_nones({
            "function":    "render",
            "interactive": False if _silent else None,
            "style":       _style,
            "render":      _render,
        })
    elif function_name == "compare":
        data = _strip_nones({
            "function":               "compare",
            "interactive":            False if _silent else None,
            "results_file":           last_run.get("results_file"),
            "compare_file":           last_run.get("compare_file"),
            "sql_comparison_enabled": last_run.get("sql_comparison_enabled"),
            "ci_improvement_enabled": last_run.get("ci_improvement_enabled"),
            "ci_file":                last_run.get("ci_file"),
            "render_enabled":         last_run.get("render_enabled"),
            "style":                  _style,
            "render":                 _render,
        })
    else:  # test — include everything
        data = _strip_nones({
            "function":               "test",
            "interactive":            False if _silent else None,
            "run_mode":               last_run.get("run_mode"),
            "prompts_source":         last_run.get("prompts_source"),
            "prompts_file":           last_run.get("prompts_file"),
            "on_sorry":               last_run.get("on_sorry"),
            "sorry_retries":          last_run.get("sorry_retries"),
            "mstr": {
                "base_url":   last_run.get("base_url"),
                "agent_id":   last_run.get("agent_id"),
                "project_id": last_run.get("project_id"),
                "login_mode": last_run.get("login_mode"),
                "username":   last_run.get("username"),
            },
            "compare_enabled":        last_run.get("compare_enabled"),
            "compare_file":           last_run.get("compare_file") if last_run.get("compare_enabled") else None,
            "sql_comparison_enabled": last_run.get("sql_comparison_enabled"),
            "ci_improvement_enabled": last_run.get("ci_improvement_enabled"),
            "ci_file":                last_run.get("ci_file"),
            "render_enabled":         last_run.get("render_enabled"),
            "style":                  _style,
            "render":                 _render,
        })

    dest = f"profiles/{safe_name}.yaml"
    try:
        _profile.save(dest, data)
        cli.success(f"Saved  →  profiles/{safe_name}.yaml")
        cli.info(   f"Run:      python main.py --profile profiles/{safe_name}.yaml")
    except Exception as e:
        cli.warn(f"Could not save profile: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Function A — Test
# ══════════════════════════════════════════════════════════════════════════════

def function_test(args=None):
    """
    a) Test parameters  — data mode, prompt source, environment, credentials.
    b) Compare?  (default: No)  → pick baseline file, pick comparison style.
    c) Render?   (default: No)  → pick output format, pick style
                                   (defaults to comparison style when b was Yes).
    """
    cli.section("Task: Test")
    cli.info("Runs all prompts and saves results. Comparison and rendering are optional.")

    # ── a) Test parameters ─────────────────────────────────────────────────────
    # Fast path disabled — use --profile profiles/name.yaml to re-run with saved settings
    # envelope = try_fast_path(args)
    # if envelope is None:
    envelope = None
    if envelope is None:
        data_mode = select_run_mode(args)
        cli.section("Running Prompts")
        envelope = run_prompts(data_mode, label="results", args=args)

    meta = envelope.get("meta", {})
    cli.section("Run Summary")
    cli.success(f"Prompts run:  {meta.get('totalPrompts', 0)}")
    cli.success(f"Successful:   {meta.get('successful', 0)}")
    if meta.get("errors"):
        cli.warn(f"Errors:       {meta.get('errors', 0)}")

    # ── b) Compare? ────────────────────────────────────────────────────────────
    comparison_style = None
    _cmp_default = _profile.get("compare_enabled")
    _cmp_default = _cmp_default if isinstance(_cmp_default, bool) else False
    _do_compare = cli.confirm("Compare these results against a baseline or gold standard?",
                              default=_cmp_default)
    last_run.set("compare_enabled", _do_compare)
    if not _do_compare:
        last_run.set("sql_comparison_enabled", False)
        last_run.set("ci_improvement_enabled", False)
        last_run.set("ci_file", None)          # string — strip when skipped
    last_run.save()
    if _do_compare:
        cli.section("Comparison — Select Target")
        if args and getattr(args, "baseline_file", None):
            second_path = Path(args.baseline_file)
            if not second_path.exists():
                raise FileNotFoundError(f"File not found: {second_path}")
            cli.success(f"Target: {second_path.name}")
            last_run.set("compare_file", str(second_path)); last_run.save()
        else:
            _prof_compare = _profile.get("compare_file") or ""
            if _prof_compare:
                second_path = Path(_prof_compare)
                if not second_path.exists():
                    raise FileNotFoundError(
                        f"Profile compare_file not found: {_prof_compare}"
                    )
                cli.info(f"Comparison target: {second_path.name}")
            else:
                second_path = _pick_comparison_file()

        cli.section("Comparison — Style")
        comparison_style = select_style(args)

        cli.section("Running Comparison")
        if second_path.suffix.lower() in (".xlsx", ".xls"):
            scored, ci_used = _do_expected_comparison(envelope, second_path, comparison_style)
            _ci_default = _profile.get("ci_improvement_enabled")
            _ci_default = _ci_default if isinstance(_ci_default, bool) else False
            _do_ci = cli.confirm("Improve Custom Instructions based on these results?",
                                 default=_ci_default)
            last_run.set("ci_improvement_enabled", _do_ci); last_run.save()
            if _do_ci:
                cli.section("CI Improvement")
                _do_ci_improvement(scored, ci_hint=ci_used)
        else:
            baseline  = load(str(second_path))
            base_meta = baseline.get("meta", {})
            cli.info(f"Baseline: {base_meta.get('runDate','?')[:10]}  "
                     f"({base_meta.get('mode','?')} mode)  "
                     f"{len(baseline.get('results', []))} prompts")
            _do_comparison(baseline, envelope, comparison_style)

    # ── c) Render? ─────────────────────────────────────────────────────────────
    _render_default = _profile.get("render_enabled")
    _render_default = _render_default if isinstance(_render_default, bool) else False
    _do_render = cli.confirm("Render an output for these results?", default=_render_default)
    last_run.set("render_enabled", _do_render)
    if not _do_render:
        last_run.set("output_format", None)
        last_run.set("xlsx_layout", None)
    last_run.save()
    if _do_render:
        cli.section("Rendering Output")
        fmt, layout = select_output_format(args)
        if comparison_style:
            cli.success(f"Style: {comparison_style['name']}  (inherited from comparison)")
            style = comparison_style
        else:
            style = select_style(args)
        _render_output(envelope, fmt, style, layout)

    _prompt_save_profile("test")
    cli.section("Done")
    cli.success("Test complete.")


# ══════════════════════════════════════════════════════════════════════════════
# Function B — Compare
# ══════════════════════════════════════════════════════════════════════════════

def function_compare(args=None):
    """
    a) Select current results file (JSON).
    b) Select comparison target — JSON for comparison to baseline, Excel for comparison to gold standard.
    c) Render?  (default: No)  → pick output format + style.
    """
    cli.section("Task: Compare")
    cli.info("Compares current results against a baseline JSON or an Excel gold standard.")

    # ── a) Current results (JSON) ──────────────────────────────────────────────
    cli.section("Step 1 of 2 — Current Results File")
    if args and getattr(args, "results_file", None):
        current = load(args.results_file)
        last_run.set("results_file", args.results_file); last_run.save()
        cli.success(f"Loaded: {args.results_file}")
    else:
        _prof_results = _profile.get("results_file") or ""
        if _prof_results:
            current = load(_prof_results)
            last_run.set("results_file", _prof_results); last_run.save()
            cli.info(f"Results file: {Path(_prof_results).name}")
        else:
            current = _pick_results_file("Choose current results file")

    curr_meta = current.get("meta", {})
    cli.info(f"Current: {curr_meta.get('runDate','?')[:10]}  "
             f"({curr_meta.get('mode','?')} mode)  "
             f"{len(current.get('results', []))} prompts")

    # ── b) Comparison target (JSON or Excel) ───────────────────────────────────
    cli.section("Step 2 of 2 — Comparison Target")
    if args and getattr(args, "baseline_file", None):
        second_path = Path(args.baseline_file)
        if not second_path.exists():
            raise FileNotFoundError(f"File not found: {second_path}")
        cli.success(f"Target: {second_path.name}")
        last_run.set("compare_file", str(second_path)); last_run.save()
    else:
        _prof_compare = _profile.get("compare_file") or ""
        if _prof_compare:
            second_path = Path(_prof_compare)
            if not second_path.exists():
                raise FileNotFoundError(
                    f"Profile compare_file not found: {_prof_compare}"
                )
            cli.info(f"Comparison target: {second_path.name}")
        else:
            second_path = _pick_comparison_file()

    cli.section("Comparison Style")
    comparison_style = select_style(args)

    cli.section("Running Comparison")
    if second_path.suffix.lower() in (".xlsx", ".xls"):
        scored, ci_used = _do_expected_comparison(current, second_path, comparison_style)
        _ci_default = _profile.get("ci_improvement_enabled")
        _ci_default = _ci_default if isinstance(_ci_default, bool) else False
        _do_ci = cli.confirm("Improve Custom Instructions based on these results?",
                             default=_ci_default)
        last_run.set("ci_improvement_enabled", _do_ci); last_run.save()
        if _do_ci:
            cli.section("CI Improvement")
            _do_ci_improvement(scored, ci_hint=ci_used)
    else:
        # JSON baseline — no SQL or CI steps; set booleans explicitly so profile is complete
        last_run.set("sql_comparison_enabled", False)
        last_run.set("ci_improvement_enabled", False)
        last_run.set("ci_file", None)
        last_run.save()
        baseline  = load(str(second_path))
        base_meta = baseline.get("meta", {})
        cli.info(f"Baseline: {base_meta.get('runDate','?')[:10]}  "
                 f"({base_meta.get('mode','?')} mode)  "
                 f"{len(baseline.get('results', []))} prompts")
        _do_comparison(baseline, current, comparison_style)

    # ── c) Render? ─────────────────────────────────────────────────────────────
    _render_default = _profile.get("render_enabled")
    _render_default = _render_default if isinstance(_render_default, bool) else False
    _do_render = cli.confirm("Also render an output for the current results?", default=_render_default)
    last_run.set("render_enabled", _do_render)
    if not _do_render:
        last_run.set("output_format", None)
        last_run.set("xlsx_layout", None)
    last_run.save()
    if _do_render:
        cli.section("Rendering Output")
        fmt, layout = select_output_format(args)
        cli.success(f"Style: {comparison_style['name']}  (inherited from comparison)")
        _render_output(current, fmt, comparison_style, layout)

    _prompt_save_profile("compare")
    cli.section("Done")
    cli.success("Compare complete.")


# ══════════════════════════════════════════════════════════════════════════════
# Function C — Render
# ══════════════════════════════════════════════════════════════════════════════

def function_render(args=None):
    """
    a) Render parameters — select results file, output format, style.
    Renders the output immediately. No agent calls are made.
    """
    cli.section("Task: Render")
    cli.info("Renders an existing results file without running new prompts.")

    # ── a) Render parameters ───────────────────────────────────────────────────
    if args and getattr(args, "results_file", None):
        envelope = load(args.results_file)
        cli.success(f"Loaded: {args.results_file}")
    else:
        _use_latest_default = _profile.get("render.use_latest")
        _use_latest_default = _use_latest_default if isinstance(_use_latest_default, bool) else True
        use_latest = cli.confirm("Use the most recent results file?", default=_use_latest_default)
        if use_latest:
            envelope = load_latest("results")
            if envelope is None:
                raise FileNotFoundError("No results file found. Run the Test function first.")
            cli.success("Loaded latest results.")
        else:
            envelope = _pick_results_file("Choose results file to render")

    fmt, layout = select_output_format(args)
    style       = select_style(args)

    cli.section("Rendering Output")
    _render_output(envelope, fmt, style, layout)

    _prompt_save_profile("render")
    cli.section("Done")
    cli.success("Render complete.")


