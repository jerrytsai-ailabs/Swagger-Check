import html
import re
from collections import Counter
from datetime import datetime, timezone

from .config import derive_swagger_ui_url

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_SEVERITY_LABEL = {"error": "Error", "warning": "Warning", "info": "Info"}
_METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}


def _esc(value):
    return html.escape(str(value)) if value is not None else ""


_BREAK_AFTER_RE = re.compile(r"([./_:{])")


def _esc_breakable(value):
    """跟 _esc 一樣會 escape，但在 . _ : {{ 這些識別字常見的分隔字元後面插入 <wbr>，
    讓瀏覽器斷行斷在字義邊界（例如 responses.200.conversation 斷在句點後面），
    而不是 overflow-wrap: anywhere 保底時那種斷在字中間的難讀斷法。
    """
    escaped = _esc(value)
    return _BREAK_AFTER_RE.sub(r"\1<wbr>", escaped)


def compute_endpoint_pass_fail(findings):
    """每支 endpoint 一個 Pass/Fail，把 LLM 文字審查的結果跟其他階段（static/live/live_write）
    的 Error 等級發現合併看：只要同一支 endpoint 有 llm_review_fail，或任何階段對它回報一筆
    Error，就算 Fail；兩種原因都沒有才算 Pass。範圍是「這支 endpoint 至少被某個階段點名過」
    （出現在 llm 審查結果，或是有一筆帶 path/method 的 Error），不含完全沒被檢查到的端點。
    """
    llm_status = {}
    endpoints_with_error = set()
    for f in findings:
        key = (f.get("path"), f.get("method"))
        if key == (None, None):
            continue
        if f["rule"] == "llm_review_pass":
            llm_status[key] = "pass"
        elif f["rule"] == "llm_review_fail":
            llm_status[key] = "fail"
        if f["severity"] == "error":
            endpoints_with_error.add(key)

    universe = set(llm_status) | endpoints_with_error
    fail_count = sum(1 for key in universe if llm_status.get(key) == "fail" or key in endpoints_with_error)
    total = len(universe)
    return total - fail_count, fail_count, total


def build_summary(findings, entries):
    by_severity = Counter(f["severity"] for f in findings)
    by_file = Counter(f["file"] for f in findings)
    real_files = [e for e in entries if e["is_real"]]
    ok_files = [e for e in real_files if not e["error"] and by_file.get(e["filename"], 0) == 0]
    return {
        "total": len(findings),
        "by_severity": by_severity,
        "by_file": by_file,
        "num_real_files": len(real_files),
        "num_clean_files": len(ok_files),
        "generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
    }


def _environment_label(base_url):
    host = re.sub(r"^https?://", "", base_url or "").split("/")[0]
    if "stg2" in host:
        return "stg2", host
    if "dev" in host:
        return "dev", host
    if host.startswith("local"):
        return "local", host
    return host.split(".")[0] if host else "unknown", host


def _severity_mark(sev):
    label = _SEVERITY_LABEL.get(sev, sev)
    return f'<span class="sev sev-{_esc(sev)}"><i></i>{_esc(label)}</span>'


def _method_badge(method):
    if not method:
        return ""
    return f'<span class="method method-{_esc(method)}">{_esc(method)}</span>'


