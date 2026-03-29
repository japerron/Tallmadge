# Tallmadge CLI  v1.9

Python CLI for running, comparing and rendering MicroStrategy agent results.

---

## Setup

```bash
pip install pyyaml requests openpyxl python-pptx
pip install Pillow          # optional — required for image embedding in Excel
```

No `.env` file required. All credentials are prompted at runtime.

**Python:** 3.10 or later required.

---

## Running on Windows

Box-drawing characters in the banner require UTF-8 output encoding.
Set `PYTHONIOENCODING=utf-8` before running:

| Shell | Command |
|-------|---------|
| CMD / Anaconda Prompt | `set PYTHONIOENCODING=utf-8 && python main.py` |
| PowerShell | `$env:PYTHONIOENCODING="utf-8"; python main.py` |
| Git Bash | `PYTHONIOENCODING=utf-8 python main.py` |

---

## Usage

### Fully interactive (recommended for first run)
```bash
python main.py
```
Presents menus for task, data mode, prompts, connection config, output format and style.
At the end of the session, offers to save all choices as a reusable YAML profile.

### With a profile (recommended for repeat runs)
```bash
python main.py --profile profiles/my_profile.yaml
```
Pre-fills all prompts from the profile. Only pauses for credentials (unless set as env vars).
Set `interactive: false` in the profile to skip all prompts and run fully unattended.

### With flags (for one-off overrides)
```bash
# Test task — API mode
python main.py --function test --api

# Test task — load prompts from file
python main.py --function test --api --prompts-file my_prompts.txt

# Compare task — compare two existing results files
python main.py --function compare --baseline-file results/baseline.json --results-file results/current.json

# Render task — render an existing results file as PowerPoint
python main.py --function render --results-file results/results_latest.json --format pptx --style corporate
```

---

## Credentials

Two credentials can be pre-loaded via environment variables so you are not prompted
for them every run.

| Environment variable | Used for |
|---|---|
| `MSTR_PASSWORD` | MicroStrategy login |
| `ANTHROPIC_API_KEY` | Claude API calls — SQL comparison, CI improvement, prompt categorisation |

Set them in your shell before launching Tallmadge:

| Shell | Command |
|---|---|
| CMD / Anaconda Prompt | `set MSTR_PASSWORD=your_password` |
| PowerShell | `$env:MSTR_PASSWORD="your_password"` |

When a profile is loaded, Tallmadge warns at startup if any credentials are missing:
```
  ⚠  MSTR_PASSWORD not set — terminate this run, set the variable, then restart.
       CMD / Anaconda Prompt:  set MSTR_PASSWORD=your_value
       PowerShell:             $env:MSTR_PASSWORD="your_value"
```

---

## Tasks

There are three tasks with progressive disclosure — each optional sub-step is confirmed
before its parameters are collected. Tasks can be daisy-chained: Test → Compare → Render
in a single session.

| Task | Description |
|------|-------------|
| **Test** | Run prompts → save results; optionally compare to a baseline or gold standard; optionally render output |
| **Compare** | Compare two existing results files → save comparison Excel; optionally render current results |
| **Render** | Load an existing results file → render output (pptx / xlsx / web) |

### Test task flow
1. Choose data mode (API or MCP-Agent) and run prompts
2. *(optional)* Compare to a baseline (JSON) or gold standard (Excel) → picks file + style → saves report
3. *(optional)* Render output → picks format + style (comparison style offered as default if step 2 ran)

### Compare task flow
1. Pick current results file and comparison target (JSON → baseline / Excel → gold standard)
2. Pick style → run comparison → saves report
3. *(optional)* Render output for the current results

### Render task flow
1. Pick results file (or use most recent)
2. Pick output format → pick style → render

---

## Data Modes

| Mode | Transport | Fields captured |
|------|-----------|-----------------|
| **API** | REST API `/api/questions` (async poll) | `responseText`, `interpretedQuestion`, `insights`, `chartData`, `attributesUsed`*, `metricsUsed`* + `sqlQueries`, `explanation`, `answerType`, `isCacheUsed`, `attributeFormsUsed`, `datasetsUsed`, `gridData`†, `imagesData`‡ |
| **MCP-Agent** | MCP `ask_agent` JSON-RPC | `responseText`, `interpretedQuestion`, `insights`, `chartData`, `attributesUsed`*, `metricsUsed`* |

\* Inferred from chart column types when not returned directly.
† `gridData` — raw tabular data (list of dicts or `{columns, rows}` object).
‡ `imagesData` — rendered PNG chart images stored as base64 strings.

### API Mode — connection config

When API mode is selected, Tallmadge prompts for connection parameters
(defaults from last run or profile):

- **Base URL** — MicroStrategy Library URL
- **Agent ID** — 32-char hex GUID; validated at input
- **Project ID** — 32-char hex GUID; validated at input
- **Username / Password** — MicroStrategy credentials

