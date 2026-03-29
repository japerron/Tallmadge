"""
core/expected.py — Expected-values comparison

Loads an Excel 'standard' file and scores a results envelope against it.

─── Field Scoring Rules (per prompt, starting from SCORE_MAX = 5.0) ──────────

  WHERE Tokens  — compared against "WHERE Tokens" column (normalised fuzzy match):
                    -1.5 / major missing token, cap 2  → max -3.0
                      Major: column absent from actual, OR same column but different
                             value with a non-cosmetic op (e.g. = vs = with wrong value).
                    -1.5 / extra token,          cap 2  → max -3.0
                      Extra: token in actual not present in gold standard.
                    -0.25 / minor difference,    cap 2  → max -0.5  (waived when major > 0)
                      Minor: same column, operator changed between LIKE-family and =
                             (e.g. LIKE '%x%' vs = 'X Inc.').
                  Extra tokens whose column matches an Optional Attribute are exempt.

  Attributes    — compared against "Attributes Used" column:
                    -1.5 / missing item,  cap 2  → max -3.0
                    -1.0 / extra item,    cap 2  → max -2.0
                  Items in "Optional Attributes" column carry zero penalty when missing or extra.
                  Matching is exact normalised name (_norm_col).

  Metrics       — compared against "Metrics Used" column:
                    -1.0 / missing item,  cap 3  → max -3.0
                    -0.25 / extra item,   cap 2  → max -0.5
                  Items in "Optional Metrics" column carry zero penalty when missing or extra.
                  Matching is exact normalised name (_norm_col).

  HAVING Tokens — compared against "Having" column (same rules as WHERE Tokens):
                    -1.5 / major missing token, cap 2  → max -3.0
                    -1.5 / extra token,          cap 2  → max -3.0
                    -0.25 / minor difference,    cap 2  → max -0.5  (waived when major > 0)
                  Extracted from sqlQueries[0] at score time via extract_having_tokens().
                  Skipped when "Having" column is absent from standard file.

  Other Used    — compared against "Other Used" column (substring / adjacency check vs SQL):
                    -0.5 / missing or different token, cap 2  → max -1.0
                  Simple tokens (no quotes): normalised substring match in SQL.
                  Quoted tokens e.g. 'ORDER BY "PTE" DESC': unquoted parts must be
                    adjacent and the quoted text must be a substring of the matched word.
                  Items in "Optional Other" carry zero penalty whether present or absent.
                  No extra penalty — extras cannot be enumerated from a substring check.
                  Skipped when "Other Used" column is absent from standard file.

  Data Rows     — -1.0 if actual row count ≠ expected count (exact integer match)
                  Skipped when "Data Rows" cell is blank.

  SQL Score     — optional LLM comparison via core/sql_judge.py (separate 0–5 scale);
                  does NOT modify the field score.

  Prompt text   — highlight only; no deduction applied.

  Blank standard cell → field skipped entirely (no deduction).
  Absent column       → field skipped entirely for all rows.

─── Column normalisation (_norm_col) ─────────────────────────────────────────
  1. Strip parenthetical content  e.g. "(internal name)" removed
  2. Take display name before first '__'  (MicroStrategy DisplayName__InternalName)
  3. Lowercase + remove spaces, underscores, hyphens, parentheses, square brackets
  Examples: "Sales Amount", "sales_amount", "SalesAmount" all → "salesamount"
            "Customer__CustomerID"                           → "customer"

─── Standard Excel columns ───────────────────────────────────────────────────
  Required : Prompt, WHERE Tokens, Attributes Used, Metrics Used
  Optional : Data Rows, SQL, Optional Attributes, Optional Metrics,
             Having, Other Used, Optional Other

Final score is floored at 0.0.
Prompts with no match in the standard are scored None (unmatched).
"""

import math
import re
from pathlib import Path

import pandas as pd

from core.results import (                                             # reuse shared helpers
    _normalise_where_expr, _strip_outer_parens, extract_having_tokens,
)

# ── Column-name normalisation ───────────────────────────────────────────────────
_PAREN_CONTENT_RE = re.compile(r'\s*\([^)]*\)')   # "(content)" including leading whitespace
_COL_NOISE_RE     = re.compile(r'[\s_()\-\[\]]')

