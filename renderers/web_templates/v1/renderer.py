"""
renderers/web_templates/v1/renderer.py — HTML site generator (version 1)
Generates index.html + one detail page per prompt.
"""

import json
import os
import re
import html
from pathlib import Path

from core.results import format_sql
from core.color import hex_darken, hex_lighten

# Path(__file__) = renderers/web_templates/v1/renderer.py
# parents[3]     = project root
OUTPUT_BASE = Path(__file__).parents[3] / "output"


def _esc(s) -> str:
    """HTML-escape a value."""
    return html.escape(str(s)) if s is not None else ""


def _nl2br(text: str | None) -> str:
    """HTML-escape then convert newlines to <br> tags."""
    if not text:
        return ""
    return _esc(str(text)).replace("\n", "<br>\n")


def _inline_md(escaped_text: str) -> str:
    """Apply inline Markdown (bold, italic, code) to already-HTML-escaped text."""
    # **bold** or __bold__
    escaped_text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped_text)
    escaped_text = re.sub(r"__(.+?)__",     r"<strong>\1</strong>", escaped_text)
    # *italic* or _italic_
    escaped_text = re.sub(r"\*(.+?)\*",     r"<em>\1</em>",         escaped_text)
    escaped_text = re.sub(r"_(.+?)_",       r"<em>\1</em>",         escaped_text)
    # `code`
    escaped_text = re.sub(
        r"`(.+?)`",
        r"<code style='background:var(--cream);padding:0 3px;border-radius:var(--radius-sm)'>\1</code>",
        escaped_text,
    )
    return escaped_text


