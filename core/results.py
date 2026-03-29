"""
core/results.py — Results file management
Handles loading, saving, versioning and merging of results files.
"""

import re
import json
import os
import glob
import shutil
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(__file__).parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ── Schema ─────────────────────────────────────────────────────────────────────

def empty_result(prompt_cfg: dict) -> dict:
    """Return a blank result record for a given prompt config."""
    raw_prompt  = prompt_cfg["prompt"]
    is_followup = raw_prompt.startswith("<Follow-up>")
    clean_prompt = raw_prompt[len("<Follow-up>"):].strip() if is_followup else raw_prompt
    return {
        "id":             prompt_cfg["id"],
        "category":       prompt_cfg["category"],
        "prompt":         clean_prompt,    # stored without the prefix
        "rawPrompt":      raw_prompt,      # original with prefix intact
        "isFollowUp":     is_followup,
        "parentId":       None,            # set by runner after grouping
        "conversationId": None,            # set by runner (tracks thread)
        "status":         None,
        "error":          None,
        "responseTime":   None,
        "mode":           None,            # "mcp-agent" | "api"
        "isCacheUsed":    None,
        # ── MCP-Agent fields (MCP ask_agent) ──
        "responseText":     None,
        "interpretedQuestion": None,
        "insights":         None,
        "chartData":        None,
        # ── Inferred from chartData (both modes) ──
        "attributesUsed":   None,       # list[str]
        "metricsUsed":      None,       # list[str]
        # ── API-only (REST API) ──
        "answerType":       None,       # str — answer type from API (e.g. "data", "text")
        "sqlQueries":       None,       # list[str]
        "explanation":      None,       # str (from queries[0].explanation)
        "attributeFormsUsed": None,     # list[str]
        "datasetsUsed":     None,       # list[str]
        "gridData":         None,       # raw tabular data object from a.data
        "imagesData":       None,       # list[{id, width, height, data(base64 PNG)}]
        "whereClauseTokens": None,      # list[str] — tokens extracted from SQL WHERE clause(s)
    }


def make_envelope(mode: str, prompts_cfg: list) -> dict:
    """Create a fresh results envelope."""
    return {
        "meta": {
            "runDate":    datetime.now(timezone.utc).isoformat(),
            "mode":       mode,
            "totalPrompts": len(prompts_cfg),
            "successful": 0,
            "errors":     0,
        },
        "results": [empty_result(p) for p in prompts_cfg],
    }


# ── Persistence ────────────────────────────────────────────────────────────────

def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def save(data: dict, label: str = "results", max_backups: int = 50) -> Path:
    """
    Save results to a timestamped JSON file.
    Also writes/overwrites 'latest.json' symlink for convenience.
    Prunes old files beyond max_backups.
    Returns the path of the saved file.
    """
    filename = RESULTS_DIR / f"{label}_{_timestamp()}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Keep a stable 'latest' pointer
    latest = RESULTS_DIR / f"{label}_latest.json"
    shutil.copy(filename, latest)

    # Prune old files (keep max_backups + the one we just wrote)
    # Temporarily disabled — re-enable by uncommenting the block below
    # pattern = str(RESULTS_DIR / f"{label}_2*.json")  # timestamped only
    # old = sorted(glob.glob(pattern))
    # while len(old) > max_backups:
    #     os.remove(old.pop(0))

    return filename