def _norm_col(s: str) -> str:
    """Normalise a column name for comparison.

    Steps applied in order:
      1. Strip parenthetical content entirely — MicroStrategy column names often carry an
         internal alias in parens, e.g. 'Promotion In-Store Start Date (promotion in store
         start date)'; the parenthetical is removed before any other processing.
      2. For MicroStrategy double-underscore patterns (DisplayName__InternalName),
         keep only the display name before the first '__'.
      3. Lowercase + remove spaces, underscores, hyphens, parentheses, square brackets.

    Examples that all normalise to the same key:
      'Sales Amount'  →  'salesamount'
      'sales_amount'  →  'salesamount'
      'Customer Level 6 Planning Account__Customer Level 6 Planning Account'  →  'customerlevel6planningaccount'
      'customer level 6 planning account (customer level 6 planning account name)'  →  'customerlevel6planningaccount'
    """
    s = _PAREN_CONTENT_RE.sub('', s)      # step 1: strip "(content)"
    if '__' in s:
        s = s.split('__', 1)[0]           # step 2: keep display name before first __
    return _COL_NOISE_RE.sub('', s.lower())  # step 3: lowercase + remove noise chars


# ── Constants (loaded from config/scoring.yaml) ────────────────────────────────

from core import scoring_config as _sc
_fs = _sc.load()["field_scoring"]

SCORE_MAX             = _fs["score_max"]

DEDUCT_WHERE_MAJOR    = _fs["where"]["major_rate"]
DEDUCT_WHERE_MINOR    = _fs["where"]["minor_rate"]
DEDUCT_WHERE_EXTRA    = _fs["where"]["extra_rate"]
WHERE_MAJOR_CAP       = _fs["where"]["major_cap"]
WHERE_MINOR_CAP       = _fs["where"]["minor_cap"]
WHERE_EXTRA_CAP       = _fs["where"]["extra_cap"]

DEDUCT_MISSING_ATTR   = _fs["attributes"]["missing_rate"]
MISSING_ATTR_CAP      = _fs["attributes"]["missing_cap"]
DEDUCT_EXTRA_ATTR     = _fs["attributes"]["extra_rate"]
EXTRA_ATTR_CAP        = _fs["attributes"]["extra_cap"]

DEDUCT_MISSING_METRIC = _fs["metrics"]["missing_rate"]
MISSING_METRIC_CAP    = _fs["metrics"]["missing_cap"]
DEDUCT_EXTRA_METRICS  = _fs["metrics"]["extra_rate"]
EXTRA_METRICS_CAP     = _fs["metrics"]["extra_cap"]

DEDUCT_ROWS           = _fs["rows"]["deduction"]

DEDUCT_HAVING_MAJOR   = _fs["having"]["major_rate"]
DEDUCT_HAVING_MINOR   = _fs["having"]["minor_rate"]
DEDUCT_HAVING_EXTRA   = _fs["having"]["extra_rate"]
HAVING_MAJOR_CAP      = _fs["having"]["major_cap"]
HAVING_MINOR_CAP      = _fs["having"]["minor_cap"]
HAVING_EXTRA_CAP      = _fs["having"]["extra_cap"]

DEDUCT_MISSING_OTHER  = _fs["other_used"]["missing_rate"]
MISSING_OTHER_CAP     = _fs["other_used"]["missing_cap"]


# ── Internal helpers ───────────────────────────────────────────────────────────

def _is_blank(val) -> bool:
    """Return True for None, NaN, or empty/sentinel strings."""
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    s = str(val).strip()
    return not s or s.lower() in ("nan", "none", "(none)")


def _split_csv_tokens(raw: str) -> list[str]:
    """
    Split a comma-separated string into parts, respecting single-quoted values.
    e.g. "Customer = 'Smith, Jr.', Year = 2026" → ["Customer = 'Smith, Jr.'", "Year = 2026"]
    """
    parts, buf, in_quote = [], [], False
    for ch in raw:
        if ch == "'":
            in_quote = not in_quote
            buf.append(ch)
        elif ch == "," and not in_quote:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]




# Operator pairs where one side is LIKE-family and the other is equality → minor difference.
# Operators that use wildcard-style partial matching.
_LIKE_FAMILY: frozenset[str] = frozenset({'LIKE', 'ILIKE', 'NOT LIKE', 'CONTAINS'})

# Both gold→actual and actual→gold orderings included.
_MINOR_OP_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ('LIKE',     '='),      ('=',     'LIKE'),
    ('LIKE',     'IN'),     ('IN',    'LIKE'),
    ('ILIKE',    '='),      ('=',     'ILIKE'),
    ('ILIKE',    'LIKE'),   ('LIKE',  'ILIKE'),
    ('NOT LIKE', '!='),     ('!=',    'NOT LIKE'),
    ('NOT LIKE', '<>'),     ('<>',    'NOT LIKE'),
    ('CONTAINS', '='),      ('=',     'CONTAINS'),
    ('CONTAINS', 'LIKE'),   ('LIKE',  'CONTAINS'),
})