def _md_to_html(text: str | None) -> str:
    """
    Convert a limited Markdown subset to HTML for web rendering.
    Handles: #### headers, **bold**, *italic*, `code`, - bullets, newlines.
    """
    if not text:
        return ""
    lines = str(text).split("\n")
    out = []
    in_list = False
    for line in lines:
        # Headers: ## text  →  <h4>text</h4>  (level capped to h3–h6)
        m = re.match(r"^(#{1,6})\s*(.+)$", line)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = min(len(m.group(1)) + 2, 6)
            out.append(
                f"<h{level} style='margin:.5rem 0 .2rem;font-size:.9rem;"
                f"color:var(--primary)'>{_inline_md(_esc(m.group(2)))}</h{level}>"
            )
            continue
        # Bullet list items:  - text  or  * text
        m2 = re.match(r"^[-*]\s+(.+)$", line)
        if m2:
            if not in_list:
                out.append("<ul style='margin:.25rem 0 .25rem 1.2rem;padding:0'>")
                in_list = True
            out.append(f"<li>{_inline_md(_esc(m2.group(1)))}</li>")
            continue
        # Close list on blank / non-bullet line
        if in_list:
            out.append("</ul>")
            in_list = False
        # Blank line
        if not line.strip():
            out.append("<br>")
            continue
        # Normal paragraph line
        out.append(f"<p style='margin:.15rem 0'>{_inline_md(_esc(line))}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _has_meaningful_categories(results: list) -> bool:
    """Return True when results carry categories other than blank or 'General'."""
    cats = {(r.get("category") or "").strip() for r in results}
    cats.discard("")
    cats.discard("General")
    return bool(cats)


def _tag_list(items: list | None, css_class: str = "tag") -> str:
    if not items:
        return ""
    return " ".join(f'<span class="{css_class}">{_esc(i)}</span>' for i in items)


def _field_card(icon: str, title: str, content_html: str | None, full: bool = False, accent: bool = False) -> str:
    if not content_html:
        return ""
    cls = "field-card"
    if full:    cls += " full"
    if accent:  cls += " accent"
    return f"""
<div class="{cls}">
  <div class="field-header">{icon} <strong>{_esc(title)}</strong></div>
  <div class="field-body">{content_html}</div>
</div>"""


def _images_html(images_data: list | None) -> str:
    """Render imagesData list as inline base64 <img> tags."""
    if not images_data:
        return ""
    parts = []
    for img in images_data:
        b64 = img.get("data", "")
        if b64:
            parts.append(
                f'<img src="data:image/png;base64,{b64}" '
                f'style="max-width:100%;height:auto;border-radius:var(--radius-sm);'
                f'margin-bottom:.5rem;display:block">'
            )
    return "\n".join(parts)


def _grid_data_html(grid_data) -> str:
    """
    Render gridData as an HTML table inside a scroll container.
    - < 20 rows:  div height = natural (no scroll), overflow-x:auto for wide grids.
    - >= 20 rows: div capped at ~20 rows tall (overflow-y:auto) with sticky header
                  and a row-count note; overflow-x:auto for wide grids.
    """
    from core.results import parse_grid_data
    headers, rows = parse_grid_data(grid_data)
    if not headers or not rows:
        return ""

    MAX_VISIBLE = 20
    total       = len(rows)

    header_html = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    rows_html   = "".join(
        "<tr>" + "".join(f"<td>{_esc(v)}</td>" for v in row) + "</tr>"
        for row in rows
    )

    if total >= MAX_VISIBLE:
        # ~34px per row + 36px header ≈ 716px for 20 visible rows
        div_style    = "overflow-x:auto;overflow-y:auto;max-height:720px"
        thead_style  = " style='position:sticky;top:0;z-index:1'"
        note         = (f"<div style='font-size:.75rem;color:var(--g4);"
                        f"margin-top:.4rem'>{total} rows — scroll to see all</div>")
    else:
        div_style    = "overflow-x:auto"
        thead_style  = ""
        note         = ""

    return (
        f"<div style='{div_style}'>"
        f"<table class='data-table' style='width:auto;min-width:100%'>"
        f"<thead{thead_style}><tr>{header_html}</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        f"</table>"
        f"</div>"
        f"{note}"
    )


def _chart_html(chart_data: dict | None, prompt_id: int) -> str:
    if not chart_data:
        return ""
    charts = chart_data.get("charts") or (chart_data if isinstance(chart_data, list) else [chart_data])
    if not charts:
        return ""

    chart = charts[0] if isinstance(charts, list) else charts
    data_rows = chart.get("data", [])
    option    = chart.get("option", {})
    columns   = option.get("columns", [])
    title     = option.get("title", "Chart")
    chart_type = chart.get("type", "bar")

    if not data_rows or not columns:
        return ""

    labels_cols  = [c["column_name"] for c in columns if c.get("type") == 1]
    metrics_cols = [c["column_name"] for c in columns if c.get("type") == 2]

    if chart_type == "bar" and labels_cols and metrics_cols:
        label_col  = labels_cols[0]
        metric_col = metrics_cols[0]
        labels = [str(row.get(label_col, "")) for row in data_rows]
        values = [row.get(metric_col, 0) for row in data_rows]
        chart_id = f"chart_{prompt_id}"
        labels_json = json.dumps(labels)
        values_json = json.dumps(values)
        return f"""
<canvas id="{chart_id}" style="max-height:280px;margin-bottom:1.5rem"></canvas>
<script>
(function(){{
  const ctx = document.getElementById('{chart_id}').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {labels_json},
      datasets: [{{ label: '{_esc(metric_col)}',
        data: {values_json},
        backgroundColor: {values_json}.map(v => v >= 1 ? '#1B6B2F' : v > 0 ? '#63513D' : '#9E9E9E'),
      }}]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ display: false }},
      title: {{ display: true, text: '{_esc(title)}' }} }} }}
  }});
}})();
</script>"""

    # Fallback: HTML table for grid types
    header = "".join(f"<th>{_esc(c['column_name'])}</th>" for c in columns)
    rows_html = ""
    for row in data_rows:
        cells = "".join(f"<td>{_esc(row.get(c['column_name'], ''))}</td>" for c in columns)
        rows_html += f"<tr>{cells}</tr>"
    return f"<table class='data-table'><thead><tr>{header}</tr></thead><tbody>{rows_html}</tbody></table>"


def _css(style: dict) -> str:
    primary   = style["primary"]
    secondary = style["secondary"]
    accent    = style["accent"]
    font      = style["font"]

    # Computed colour shades — derived from the theme colours so every style
    # automatically gets correct hover tints and accent highlights.
    primary_dark  = hex_darken(primary,  0.78)   # ~22% darker, for active borders
    primary_light = hex_lighten(primary, 0.88)   # ~88% toward white, for hover fills
    accent_light  = hex_lighten(accent,  0.88)   # used in accent field-card header

    return f"""
:root {{
  /* ── Brand colours ── */
  --primary:{primary}; --secondary:{secondary}; --accent:{accent};
  --primary-dark:{primary_dark}; --primary-light:{primary_light};
  --accent-light:{accent_light};
  /* ── Grey scale ── */
  --g1:#1a1a1a; --g2:#444; --g3:#666; --g4:#888; --g5:#ccc; --g6:#eee;
  --white:#fff; --cream:#FAF8F5;
  /* ── Semantic colours ── */
  --green:#1B6B2F; --green-bg:#e8f5e9;
  --red:#B71C1C;   --red-bg:#fdecea;
  /* ── Elevation ── */
  --shadow-sm:0 1px 3px rgba(0,0,0,.08),0 1px 2px rgba(0,0,0,.05);
  --shadow-md:0 4px 12px rgba(0,0,0,.12),0 2px 4px rgba(0,0,0,.06);
  --shadow-lg:0 10px 24px rgba(0,0,0,.14),0 4px 8px rgba(0,0,0,.07);
  /* ── Shape ── */
  --radius-sm:4px; --radius:8px; --radius-lg:12px;
  /* ── Motion ── */
  --transition:150ms ease;
  /* ── Layout ── */
  --sidebar-w:280px; --header-h:64px;
  /* ── Typography ── */
  --font:'{font}',system-ui,sans-serif;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:var(--font);background:var(--cream);color:var(--g1);display:flex;flex-direction:column;min-height:100vh}}
a{{color:var(--primary);text-decoration:none}}
a:hover{{text-decoration:underline}}
/* Header */
.site-header{{position:fixed;top:0;left:0;right:0;height:var(--header-h);background:var(--primary);color:#fff;display:flex;align-items:center;padding:0 2rem;z-index:100;gap:1rem;box-shadow:var(--shadow-md)}}
.site-header h1{{font-size:1.2rem;font-weight:700;letter-spacing:.05em}}
.site-header .sub{{font-size:.8rem;opacity:.7;margin-left:auto}}
/* Sidebar */
.sidebar{{position:fixed;top:var(--header-h);left:0;width:var(--sidebar-w);height:calc(100vh - var(--header-h));overflow-y:auto;background:#fff;border-right:1px solid var(--g6);padding:1rem 0;box-shadow:var(--shadow-sm)}}
.sidebar .s-label{{font-size:.6rem;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:var(--g4);padding:.5rem 1.2rem .2rem}}
.sidebar a{{display:flex;align-items:center;gap:.5rem;padding:.5rem 1.2rem;font-size:.82rem;color:var(--g1);border-left:3px solid transparent;transition:background var(--transition)}}
.sidebar a:hover{{background:var(--primary-light);text-decoration:none}}
.sidebar a.active{{border-left-color:var(--primary);background:var(--primary-light);font-weight:600}}
.sidebar .cat{{font-size:.7rem;color:var(--g4)}}
.s-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.s-dot.ok{{background:var(--green)}} .s-dot.err{{background:var(--red)}}
/* Main */
.main{{margin-left:var(--sidebar-w);margin-top:var(--header-h);flex:1;min-height:calc(100vh - var(--header-h))}}
/* Meta bar */
.meta-bar{{display:flex;flex-wrap:wrap;gap:.5rem;padding:.8rem 2rem;background:#fff;border-bottom:1px solid var(--g6)}}
.meta-item{{display:flex;align-items:center;gap:.4rem}}
.meta-lbl{{font-size:.6rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--g4)}}
.meta-val{{font-size:.82rem;font-weight:600}}
.meta-item+.meta-item::before{{content:'';display:block;width:1px;height:1rem;background:var(--g5);margin-right:.1rem}}
.status-badge{{display:inline-flex;align-items:center;gap:4px;font-size:.72rem;font-weight:700;padding:2px 9px;border-radius:var(--radius-lg)}}
.status-badge.ok{{background:var(--green-bg);color:var(--green)}}
.status-badge.err{{background:var(--red-bg);color:var(--red)}}
/* Prompt banner */
.prompt-banner{{padding:1.5rem 2rem;background:var(--primary);color:#fff}}
.prompt-banner .cat{{font-size:.7rem;font-weight:700;letter-spacing:.15em;text-transform:uppercase;opacity:.7;margin-bottom:.4rem}}
.prompt-banner h2{{font-size:1.3rem;font-weight:700;line-height:1.3}}
/* Fields */
.fields{{padding:1.5rem 2rem;display:grid;grid-template-columns:1fr 1fr;gap:1rem}}
.field-card{{background:#fff;border:1px solid var(--g6);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow-sm)}}
.field-card.full{{grid-column:1/-1}}
.field-card.accent{{border-left:4px solid var(--secondary)}}
.field-card.accent .field-header{{background:var(--accent-light)}}
.field-header{{padding:.7rem 1rem;background:var(--cream);border-bottom:1px solid var(--g6);font-size:.82rem;display:flex;align-items:center;gap:.4rem}}
.field-body{{padding:1rem;font-size:.88rem;line-height:1.6;color:var(--g1)}}
.field-body p{{margin-bottom:.5rem}}
.field-body pre{{background:var(--cream);border-radius:var(--radius);padding:.8rem;overflow-x:auto;font-size:.8rem;line-height:1.5;white-space:pre-wrap;word-break:break-word}}
.tag{{display:inline-block;background:var(--cream);border:1px solid var(--g5);border-radius:var(--radius-lg);padding:2px 10px;font-size:.75rem;margin:2px}}
.tag.metric{{background:#e3f2fd;border-color:#90caf9;color:#1565c0}}
/* Data table */
.data-table{{width:100%;border-collapse:collapse;font-size:.82rem}}
.data-table th{{background:var(--primary);color:#fff;padding:.5rem .8rem;text-align:left;font-weight:600}}
.data-table td{{padding:.45rem .8rem;border-bottom:1px solid var(--g6)}}
.data-table tr:nth-child(even){{background:var(--cream)}}
/* Nav */
.page-nav{{display:flex;justify-content:space-between;padding:1rem 2rem;border-top:1px solid var(--g6);background:#fff;margin-top:auto}}
.page-nav a{{font-size:.85rem;font-weight:600;color:var(--primary)}}
/* Dashboard */
.dash-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1rem;padding:2rem}}
.prompt-card{{background:#fff;border:1px solid var(--g6);border-radius:var(--radius);padding:1.2rem;cursor:pointer;transition:box-shadow var(--transition),transform var(--transition)}}
.prompt-card:hover{{box-shadow:var(--shadow-md);transform:translateY(-1px)}}
.prompt-card .cat{{font-size:.65rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--g4);margin-bottom:.4rem}}
.prompt-card .q{{font-size:.9rem;font-weight:600;line-height:1.4;margin-bottom:.8rem}}
.prompt-card .pill{{display:inline-block;font-size:.7rem;padding:2px 8px;border-radius:var(--radius-lg);background:var(--green-bg);color:var(--green);font-weight:600}}
.prompt-card .pill.err{{background:var(--red-bg);color:var(--red)}}
.filters{{padding:1rem 2rem 0;display:flex;gap:.5rem;flex-wrap:wrap}}
.filter-btn{{padding:4px 14px;border:1px solid var(--g5);border-radius:var(--radius-lg);font-size:.78rem;background:#fff;cursor:pointer;transition:background var(--transition),color var(--transition),border-color var(--transition)}}
.filter-btn:hover{{background:var(--primary-light);border-color:var(--primary)}}
.filter-btn.active{{background:var(--primary);color:#fff;border-color:var(--primary)}}
footer{{text-align:center;padding:1rem;font-size:.75rem;color:var(--g4);border-top:1px solid var(--g6)}}
.third-row{{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem}}
@media(max-width:900px){{.sidebar{{display:none}}.main{{margin-left:0}}}}
"""


def _sidebar_html(results: list, current_id: int | None = None,
                  show_cats: bool = True) -> str:
    links = ""
    for r in results:
        active   = "active" if r["id"] == current_id else ""
        dot_cls  = "ok" if r["status"] == "Success" else "err"
        cat_line = (f'<span class="cat">{_esc(r["category"])}</span><br>'
                    if show_cats else "")
        snippet  = _esc(r["prompt"][:45]) + ("…" if len(r["prompt"]) > 45 else "")
        links += f"""
<a href="prompt-{r['id']}.html" class="{active}">
  <span class="s-dot {dot_cls}"></span>
  <span>
    {cat_line}
    <strong>#{r['id']}</strong> {snippet}
  </span>
</a>"""
    return f"""
<nav class="sidebar">
  <div class="s-label">Prompts</div>
  <a href="index.html">🏠 Dashboard</a>
  <div class="s-label" style="margin-top:.8rem">Results</div>
  {links}
</nav>"""


def _page_shell(title: str, style: dict, sidebar_html: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>{_css(style)}</style>
</head>
<body>
<header class="site-header">
  <span>🔮</span>
  <h1>Agent Test Results</h1>
  <span class="sub">Prepared for {_esc(style["name"])}</span>
</header>
{sidebar_html}
<main class="main">
{body_html}
</main>
<footer>Tallmadge · Generated by Tallmadge CLI</footer>
</body>
</html>"""


def render(envelope: dict, style: dict, out_dir: Path | None = None) -> Path:
    """
    Generate the full HTML site from a results envelope.
    Returns the output directory path.
    """
    results = envelope.get("results", [])
    meta    = envelope.get("meta", {})
    _raw_date = meta.get("runDate", "")
    run_date  = _raw_date[:10]                                        # YYYY-MM-DD  (display)
    run_ts    = (_raw_date[:19].replace("T", "_").replace(":", "-")   # YYYY-MM-DD_HH-MM-SS
                 if len(_raw_date) >= 19 else run_date)               # (dirname)
    mode     = meta.get("mode", "standard")

    if out_dir is None:
        out_dir = OUTPUT_BASE / f"web_{run_ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    show_cats = _has_meaningful_categories(results)
    sidebar   = _sidebar_html(results, show_cats=show_cats)

    # ── Detail pages ────────────────────────────────────────────────────────────
    for i, r in enumerate(results):
        rid      = r["id"]
        is_ok    = r["status"] == "Success"
        badge_cls = "ok" if is_ok else "err"
        badge_lbl = f"✓ {r['status']}" if is_ok else f"✗ {r['status']}"

        meta_bar = f"""
<div class="meta-bar">
  <div class="meta-item"><span class="meta-lbl">Status</span>
    <span class="status-badge {badge_cls}">{badge_lbl}</span></div>
  <div class="meta-item"><span class="meta-lbl">Response Time</span>
    <span class="meta-val">{r['responseTime']}s</span></div>
  <div class="meta-item"><span class="meta-lbl">Mode</span>
    <span class="meta-val">{_esc(r.get('mode',''))}</span></div>
  {"<div class='meta-item'><span class='meta-lbl'>Cache</span><span class='meta-val'>" + ("Yes" if r.get("isCacheUsed") else "No") + "</span></div>" if r.get("isCacheUsed") is not None else ""}
</div>"""

        # Build "Images / Data" card content — show all available; chart and
        # grid are independent (config vs raw rows) so both may be present.
        chart_html = _chart_html(r.get("chartData"), rid)
        grid_html  = _grid_data_html(r.get("gridData"))
        imgs_html  = _images_html(r.get("imagesData"))
        data_parts = [p for p in [imgs_html, chart_html, grid_html] if p]
        img_content = "\n".join(data_parts) or None

        # SQL — format with newlines before keywords, then display in <pre>
        sql_list = r.get("sqlQueries") or []
        sql_content = None
        if sql_list:
            sql_content = "".join(f"<pre>{_esc(format_sql(q))}</pre>" for q in sql_list)

        # Attributes / metrics / WHERE tokens
        attrs   = r.get("attributesUsed") or []
        metrics = r.get("metricsUsed") or []
        where_tokens = r.get("whereClauseTokens") or []
        attr_html    = _tag_list(attrs, "tag") or None
        metric_html  = _tag_list(metrics, "tag metric") or None
        where_html   = _tag_list(where_tokens, "tag") or None

        prev_id = results[i-1]["id"] if i > 0 else None
        next_id = results[i+1]["id"] if i < len(results)-1 else None
        nav = f"""
<div class="page-nav">
  {"<a href='prompt-"+str(prev_id)+".html'>← Prev</a>" if prev_id else "<span></span>"}
  <a href="index.html">Dashboard</a>
  {"<a href='prompt-"+str(next_id)+".html'>Next →</a>" if next_id else "<span></span>"}
</div>"""

        cat_line = (f'<div class="cat">{_esc(r["category"])}</div>'
                    if show_cats else "")
        bottom_row = ""
        bottom_cards = [
            _field_card("🏷️", "Attributes Used", attr_html),
            _field_card("📐", "Metrics Used",    metric_html),
            _field_card("🔎", "WHERE Tokens",    where_html),
        ]
        bottom_cards = [c for c in bottom_cards if c]
        if bottom_cards:
            n_cols = len(bottom_cards)
            cols_style = f"grid-template-columns: repeat({n_cols}, 1fr)"
            bottom_row = f'<div class="third-row" style="{cols_style}">{"".join(bottom_cards)}</div>'

        body = f"""
{meta_bar}
<div class="prompt-banner">
  {cat_line}
  <h2>#{rid} {_esc(r['prompt'])}</h2>
</div>
<div class="fields">
  {_field_card("💬", "Response Text", f"<p>{_nl2br(r.get('responseText'))}</p>" if r.get('responseText') else None, full=True)}
  {_field_card("⚠️", "Error", f"<p style='color:var(--red)'>{_nl2br(r.get('error'))}</p>", full=True) if r.get('error') else ""}
  {_field_card("📊", "Images / Data", img_content, full=True)}
  {_field_card("🔍", "Interpretation", _md_to_html(r.get('interpretedQuestion')) if r.get('interpretedQuestion') else None, full=True)}
  {_field_card("📖", "Explanation", _md_to_html(r.get('explanation')) if r.get('explanation') else None, full=True)}
  {_field_card("💡", "Insights", f"<p>{_nl2br(r.get('insights'))}</p>" if r.get('insights') else None, full=True)}
  {_field_card("🗄️", "SQL", sql_content, full=True) if sql_content else ""}
  {bottom_row}
</div>
{nav}"""

        page = _page_shell(
            f"Prompt {rid} — {r['prompt'][:50]}",
            style,
            _sidebar_html(results, current_id=rid, show_cats=show_cats),
            body,
        )
        (out_dir / f"prompt-{rid}.html").write_text(page, encoding="utf-8")

    # ── Dashboard / index ───────────────────────────────────────────────────────
    categories  = sorted(set(r["category"] for r in results)) if show_cats else []
    filter_section = ""
    if show_cats and categories:
        filter_btns  = "<button class='filter-btn active' onclick='filter(this,\"all\")'>All</button>"
        filter_btns += "".join(
            f"<button class='filter-btn' onclick='filter(this,\"{_esc(c)}\")'>{_esc(c)}</button>"
            for c in categories
        )
        filter_section = f"<div class='filters'>{filter_btns}</div>"

    cards = ""
    for r in results:
        pill_cls = "pill" if r["status"] == "Success" else "pill err"
        rt       = f" · {r['responseTime']}s" if r.get("responseTime") else ""
        cat_div  = (f'<div class="cat">{_esc(r["category"])}</div>'
                    if show_cats else "")
        cards += f"""
<a href="prompt-{r['id']}.html" style="text-decoration:none">
  <div class="prompt-card" data-cat="{_esc(r['category'])}">
    {cat_div}
    <div class="q"><strong>#{r['id']}</strong> {_esc(r['prompt'])}</div>
    <span class="{pill_cls}">{_esc(r['status'])}{rt}</span>
  </div>
</a>"""

    filter_script = """
<script>
function filter(btn, cat) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.prompt-card').forEach(c => {
    c.parentElement.style.display = (cat === 'all' || c.dataset.cat === cat) ? '' : 'none';
  });
}
</script>""" if show_cats else ""

    dash_body = f"""
{filter_section}
<div class="dash-grid" id="grid">{cards}</div>
{filter_script}"""

    index_page = _page_shell("Agent Test Results — Dashboard", style, sidebar, dash_body)
    (out_dir / "index.html").write_text(index_page, encoding="utf-8")

    return out_dir
