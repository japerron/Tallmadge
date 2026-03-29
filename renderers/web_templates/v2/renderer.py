"""
renderers/web_templates/v2/renderer.py — HTML site generator (version 2)
Premium editorial template: hero index + two-column detail pages.
"""

import json
import re
import html
from pathlib import Path

from core.results import format_sql
from core.color import hex_darken, hex_lighten

# Path(__file__) = renderers/web_templates/v2/renderer.py
# parents[3]     = project root
OUTPUT_BASE = Path(__file__).parents[3] / "output"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _nl2br(text: str | None) -> str:
    if not text:
        return ""
    return _esc(str(text)).replace("\n", "<br>\n")


def _inline_md(escaped_text: str) -> str:
    escaped_text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped_text)
    escaped_text = re.sub(r"__(.+?)__",     r"<strong>\1</strong>", escaped_text)
    escaped_text = re.sub(r"\*(.+?)\*",     r"<em>\1</em>",         escaped_text)
    escaped_text = re.sub(r"_(.+?)_",       r"<em>\1</em>",         escaped_text)
    escaped_text = re.sub(
        r"`(.+?)`",
        r"<code style='font-family:var(--font-mono);background:rgba(0,0,0,.06);"
        r"padding:1px 4px;border-radius:2px'>\1</code>",
        escaped_text,
    )
    return escaped_text