def _token_col_op(tok: str) -> tuple[str, str]:
    """Extract (normalised_col, UPPERCASE_op) from a normalised WHERE token.
    Token format produced by _normalise_where_expr: '{col} {OP} {val}'
    where col has no spaces.  Handles multi-word operators: NOT LIKE, IS NULL, etc.
    """
    parts = tok.split()
    if not parts:
        return '', ''
    col = parts[0]
    if len(parts) < 2:
        return col, ''
    p1 = parts[1].upper()
    if len(parts) >= 4 and p1 == 'IS' and parts[2].upper() == 'NOT':
        op = 'IS NOT NULL'                       # IS NOT NULL
    elif len(parts) >= 3 and p1 in ('NOT', 'IS'):
        op = f"{p1} {parts[2].upper()}"          # NOT LIKE / IS NULL / NOT IN / …
    else:
        op = p1                                  # =  !=  LIKE  IN  BETWEEN  …
    return col, op


def _tok_val(tok: str, col: str, op: str) -> str:
    """Extract the value portion from a normalised WHERE token (format: '{col} {op} {val}')."""
    prefix = f"{col} {op} "
    return tok[len(prefix):].strip() if tok.startswith(prefix) else ""


def _like_vals_compatible(g_tok: str, a_tok: str, g_op: str, a_op: str) -> bool:
    """Check value compatibility for a LIKE-family ↔ = minor pair candidate.

    Strip % wildcards (and * wildcards) from whichever side uses a LIKE-family
    operator, then check whether the shorter stripped value is a substring of the
    longer (both values are already lowercased by _normalise_where_expr).

    Returns True  → keep as minor  (values are semantically related)
    Returns False → upgrade to major (values share no content)

    Examples:
      LIKE '%supermart%'  vs  = 'supermart inc.'  → 'supermart' in 'supermart inc.' → True
      LIKE '%supermart%'  vs  = 'walmart inc.'    → 'supermart' not in 'walmart'    → False
    """
    g_col, _ = _token_col_op(g_tok)
    a_col, _ = _token_col_op(a_tok)
    g_val = _tok_val(g_tok, g_col, g_op).strip("'")
    a_val = _tok_val(a_tok, a_col, a_op).strip("'")
    if g_op in _LIKE_FAMILY:
        g_val = g_val.replace('%', '').replace('*', '').strip()
    if a_op in _LIKE_FAMILY:
        a_val = a_val.replace('%', '').replace('*', '').strip()
    if not g_val or not a_val:
        return True   # wildcard-only LIKE or empty value — treat as compatible
    shorter, longer = (g_val, a_val) if len(g_val) <= len(a_val) else (a_val, g_val)
    return shorter in longer


def _classify_where_mismatches(
    missing_norms: set[str],
    extra_norms:   set[str],
) -> tuple[set[str], set[str]]:
    """Classify each missing gold token as major or minor by comparing against extra tokens.

    For each missing gold token:
      • If the same column name appears in extra_norms and the operator pair
        is in _MINOR_OP_PAIRS → minor candidate.
        For LIKE-family ↔ = pairs, also run _like_vals_compatible(): if the
        stripped LIKE value is not a substring of the = value (or vice versa),
        upgrade to major (different entity, not just a style difference).
      • Otherwise → major (column absent from actual, wrong value, or non-cosmetic op change).

    Returns (major_set, minor_set) — disjoint subsets of missing_norms.
    """
    # Build column → token map from extras (first match wins per column)
    extra_by_col: dict[str, str] = {}
    for a_tok in extra_norms:
        col = a_tok.split(' ', 1)[0]
        if col not in extra_by_col:
            extra_by_col[col] = a_tok

    major: set[str] = set()
    minor: set[str] = set()
    for g_tok in missing_norms:
        g_col, g_op = _token_col_op(g_tok)
        a_tok = extra_by_col.get(g_col)
        if a_tok is None:
            major.add(g_tok)                     # column absent from actual
        else:
            _, a_op = _token_col_op(a_tok)
            if (g_op, a_op) in _MINOR_OP_PAIRS:
                # For LIKE-family pairs, verify values are semantically compatible
                if (g_op in _LIKE_FAMILY or a_op in _LIKE_FAMILY) and \
                        not _like_vals_compatible(g_tok, a_tok, g_op, a_op):
                    major.add(g_tok)             # same col, LIKE pair but incompatible values
                else:
                    minor.add(g_tok)             # cosmetic op change, values compatible
            else:
                major.add(g_tok)                 # same col, non-cosmetic op or wrong value
    return major, minor