def _render_chapter(number, section_id, title, findings, note=None, open_by_default=True):
    rows = []
    for f in sorted(findings, key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 9), _METHOD_ORDER.get(f["method"] or "", 9), f["path"] or "")):
        endpoint = (
            f'{_method_badge(f["method"])}<code>{_esc_breakable(f["path"])}</code>' if f["path"] else '<span class="muted">—</span>'
        )
        location = f'<code class="loc">{_esc_breakable(f["location"])}</code>' if f["location"] else '<span class="muted">—</span>'
        rows.append(
            f"""<tr>
  <td>{_severity_mark(f["severity"])}</td>
  <td>{_esc(f["group"])}<div class="muted mono">{_esc_breakable(f["file"])}</div></td>
  <td>{endpoint}</td>
  <td>{location}</td>
  <td>{_esc(f["message"])}</td>
  <td><code class="rule">{_esc_breakable(f["rule"])}</code></td>
</tr>"""
        )

    counts = Counter(f["severity"] for f in findings)
    tally = "".join(
        f'<span class="tally-item ink-{sev}">{counts[sev]} {label}</span>'
        for sev, label in (("error", "error"), ("warning", "warning"), ("info", "info"))
        if counts.get(sev)
    )
    if not findings:
        tally = '<span class="tally-item ink-pass">全部通過</span>'

    body = (
        f"""<div class="table-scroll"><table class="ledger">
<thead><tr><th>嚴重度</th><th>分頁</th><th>Endpoint</th><th>位置</th><th>訊息</th><th>規則</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table></div>"""
        if findings
        else '<p class="empty">這個階段沒有發現任何問題。</p>'
    )

    note_html = f'<p class="chapter-note">{_esc(note)}</p>' if note else ""

    return f"""<details class="chapter" id="{section_id}" {"open" if open_by_default else ""}>
<summary>
  <span class="chapter-num">{number:02d}</span>
  <span class="chapter-title">{_esc(title)}</span>
  <span class="chapter-tally">{tally}</span>
</summary>
<div class="chapter-body">
{note_html}
{body}
</div>
</details>"""