### API Mode — rephrasing strategy

If the agent requests rephrasing, Tallmadge can:

| Option | Behaviour |
|--------|-----------|
| **Stop** | Log partial results and exit |
| **Continue** | Move on to the next prompt |
| **Resubmit** | Retry the prompt (0–3 attempts, with a delay) |

Per-prompt outcomes are shown as: `✓ Success` / `⚠ Agent requests rephrasing` / `✗ Error`

---

## Profile System

Profiles are YAML files that pre-answer every interactive prompt, enabling unattended runs.

### Generating a profile
Run interactively and answer **Yes** when asked *"Save this session as a reusable profile?"* at the end.
Profiles are saved to `profiles/` and contain only the fields relevant to the task that ran:

- **Render profile** — style + render format only
- **Compare profile** — comparison flags + style + render format
- **Test profile** — everything: connection config, prompts, rephrasing strategy, comparison and render flags

All boolean decisions (`compare_enabled`, `render_enabled`, etc.) are written explicitly — both `true` and `false` — so any option can be changed by editing the YAML.

Sensitive values (password, API keys) are **never written** to profiles. Use environment variables instead.

### Running with a profile
```
set PYTHONIOENCODING=utf-8 && python main.py --profile profiles/my_profile.yaml
```

### Unattended (fully automated) runs
Set `interactive: false` in the profile. All prompts are skipped; the stored values are used directly.
If a required field is missing, Tallmadge stops immediately with a clear error naming the missing field.
```yaml
interactive: false
```

### Environment variable substitution
Profile values can reference env vars:
```yaml
mstr:
  username: ${MSTR_USERNAME}
```

### Sample profiles
Three annotated starter profiles are included in `profiles/`:

| File | Starting point |
|---|---|
| `_sample_test.yaml` | Fresh test run — full connection config + optional compare + optional render |
| `_sample_compare.yaml` | Standalone compare — requires two input files |
| `_sample_render.yaml` | Standalone render — requires one results file |

### Profile field reference

| Field | Values | Notes |
|---|---|---|
| `function` | `test` / `compare` / `render` | Required — determines which task runs |
| `interactive` | `true` / `false` | `false` = fully unattended; omit for attended run with profile defaults |
| `run_mode` | `api` / `mcp-agent` / `mcp-mosaic` | Test task only |
| `prompts_source` | `settings` / `file` | Test task only |
| `prompts_file` | file path | Required when `prompts_source: file` |
| `on_sorry` | `stop` / `continue` / `resubmit` | What to do when the agent requests rephrasing |
| `sorry_retries` | `0`–`3` | Number of resubmit attempts; clamped to 3 if higher |
| `mstr.base_url` | URL | MicroStrategy Library base URL |
| `mstr.agent_id` | 32-char hex | MSTR Agent / Bot ID |
| `mstr.project_id` | 32-char hex | MSTR Project GUID |
| `mstr.login_mode` | `1` (Standard) / `16` (LDAP) / `4096` (API Token) | |
| `mstr.username` | string | |
| `compare_enabled` | `true` / `false` | Whether to run a comparison step |
| `compare_file` | file path | Baseline JSON or gold standard Excel to compare against |
| `results_file` | file path | Results JSON to load (Compare / Render tasks only) |
| `sql_comparison_enabled` | `true` / `false` | LLM SQL comparison; requires gold standard with SQL column |
| `ci_improvement_enabled` | `true` / `false` | Generate CI improvement suggestions via Claude |
| `ci_file` | file path | Path to the Custom Instructions file |
| `render_enabled` | `true` / `false` | Whether to render an output file |
| `style.source` | `saved` / `url` / `json` | Brand style source |
| `style.key` | string | Style name (when `source: saved`) |
| `render.format` | `pptx` / `xlsx` / `web` | Output format |
| `render.xlsx_layout` | `detail` / `wide` / `both` | Excel layout (xlsx only) |

Any value can reference an environment variable: `username: ${MSTR_USERNAME}`

### Sample test profile (fully unattended)
```yaml
function: test
interactive: false
run_mode: api
prompts_source: file
prompts_file: prompts/weekly.txt
on_sorry: resubmit
sorry_retries: 1

mstr:
  base_url: https://your-server.com/MicroStrategyLibrary
  agent_id: XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
  project_id: XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
  login_mode: 1
  username: your_username

compare_enabled: true
compare_file: standards/gold_standard.xlsx
sql_comparison_enabled: true
ci_file: standards/ci.txt
ci_improvement_enabled: false
render_enabled: true

style:
  source: saved
  key: corporate
render:
  format: xlsx
  xlsx_layout: detail
```

---

## Configuration Files

Three files control Tallmadge's behaviour. Edit them in a text editor; restart after changes.