_OTHER_QUOTE_RE   = re.compile(r'"([^"]*)"')          # matches "quoted" segments in Other Used tokens
_OTHER_NORM_RE    = re.compile(r'[\s_\-"\'()]')        # chars stripped when comparing quoted values


def _match_other_token(token: str, sql: str) -> bool:
    """
    Check whether an Other Used token is satisfied by the actual SQL string.

    Simple token (no double-quotes):
        Word-boundary regex match on the SQL (case-insensitive).
        Examples: 'LIMIT', 'LIMIT 25', 'DISTINCT', 'ROW_NUMBER'

    Quoted token, e.g. 'ORDER BY "PTE" DESC':
        1. Split into alternating unquoted / quoted segments.
        2. Build a regex: unquoted words joined by \\s+, quoted segments as capture groups.
        3. Search SQL for the pattern.
        4. Each quoted text must be a case-insensitive substring of its captured word.
        Examples:
          'ORDER BY "PTE" DESC'  vs  'ORDER BY avg_pte DESC'  →  True  ('pte' in 'avg_pte')
          'ORDER BY "PTE" DESC'  vs  'ORDER BY spend DESC'    →  False ('pte' not in 'spend')
          'ORDER BY "PTE" DESC'  vs  'ORDER BY avg_pte'       →  False (DESC missing)
    """
    if not sql:
        return False
    token = token.strip()
    if not token:
        return False

    # ── Simple token: no double-quotes ────────────────────────────────────────
    if '"' not in token:
        words = token.split()
        pat = r'\s+'.join(re.escape(w) for w in words)
        return bool(re.search(r'\b' + pat + r'\b', sql, re.IGNORECASE))

    # ── Quoted token ──────────────────────────────────────────────────────────
    # _OTHER_QUOTE_RE.split gives alternating [unquoted, quoted, unquoted, ...]
    segments = _OTHER_QUOTE_RE.split(token)
    unquoted = [segments[i].strip() for i in range(0, len(segments), 2)]
    quoted   = [segments[i]         for i in range(1, len(segments), 2)]

    # Build a list of regex atoms: words from unquoted parts + capture groups for quoted parts
    atoms: list[str] = []
    for i, uq in enumerate(unquoted):
        for word in uq.split():
            atoms.append(re.escape(word))
        if i < len(quoted):
            atoms.append(r'("[^"]*"|\S+)')   # capture quoted identifier or plain word

    if not atoms:
        return False

    pat = re.compile(r'\s+'.join(atoms), re.IGNORECASE)
    m = pat.search(sql)
    if not m:
        return False

    # Verify each capture group contains its quoted text as a substring.
    # Normalize both sides (lowercase, strip spaces/underscores/hyphens/quotes)
    # so that e.g. "sales_per_dollar" matches "Sales per Dollar".
    for i, q_text in enumerate(quoted):
        q_norm  = _OTHER_NORM_RE.sub('', q_text.lower())
        cap_norm = _OTHER_NORM_RE.sub('', m.group(i + 1).lower())
        if q_norm not in cap_norm:
            return False
    return True


def _parse_where_map(raw) -> dict[str, str]:
    """
    Parse a WHERE-tokens cell into a {normalised: original} dict.
    Keys are used for comparison; values are used for display.
    Returns {} when the cell is blank — meaning zero tokens are expected,
    so any tokens the agent produces will be treated as extras.
    """
    if _is_blank(raw):
        return {}
    parts = _split_csv_tokens(str(raw).strip())
    return {_normalise_where_expr(_strip_outer_parens(p)): p.strip()
            for p in parts if p}


def _parse_list(raw) -> list[str]:
    """
    Parse a comma-separated cell into a stripped list.
    Returns [] when the cell is blank — meaning zero items are expected,
    so any items the agent produces will be treated as extras.
    """
    if _is_blank(raw):
        return []
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _safe_int(val) -> int | None:
    """Convert a cell value to int; return None when blank or unconvertible."""
    if _is_blank(val):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _parse_sql(raw) -> str | None:
    """Return the SQL string if present; None if blank."""
    if _is_blank(raw):
        return None
    s = str(raw).strip()
    return s or None


# ── Public API ─────────────────────────────────────────────────────────────────