def _md_to_html(text: str | None) -> str:
    if not text:
        return ""
    lines = str(text).split("\n")
    out = []
    in_list = False
    for line in lines:
        m = re.match(r"^(#{1,6})\s*(.+)$", line)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = min(len(m.group(1)) + 2, 6)
            out.append(
                f"<h{level} style='font-family:var(--font-display);font-weight:300;"
                f"font-size:.95rem;color:var(--primary-dark);margin:.6rem 0 .2rem'>"
                f"{_inline_md(_esc(m.group(2)))}</h{level}>"
            )
            continue
        m2 = re.match(r"^[-*]\s+(.+)$", line)
        if m2:
            if not in_list:
                out.append("<ul class='exp-list' style='list-style:none;padding:0'>")
                in_list = True
            out.append(f"<li class='exp-item'>{_inline_md(_esc(m2.group(1)))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not line.strip():
            out.append("<div style='height:.5rem'></div>")
            continue
        out.append(f"<p style='margin:.2rem 0;font-size:14px;line-height:1.75'>{_inline_md(_esc(line))}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _has_meaningful_categories(results: list) -> bool:
    cats = {(r.get("category") or "").strip() for r in results}
    cats.discard("")
    cats.discard("General")
    return bool(cats)


def _answer_type(r: dict) -> str:
    """'visualization' if the result has any visual data, else 'text'."""
    if r.get("chartData") or r.get("gridData") or r.get("imagesData"):
        return "visualization"
    return "text"


def _tag_list(items: list | None, css_class: str = "tag") -> str:
    if not items:
        return ""
    return "".join(f'<span class="{css_class}">{_esc(i)}</span>' for i in items)


def _images_html(images_data: list | None) -> str:
    if not images_data:
        return ""
    parts = []
    for img in images_data:
        b64 = img.get("data", "")
        if b64:
            parts.append(
                f'<div class="chart-wrapper">'
                f'<img src="data:image/png;base64,{b64}" '
                f'style="max-width:100%;height:auto;display:block">'
                f'</div>'
            )
    return "\n".join(parts)


def _grid_data_html(grid_data) -> str:
    from core.results import parse_grid_data
    headers, rows = parse_grid_data(grid_data)
    if not headers or not rows:
        return ""
    MAX_VISIBLE = 20
    total = len(rows)
    header_html = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    rows_html = "".join(
        "<tr>" + "".join(f"<td>{_esc(v)}</td>" for v in row) + "</tr>"
        for row in rows
    )
    scroll = "overflow-x:auto;overflow-y:auto;max-height:520px" if total >= MAX_VISIBLE else "overflow-x:auto"
    thead_sticky = " style='position:sticky;top:0;z-index:1'" if total >= MAX_VISIBLE else ""
    note = (
        f"<p style='font-size:11.5px;color:var(--text-muted);margin-top:6px'>"
        f"{total} rows — scroll to see all</p>"
    ) if total >= MAX_VISIBLE else ""
    return (
        f"<div class='data-grid-wrapper' style='{scroll}'>"
        f"<table class='data-grid'>"
        f"<thead{thead_sticky}><tr>{header_html}</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        f"</table></div>{note}"
    )


def _sql_highlight(sql: str) -> str:
    """Wrap SQL keywords, strings, and numbers in highlight spans."""
    keywords = (
        r'\b(SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|FULL|CROSS|ON|AS|AND|OR|NOT|IN|'
        r'EXISTS|BETWEEN|LIKE|IS|NULL|CASE|WHEN|THEN|ELSE|END|GROUP BY|ORDER BY|'
        r'HAVING|LIMIT|OFFSET|UNION|ALL|DISTINCT|WITH|INSERT|UPDATE|DELETE|CREATE|DROP|'
        r'TABLE|VIEW|INDEX|SET|INTO|VALUES|BY|ASC|DESC|OVER|PARTITION|ROWS|RANGE|'
        r'PRECEDING|FOLLOWING|CURRENT ROW|UNBOUNDED|COUNT|SUM|AVG|MIN|MAX|COALESCE|'
        r'NULLIF|CAST|CONVERT|LOWER|UPPER|TRIM|SUBSTR|SUBSTRING|LENGTH|ROUND|FLOOR|'
        r'CEILING|DATEADD|DATEDIFF|GETDATE|NOW|YEAR|MONTH|DAY)\b'
    )
    s = _esc(sql)
    # Strings → green (match both single-quoted and HTML-entity-escaped quotes)
    s = re.sub(r"(&#x27;[^<]*?&#x27;|'[^']*')",
               r"<span class='sql-string'>\1</span>", s, flags=re.IGNORECASE)
    # Numbers → amber
    s = re.sub(r'\b(\d+\.?\d*)\b', r"<span class='sql-num'>\1</span>", s)
    # Keywords → blue
    s = re.sub(keywords, r"<span class='sql-kw'>\1</span>", s, flags=re.IGNORECASE)
    return s


# ── CSS ───────────────────────────────────────────────────────────────────────

def _css(style: dict) -> str:
    primary        = style["primary"]
    secondary      = style["secondary"]
    accent         = style["accent"]
    font           = style["font"]
    primary_dark   = hex_darken(primary,   0.55)
    primary_light  = hex_lighten(primary,  0.50)
    secondary_dark = hex_darken(secondary, 0.78)
    band_color     = hex_darken(secondary, 0.60)

    return f"""
:root{{
  --primary:{primary};--primary-dark:{primary_dark};--primary-light:{primary_light};
  --secondary:{secondary};--secondary-dark:{secondary_dark};
  --accent:{accent};--band:{band_color};
  --bg-warm:#f6f5f4;--bg-light:#f0ede9;--white:#fff;
  --text-muted:#7a6a5a;--border:#d1cbc4;--code-bg:#1e1a16;
  --green:#2e7d52;--green-bg:#e8f5ee;--red:#c62828;--red-bg:#fdecea;
  --font:'{font}',system-ui,sans-serif;
  --font-display:'Playfair Display',Georgia,serif;
  --font-mono:'JetBrains Mono','Fira Mono',monospace;
}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:var(--font);font-weight:300;color:var(--primary);background:var(--white);-webkit-font-smoothing:antialiased}}
a{{color:var(--secondary);text-decoration:none}}
a:hover{{text-decoration:underline}}

/* ── Utility bar ── */
.utility-bar{{background:var(--primary-dark);color:rgba(255,255,255,.65);font-size:12px;letter-spacing:.06em;display:flex;justify-content:flex-end;align-items:center;gap:24px;padding:7px 48px}}
.utility-bar a{{color:rgba(255,255,255,.65);text-decoration:none}}

/* ── Site header ── */
.site-header{{background:var(--white);border-bottom:1px solid var(--border);padding:0 48px;display:flex;align-items:center;justify-content:space-between;height:80px;position:sticky;top:0;z-index:100;box-shadow:0 1px 8px rgba(0,0,0,.07)}}
.logo-area{{display:flex;align-items:center;gap:14px;text-decoration:none}}
.logo-icon{{width:48px;height:48px}}
.brand-name{{font-family:var(--font-display);font-size:22px;font-weight:400;color:var(--primary-dark)}}
.brand-sub{{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--primary-light);display:block;margin-top:2px}}
.main-nav{{display:flex;align-items:center;gap:32px}}
.main-nav a{{font-size:13.5px;font-weight:400;letter-spacing:.04em;text-transform:uppercase;color:var(--primary);text-decoration:none;padding-bottom:4px;border-bottom:2px solid transparent;transition:border-color .2s,color .2s}}
.main-nav a:hover,.main-nav a.active{{border-bottom-color:var(--secondary);color:var(--secondary)}}

/* ── Badges ── */
.badge{{display:inline-block;padding:3px 10px;font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase}}
.badge-pass{{background:var(--green-bg);color:var(--green)}}
.badge-fail{{background:var(--red-bg);color:var(--red)}}
.badge-vis{{background:rgba(0,129,143,.1);color:var(--accent)}}
.badge-md{{background:rgba(0,0,0,.06);color:var(--primary)}}

/* ── Breadcrumb ── */
.breadcrumb{{padding:12px 48px;font-size:11.5px;color:var(--text-muted);background:var(--bg-warm);border-bottom:1px solid var(--border)}}
.breadcrumb a{{color:var(--secondary);text-decoration:none}}
.breadcrumb span{{margin:0 6px}}

/* ── Footer ── */
.site-footer{{background:var(--bg-warm);padding:40px 48px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--text-muted)}}
.footer-links{{display:flex;gap:20px}}
.footer-links a{{color:var(--text-muted);text-decoration:none}}

/* ── Index: Hero ── */
.hero{{background:var(--primary-dark);padding:80px 48px 88px;position:relative;overflow:hidden}}
.hero::before{{content:'';position:absolute;right:-80px;top:-40px;width:560px;height:560px;border-radius:50%;background:radial-gradient(circle,rgba(0,124,186,.18) 0%,transparent 70%)}}
.hero-eyebrow{{font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);font-weight:500;margin-bottom:12px}}
.hero h1{{font-family:var(--font-display);font-size:54px;font-weight:300;color:#fff;line-height:1.1;max-width:700px;margin-bottom:20px}}
.hero-sub{{font-size:17px;font-weight:300;color:rgba(255,255,255,.62);max-width:560px;line-height:1.6;margin-bottom:36px}}
.hero-cta{{display:inline-flex;gap:16px}}
.btn-primary{{background:var(--secondary);color:#fff;font-size:13px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;padding:12px 28px;text-decoration:none;display:inline-block}}
.btn-primary:hover{{text-decoration:none;opacity:.9}}

/* ── Index: Stats strip ── */
.stats-strip{{background:var(--primary-dark);border-top:1px solid rgba(255,255,255,.08);padding:28px 48px;display:flex;gap:0}}
.stat-item{{flex:1;padding:0 32px;border-right:1px solid rgba(255,255,255,.1)}}
.stat-item:first-child{{padding-left:0}}
.stat-item:last-child{{border-right:none}}
.stat-num{{font-family:var(--font-display);font-size:36px;font-weight:300;color:#fff;line-height:1}}
.stat-label{{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:rgba(255,255,255,.42);margin-top:6px;font-weight:500}}

/* ── Index: Category section ── */
.cat-section{{padding:52px 48px 0}}
.cat-section-head{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:28px}}
.section-title{{font-family:var(--font-display);font-size:30px;font-weight:300;color:var(--primary-dark)}}
.section-sub{{font-size:13px;color:var(--text-muted)}}
.cat-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:0;background:var(--white)}}
.cat-card{{background:var(--white);padding:24px 26px;display:flex;flex-direction:column;gap:10px;border:1px solid var(--border);margin:-1px 0 0 -1px}}
.cat-card-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}
.cat-name{{font-family:var(--font-display);font-size:17px;font-weight:400;color:var(--primary-dark);line-height:1.25;flex:1}}
.cat-count{{font-family:var(--font-display);font-size:28px;font-weight:300;color:var(--secondary);line-height:1;white-space:nowrap}}
.cat-count-label{{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--text-muted);margin-top:2px;text-align:right}}
.cat-badges{{display:flex;gap:6px;flex-wrap:wrap}}
.cat-links{{display:flex;gap:10px;flex-wrap:wrap;margin-top:4px;border-top:1px solid var(--bg-light);padding-top:10px}}
.cat-link{{font-size:11.5px;color:var(--secondary)}}
.cat-link:hover{{text-decoration:underline}}
.cat-link-sep{{font-size:11px;color:var(--border)}}
.cat-indicators{{display:flex;gap:12px;font-size:11.5px;color:var(--text-muted)}}
.cat-ind{{display:flex;align-items:center;gap:4px}}
.cat-ind-dot{{width:5px;height:5px;border-radius:50%;flex-shrink:0}}
.cat-ind-dot.has{{background:var(--accent)}}
.cat-ind-dot.none{{background:var(--border)}}

/* ── Index: Accent band ── */
.accent-band{{background:var(--band);padding:52px 48px;margin-top:52px}}
.accent-band-inner{{max-width:1240px;margin:0 auto;display:flex;justify-content:space-between;align-items:center;gap:40px}}
.band-title{{font-family:var(--font-display);font-size:28px;font-weight:300;color:#fff;max-width:480px;line-height:1.25}}
.band-stats{{display:flex;gap:40px}}
.band-stat-num{{font-family:var(--font-display);font-size:40px;font-weight:300;color:#fff}}
.band-stat-label{{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:rgba(255,255,255,.6);margin-top:4px}}

/* ── Index: Results table ── */
.table-section{{max-width:1240px;margin:0 auto;padding:60px 48px 80px}}
.section-head{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:28px}}
.results-table{{width:100%;border-collapse:collapse;font-size:13px}}
.results-table thead tr{{background:var(--bg-warm)}}
.results-table th{{padding:12px 16px;text-align:left;font-size:10px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--primary-dark);border-bottom:2px solid var(--border)}}
.results-table th.num,.results-table td.num{{text-align:right}}
.results-table td{{padding:12px 16px;border-bottom:1px solid var(--bg-light);color:var(--primary);vertical-align:middle}}
.results-table tr:last-child td{{border-bottom:none}}
.results-table tr:hover td{{background:rgba(246,245,244,.55)}}
.results-table td a{{color:var(--secondary);font-weight:400}}
.results-table td a:hover{{text-decoration:underline}}
.id-cell{{font-family:var(--font-display);font-size:16px;font-weight:300;color:var(--primary-light)}}

/* ── Detail: Page hero ── */
.page-hero{{background:var(--primary-dark);padding:44px 48px 52px;position:relative;overflow:hidden}}
.page-hero::before{{content:'';position:absolute;right:0;top:0;width:40%;height:100%;opacity:.15}}
.page-hero.vis::before{{background:linear-gradient(135deg,var(--secondary) 0%,transparent 70%)}}
.page-hero.txt::before{{background:linear-gradient(135deg,var(--accent) 0%,transparent 70%)}}
.test-eyebrow{{font-size:11px;letter-spacing:.18em;text-transform:uppercase;margin-bottom:10px;font-weight:500}}
.page-hero.vis .test-eyebrow{{color:var(--accent)}}
.page-hero.txt .test-eyebrow{{color:rgba(255,255,255,.55)}}
.page-hero h1{{font-family:var(--font-display);font-size:40px;font-weight:300;color:#fff;line-height:1.18;margin-bottom:18px;max-width:800px}}
.hero-meta-row{{display:flex;gap:32px;flex-wrap:wrap;align-items:center}}
.hero-meta-item{{display:flex;flex-direction:column;gap:3px}}
.hero-meta-key{{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.42);font-weight:500}}
.hero-meta-val{{font-size:14.5px;color:rgba(255,255,255,.88);font-weight:400}}

/* ── Detail: Page body ── */
.page-body{{max-width:1240px;margin:0 auto;padding:44px 48px 80px}}
.two-col{{display:grid;grid-template-columns:2fr 1fr;gap:48px;align-items:start}}
.section-label{{font-size:10px;letter-spacing:.16em;text-transform:uppercase;font-weight:600;color:var(--primary-light);margin-bottom:8px}}
.section-divider{{border:none;border-top:1px solid var(--border);margin:38px 0}}
h2.field-title{{font-family:var(--font-display);font-size:24px;font-weight:300;color:var(--primary-dark);margin-bottom:12px}}
.question-box{{border-left:3px solid var(--accent);padding:18px 22px;background:rgba(0,0,0,.03);font-size:15px;line-height:1.7;color:var(--primary-dark);font-style:italic}}
.interpreted-box{{border-left:3px solid var(--secondary);padding:18px 22px;background:rgba(0,0,0,.02);font-size:14px;line-height:1.7;color:var(--primary)}}
.response-box{{background:var(--bg-warm);padding:24px 26px;font-size:14.5px;line-height:1.78;color:var(--primary)}}
.response-box p{{margin-bottom:12px}}.response-box p:last-child{{margin-bottom:0}}
.response-box strong{{font-weight:600;color:var(--primary-dark)}}
.error-box{{border-left:3px solid var(--red);padding:18px 22px;background:var(--red-bg);font-size:14px;line-height:1.7;color:var(--red)}}

/* ── Detail: Info panel ── */
.info-panel{{background:var(--bg-warm)}}
.info-row{{padding:14px 18px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:baseline;gap:12px}}
.info-row:last-child{{border-bottom:none}}
.info-key{{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--text-muted);font-weight:600;white-space:nowrap}}
.info-val{{font-size:13.5px;color:var(--primary-dark);font-weight:400;text-align:right;word-break:break-word}}

/* ── Detail: Insights ── */
.insights-list{{list-style:none;display:flex;flex-direction:column}}
.insight-item{{padding:14px 0;border-bottom:1px solid var(--bg-light);font-size:14px;line-height:1.65;color:var(--primary);display:flex;gap:12px;align-items:flex-start}}
.insight-item:last-child{{border-bottom:none}}
.insight-dot{{width:6px;height:6px;background:var(--accent);border-radius:50%;flex-shrink:0;margin-top:7px}}
.exp-list{{list-style:none;padding:0;margin:.4rem 0}}
.exp-item{{display:flex;gap:14px;align-items:flex-start;padding:7px 0;font-size:14px;line-height:1.68;color:var(--primary);border-bottom:1px solid var(--bg-warm)}}
.exp-item:last-child{{border-bottom:none}}
.exp-item::before{{content:'';display:block;width:5px;height:5px;background:var(--secondary);border-radius:50%;flex-shrink:0;margin-top:8px}}

/* ── Detail: Data / charts ── */
.chart-wrapper{{border:1px solid var(--border);background:var(--white);padding:4px;margin:20px 0;overflow-x:auto}}
.data-grid-wrapper{{overflow-x:auto;margin:20px 0;border:1px solid var(--border)}}
.data-grid{{width:100%;border-collapse:collapse;font-size:13px;background:var(--white)}}
.data-grid thead tr{{background:var(--bg-warm)}}
.data-grid th{{padding:11px 16px;text-align:left;font-size:10px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--primary-dark);border-bottom:2px solid var(--border);white-space:nowrap}}
.data-grid td{{padding:11px 16px;border-bottom:1px solid var(--bg-light);color:var(--primary);vertical-align:middle}}
.data-grid tr:last-child td{{border-bottom:none}}
.data-grid tr:hover td{{background:rgba(246,245,244,.55)}}

/* ── Detail: Tags ── */
.tag-list{{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}}
.tag{{display:inline-block;padding:3px 9px;font-size:11px;font-weight:500;background:var(--bg-warm);color:var(--primary);border:1px solid var(--border)}}
.tag.metric{{background:#e3f2fd;border-color:#90caf9;color:#1565c0}}

/* ── Detail: SQL ── */
.sql-section{{background:var(--code-bg)}}
.sql-header{{display:flex;align-items:center;justify-content:space-between;padding:14px 24px;background:rgba(255,255,255,.04);border-bottom:1px solid rgba(255,255,255,.07)}}
.sql-header-left{{display:flex;align-items:center;gap:12px}}
.sql-traffic-lights{{display:flex;gap:6px}}
.sql-dot{{width:11px;height:11px;border-radius:50%}}
.sql-dot-red{{background:#ff5f57}}.sql-dot-amber{{background:#ffbd2e}}.sql-dot-green{{background:#28c841}}
.sql-lang-label{{font-family:var(--font-mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:rgba(255,255,255,.35);font-weight:500}}
.sql-copy-btn{{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);color:rgba(255,255,255,.5);font-family:var(--font);font-size:11px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;padding:5px 12px;cursor:pointer;transition:background .2s,color .2s}}
.sql-copy-btn:hover{{background:rgba(255,255,255,.13);color:rgba(255,255,255,.85)}}
.sql-copy-btn.copied{{color:#28c841;border-color:rgba(40,200,65,.3)}}
.sql-body{{padding:24px 28px 28px;overflow-x:auto}}
.sql-block{{font-family:var(--font-mono);font-size:13.5px;line-height:1.85;color:rgba(255,255,255,.78);white-space:pre-wrap;word-break:break-all}}
.sql-kw{{color:#6eb3f7;font-weight:500}}
.sql-string{{color:#8de3b0}}
.sql-num{{color:#f4a261}}
.sql-separator{{height:1px;background:rgba(255,255,255,.07);margin:20px 0}}
.sql-query-num{{font-family:var(--font-mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.2);margin-bottom:10px}}

/* ── Detail: Test nav ── */
.test-nav{{display:flex;gap:1px;background:var(--border)}}
.test-nav-item{{flex:1;background:var(--white);padding:18px 22px;text-decoration:none;display:flex;flex-direction:column;gap:4px;transition:background .2s}}
.test-nav-item:hover{{background:var(--bg-warm);text-decoration:none}}
.test-nav-dir{{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--text-muted);font-weight:600}}
.test-nav-title{{font-size:14.5px;color:var(--secondary);font-weight:400}}
.test-nav-item.next{{text-align:right}}
.test-nav-spacer{{flex:1;background:var(--white);padding:18px 22px}}

@media(max-width:900px){{
  .two-col{{grid-template-columns:1fr}}
  .hero{{padding:48px 24px 56px}}.hero h1{{font-size:36px}}
  .page-body,.table-section,.cat-section{{padding-left:24px;padding-right:24px}}
  .site-header,.utility-bar,.breadcrumb,.stats-strip,.accent-band{{padding-left:24px;padding-right:24px}}
}}
"""


# ── Shared page chrome ────────────────────────────────────────────────────────

def _logo_svg(style: dict) -> str:
    pd = hex_darken(style["primary"], 0.55)
    return (
        f'<svg class="logo-icon" viewBox="0 0 52 52" fill="none">'
        f'<circle cx="26" cy="26" r="25" fill="none" stroke="{pd}" stroke-width="1.5"/>'
        f'<path d="M14 36V22l12-8 12 8v14" stroke="{pd}" stroke-width="1.5" '
        f'stroke-linejoin="round" fill="none"/>'
        f'<path d="M22 36v-8h8v8" stroke="{pd}" stroke-width="1.5" '
        f'stroke-linejoin="round" fill="none"/>'
        f'<circle cx="26" cy="18" r="2" fill="{style["secondary"]}"/>'
        f'</svg>'
    )


def _chrome(style: dict, active_nav: str = "overview") -> str:
    name   = _esc(style["name"])
    ov_cls = "active" if active_nav == "overview" else ""
    ts_cls = "active" if active_nav == "tests"    else ""
    return f"""<div class="utility-bar">
  <a href="index.html">Overview</a>
  <a href="#results">All Results</a>
</div>
<header class="site-header">
  <a href="index.html" class="logo-area">
    {_logo_svg(style)}
    <div>
      <div class="brand-name">{name}</div>
      <span class="brand-sub">Agent Test Results</span>
    </div>
  </a>
  <nav class="main-nav">
    <a href="index.html" class="{ov_cls}">Overview</a>
    <a href="#results" class="{ts_cls}">Tests</a>
  </nav>
</header>"""


def _footer(style: dict) -> str:
    return f"""<footer class="site-footer">
  <span>Generated by Tallmadge CLI · {_esc(style["name"])}</span>
  <div class="footer-links">
    <a href="index.html">Overview</a>
    <a href="#results">All Results</a>
  </div>
</footer>"""


def _page_shell(title: str, style: dict, body_html: str,
                active_nav: str = "overview") -> str:
    font = style["font"]
    # Build Google Fonts URL for the body font alongside Playfair Display + JetBrains Mono
    safe = font.replace(" ", "+")
    gf_url = (
        f"https://fonts.googleapis.com/css2?"
        f"family=Playfair+Display:wght@300;400;500"
        f"&family={safe}:wght@300;400;500;600"
        f"&family=JetBrains+Mono:wght@400;500&display=swap"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="{gf_url}" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>{_css(style)}</style>
</head>
<body>
{_chrome(style, active_nav)}
{body_html}
{_footer(style)}
</body>
</html>"""


# ── Index page ────────────────────────────────────────────────────────────────

def _cat_section_html(results: list) -> str:
    """Build the 'Results by Category' section. Returns '' when no meaningful cats."""
    cats: dict[str, list] = {}
    for r in results:
        c = (r.get("category") or "General").strip()
        cats.setdefault(c, []).append(r)

    meaningful = {k: v for k, v in cats.items() if k not in ("", "General")}
    if not meaningful:
        return ""

    n_cats  = len(meaningful)
    n_tests = len(results)

    cards_html = ""
    for cat_name, cat_results in sorted(meaningful.items()):
        n          = len(cat_results)
        count_lbl  = "test" if n == 1 else "tests"
        vis_count  = sum(1 for r in cat_results if _answer_type(r) == "visualization")
        txt_count  = n - vis_count
        has_charts = any(r.get("chartData") for r in cat_results)
        has_grids  = any(r.get("gridData")  for r in cat_results)

        badges = ""
        if vis_count:
            badges += (f'<span class="badge badge-vis">'
                       f'{vis_count} Visualization{"s" if vis_count > 1 else ""}</span>')
        if txt_count:
            badges += f'<span class="badge badge-md">{txt_count} Text</span>'

        links = ""
        for r in cat_results:
            if links:
                links += ' <span class="cat-link-sep">·</span> '
            links += f'<a href="prompt-{r["id"]}.html" class="cat-link">#{r["id"]:02d}</a>'

        cards_html += f"""<div class="cat-card">
  <div class="cat-card-head">
    <div class="cat-name">{_esc(cat_name)}</div>
    <div><div class="cat-count">{n}</div><div class="cat-count-label">{count_lbl}</div></div>
  </div>
  <div class="cat-badges">{badges}</div>
  <div class="cat-indicators">
    <span class="cat-ind">
      <span class="cat-ind-dot {'has' if has_charts else 'none'}"></span>Charts
    </span>
    <span class="cat-ind">
      <span class="cat-ind-dot {'has' if has_grids else 'none'}"></span>Data grids
    </span>
  </div>
  <div class="cat-links">{links}</div>
</div>"""

    return f"""<div class="cat-section">
  <div class="cat-section-head">
    <div class="section-title">Results by Category</div>
    <div class="section-sub">
      {n_cats} {"category" if n_cats == 1 else "categories"} · {n_tests} tests
    </div>
  </div>
  <div class="cat-grid">{cards_html}</div>
</div>"""


def _index_page(results: list, style: dict, run_date: str, mode: str) -> str:
    total     = len(results)
    passed    = sum(1 for r in results if r["status"] == "Success")
    errors    = total - passed
    vis_total = sum(1 for r in results if _answer_type(r) == "visualization")
    txt_total = total - vis_total

    cats = {(r.get("category") or "").strip() for r in results}
    cats.discard("")
    cats.discard("General")
    n_cats = len(cats)

    # ── Hero ──────────────────────────────────────────────────────────────────
    hero_html = f"""<div class="hero">
  <div class="hero-eyebrow">Agent Test Results</div>
  <h1>{_esc(style["name"])}<br>Test Results</h1>
  <p class="hero-sub">Evaluation of AI agent prompts — covering SQL generation,
    data visualization, insight quality, and response accuracy.</p>
  <div class="hero-cta">
    <a href="#results" class="btn-primary">View All Results →</a>
  </div>
</div>"""

    # ── Stats strip ───────────────────────────────────────────────────────────
    cat_stat = (
        f'<div class="stat-item">'
        f'<div class="stat-num">{n_cats}</div>'
        f'<div class="stat-label">Categories</div>'
        f'</div>'
    ) if n_cats else ""

    stats_html = f"""<div class="stats-strip">
  <div class="stat-item"><div class="stat-num">{total}</div><div class="stat-label">Total Tests</div></div>
  <div class="stat-item"><div class="stat-num">{passed}</div><div class="stat-label">Passed</div></div>
  <div class="stat-item"><div class="stat-num">{errors}</div><div class="stat-label">Errors</div></div>
  {cat_stat}
  <div class="stat-item"><div class="stat-num">{_esc(run_date)}</div><div class="stat-label">Run Date</div></div>
  <div class="stat-item"><div class="stat-num">{_esc(mode.upper())}</div><div class="stat-label">Mode</div></div>
</div>"""

    # ── Category section (conditional) ───────────────────────────────────────
    cat_html = _cat_section_html(results)

    # ── Accent band ───────────────────────────────────────────────────────────
    status_word = "successfully" if errors == 0 else f"with {errors} error{'s' if errors > 1 else ''}"
    cat_band_stat = (
        f'<div><div class="band-stat-num">{n_cats}</div>'
        f'<div class="band-stat-label">Categories</div></div>'
    ) if n_cats else ""

    band_html = f"""<div class="accent-band">
  <div class="accent-band-inner">
    <div class="band-title">
      All {total} agent tests completed {status_word} with SQL tracing and insight generation.
    </div>
    <div class="band-stats">
      <div><div class="band-stat-num">{vis_total}</div><div class="band-stat-label">Visualizations</div></div>
      <div><div class="band-stat-num">{txt_total}</div><div class="band-stat-label">Text</div></div>
      {cat_band_stat}
    </div>
  </div>
</div>"""

    # ── Results table ─────────────────────────────────────────────────────────
    show_cat_col = bool(n_cats)
    thead_cat    = "<th>Category</th>" if show_cat_col else ""
    rows_html    = ""
    for r in results:
        rid         = r["id"]
        atype       = _answer_type(r)
        status_cls  = "badge-pass" if r["status"] == "Success" else "badge-fail"
        atype_cls   = "badge-vis"  if atype == "visualization"  else "badge-md"
        has_chart   = "✓" if r.get("chartData") else "–"
        has_grid    = "✓" if r.get("gridData")  else "–"
        cat_td      = f"<td>{_esc(r.get('category',''))}</td>" if show_cat_col else ""
        rows_html += f"""<tr>
  <td class="id-cell">{rid:02d}</td>
  <td><a href="prompt-{rid}.html">{_esc(r['prompt'])}</a></td>
  {cat_td}
  <td><span class="badge {status_cls}">{_esc(r['status'])}</span></td>
  <td><span class="badge {atype_cls}">{atype}</span></td>
  <td class="num">{has_chart}</td>
  <td class="num">{has_grid}</td>
</tr>"""

    table_html = f"""<div id="results">
<div class="table-section">
  <div class="section-head">
    <div class="section-title">All Test Results</div>
    <div class="section-sub">{total} tests · {_esc(run_date)}</div>
  </div>
  <table class="results-table">
    <thead><tr>
      <th>#</th><th>Prompt</th>{thead_cat}
      <th>Status</th><th>Answer Type</th>
      <th class="num">Chart</th><th class="num">Grid</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
</div>"""

    body = hero_html + stats_html + cat_html + band_html + table_html
    return _page_shell(f"Agent Test Results — {style['name']}", style, body, "overview")


# ── Detail pages ─────────────────────────────────────────────────────────────

def _sql_section_html(sql_queries: list, script: bool = True) -> str:
    """Render a contained SQL block (sits inside page-body, scrolls horizontally)."""
    if not sql_queries:
        return ""
    blocks = ""
    for i, q in enumerate(sql_queries):
        formatted   = format_sql(q)
        highlighted = _sql_highlight(formatted)
        sep  = '<div class="sql-separator"></div>' if i > 0 else ""
        num  = (f'<div class="sql-query-num">Query {i+1}</div>'
                if len(sql_queries) > 1 else "")
        blocks += f"{sep}{num}<div class='sql-block' id='sql-block-{i}'>{highlighted}</div>"

    js = """
<script>
function copySql(btn,id){
  const el=document.getElementById(id);
  if(!el)return;
  navigator.clipboard.writeText(el.innerText).then(()=>{
    btn.textContent='Copied!';btn.classList.add('copied');
    setTimeout(()=>{btn.textContent='Copy';btn.classList.remove('copied');},1800);
  });
}
</script>""" if script else ""

    return f"""<div class="sql-section">
  <div class="sql-header">
    <div class="sql-header-left">
      <div class="sql-traffic-lights">
        <div class="sql-dot sql-dot-red"></div>
        <div class="sql-dot sql-dot-amber"></div>
        <div class="sql-dot sql-dot-green"></div>
      </div>
      <span class="sql-lang-label">SQL</span>
    </div>
    <button class="sql-copy-btn" onclick="copySql(this,'sql-block-0')">Copy</button>
  </div>
  <div class="sql-body" style="overflow-x:auto">{blocks}</div>
</div>{js}"""


def _detail_page(r: dict, results: list, i: int, style: dict,
                 show_cats: bool) -> str:
    rid      = r["id"]
    atype    = _answer_type(r)
    hero_cls = "vis" if atype == "visualization" else "txt"
    is_ok    = r["status"] == "Success"

    # ── Eyebrow ──────────────────────────────────────────────────────────────
    eyebrow_parts = [f"Test {rid:02d}"]
    if show_cats and r.get("category"):
        eyebrow_parts.append(_esc(r["category"]))
    eyebrow_parts.append("Visualization" if atype == "visualization" else "Text")
    eyebrow = " · ".join(eyebrow_parts)

    # ── Hero meta ────────────────────────────────────────────────────────────
    meta_items = []
    if show_cats and r.get("category"):
        meta_items.append(
            f'<div class="hero-meta-item">'
            f'<span class="hero-meta-key">Category</span>'
            f'<span class="hero-meta-val">{_esc(r["category"])}</span>'
            f'</div>'
        )
    meta_items.append(
        f'<div class="hero-meta-item">'
        f'<span class="hero-meta-key">Status</span>'
        f'<span class="hero-meta-val">'
        f'<span class="badge {"badge-pass" if is_ok else "badge-fail"}">'
        f'{_esc(r["status"])}</span></span></div>'
    )
    meta_items.append(
        f'<div class="hero-meta-item">'
        f'<span class="hero-meta-key">Response Time</span>'
        f'<span class="hero-meta-val">{r.get("responseTime", "–")}s</span>'
        f'</div>'
    )
    if r.get("isCacheUsed") is not None:
        meta_items.append(
            f'<div class="hero-meta-item">'
            f'<span class="hero-meta-key">Cache</span>'
            f'<span class="hero-meta-val">{"Yes" if r["isCacheUsed"] else "No"}</span>'
            f'</div>'
        )

    hero_html = f"""<div class="page-hero {hero_cls}">
  <div class="test-eyebrow">{eyebrow}</div>
  <h1>{_esc(r['prompt'])}</h1>
  <div class="hero-meta-row">{"".join(meta_items)}</div>
</div>"""

    breadcrumb = f"""<div class="breadcrumb">
  <a href="index.html">Home</a><span>›</span>
  <a href="index.html">Tests</a><span>›</span>
  Test {rid:02d}
</div>"""

    # ── TOP two-col: prompt/interpretation (left) + info panel (right) ───────
    top_left = []
    top_left.append(
        f'<div class="section-label">Original Prompt</div>'
        f'<div class="question-box">{_esc(r["prompt"])}</div>'
    )
    if r.get("interpretedQuestion"):
        top_left.append(
            f'<hr class="section-divider">'
            f'<div class="section-label">Interpreted Question</div>'
            f'<div class="interpreted-box">{_esc(r["interpretedQuestion"])}</div>'
        )

    # Info panel rows (scalar values)
    attrs   = r.get("attributesUsed")   or []
    metrics = r.get("metricsUsed")      or []

    info_rows = [
        ("Test ID",       f"#{rid:02d}"),
        ("Answer Type",   "Visualization" if atype == "visualization" else "Text"),
        ("Response Time", f"{r.get('responseTime','–')}s"),
        ("Mode",          r.get("mode", "–")),
    ]
    if r.get("isCacheUsed") is not None:
        info_rows.append(("Cache", "Yes" if r["isCacheUsed"] else "No"))

    info_body = "".join(
        f'<div class="info-row">'
        f'<span class="info-key">{_esc(k)}</span>'
        f'<span class="info-val">{_esc(v)}</span>'
        f'</div>'
        for k, v in info_rows
    )

    # Attributes and metrics as tag rows inside the info panel
    if attrs:
        tags_html = "".join(
            f'<span class="tag" style="margin:2px 2px 0 0">{_esc(a)}</span>'
            for a in attrs
        )
        info_body += (
            f'<div class="info-row" style="align-items:flex-start">'
            f'<span class="info-key">Attributes</span>'
            f'<div style="display:flex;flex-wrap:wrap;justify-content:flex-end;flex:1;'
            f'margin-left:12px">{tags_html}</div>'
            f'</div>'
        )
    if metrics:
        tags_html = "".join(
            f'<span class="tag metric" style="margin:2px 2px 0 0">{_esc(m)}</span>'
            for m in metrics
        )
        info_body += (
            f'<div class="info-row" style="align-items:flex-start">'
            f'<span class="info-key">Metrics</span>'
            f'<div style="display:flex;flex-wrap:wrap;justify-content:flex-end;flex:1;'
            f'margin-left:12px">{tags_html}</div>'
            f'</div>'
        )

    top_right = (
        f'<div class="section-label">Test Details</div>'
        f'<div class="info-panel">{info_body}</div>'
    )

    # ── BOTTOM single column: Response → Error → Viz → Insights → Explanation ─
    bottom = []

    if r.get("responseText"):
        bottom.append(
            f'<hr class="section-divider">'
            f'<h2 class="field-title">Response</h2>'
            f'<div class="response-box"><p>{_nl2br(r["responseText"])}</p></div>'
        )

    if r.get("error"):
        bottom.append(
            f'<hr class="section-divider">'
            f'<div class="section-label" style="color:var(--red)">Error</div>'
            f'<div class="error-box">{_nl2br(r["error"])}</div>'
        )

    # Visual data
    imgs_html  = _images_html(r.get("imagesData"))
    grid_html  = _grid_data_html(r.get("gridData"))
    chart_html = ""
    if r.get("chartData"):
        chart_data = r["chartData"]
        charts = chart_data.get("charts") or (
            chart_data if isinstance(chart_data, list) else [chart_data]
        )
        for ch in (charts if isinstance(charts, list) else [charts]):
            data_rows = ch.get("data", [])
            option    = ch.get("option", {})
            columns   = option.get("columns", [])
            if data_rows and columns:
                hdr = "".join(f"<th>{_esc(c['column_name'])}</th>" for c in columns)
                bdy = "".join(
                    "<tr>" + "".join(
                        f"<td>{_esc(row.get(c['column_name'],''))}</td>"
                        for c in columns
                    ) + "</tr>"
                    for row in data_rows
                )
                chart_html += (
                    f"<div class='data-grid-wrapper'>"
                    f"<table class='data-grid'>"
                    f"<thead><tr>{hdr}</tr></thead>"
                    f"<tbody>{bdy}</tbody>"
                    f"</table></div>"
                )

    vis_parts = [p for p in [imgs_html, chart_html, grid_html] if p]
    if vis_parts:
        bottom.append(
            f'<hr class="section-divider">'
            f'<h2 class="field-title">Data &amp; Visualizations</h2>'
            + "\n".join(vis_parts)
        )

    # Insights — response-box format
    if r.get("insights"):
        bottom.append(
            f'<hr class="section-divider">'
            f'<h2 class="field-title">Insights</h2>'
            f'<div class="response-box"><p>{_nl2br(r["insights"])}</p></div>'
        )

    # Explanation — response-box format
    if r.get("explanation"):
        bottom.append(
            f'<hr class="section-divider">'
            f'<h2 class="field-title">Explanation</h2>'
            f'<div class="response-box"><p>{_nl2br(r["explanation"])}</p></div>'
        )

    # SQL — contained inside page-body, scrollable
    sql_html = _sql_section_html(r.get("sqlQueries") or [], script=True)
    if sql_html:
        bottom.append(
            f'<hr class="section-divider">'
            f'<h2 class="field-title">SQL</h2>'
            + sql_html
        )

    # ── Test nav ─────────────────────────────────────────────────────────────
    prev_r = results[i - 1] if i > 0              else None
    next_r = results[i + 1] if i < len(results)-1 else None

    prev_html = (
        f'<a href="prompt-{prev_r["id"]}.html" class="test-nav-item">'
        f'<span class="test-nav-dir">← Previous</span>'
        f'<span class="test-nav-title">{_esc(prev_r["prompt"][:70])}</span>'
        f'</a>'
        if prev_r else '<div class="test-nav-spacer"></div>'
    )
    next_html = (
        f'<a href="prompt-{next_r["id"]}.html" class="test-nav-item next">'
        f'<span class="test-nav-dir">Next →</span>'
        f'<span class="test-nav-title">{_esc(next_r["prompt"][:70])}</span>'
        f'</a>'
        if next_r else '<div class="test-nav-spacer"></div>'
    )

    body = f"""{breadcrumb}
{hero_html}
<div class="page-body">
  <div class="two-col">
    <div>{"".join(top_left)}</div>
    <div>{top_right}</div>
  </div>
  {"".join(bottom)}
</div>
<div class="test-nav">{prev_html}{next_html}</div>"""

    return _page_shell(
        f"Test {rid:02d} — {r['prompt'][:55]} | {style['name']}",
        style, body, "tests"
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def render(envelope: dict, style: dict, out_dir: Path | None = None) -> Path:
    """Generate the full HTML site from a results envelope. Returns output directory."""
    results   = envelope.get("results", [])
    meta      = envelope.get("meta", {})
    _raw_date = meta.get("runDate", "")
    run_date  = _raw_date[:10]
    run_ts    = (_raw_date[:19].replace("T", "_").replace(":", "-")
                 if len(_raw_date) >= 19 else run_date)
    mode      = meta.get("mode", "api")

    if out_dir is None:
        out_dir = OUTPUT_BASE / f"web_{run_ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    show_cats = _has_meaningful_categories(results)

    for i, r in enumerate(results):
        page = _detail_page(r, results, i, style, show_cats)
        (out_dir / f"prompt-{r['id']}.html").write_text(page, encoding="utf-8")

    index_page = _index_page(results, style, run_date, mode)
    (out_dir / "index.html").write_text(index_page, encoding="utf-8")

    return out_dir