### `config/settings.py` — deployment-level defaults

| Setting | What it controls |
|---|---|
| `LOGIN_MODE` | Default MSTR login mode for fresh installs and MCP-Agent mode |
| `MCP_CONNECTORS` | Named MCP connectors shown in the data mode menu |
| `ANTHROPIC_API_KEY` | API key — blank means prompted; prefer the `ANTHROPIC_API_KEY` env var |
| `PROMPTS` | Built-in prompt list (used when `prompts_source: settings`) |
| `STYLES` | Saved brand styles available at the style prompt |
| `OUTPUT_FORMATS` | Which output formats are offered (`pptx` / `xlsx` / `web`) |
| `WEB_VERSION` | Web renderer: `1` = stable, `2` = premium editorial |
| `MAX_RESULT_BACKUPS` | How many timestamped result JSON files to keep |
| `SQL_DEBUG` | `True` = write SQL normalisation debug Excel after each comparison |

Each field is documented inline in the file.

### `config/scoring.yaml` — scoring rates and Claude prompts

| Section | What it controls |
|---|---|
| `field_scoring.*` | Deduction rates + caps for WHERE tokens, attributes, metrics, rows, HAVING, Other Used |
| `sql_scoring.deduct_rates` | Per-category rates + caps for LLM SQL comparison (8 categories) |
| `sql_scoring.system_prompt` | Full Claude prompt for SQL comparison |
| `ci_advisor.max_chars` | Character cap on CI content sent to Claude |
| `ci_advisor.system_prompt` | Claude prompt for CI improvement suggestions |

> **Caution — SQL prompt labels:** The response-format labels (`MISSING_ATTRS:` etc.) in
> `sql_scoring.system_prompt` are parsed by regex in `core/sql_judge.py`. Renaming a label
> silently zeros that category's score. Change labels only if you also update the regex.

---

## Prompts

### From settings (default)
Defined in `config/settings.py` as a list of `{id, category, prompt}` dicts.

### From a text file
Pass `--prompts-file path/to/prompts.txt` or choose it interactively. One prompt per line; blank lines are ignored.

### Follow-up prompts (conversation threads)
Prefix a prompt with `<Follow-up>` to chain it to the previous prompt's conversation:

```
What were the top 10 promoted products last quarter?
<Follow-up> Break that down by region.
<Follow-up> Which region had the highest lift?
```

### Auto-categorisation (Claude API)
After loading from a file, Tallmadge can call the Claude API to automatically assign categories.
Set `ANTHROPIC_API_KEY` as an environment variable or leave blank to be prompted at runtime.

---

## Output Formats

### PowerPoint (`pptx`)
- One content slide per prompt: header bar, response text, interpretation, explanation, insights, SQL
- Right column: rendered PNG image (if available)
- Extra data slide appended when grid/tabular data is present

### Excel (`xlsx`)
When Excel is selected, Tallmadge asks for the **layout**:

| Layout | Sheets generated |
|--------|-----------------|
| **Detail** | Summary + one detail sheet per prompt |
| **Wide** | Summary + Wide View (one row per prompt, all fields as columns) + one data sheet per prompt with gridData |
| **Both** | Summary + Wide View + one detail sheet per prompt |

### Web (`web`)
- Index page with category filter and prompt cards
- Detail page per prompt with full field cards
- v1 (stable) or v2 (active, premium editorial) — controlled by `WEB_VERSION` in `config/settings.py`

---

## Styles

Styles are defined in `config/settings.py` under `STYLES`. Each style requires:
`name`, `primary`, `secondary`, `accent`, `font` (hex colours must include `#`).

### Custom styles
At the style selection prompt, choose from three sources:
1. **Pick from saved styles** — defined in `config/settings.py`
2. **Extract from URL** — fetches page CSS and lets you assign colours to roles
3. **Load from JSON file** — a `.json` with fields: `name`, `primary`, `secondary`, `accent`, `font`

After creating a custom style you can save it to `config/settings.py` for future runs.

---

## Results Files

Results are saved as timestamped JSON files in `results/`:

```
results/results_20260302T143000.json   ← timestamped snapshot
results/results_latest.json            ← always points to the most recent run
```

Up to `MAX_RESULT_BACKUPS` (default: 50) timestamped files are kept; older ones are pruned.

---

## Comparison Reports

### Comparison to Baseline (JSON vs JSON)
Compares two results envelopes field-by-field. Output: `tallmadge_baseline_YYYYMMDD_HHMMSS.xlsx`

### Comparison to Gold Standard (JSON vs Excel)
Scores current results against an Excel standard file. Output: `tallmadge_goldstandard_YYYYMMDD_HHMMSS.xlsx`

#### Standard file columns