def load_standard(path: str | Path) -> list[dict]:
    """
    Read an Excel standard file and return a list of expected-record dicts:
      { prompt, where_map, attrs, metrics, data_rows, sql,
        optional_attrs, optional_metrics }
    Blank cells: WHERE/Attributes/Metrics → empty collection (agent expected to return none).
                 Data Rows → None (skip — no row-count expectation).
                 SQL → None (skip — SQL comparison requires gold SQL to be present).
                 Optional Attributes / Optional Metrics → [] (no optionals defined).
    Expected columns: Prompt, WHERE Tokens, Attributes Used, Metrics Used, Data Rows
    Optional columns: SQL                (gold-standard SQL for LLM comparison via sql_judge.py)
                      Optional Attributes (comma-separated attributes that carry zero penalty
                                          when present in actual but absent from gold)
                      Optional Metrics   (comma-separated metrics that carry zero penalty
                                          when present in actual but absent from gold)
    """
    df = pd.read_excel(str(path))
    df.columns = [str(c).strip() for c in df.columns]
    cols = set(df.columns)   # fast membership test for optional columns

    records = []
    for _, row in df.iterrows():
        prompt = str(row.get("Prompt", "")).strip()
        if not prompt or prompt.lower() == "nan":
            continue
        records.append({
            "prompt":          prompt,
            "where_map":       _parse_where_map(row.get("WHERE Tokens")),
            "attrs":           _parse_list(row.get("Attributes Used")),
            "metrics":         _parse_list(row.get("Metrics Used")),
            "data_rows":       _safe_int(row.get("Data Rows")),
            "sql":              _parse_sql(row.get("SQL")),
            "optional_attrs":   _parse_list(row.get("Optional Attributes")),
            "optional_metrics": _parse_list(row.get("Optional Metrics")),
            # ── New optional columns (None when column absent → field skipped) ──
            "having_map":     _parse_where_map(row["Having"])       if "Having"        in cols else None,
            "other":          _parse_list(row["Other Used"])         if "Other Used"    in cols else None,
            "optional_other": _parse_list(row["Optional Other"])     if "Optional Other" in cols else None,
        })
    return records


