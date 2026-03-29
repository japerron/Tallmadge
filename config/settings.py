# ── Tallmadge Configuration ───────────────────────────────────────────────────
# This file contains deployment-level settings that apply to all runs.
# Per-run overrides belong in a profile YAML (profiles/*.yaml), not here.
# Restart Tallmadge after editing this file.

# ── Authentication ─────────────────────────────────────────────────────────────

# Default MicroStrategy login mode used on a fresh install (before any run has
# been saved to last_run.json) and as the fallback for MCP-Agent mode, which
# skips the connection config prompt.
# After the first interactive run the chosen mode is remembered automatically.
#
# 1       = Standard  (username + password)
# 16      = LDAP      (username + password, LDAP-authenticated)
# 4096    = API Token (token passed as username; password is empty)
LOGIN_MODE = 1

# ── MCP Connectors ─────────────────────────────────────────────────────────────

# Named MCP connectors shown in the data mode menu.
# Each key is the display name the user sees; the value is the full MCP
# endpoint URL for that connector.
# Add one entry per connector your deployment exposes.
MCP_CONNECTORS = {
    "ENV_Agent": "https://<env>.strategy.com/collaboration/mcp/agent",
    "ENV_Mosaic": "https://<env>.strategy.com/collaboration/mcp/mosaic",
}

# ── Results ────────────────────────────────────────────────────────────────────

# Number of timestamped results JSON files to keep in results/.
# The most recent file is always copied to results/results_latest.json
# regardless of this limit. Older files are pruned automatically.
MAX_RESULT_BACKUPS = 50

# ── Anthropic API Key ──────────────────────────────────────────────────────────

# Used for: prompt auto-categorisation, SQL comparison, CI improvement.
#
# Recommended: set the ANTHROPIC_API_KEY environment variable instead of
# storing the key here. Environment variables keep credentials out of files
# that may be shared or version-controlled.
#
#   CMD / Anaconda Prompt:  set ANTHROPIC_API_KEY=your_key
#   PowerShell:             $env:ANTHROPIC_API_KEY="your_key"
#
# If both are set, the environment variable takes precedence.
# Leave blank to be prompted at runtime.
ANTHROPIC_API_KEY = ""

# ── Prompts ────────────────────────────────────────────────────────────────────

# Built-in prompt list used when prompts_source is "settings" (the default).
# Each entry requires: id (int), category (str), prompt (str).
#
# Alternatively, load prompts from a text file at runtime by choosing
# "From file" at the prompts menu, or by setting prompts_source: file in a
# profile. One prompt per line; blank lines are ignored.
#
# Prefix a prompt with "<Follow-up>" to chain it to the previous prompt's
# conversation (shares the same conversationId). Follow-ups must immediately
# follow their parent prompt in this list.
PROMPTS = [
    {"id": 1, "category": "Example", "prompt": "Example prompt — replace with your own"},
    {"id": 2,  "category": "Promotion Performance Ranking",  "prompt": "Show me the top 10 promotions for Supermart in 2026 by incremental sales"},
    {"id": 3,  "category": "Trade Spend Analysis",  "prompt": "What was the total trade spend for coffee promotions at ACME this year?"},
    {"id": 4,  "category": "Product Performance",  "prompt": "How are promotions for kcup performing at SuperMart this year?"},

]

# ── Styles ────────────────────────────────────────────────────────────────────

# Saved brand styles available at the style selection prompt.
# Each key is the style's short name used in profile YAMLs (style.key).
# Required fields: name, primary, secondary, accent, font (all strings).
# Hex colours must include the # prefix.
# Optional field: highlight (used by some renderers for a fourth colour).
#
# Styles can also be created at runtime from a URL or a JSON file and saved
# back here without editing this file manually.
STYLES = {
    "Strategy": {"name": "Strategy", "primary": "#FA660F", "secondary": "#000000", "accent": "#ffffff", "font": "Calibri"},
    "corporate": {"name": "Corporate",  "primary": "#1E2761", "secondary": "#CADCFC", "accent": "#F96167",  "font": "Arial"},
    "minimal":   {"name": "Minimal",    "primary": "#36454F", "secondary": "#F2F2F2", "accent": "#028090",  "font": "Calibri"},
    "warm":      {"name": "Warm",       "primary": "#B85042", "secondary": "#E7E8D1", "accent": "#A7BEAE",  "font": "Georgia"},
}

# ── Output Formats ─────────────────────────────────────────────────────────────

# Output formats offered at the render step.
# Remove an entry to hide that format from the menu.
# Valid values: "pptx", "xlsx", "web"
OUTPUT_FORMATS = ["pptx", "xlsx", "web"]

# ── Web Renderer ───────────────────────────────────────────────────────────────

# Which web template version to use when format is "web".
# 1 = siomple renderer  (renderers/web_templates/v1/)
# 2 = editorial renderer  (renderers/web_templates/v2/)
WEB_VERSION = 2

# ── Debug ──────────────────────────────────────────────────────────────────────

# Set to True to write a debug Excel file (output/sql_debug_*.xlsx) after each
# SQL comparison run. The file shows the normalised SQL sent to Claude for each
# prompt, useful for diagnosing unexpected scoring results.
# Set to False for normal operation.
SQL_DEBUG = False