def render_html(findings, entries, base_url):
    summary = build_summary(findings, entries)
    env_label, _ = _environment_label(base_url)
    swagger_ui_url = derive_swagger_ui_url(base_url)

    static_findings = [f for f in findings if f.get("phase", "static") == "static"]
    live_findings = [f for f in findings if f.get("phase") == "live"]
    diff_findings = [f for f in findings if f.get("phase") == "diff"]
    llm_findings = [f for f in findings if f.get("phase") == "llm"]
    live_write_findings = [f for f in findings if f.get("phase") == "live_write"]

    has_live = bool(live_findings) or any(f.get("phase") == "live" for f in findings)
    has_diff = any(f.get("phase") == "diff" for f in findings)
    has_llm = any(f.get("phase") == "llm" for f in findings)
    has_live_write = any(f.get("phase") == "live_write" for f in findings)

    endpoint_pass_count, endpoint_fail_count, endpoint_total = compute_endpoint_pass_fail(findings)

    manifest_rows = []
    for e in entries:
        n = summary["by_file"].get(e["filename"], 0)
        if e["error"]:
            status, css, mark = "抓取失敗", "manifest-fail", "✕"
        elif not e["is_real"]:
            status, css, mark = "說明用分頁", "manifest-muted", "·"
        elif n == 0:
            status, css, mark = "沒問題", "manifest-ok", "✓"
        else:
            status, css, mark = f"{n} 個發現", "manifest-warn", str(n)
        manifest_rows.append(
            f"""<div class="manifest-row {css}">
  <span class="manifest-mark">{mark}</span>
  <span class="manifest-name">{_esc(e['group'])}</span>
  <span class="manifest-status">{_esc(status)}</span>
</div>"""
        )

    chapters = []
    chapters.append(_render_chapter(1, "sec-static", "靜態比對 — Swagger 定義本身", static_findings))
    n = 1
    if has_live:
        n += 1
        chapters.append(
            _render_chapter(
                n,
                "sec-live",
                "Live Call — 唯讀端點（GET）",
                live_findings,
                note="只打 GET，檢查狀態碼是否有宣告過、回應內容是否符合宣告的 schema，不含 POST/PUT/DELETE。",
            )
        )
    if has_live_write:
        n += 1
        chapters.append(
            _render_chapter(
                n,
                "sec-live-write",
                "Live Call — 寫入方法測試（POST/PUT/DELETE）",
                live_write_findings,
                note="小範圍驗證：目前涵蓋 Chat V2 對話、FAQ 問答集與 entry、Knowledge 知識庫與文件、FedFlow 執行、Helix 聲紋，不是全面涵蓋所有寫入端點。",
            )
        )
    if has_diff:
        n += 1
        chapters.append(
            _render_chapter(
                n,
                "sec-diff",
                "與上次執行的差異",
                diff_findings,
                note="比對這次抓到的 spec 跟上一次執行存的基準，用來追蹤 spec 隨時間的變化，不是跟特定發布版本比對。",
            )
        )
    if has_llm:
        n += 1
        chapters.append(
            _render_chapter(
                n,
                "sec-llm",
                "LLM 文字審查",
                llm_findings,
                note="AI 對 description 清晰度與邏輯一致性的判斷，僅供參考，不是硬性規則。",
            )
        )

    verdict = "fail" if endpoint_fail_count else "pass"
    verdict_word = "FAIL" if endpoint_fail_count else "PASS"

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>API Review Agent</title>
<style>
  :root {{
    --paper: #f5f3ea;
    --paper-2: #ece7d6;
    --surface: #fffdf7;
    --rule: #d9d0b8;
    --ink: #2a2418;
    --ink-muted: #756c58;
    --fail: #8c3020;
    --fail-soft: #ede0d9;
    --pass: #2b5d3f;
    --pass-soft: #e2e9e0;
    --warn: #8a5a12;
    --warn-soft: #eee3cd;
    --info: #2e4a66;
    --info-soft: #e1e7ed;
    --method-get: #2e4a66;
    --method-post: #2b5d3f;
    --method-put: #8a5a12;
    --method-patch: #5b4b8a;
    --method-delete: #8c3020;
    --sheet-shadow: 0 1px 3px rgba(30, 26, 14, 0.07), 0 14px 34px rgba(30, 26, 14, 0.06);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --paper: #1b1913; --paper-2: #221f17; --surface: #221f17; --rule: #3c3627;
      --ink: #ece5d4; --ink-muted: #a89c81;
      --fail: #e2795e; --fail-soft: #3a2419;
      --pass: #85c49b; --pass-soft: #1e2b21;
      --warn: #d9a94b; --warn-soft: #332810;
      --info: #93b3d1; --info-soft: #1c2733;
      --method-get: #93b3d1; --method-post: #85c49b; --method-put: #d9a94b;
      --method-patch: #b9a8e2; --method-delete: #e2795e;
      --sheet-shadow: 0 1px 3px rgba(0, 0, 0, 0.4), 0 14px 34px rgba(0, 0, 0, 0.3);
    }}
  }}
  :root[data-theme="dark"] {{
    --paper: #1b1913; --paper-2: #221f17; --surface: #221f17; --rule: #3c3627;
    --ink: #ece5d4; --ink-muted: #a89c81;
    --fail: #e2795e; --fail-soft: #3a2419;
    --pass: #85c49b; --pass-soft: #1e2b21;
    --warn: #d9a94b; --warn-soft: #332810;
    --info: #93b3d1; --info-soft: #1c2733;
    --method-get: #93b3d1; --method-post: #85c49b; --method-put: #d9a94b;
    --method-patch: #b9a8e2; --method-delete: #e2795e;
    --sheet-shadow: 0 1px 3px rgba(0, 0, 0, 0.4), 0 14px 34px rgba(0, 0, 0, 0.3);
  }}

  * {{ box-sizing: border-box; }}
  @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }} }}

  body {{
    margin: 0; padding: 28px 20px 64px;
    background: var(--paper-2);
    color: var(--ink);
    font-family: "IBM Plex Sans", "Noto Sans TC", -apple-system, "Microsoft JhengHei", sans-serif;
    font-size: 14.5px;
    line-height: 1.6;
  }}
  .mono {{ font-family: "IBM Plex Mono", ui-monospace, "SFMono-Regular", monospace; }}
  code, .mono {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 0.9em; }}
  code {{ background: var(--paper-2); padding: 1px 6px; border-radius: 3px; color: var(--ink); }}
  code.loc {{ color: var(--ink-muted); }}
  code.rule {{ background: transparent; color: var(--ink-muted); padding: 0; }}
  a {{ color: var(--info); }}

  .sheet {{
    max-width: 1180px; margin: 0 auto;
    background: var(--paper); border: 1px solid var(--rule); border-radius: 3px;
    box-shadow: var(--sheet-shadow);
    padding: 40px 48px 48px;
  }}
  @media (max-width: 720px) {{ .sheet {{ padding: 28px 18px 32px; }} }}

  header.masthead {{ position: relative; padding-bottom: 22px; border-bottom: 2px solid var(--ink); margin-bottom: 26px; }}
  .masthead-top {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 18px; }}
  .tool-id {{ font-family: "IBM Plex Mono", monospace; font-size: 12px; color: var(--ink-muted); }}
  .classification {{
    font-family: "IBM Plex Mono", monospace; font-size: 11px; font-weight: 600;
    letter-spacing: 0.08em; color: var(--ink-muted); border: 1px solid var(--rule);
    border-radius: 3px; padding: 2px 8px;
  }}
  h1 {{
    font-family: "Source Serif 4", "Noto Serif TC", Georgia, serif;
    font-size: 32px; font-weight: 600; margin: 0 0 16px; letter-spacing: -0.005em;
    max-width: 560px; padding-right: 140px;
  }}
  @media (max-width: 720px) {{ h1 {{ padding-right: 0; }} }}
  dl.meta {{ display: flex; flex-wrap: wrap; gap: 6px 40px; margin: 0; }}
  dl.meta > div {{ display: flex; flex-direction: column; gap: 2px; }}
  dl.meta dt {{ font-size: 11px; color: var(--ink-muted); }}
  dl.meta dd {{ margin: 0; font-size: 13.5px; }}
  dl.meta dd a {{ text-decoration-color: var(--rule); }}

  .stamp {{
    position: absolute; top: -6px; right: 4px; width: 128px; height: 128px;
    border-radius: 50%; border: 3px double currentColor;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    transform: rotate(-9deg); background: transparent;
    animation: stamp-in 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) both;
  }}
  @keyframes stamp-in {{ from {{ opacity: 0; transform: rotate(-9deg) scale(1.5); }} to {{ opacity: 1; transform: rotate(-9deg) scale(1); }} }}
  .stamp-pass {{ color: var(--pass); }}
  .stamp-fail {{ color: var(--fail); }}
  .stamp-word {{ font-family: "Source Serif 4", serif; font-weight: 700; font-size: 22px; letter-spacing: 0.06em; }}
  .stamp-ratio {{ font-family: "IBM Plex Mono", monospace; font-size: 12.5px; margin-top: 2px; }}
  .stamp-caption {{ font-family: "IBM Plex Mono", monospace; font-size: 8.5px; letter-spacing: 0.03em; margin-top: 1px; }}
  @media (max-width: 720px) {{
    .stamp {{ position: static; margin: 4px 0 18px; transform: rotate(-4deg); }}
  }}

  dl.stat-strip {{
    display: flex; flex-wrap: wrap; margin: 0 0 30px; border: 1px solid var(--rule); border-radius: 3px;
    background: var(--surface); overflow: hidden;
  }}
  dl.stat-strip > div {{
    flex: 1 1 130px; padding: 12px 16px; border-right: 1px solid var(--rule);
  }}
  dl.stat-strip > div:last-child {{ border-right: none; }}
  dl.stat-strip dt {{ font-size: 11px; color: var(--ink-muted); margin: 0 0 3px; }}
  dl.stat-strip dd {{ margin: 0; font-family: "IBM Plex Mono", monospace; font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  dl.stat-strip dd.ink-error {{ color: var(--fail); }}
  dl.stat-strip dd.ink-warning {{ color: var(--warn); }}
  dl.stat-strip dd.ink-info {{ color: var(--info); }}

  .manifest {{ margin-bottom: 30px; }}
  .manifest-title {{
    font-size: 12px; font-weight: 600; color: var(--ink-muted); margin: 0 0 10px;
    padding-bottom: 6px; border-bottom: 1px solid var(--rule);
  }}
  .manifest-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 0 24px; }}
  .manifest-row {{
    display: flex; align-items: baseline; gap: 9px; padding: 6px 0;
    border-bottom: 1px solid var(--rule); font-size: 13px;
  }}
  .manifest-mark {{ font-family: "IBM Plex Mono", monospace; font-weight: 700; width: 16px; text-align: center; flex-shrink: 0; }}
  .manifest-ok .manifest-mark {{ color: var(--pass); }}
  .manifest-warn .manifest-mark {{ color: var(--fail); }}
  .manifest-fail .manifest-mark {{ color: var(--fail); }}
  .manifest-muted .manifest-mark {{ color: var(--ink-muted); }}
  .manifest-name {{ font-weight: 600; flex: 1; }}
  .manifest-status {{ color: var(--ink-muted); font-size: 12px; white-space: nowrap; }}
  .manifest-warn .manifest-status, .manifest-fail .manifest-status {{ color: var(--fail); font-weight: 600; }}
  .manifest-muted {{ opacity: 0.6; }}

  details.chapter {{ border-bottom: 1px solid var(--rule); }}
  details.chapter:first-of-type {{ border-top: 2px solid var(--ink); }}
  details.chapter > summary {{
    list-style: none; cursor: pointer; padding: 16px 4px;
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
  }}
  details.chapter > summary::-webkit-details-marker {{ display: none; }}
  .chapter-num {{
    font-family: "Source Serif 4", serif; font-size: 20px; color: var(--ink-muted);
    width: 30px; flex-shrink: 0;
  }}
  .chapter-title {{ font-weight: 600; font-size: 15.5px; flex: 1; min-width: 200px; }}
  .chapter-title::before {{
    content: "▸"; display: inline-block; margin-right: 8px; color: var(--ink-muted); font-size: 11px;
    transition: transform 0.15s ease;
  }}
  details.chapter[open] > summary .chapter-title::before {{ transform: rotate(90deg); }}
  .chapter-tally {{ display: flex; gap: 14px; flex-wrap: wrap; }}
  .tally-item {{ font-size: 12.5px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .ink-error {{ color: var(--fail); }}
  .ink-warning {{ color: var(--warn); }}
  .ink-info {{ color: var(--info); }}
  .ink-pass {{ color: var(--pass); }}

  .chapter-body {{ padding: 0 4px 22px 44px; }}
  .chapter-note {{ color: var(--ink-muted); font-size: 13px; margin: 0 0 14px; max-width: 72ch; }}
  .empty {{ color: var(--pass); font-weight: 600; padding: 4px 0 4px; }}

  .table-scroll {{ overflow-x: auto; }}
  table.ledger {{ border-collapse: collapse; width: 100%; table-layout: fixed; font-size: 13px; }}
  table.ledger th, table.ledger td {{
    padding: 9px 10px; text-align: left; vertical-align: top;
    border-bottom: 1px solid var(--rule); word-break: break-word; overflow-wrap: anywhere;
  }}
  table.ledger th {{ color: var(--ink-muted); font-weight: 600; font-size: 11px; }}
  table.ledger tbody tr:last-child td {{ border-bottom: none; }}
  table.ledger tbody tr:hover {{ background: var(--surface); }}
  table.ledger th:nth-child(1), table.ledger td:nth-child(1) {{ width: 9%; }}
  table.ledger th:nth-child(2), table.ledger td:nth-child(2) {{ width: 11%; }}
  table.ledger th:nth-child(3), table.ledger td:nth-child(3) {{ width: 20%; }}
  table.ledger th:nth-child(4), table.ledger td:nth-child(4) {{ width: 12%; }}
  table.ledger th:nth-child(5), table.ledger td:nth-child(5) {{ width: 35%; }}
  table.ledger th:nth-child(6), table.ledger td:nth-child(6) {{ width: 13%; }}
  @media (max-width: 640px) {{
    table.ledger {{ table-layout: auto; min-width: 640px; }}
    table.ledger th:nth-child(n), table.ledger td:nth-child(n) {{ width: auto; }}
  }}

  .sev {{ display: inline-flex; align-items: center; gap: 6px; font-weight: 600; font-size: 12.5px; white-space: nowrap; }}
  .sev i {{ width: 8px; height: 8px; flex-shrink: 0; }}
  .sev-error {{ color: var(--fail); }}
  .sev-error i {{ background: var(--fail); }}
  .sev-warning {{ color: var(--warn); }}
  .sev-warning i {{ background: var(--warn); }}
  .sev-info {{ color: var(--info); }}
  .sev-info i {{ background: var(--info); }}

  .method {{
    display: inline-block; font-family: "IBM Plex Mono", monospace; font-size: 10px; font-weight: 700;
    padding: 1px 6px; border-radius: 3px; margin-right: 6px; color: var(--paper); letter-spacing: 0.02em;
  }}
  .method-GET {{ background: var(--method-get); }}
  .method-POST {{ background: var(--method-post); }}
  .method-PUT {{ background: var(--method-put); }}
  .method-PATCH {{ background: var(--method-patch); }}
  .method-DELETE {{ background: var(--method-delete); }}

  .muted {{ color: var(--ink-muted); }}
  footer.colophon {{
    margin-top: 36px; color: var(--ink-muted); font-size: 12px;
    border-top: 1px solid var(--rule); padding-top: 14px; max-width: 72ch;
  }}
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,500;8..60,600;8..60,700&family=Noto+Serif+TC:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700&family=Noto+Sans+TC:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
</head>
<body>
<div class="sheet">
  <header class="masthead">
    <div class="stamp stamp-{verdict}">
      <span class="stamp-word">{verdict_word}</span>
      <span class="stamp-ratio">{endpoint_pass_count}/{endpoint_total}</span>
      <span class="stamp-caption">ENDPOINTS</span>
    </div>
    <div class="masthead-top">
      <span class="tool-id">api-review-agent</span>
      <span class="classification">{_esc(env_label)}</span>
    </div>
    <h1>Public Swagger 稽核報告</h1>
    <dl class="meta">
      <div><dt>來源</dt><dd><a class="mono" href="{_esc(swagger_ui_url)}" target="_blank" rel="noopener">{_esc(swagger_ui_url)}</a></dd></div>
      <div><dt>產生時間</dt><dd class="mono">{_esc(summary['generated_at'])}</dd></div>
    </dl>
  </header>

  <dl class="stat-strip">
    <div><dt>總發現數</dt><dd>{summary['total']}</dd></div>
    <div><dt>Error</dt><dd class="ink-error">{summary['by_severity'].get('error', 0)}</dd></div>
    <div><dt>Warning</dt><dd class="ink-warning">{summary['by_severity'].get('warning', 0)}</dd></div>
    <div><dt>Info</dt><dd class="ink-info">{summary['by_severity'].get('info', 0)}</dd></div>
    <div><dt>無發現的分頁</dt><dd>{summary['num_clean_files']}/{summary['num_real_files']}</dd></div>
  </dl>

  <div class="manifest">
    <div class="manifest-title">受檢規格清單</div>
    <div class="manifest-grid">
{''.join(manifest_rows)}
    </div>
  </div>

  <div class="chapters">
    {''.join(chapters)}
  </div>

  <footer class="colophon">
    靜態比對涵蓋 Swagger 定義本身是否完整、一致；其餘各階段的涵蓋範圍見各區塊內的說明。展開／收合各區塊可以聚焦想看的部分。
  </footer>
</div>
</body>
</html>
"""