def score_results(envelope: dict, standard: list[dict]) -> list[dict]:
    """
    Score each result in the envelope against the standard.
    Matching is by prompt text (case-insensitive, stripped).
    Returns a list of scored-record dicts with shape:
      {
        id, category, prompt, expected_prompt, prompt_differs,
        matched, score,       # score = None if unmatched
        deductions: [
          { field, expected, actual, deduction, missing, extra }
        ]
      }
    """
    std_map = {r["prompt"].strip().lower(): r for r in standard}
    scored  = []

    for result in envelope.get("results", []):
        actual_prompt = result.get("prompt", "").strip()
        std = std_map.get(actual_prompt.lower())

        rec = {
            "id":              result.get("id"),
            "category":        result.get("category", ""),
            "prompt":          actual_prompt,
            "expected_prompt": std["prompt"] if std else None,
            "prompt_differs":  (std is not None
                                and std["prompt"].strip() != actual_prompt),
            "matched":         std is not None,
            "score":           SCORE_MAX if std is not None else None,
            "deductions":      [],
        }

        if std is None:
            scored.append(rec)
            continue

        # ── WHERE Tokens ──────────────────────────────────────────────────────
        # std["where_map"] is always a dict (never None): {} means blank cell
        # = zero tokens expected, so all actual tokens are treated as extras.
        std_wmap    = std["where_map"]          # {norm: original}
        actual_wmap = _actual_where_set(result) # {norm: original}
        missing_norms = set(std_wmap)    - set(actual_wmap)
        # Exempt extra WHERE tokens whose column exactly matches an optional attribute
        opt_attrs_norm = {_norm_col(v) for v in (std.get("optional_attrs") or [])}
        extra_norms   = {n for n in (set(actual_wmap) - set(std_wmap))
                         if n.split(' ', 1)[0] not in opt_attrs_norm}
        if missing_norms or extra_norms:
            if std_wmap:
                # Classify each missing token as major or minor
                major_set, minor_set = _classify_where_mismatches(
                    missing_norms, extra_norms
                )
                major_count = min(len(major_set), WHERE_MAJOR_CAP)
                minor_count = min(len(minor_set), WHERE_MINOR_CAP)
                if major_count > 0:
                    minor_count = 0   # minor waived when any major difference exists
                # Primary pairing: same-column extras exempt from double-counting
                extra_by_col_map = {_token_col_op(t)[0]: t for t in extra_norms}
                paired_cols      = {_token_col_op(t)[0] for t in major_set | minor_set}
                primary_unpaired = {t for t in extra_norms
                                    if _token_col_op(t)[0] not in paired_cols}
                # Secondary pairing: cross-column substitution —
                # same op + value on a different column, OR a LIKE-compatible pair
                # e.g. ProductLevel2 LIKE '%coffee%'  →  ProductLevel5 LIKE '%coffee%'
                # e.g. CustLevel6 LIKE '%acme%'       →  CustLevel3 = 'Team ACME'
                cross_col_pairs: dict[str, str] = {}   # gold_norm → extra_norm
                cross_col_used:  set[str]       = set()
                for g in sorted(major_set):
                    g_col, g_op = _token_col_op(g)
                    if extra_by_col_map.get(g_col) is not None:
                        continue   # already handled by same-column pairing
                    g_val = _tok_val(g, g_col, g_op)
                    if not g_val:
                        continue
                    for a_tok in primary_unpaired - cross_col_used:
                        a_col, a_op = _token_col_op(a_tok)
                        a_val = _tok_val(a_tok, a_col, a_op)
                        exact = (a_op == g_op and a_val == g_val)
                        like_compat = (
                            a_val is not None
                            and (g_op, a_op) in _MINOR_OP_PAIRS
                            and _like_vals_compatible(g, a_tok, g_op, a_op)
                        )
                        if exact or like_compat:
                            cross_col_pairs[g] = a_tok
                            cross_col_used.add(a_tok)
                            break
                extra_unpaired = primary_unpaired - cross_col_used
                extra_count    = min(len(extra_unpaired), WHERE_EXTRA_CAP)
                deduct = round(
                    major_count * DEDUCT_WHERE_MAJOR
                    + minor_count * DEDUCT_WHERE_MINOR
                    + extra_count * DEDUCT_WHERE_EXTRA,
                    2,
                )
                # Build display lists for the deduction record
                subst_list   = []   # major pairs → "Substituted: gold → actual"
                missing_list = []   # gold tokens with no counterpart in actual at all
                for g in sorted(major_set):
                    g_col = _token_col_op(g)[0]
                    a_tok = (extra_by_col_map.get(g_col)   # same-column pair
                             or cross_col_pairs.get(g))    # cross-column pair
                    if a_tok is not None:
                        subst_list.append((std_wmap[g], actual_wmap[a_tok]))
                    else:
                        missing_list.append(std_wmap[g])
                op_diff_list = [
                    (std_wmap[g], actual_wmap[extra_by_col_map[_token_col_op(g)[0]]])
                    for g in sorted(minor_set)
                ]
                extra_list = [actual_wmap[t] for t in sorted(extra_unpaired)]
            else:
                # Blank standard — any actual filter is unexpected: charge as major
                deduct = round(
                    min(len(extra_norms), WHERE_MAJOR_CAP) * DEDUCT_WHERE_MAJOR, 2
                )
                missing_list = []
                subst_list   = []
                op_diff_list = []
                extra_list   = [actual_wmap[n] for n in sorted(extra_norms)]
            rec["score"] -= deduct
            rec["deductions"].append({
                "field":       "WHERE Tokens",
                "expected":    ", ".join(std_wmap[n] for n in sorted(std_wmap))
                               or "(none)",
                "actual":      ", ".join(actual_wmap[n] for n in sorted(actual_wmap))
                               or "(none)",
                "deduction":   deduct,
                "missing":     missing_list,
                "substituted": subst_list,
                "op_diff":     op_diff_list,
                "extra":       extra_list,
            })

        # ── Attributes Used ───────────────────────────────────────────────────
        _score_list_field(rec, result, std,
                          result_key="attributesUsed",
                          std_key="attrs",
                          label="Attributes Used",
                          missing_deduct=DEDUCT_MISSING_ATTR,
                          extra_deduct=DEDUCT_EXTRA_ATTR,
                          missing_cap=MISSING_ATTR_CAP,
                          extra_cap=EXTRA_ATTR_CAP,
                          optional_set=std.get("optional_attrs", []))

        # ── Metrics Used ──────────────────────────────────────────────────────
        _score_list_field(rec, result, std,
                          result_key="metricsUsed",
                          std_key="metrics",
                          label="Metrics Used",
                          missing_deduct=DEDUCT_MISSING_METRIC,
                          extra_deduct=DEDUCT_EXTRA_METRICS,
                          missing_cap=MISSING_METRIC_CAP,
                          extra_cap=EXTRA_METRICS_CAP,
                          optional_set=std.get("optional_metrics", []))

        # ── HAVING Tokens ─────────────────────────────────────────────────────
        # None means the "Having" column is absent from the standard file — skip entirely.
        # {} means the cell is blank = zero tokens expected; actual tokens are extras.
        std_hmap = std.get("having_map")
        if std_hmap is not None:
            having_tokens = extract_having_tokens(result.get("sqlQueries")) or []
            actual_hmap   = {_normalise_where_expr(_strip_outer_parens(t)): t
                             for t in having_tokens}
            result["_havingTokens"] = list(actual_hmap.values())  # stored for report display
            missing_norms_h = set(std_hmap)    - set(actual_hmap)
            extra_norms_h   = set(actual_hmap) - set(std_hmap)
            if missing_norms_h or extra_norms_h:
                if std_hmap:
                    major_h, minor_h = _classify_where_mismatches(
                        missing_norms_h, extra_norms_h
                    )
                    major_count_h = min(len(major_h), HAVING_MAJOR_CAP)
                    minor_count_h = min(len(minor_h), HAVING_MINOR_CAP)
                    if major_count_h > 0:
                        minor_count_h = 0
                    extra_by_col_map_h = {_token_col_op(t)[0]: t for t in extra_norms_h}
                    paired_cols_h      = {_token_col_op(t)[0] for t in major_h | minor_h}
                    primary_unpaired_h = {t for t in extra_norms_h
                                          if _token_col_op(t)[0] not in paired_cols_h}
                    # Secondary pairing: cross-column substitution —
                    # same op + value on a different column, OR a LIKE-compatible pair
                    cross_col_pairs_h: dict[str, str] = {}
                    cross_col_used_h:  set[str]       = set()
                    for g in sorted(major_h):
                        g_col_h, g_op_h = _token_col_op(g)
                        if extra_by_col_map_h.get(g_col_h) is not None:
                            continue  # already handled by same-column pairing
                        g_val_h = _tok_val(g, g_col_h, g_op_h)
                        if not g_val_h:
                            continue
                        for a_tok_h in primary_unpaired_h - cross_col_used_h:
                            a_col_h, a_op_h = _token_col_op(a_tok_h)
                            a_val_h = _tok_val(a_tok_h, a_col_h, a_op_h)
                            exact_h = (a_op_h == g_op_h and a_val_h == g_val_h)
                            like_compat_h = (
                                a_val_h is not None
                                and (g_op_h, a_op_h) in _MINOR_OP_PAIRS
                                and _like_vals_compatible(g, a_tok_h, g_op_h, a_op_h)
                            )
                            if exact_h or like_compat_h:
                                cross_col_pairs_h[g] = a_tok_h
                                cross_col_used_h.add(a_tok_h)
                                break
                    extra_unpaired_h   = primary_unpaired_h - cross_col_used_h
                    extra_count_h      = min(len(extra_unpaired_h), HAVING_EXTRA_CAP)
                    deduct_h = round(
                        major_count_h * DEDUCT_HAVING_MAJOR
                        + minor_count_h * DEDUCT_HAVING_MINOR
                        + extra_count_h * DEDUCT_HAVING_EXTRA,
                        2,
                    )
                    subst_list_h   = []
                    missing_list_h = []
                    for g in sorted(major_h):
                        g_col_h = _token_col_op(g)[0]
                        a_tok_h = (extra_by_col_map_h.get(g_col_h)
                                   or cross_col_pairs_h.get(g))
                        if a_tok_h is not None:
                            subst_list_h.append((std_hmap[g], actual_hmap[a_tok_h]))
                        else:
                            missing_list_h.append(std_hmap[g])
                    op_diff_list_h = [
                        (std_hmap[g],
                         actual_hmap[extra_by_col_map_h[_token_col_op(g)[0]]])
                        for g in sorted(minor_h)
                        if _token_col_op(g)[0] in extra_by_col_map_h
                    ]
                    extra_list_h = [actual_hmap[t] for t in sorted(extra_unpaired_h)]
                else:
                    deduct_h = round(
                        min(len(extra_norms_h), HAVING_MAJOR_CAP) * DEDUCT_HAVING_MAJOR, 2
                    )
                    missing_list_h = []
                    subst_list_h   = []
                    op_diff_list_h = []
                    extra_list_h   = [actual_hmap[n] for n in sorted(extra_norms_h)]
                rec["score"] -= deduct_h
                rec["deductions"].append({
                    "field":       "HAVING Tokens",
                    "expected":    ", ".join(std_hmap[n] for n in sorted(std_hmap))
                                   or "(none)",
                    "actual":      ", ".join(actual_hmap[n] for n in sorted(actual_hmap))
                                   or "(none)",
                    "deduction":   deduct_h,
                    "missing":     missing_list_h,
                    "substituted": subst_list_h,
                    "op_diff":     op_diff_list_h,
                    "extra":       extra_list_h,
                })

        # ── Other Used ────────────────────────────────────────────────────────
        # None means the "Other Used" column is absent from the standard file — skip.
        # [] means the cell is blank = zero tokens expected.
        std_other = std.get("other")
        if std_other is not None:
            sql_text = ((result.get("sqlQueries") or [None])[0]) or ""
            optional_other = {_norm_col(v) for v in (std.get("optional_other") or [])}
            missing_other = [tok for tok in std_other
                             if _norm_col(tok) not in optional_other
                             and not _match_other_token(tok, sql_text)]
            found_other   = [tok for tok in std_other
                             if _match_other_token(tok, sql_text)]
            result["_otherFound"] = found_other  # stored for report display
            if missing_other:
                deduct_o = round(
                    min(len(missing_other), MISSING_OTHER_CAP) * DEDUCT_MISSING_OTHER, 2
                )
                non_opt_list = [tok for tok in std_other
                                if _norm_col(tok) not in optional_other]
                found_non_opt = [tok for tok in non_opt_list
                                 if _match_other_token(tok, sql_text)]
                rec["score"] -= deduct_o
                rec["deductions"].append({
                    "field":     "Other Used",
                    "expected":  ", ".join(sorted(non_opt_list, key=str.lower)) or "(none)",
                    "actual":    ", ".join(sorted(found_non_opt, key=str.lower)) or "(none)",
                    "deduction": deduct_o,
                    "missing":   sorted(missing_other, key=str.lower),
                    "extra":     [],
                })

        # ── Data Rows ─────────────────────────────────────────────────────────
        if std["data_rows"] is not None:
            act_grid  = result.get("gridData")
            act_count = len(act_grid) if isinstance(act_grid, list) else None
            if act_count != std["data_rows"]:
                rec["score"] -= DEDUCT_ROWS
                rec["deductions"].append({
                    "field":     "Data Rows",
                    "expected":  str(std["data_rows"]),
                    "actual":    str(act_count) if act_count is not None else "(none)",
                    "deduction": DEDUCT_ROWS,
                    "missing":   [],
                    "extra":     [],
                })

        rec["score"] = round(max(0.0, rec["score"]), 2)
        scored.append(rec)

    return scored