| Column | Required | Description |
|--------|----------|-------------|
| Prompt | Yes | Exact prompt text (matched case-insensitively) |
| Category | No | Category label |
| WHERE | No | Expected WHERE clause tokens |
| Attributes | No | Expected attributes used |
| Metrics | No | Expected metrics used |
| Data Rows | No | Expected row count in grid data |
| SQL | No | Gold-standard SQL (enables SQL comparison) |

#### Score Card columns
`#` · `Category` · `Prompt` · `WHERE δ` · `Attrs δ` · `Metrics δ` · `Rows δ` · `Field Score` · `SQL Score` · `Notes`

---

## SQL Comparison (optional, Gold Standard only)

When the standard file contains a SQL column, Tallmadge offers an optional LLM-based SQL comparison step.

Score starts at 5.0; each category deducts independently; floored at 0.0.

| Category | Rate | Max issues | Max deduction |
|---|---|---|---|
| missing_attrs | 1.5 | 2 | 3.0 |
| extra_attrs | 1.0 | 2 | 2.0 |
| missing_metrics | 1.0 | 3 | 3.0 |
| major_filters | 1.5 | 2 | 3.0 |
| other_major | 0.5 | 2 | 1.0 |
| other | 0.5 | 2 | 1.0 |
| added_metrics | 0.25 | 2 | 0.5 |
| minor_filters | 0.25 | 2 | 0.5 |

> All rates are configurable in `config/scoring.yaml` — see [Configuration Files](#configuration-files) above.

---

## Project Structure

```
Tallmadge1.0/
├── main.py                        ← CLI entry point
├── requirements.txt
├── tallmadge_runsheet.pptx        ← Quick-reference slide (inputs + options)
├── tallmadge_runsheet.py          ← Generator script for the runsheet slide
├── tallmadge_navigation.drawio    ← Task flow diagram (draw.io)
├── config/
│   ├── settings.py                ← Deployment-level config (LOGIN_MODE, PROMPTS,
│   │                                 STYLES, MCP_CONNECTORS, etc.)
│   └── scoring.yaml               ← Scoring deduction rates + Claude system prompts
├── core/
│   ├── api.py                     ← REST API mode (async poll + image fetch)
│   ├── agent.py                   ← MCP-Agent mode
│   ├── ci_advisor.py              ← CI improvement via Claude API
│   ├── cli.py                     ← Interactive menus, ANSI colour output
│   ├── color.py                   ← hex_darken / hex_lighten utilities
│   ├── expected.py                ← load_standard(), score_results()
│   ├── last_run.py                ← Persist last interactive session to last_run.json
│   ├── profile.py                 ← YAML profile load/get/save; env var resolution
│   ├── scoring_config.py          ← Singleton loader for config/scoring.yaml
│   ├── prompts.py                 ← Text file loading, Claude categorisation
│   ├── results.py                 ← Schema, load/save, compare, format_sql
│   ├── runner.py                  ← Mode config, login, connector selection
│   ├── sql_judge.py               ← LLM-based SQL comparison
│   └── styles.py                  ← Style + format selection, URL CSS extraction
├── functions/
│   └── functions.py               ← function_test(), function_compare(), function_render()
├── renderers/
│   ├── comparison.py              ← Baseline comparison report
│   ├── expected_report.py         ← Gold standard scored report
│   ├── pptx.py                    ← PowerPoint generator
│   ├── web.py                     ← Routing shim (v1 / v2 per WEB_VERSION)
│   ├── xlsx.py                    ← Excel generator
│   └── web_templates/
│       ├── v1/renderer.py         ← Stable HTML renderer
│       └── v2/renderer.py         ← Premium editorial renderer (active)
├── profiles/                      ← YAML run profiles (generated at end of sessions)
│   ├── _sample_test.yaml          ← Annotated starter — Test task
│   ├── _sample_compare.yaml       ← Annotated starter — Compare task
│   └── _sample_render.yaml        ← Annotated starter — Render task
├── docs/
│   └── tallmadge_flow.md          ← Mermaid system flow diagram
├── results/                       ← Saved results JSON (auto-created)
└── output/                        ← Generated output files (auto-created)
```

---

## CLI Flags Reference

| Flag | Description |
|------|-------------|
| `--function` | `test` \| `compare` \| `render` — skip the task selection menu |
| `--profile` | Path to a YAML profile for unattended / repeat runs |
| `--api` | Force API data mode (Test task only) |
| `--mcp-agent` | Force MCP-Agent data mode (Test task only) |
| `--format` | `pptx` \| `xlsx` \| `web` |
| `--style` | Style key from `config/settings.py` |
| `--prompts-file` | Path to a `.txt` prompts file (Test task only) |
| `--results-file` | Path to a results JSON file (Compare / Render tasks) |
| `--baseline-file` | Path to a baseline or gold standard file (Test / Compare tasks) |

All flags are optional — omit any to be prompted interactively.