def load(path: str) -> dict:
    """Load a results file from disk."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_latest(label: str = "results") -> dict | None:
    """Load the most recent results file, or None if none exist."""
    latest = RESULTS_DIR / f"{label}_latest.json"
    if latest.exists():
        return load(str(latest))
    # Fall back to newest timestamped file
    pattern = str(RESULTS_DIR / f"{label}_2*.json")
    files = sorted(glob.glob(pattern))
    if files:
        return load(files[-1])
    return None


def list_files(label: str = "results") -> list[Path]:
    """Return all results files sorted newest-first."""
    pattern = str(RESULTS_DIR / f"{label}_*.json")
    return sorted(
        (Path(p) for p in glob.glob(pattern)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


# ── Inference helpers ──────────────────────────────────────────────────────────

def infer_attributes_metrics(chart_data: dict | None) -> tuple[list[str], list[str]]:
    """
    Parse chartData columns to infer attributesUsed and metricsUsed.
    column type 1 = attribute, type 2 = metric.
    Returns (attributes, metrics) as lists of strings.
    """
    if not chart_data:
        return [], []

    attributes, metrics = [], []
    charts = chart_data if isinstance(chart_data, list) else [chart_data]

    for chart in charts:
        option = chart.get("option", {})
        columns = option.get("columns", [])
        for col in columns:
            name = col.get("column_name", "")
            ctype = col.get("type", 0)
            if ctype == 1:
                attributes.append(name)
            elif ctype == 2:
                metrics.append(name)

    return list(dict.fromkeys(attributes)), list(dict.fromkeys(metrics))  # dedupe, preserve order


# ── Comparison helpers ─────────────────────────────────────────────────────────

COMPARABLE_FIELDS = {
    "prompt": "Prompt",
}

# Fields compared with count-first, then order-insensitive element comparison
_SET_FIELDS = {
    "attributesUsed": "Attributes Used",
    "metricsUsed":    "Metrics Used",
}

# SQL normalisation regexes (used by _normalise_sql)
_SQL_FROM_RE = re.compile(
    r'\b((?:(?:LEFT|RIGHT|INNER|CROSS|FULL)\s+)?(?:OUTER\s+)?JOIN|FROM)\s+\w+(?:\s+(?:AS\s+)?\w+)?',
    re.IGNORECASE,
)
_SQL_QUAL_RE = re.compile(r'\b\w+\.',  re.ASCII)   # "alias." or "Table." qualifiers
_SQL_NORM_RE = re.compile(r'[\s_\-()\[\]]')         # spaces, underscores, dashes, parens, brackets


# WHERE clause expression extraction
_WHERE_CLAUSE_RE = re.compile(
    r'\bWHERE\b(.*?)(?=\b(?:GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|OFFSET|UNION)\b|$)',
    re.IGNORECASE | re.DOTALL,
)
# HAVING clause expression extraction (stops before ORDER BY / LIMIT / OFFSET / UNION)
_HAVING_CLAUSE_RE = re.compile(
    r'\bHAVING\b(.*?)(?=\b(?:ORDER\s+BY|LIMIT|OFFSET|UNION)\b|$)',
    re.IGNORECASE | re.DOTALL,
)
_WHERE_LIT_RE   = re.compile(r"'[^']*'")           # string literals inside WHERE/HAVING
_WHERE_SPLIT_RE = re.compile(r'\bAND\b|\bOR\b', re.IGNORECASE)


def _truncate_at_paren_close(text: str) -> str:
    """
    Truncate text at the first closing parenthesis that has no matching open
    paren within the text.  Handles CTEs and subqueries where the WHERE clause
    is inside an enclosing (...) block — the unmatched ')' marks the end of
    that block, not the end of the WHERE conditions.
    String literals are *not* scanned (they cannot contain unbalanced parens
    in well-formed SQL), so the scan is intentionally simple.
    """
    depth = 0
    for i, ch in enumerate(text):
        if ch == '(':
            depth += 1
        elif ch == ')':
            if depth == 0:
                return text[:i]
            depth -= 1
    return text

# WHERE expression normalisation (used by compare(); originals are always stored/displayed)

def _strip_unbalanced_parens(s: str) -> str:
    """
    Strip unmatched leading '(' or trailing ')' that result from splitting a
    parenthesised OR/AND group on conjunctions without respecting paren depth.

    e.g. "(col LIKE '%x%'"   → "col LIKE '%x%'"   (leading '(' has no matching ')')
         "col LIKE '%y%'))"  → "col LIKE '%y%'"   (trailing ')' has no matching '(')

    Correctly-balanced expressions (IN (a,b), LOWER(col)) are left untouched.
    """
    s = s.strip()
    depth = sum(1 if c == '(' else -1 if c == ')' else 0 for c in s)
    while depth > 0 and s.startswith('('):
        s = s[1:].strip()
        depth -= 1
    while depth < 0 and s.endswith(')'):
        s = s[:-1].strip()
        depth += 1
    return s


def _strip_outer_parens(s: str) -> str:
    """
    Strip matching outer parentheses that wrap the entire expression.
    e.g. "(Year = 2026)" → "Year = 2026"
    Leaves inner parens intact, e.g. "Year IN (2025, 2026)" is unchanged.
    """
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        wrapped = False
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if depth == 0:
                wrapped = (i == len(s) - 1)
                break
        if wrapped:
            s = s[1:-1].strip()
        else:
            break
    return s


# Column pattern: handles LOWER("quoted col"), "quoted col", LOWER(word), word
_WHERE_COL_PAT = (
    r'(LOWER\s*\(\s*(?:"[^"]*"|\w+)\s*\)'   # LOWER("quoted") or LOWER(word)
    r'|"[^"]*"'                               # "quoted col with spaces"
    r'|\S+)'                                  # fallback: any non-whitespace sequence
)
_WHERE_EXPR_RE = re.compile(
    _WHERE_COL_PAT + r'\s*'
    r'(!=|<>|>=|<=|=|>|<'
    r'|NOT\s+LIKE|NOT\s+IN|NOT\s+BETWEEN'
    r'|LIKE|IN|BETWEEN'
    r'|IS\s+NOT\s+NULL|IS\s+NULL|IS\s+NOT|IS'
    r')\s*(.*)',
    re.IGNORECASE | re.DOTALL,
)
_WHERE_COL_NORM_RE   = re.compile(r'[\s_\-()\[\]]')          # noise chars stripped from col names
_WHERE_PAREN_CONT_RE = re.compile(r'\s*\([^)]*\)')            # strip (parenthetical content)
_WHERE_LOWER_WRAP_RE = re.compile(r'LOWER\s*\(\s*(.*)\s*\)\s*$', re.IGNORECASE | re.DOTALL)
_WHERE_QUOTED_RE     = re.compile(r'^"([^"]*)"$')             # strip outer double-quotes
_IN_SINGLE_RE        = re.compile(r"^\(\s*('[^']*'|\d+(?:\.\d+)?)\s*\)$")  # IN ('x') or IN (n)


def _norm_where_col(raw: str) -> str:
    """Normalise a WHERE clause column name for fuzzy matching.
    Steps:
      1. Strip LOWER() wrapper if present
      2. Strip outer double-quotes
      3. Strip parenthetical content  e.g. '(internal name)'
      4. Take the display name before the first '__' (MicroStrategy hierarchy separator)
      5. Lowercase + remove noise chars (spaces, underscores, hyphens, parens, brackets)
    """
    col = raw.strip()
    m = _WHERE_LOWER_WRAP_RE.match(col)
    if m:
        col = m.group(1).strip()                     # step 1: strip LOWER()
    m = _WHERE_QUOTED_RE.match(col)
    if m:
        col = m.group(1)                             # step 2: strip outer quotes
    col = _WHERE_PAREN_CONT_RE.sub('', col)          # step 3: strip (parenthetical)
    if '__' in col:
        col = col.split('__', 1)[0]                  # step 4: before first __
    return _WHERE_COL_NORM_RE.sub('', col.lower())   # step 5: lowercase + noise


def _normalise_where_expr(expr: str) -> str:
    """
    Normalise a single WHERE expression for fuzzy comparison.
      - Column:   strip LOWER() wrapper, double-quotes, parenthetical content,
                  __ hierarchy suffix; lowercase + remove noise chars
      - Operator: normalised to uppercase; <> → !=
      - Value:    strip LOWER() when wrapping a literal; always lowercased
                  (matches sql_judge.py pre-normalisation)
      - IN ('x') / IN (n): converted to = 'x' / = n (single-value IN → equality)
    Falls back to fully-lowercased string if expression is not parseable.
    """
    expr = re.sub(r'^\s*WHERE\s+', '', expr.strip(), flags=re.IGNORECASE)
    m = _WHERE_EXPR_RE.match(expr.strip())
    if not m:
        return expr.lower().strip()
    col_raw, op_raw, val_raw = m.group(1), m.group(2), m.group(3).strip()
    col = re.sub(r'^\w+\.', '', col_raw)        # strip "t." / "alias." table prefix
    col = _norm_where_col(col)
    op  = ' '.join(op_raw.split()).upper()
    if op == '<>':
        op = '!='                               # normalise <> to !=
    # Strip LOWER() from value when it wraps a string literal
    vm = _WHERE_LOWER_WRAP_RE.match(val_raw)
    if vm:
        val_raw = vm.group(1).strip()
    # Single-value IN → equality  e.g. IN ('Smith') → = 'Smith', IN (42) → = 42
    if op == 'IN':
        sm = _IN_SINGLE_RE.match(val_raw)
        if sm:
            op      = '='
            val_raw = sm.group(1)
    val = val_raw.lower()                       # always lowercase (mirrors sql_judge pre-normalisation)
    return f"{col} {op} {val}"


def extract_where_tokens(sql_queries) -> list[str] | None:
    """
    Extract WHERE clause conditions as individual expressions, split on AND/OR.
    String literals are masked before splitting so AND/OR inside quoted values are
    not treated as conjunctions. Table qualifiers and original casing are preserved.
    Deduplicates across multiple SQL strings, preserves order.
    Returns None when no WHERE clause is found or input is empty.
    """
    if not sql_queries:
        return None
    queries = sql_queries if isinstance(sql_queries, list) else [sql_queries]
    expressions = []
    for sql in queries:
        m = _WHERE_CLAUSE_RE.search(str(sql))
        if not m:
            continue
        clause = _truncate_at_paren_close(m.group(1).strip())
        # Mask string literals so AND/OR inside them don't cause false splits
        literals: dict[str, str] = {}
        def _mask(match: re.Match) -> str:
            key = f"__LIT{len(literals)}__"
            literals[key] = match.group(0)
            return key
        masked = _WHERE_LIT_RE.sub(_mask, clause)
        # Split on AND / OR conjunctions
        for part in _WHERE_SPLIT_RE.split(masked):
            expr = part
            for key, val in literals.items():
                expr = expr.replace(key, val)
            expr = _strip_unbalanced_parens(" ".join(expr.split()))
            if expr:
                expressions.append(expr)
    return list(dict.fromkeys(expressions)) if expressions else None


def extract_having_tokens(sql_queries) -> list[str] | None:
    """
    Extract HAVING clause conditions as individual expressions, split on AND/OR.
    Mirrors extract_where_tokens() exactly but targets the HAVING clause.
    String literals are masked before splitting. Deduplicates, preserves order.
    Returns None when no HAVING clause is found or input is empty.
    """
    if not sql_queries:
        return None
    queries = sql_queries if isinstance(sql_queries, list) else [sql_queries]
    expressions = []
    for sql in queries:
        m = _HAVING_CLAUSE_RE.search(str(sql))
        if not m:
            continue
        clause = _truncate_at_paren_close(m.group(1).strip())
        literals: dict[str, str] = {}
        def _mask(match: re.Match) -> str:
            key = f"__LIT{len(literals)}__"
            literals[key] = match.group(0)
            return key
        masked = _WHERE_LIT_RE.sub(_mask, clause)
        for part in _WHERE_SPLIT_RE.split(masked):
            expr = part
            for key, val in literals.items():
                expr = expr.replace(key, val)
            expr = _strip_unbalanced_parens(" ".join(expr.split()))
            if expr:
                expressions.append(expr)
    return list(dict.fromkeys(expressions)) if expressions else None


def _normalise_sql(sql: str) -> str:
    """
    Normalise a SQL string for fuzzy comparison:
      - Drop table names after FROM/JOIN keywords (keep the keyword itself)
      - Strip table/alias qualifiers on column references (e.g. 't.Column' → 'Column')
      - Lowercase and remove spaces, underscores, dashes, parentheses
    Original strings are preserved for display; only normalised forms are compared.
    """
    if not sql:
        return ""
    s = str(sql)
    s = _SQL_FROM_RE.sub(lambda m: m.group(1), s)  # keep keyword, drop table name + alias
    s = _SQL_QUAL_RE.sub("", s)                     # drop "table." qualifiers
    s = _SQL_NORM_RE.sub("", s.lower())             # lowercase + strip noise chars
    return s


def compare(baseline: dict, current: dict) -> list[dict]:
    """
    Compare two results envelopes and return a list of difference records.
    Each record: {id, category, prompt, field, baseline_val, current_val, change_type}
    change_type: "changed" | "added" | "removed" | "new_prompt" | "missing_prompt"
    """
    diffs = []

    base_map = {r["id"]: r for r in baseline.get("results", [])}
    curr_map = {r["id"]: r for r in current.get("results", [])}

    all_ids = sorted(set(base_map) | set(curr_map))

    for pid in all_ids:
        b = base_map.get(pid)
        c = curr_map.get(pid)

        if b is None:
            diffs.append({"id": pid, "category": c["category"], "prompt": c["prompt"],
                          "field": "*", "baseline_val": None, "current_val": "*",
                          "change_type": "new_prompt"})
            continue
        if c is None:
            diffs.append({"id": pid, "category": b["category"], "prompt": b["prompt"],
                          "field": "*", "baseline_val": "*", "current_val": None,
                          "change_type": "missing_prompt"})
            continue

        # Compare field by field
        for field, label in COMPARABLE_FIELDS.items():
            bv = b.get(field)
            cv = c.get(field)
            if bv == cv:
                continue
            if bv is None:
                ct = "added"
            elif cv is None:
                ct = "removed"
            else:
                ct = "changed"
            diffs.append({
                "id":           pid,
                "category":     c["category"],
                "prompt":       c["prompt"],
                "field":        label,
                "field_key":    field,
                "baseline_val": bv,
                "current_val":  cv,
                "change_type":  ct,
            })

        # Attributes / Metrics — count first, then order-insensitive element comparison
        for field, label in _SET_FIELDS.items():
            bv = b.get(field)
            cv = c.get(field)
            if bv == cv:
                continue
            if bv is None:
                ct = "added"
                _flag_set = True
            elif cv is None:
                ct = "removed"
                _flag_set = True
            else:
                ct = "changed"
                b_list = list(bv) if isinstance(bv, list) else []
                c_list = list(cv) if isinstance(cv, list) else []
                # Normalise before comparison: lowercase + strip (mirrors JSON/Excel behaviour)
                b_norm = {v.strip().lower() for v in b_list}
                c_norm = {v.strip().lower() for v in c_list}
                if len(b_norm) != len(c_norm):
                    _flag_set = True   # counts differ
                else:
                    _flag_set = b_norm != c_norm  # same count, different elements
            if _flag_set:
                diffs.append({
                    "id":           pid,
                    "category":     c["category"],
                    "prompt":       c["prompt"],
                    "field":        label,
                    "field_key":    field,
                    "baseline_val": bv,
                    "current_val":  cv,
                    "change_type":  ct,
                })

        # WHERE tokens — count first, then set comparison with expression normalisation
        bwt = b.get("whereClauseTokens")
        cwt = c.get("whereClauseTokens")
        if bwt != cwt:
            if bwt is None:
                ct = "added"
                _flag_wt = True
            elif cwt is None:
                ct = "removed"
                _flag_wt = True
            else:
                ct = "changed"
                if len(bwt) != len(cwt):
                    _flag_wt = True   # counts differ
                else:
                    b_norm = {_normalise_where_expr(_strip_outer_parens(e)) for e in bwt}
                    c_norm = {_normalise_where_expr(_strip_outer_parens(e)) for e in cwt}
                    _flag_wt = (b_norm != c_norm)
            if _flag_wt:
                diffs.append({
                    "id":           pid,
                    "category":     c["category"],
                    "prompt":       c["prompt"],
                    "field":        "WHERE Tokens",
                    "field_key":    "whereClauseTokens",
                    "baseline_val": bwt,
                    "current_val":  cwt,
                    "change_type":  ct,
                })

        # SQL comparison — ignore table names, normalise column names before comparing;
        # report original values in the diff
        bsql = b.get("sqlQueries")
        csql = c.get("sqlQueries")
        if bsql != csql:
            if bsql is None:
                ct = "added"
                _flag_sql = True
            elif csql is None:
                ct = "removed"
                _flag_sql = True
            else:
                ct = "changed"
                b_norm = [_normalise_sql(q) for q in bsql] if isinstance(bsql, list) else _normalise_sql(bsql)
                c_norm = [_normalise_sql(q) for q in csql] if isinstance(csql, list) else _normalise_sql(csql)
                _flag_sql = (b_norm != c_norm)
            if _flag_sql:
                diffs.append({
                    "id":           pid,
                    "category":     c["category"],
                    "prompt":       c["prompt"],
                    "field":        "SQL",
                    "field_key":    "sqlQueries",
                    "baseline_val": bsql,
                    "current_val":  csql,
                    "change_type":  ct,
                })

        # Chart data numeric comparison
        bc = b.get("chartData")
        cc = c.get("chartData")
        if bc != cc:
            diffs.append({
                "id":           pid,
                "category":     c["category"],
                "prompt":       c["prompt"],
                "field":        "Chart Data / Numbers",
                "field_key":    "chartData",
                "baseline_val": _summarise_chart(bc),
                "current_val":  _summarise_chart(cc),
                "change_type":  "changed",
            })

        # Grid data comparison
        bg = b.get("gridData")
        cg = c.get("gridData")
        b_count = len(bg) if isinstance(bg, list) else 0
        c_count = len(cg) if isinstance(cg, list) else 0

        if b_count != c_count:
            if b_count == 0:
                ct = "added"
            elif c_count == 0:
                ct = "removed"
            else:
                ct = "changed"
            diffs.append({
                "id":           pid,
                "category":     c["category"],
                "prompt":       c["prompt"],
                "field":        "Data Rows",
                "field_key":    "gridData",
                "baseline_val": f"{b_count} rows",
                "current_val":  f"{c_count} rows",
                "change_type":  ct,
            })

        # When both have data, compare headers and first 10 rows
        if b_count > 0 and c_count > 0:
            b_headers, b_rows = parse_grid_data(bg)
            c_headers, c_rows = parse_grid_data(cg)

            _headers_differ = (
                len(b_headers) != len(c_headers) or
                set(b_headers) != set(c_headers)
            )
            if _headers_differ:
                diffs.append({
                    "id":           pid,
                    "category":     c["category"],
                    "prompt":       c["prompt"],
                    "field":        "Data Headers",
                    "field_key":    "gridData_headers",
                    "baseline_val": b_headers,
                    "current_val":  c_headers,
                    "change_type":  "changed",
                })

            b_sample = b_rows[:10]
            c_sample = c_rows[:10]
            if b_sample != c_sample:
                diffs.append({
                    "id":           pid,
                    "category":     c["category"],
                    "prompt":       c["prompt"],
                    "field":        "Data Sample (10 rows)",
                    "field_key":    "gridData_sample",
                    "baseline_val": b_sample,
                    "current_val":  c_sample,
                    "change_type":  "changed",
                })

        # Response time comparison — tolerate ±5% difference when both values are numeric
        brt = b.get("responseTime")
        crt = c.get("responseTime")
        if brt != crt:
            if brt is None:
                ct = "added"
                _flag_rt = True
            elif crt is None:
                ct = "removed"
                _flag_rt = True
            else:
                try:
                    _flag_rt = abs(float(brt) - float(crt)) / float(brt) > 0.05
                except (TypeError, ZeroDivisionError):
                    _flag_rt = True
                ct = "changed"
            if _flag_rt:
                diffs.append({
                    "id":           pid,
                    "category":     c["category"],
                    "prompt":       c["prompt"],
                    "field":        "Response Time",
                    "field_key":    "responseTime",
                    "baseline_val": brt,
                    "current_val":  crt,
                    "change_type":  ct,
                })

    return diffs


def _summarise_chart(chart_data) -> str:
    """Return a short string summarising chart data for diff display."""
    if not chart_data:
        return "(none)"
    if isinstance(chart_data, dict):
        rows = chart_data.get("rows", [])
        return f"{len(rows)} rows"
    return str(chart_data)[:80]


# ── SQL / text formatters ─────────────────────────────────────────────────────

_SQL_CLAUSES = [
    "WITH", "SELECT", "FROM",
    "LEFT OUTER JOIN", "RIGHT OUTER JOIN", "FULL OUTER JOIN",
    "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "CROSS JOIN", "JOIN",
    "WHERE", "GROUP BY", "HAVING", "ORDER BY",
    "UNION ALL", "UNION",
    "LIMIT", "OFFSET",
    "INSERT INTO", "VALUES", "UPDATE", "SET", "DELETE FROM",
]
_SQL_PATTERN = re.compile(
    r" (" + "|".join(re.escape(k) for k in sorted(_SQL_CLAUSES, key=len, reverse=True)) + r")(?=[ \n(]|$)",
    flags=re.IGNORECASE,
)


def format_sql(sql: str | None) -> str:
    """
    Insert newlines before major SQL clause keywords for readability.
    No-op if the SQL is already multi-line or absent.
    """
    if not sql:
        return ""
    sql = str(sql).strip()
    if "\n" in sql:
        return sql          # already structured
    return _SQL_PATTERN.sub(r"\n\1", sql).strip()


def strip_markdown(text: str | None) -> str:
    """
    Remove common Markdown syntax for plain-text renderers (xlsx, pptx).
    Converts headers to a preceding blank line + text; removes bold/italic/code markers.
    Preserves existing newlines.
    """
    if not text:
        return ""
    text = str(text)
    # #### Heading  →  \nHeading
    text = re.sub(r"#{1,6}\s*", "\n", text)
    # **bold** or __bold__  →  bold
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__",     r"\1", text, flags=re.DOTALL)
    # *italic* or _italic_  →  italic
    text = re.sub(r"\*(.+?)\*",     r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_(.+?)_",       r"\1", text, flags=re.DOTALL)
    # `code`  →  code
    text = re.sub(r"`(.+?)`",       r"\1", text)
    # Collapse 3+ consecutive blank lines  →  2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Grid data normaliser ───────────────────────────────────────────────────────

def parse_grid_data(grid_data) -> tuple[list[str], list[list]]:
    """
    Normalise the raw gridData (a.data from API) into (headers, rows).
    Handles two common formats:
      • List of dicts  → [{col: val, ...}, ...]
      • Keyed dict     → {"columns": [...], "rows": [[...], ...]}
    Returns ([], []) when the data is absent or unrecognisable.
    """
    if not grid_data:
        return [], []

    # Format 1 — list of dicts
    if isinstance(grid_data, list) and grid_data and isinstance(grid_data[0], dict):
        headers = list(grid_data[0].keys())
        rows    = [[row.get(h, "") for h in headers] for row in grid_data]
        return headers, rows

    # Format 2 — {columns, rows}
    if isinstance(grid_data, dict):
        columns = grid_data.get("columns", [])
        rows    = grid_data.get("rows")
        if columns and rows is not None:
            headers = [c if isinstance(c, str) else c.get("name", str(c)) for c in columns]
            if rows and isinstance(rows[0], dict):
                data_rows = [[row.get(h, "") for h in headers] for row in rows]
            else:
                data_rows = rows
            return headers, data_rows

    return [], []


# ── Conversation grouping ──────────────────────────────────────────────────────

def build_conversation_groups(prompts_cfg: list) -> list[dict]:
    """
    Group prompts into conversation threads.
    A root prompt starts a new group; any prompt whose text starts with
    "<Follow-up>" is attached to the most recent root.

    Returns a list of groups:
      { "root": prompt_cfg, "children": [prompt_cfg, ...] }

    Also sets parentId on each child prompt_cfg in-place.
    """
    groups = []
    current_group = None

    for p in prompts_cfg:
        is_followup = p["prompt"].startswith("<Follow-up>")
        if not is_followup:
            current_group = {"root": p, "children": []}
            groups.append(current_group)
        else:
            if current_group is None:
                raise ValueError(
                    f"Prompt id={p['id']} is a <Follow-up> but has no preceding root prompt."
                )
            p["_parentId"] = current_group["root"]["id"]
            current_group["children"].append(p)

    return groups