# ── Private scoring helpers ────────────────────────────────────────────────────

def _actual_where_set(result: dict) -> dict[str, str]:
    """Return {normalised: original} for the result's whereClauseTokens."""
    tokens = result.get("whereClauseTokens") or []
    return {_normalise_where_expr(_strip_outer_parens(t)): t for t in tokens}


def _score_list_field(rec: dict, result: dict, std: dict,
                      result_key: str, std_key: str, label: str,
                      missing_deduct: float = DEDUCT_MISSING_ATTR,
                      extra_deduct: float = DEDUCT_EXTRA_ATTR,
                      missing_cap: int = MISSING_ATTR_CAP,
                      extra_cap: int = EXTRA_ATTR_CAP,
                      optional_set: list[str] | None = None) -> None:
    """
    Apply missing/extra deductions for an attribute or metric list field.
    missing_deduct — penalty per missing item
    extra_deduct   — penalty per extra item
    missing_cap    — max number of missing items charged
    extra_cap      — max number of extra items charged
    optional_set   — items that carry zero penalty when missing or extra; matched
                     by exact normalised name only (qualifier-prefix matching is
                     handled in the SQL comparison path by Claude, not here).
    """
    exp_list = std[std_key]
    if exp_list is None:
        return   # safety net — _parse_list now returns [] not None

    optional = {_norm_col(v) for v in (optional_set or [])}
    act_list = result.get(result_key) or []
    # Build norm→original maps so we can report original names in the output
    exp_norm_map = {_norm_col(v): v for v in exp_list}
    act_norm_map = {_norm_col(v): v for v in act_list}
    exp_set  = set(exp_norm_map)
    act_set  = set(act_norm_map)
    missing  = (exp_set - act_set) - optional   # optional items carry zero missing penalty
    extra    = (act_set - exp_set) - optional   # optional items carry zero extra penalty
    deduct   = round(
        min(len(missing), missing_cap) * missing_deduct
        + min(len(extra),  extra_cap)  * extra_deduct,
        2,
    )
    if deduct or extra:   # always record if there's anything to show
        rec["score"] -= deduct
        rec["deductions"].append({
            "field":     label,
            "expected":  ", ".join(sorted(exp_list, key=str.lower)) or "(none)",
            "actual":    ", ".join(sorted(act_list, key=str.lower)) if act_list else "(none)",
            "deduction": deduct,
            "missing":   sorted(exp_norm_map[n] for n in missing),
            "extra":     sorted(act_norm_map[n] for n in extra),
        })
