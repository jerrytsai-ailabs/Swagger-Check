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


def _severity_chip(sev):
    label = _SEVERITY_LABEL.get(sev, sev)
    return f'<span class="chip chip-{_esc(sev)}">{_esc(label)}</span>'


def _method_badge(method):
    if not method:
        return ""
    return f'<span class="method method-{_esc(method)}">{_esc(method)}</span>'


def _render_section(section_id, title, findings, note=None, open_by_default=True):
    rows = []
    for f in sorted(findings, key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 9), _METHOD_ORDER.get(f["method"] or "", 9), f["path"] or "")):
        endpoint = (
            f'{_method_badge(f["method"])}<code>{_esc(f["path"])}</code>' if f["path"] else '<span class="muted">—</span>'
        )
        location = f'<code class="loc">{_esc(f["location"])}</code>' if f["location"] else '<span class="muted">—</span>'
        rows.append(
            f"""<tr>
  <td>{_severity_chip(f["severity"])}</td>
  <td>{_esc(f["group"])}<div class="muted mono">{_esc(f["file"])}</div></td>
  <td>{endpoint}</td>
  <td>{location}</td>
  <td>{_esc(f["message"])}</td>
  <td><code class="rule">{_esc(f["rule"])}</code></td>
</tr>"""
        )

    counts = Counter(f["severity"] for f in findings)
    count_chips = "".join(
        f'<span class="count-chip count-chip-{sev}">{counts[sev]} {label}</span>'
        for sev, label in (("error", "error"), ("warning", "warning"), ("info", "info"))
        if counts.get(sev)
    )
    if not findings:
        count_chips = '<span class="count-chip count-chip-ok">全部通過</span>'

    body = (
        f"""<div class="table-scroll"><table>
<thead><tr><th>嚴重度</th><th>分頁</th><th>Endpoint</th><th>位置</th><th>訊息</th><th>規則</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table></div>"""
        if findings
        else '<p class="empty">這個階段沒有發現任何問題。</p>'
    )

    note_html = f'<p class="section-note">{_esc(note)}</p>' if note else ""

    return f"""<details class="section" id="{section_id}" {"open" if open_by_default else ""}>
<summary>
  <span class="section-title">{_esc(title)}</span>
  <span class="section-counts">{count_chips}</span>
</summary>
<div class="section-body">
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

    file_rows = []
    for e in entries:
        n = summary["by_file"].get(e["filename"], 0)
        if e["error"]:
            status, css = "抓取失敗", "err"
        elif not e["is_real"]:
            status, css = "說明用分頁", "muted-pill"
        elif n == 0:
            status, css = "沒問題", "ok"
        else:
            status, css = f"{n} 個發現", "warn"
        file_rows.append(
            f"""<div class="file-pill file-pill-{css}">
  <span class="file-pill-name">{_esc(e['group'])}</span>
  <span class="file-pill-status">{_esc(status)}</span>
</div>"""
        )

    sections = []
    sections.append(_render_section("sec-static", "靜態比對 — Swagger 定義本身", static_findings))
    if has_live:
        sections.append(
            _render_section(
                "sec-live",
                "Live Call — 唯讀端點（GET）",
                live_findings,
                note="只打 GET，檢查狀態碼是否有宣告過、回應內容是否符合宣告的 schema，不含 POST/PUT/DELETE。",
            )
        )
    if has_live_write:
        sections.append(
            _render_section(
                "sec-live-write",
                "Live Call — 寫入方法測試（POST/PUT/DELETE）",
                live_write_findings,
                note="小範圍驗證：目前涵蓋 Chat V2 對話、FAQ 問答集與 entry、Knowledge 知識庫與文件、FedFlow 執行、Helix 聲紋，不是全面涵蓋所有寫入端點。",
            )
        )
    if has_diff:
        sections.append(
            _render_section(
                "sec-diff",
                "與上次執行的差異",
                diff_findings,
                note="比對這次抓到的 spec 跟上一次執行存的基準，用來追蹤 spec 隨時間的變化，不是跟特定發布版本比對。",
            )
        )
    if has_llm:
        sections.append(
            _render_section(
                "sec-llm",
                "LLM 文字審查",
                llm_findings,
                note="AI 對 description 清晰度與邏輯一致性的判斷，僅供參考，不是硬性規則。",
            )
        )

    return f"""<title>API Review Agent</title>
<style>
  :root {{
    --bg: #f3f7f7;
    --surface: #ffffff;
    --surface-2: #eef4f4;
    --border: #d7e3e2;
    --text: #16262a;
    --text-muted: #5c7378;
    --accent: #0f7a82;
    --accent-soft: #e1f1f0;
    --error: #b73326;
    --error-soft: #fbe9e7;
    --warning: #966210;
    --warning-soft: #fbf1de;
    --info: #34518f;
    --info-soft: #e7ecf9;
    --ok: #1f7a55;
    --ok-soft: #e4f4ec;
    --method-get: #2f6fb0;
    --method-post: #1f8f63;
    --method-put: #b3760f;
    --method-patch: #7a52c9;
    --method-delete: #c23b32;
    --shadow: 0 1px 2px rgba(22, 38, 42, 0.06), 0 1px 1px rgba(22, 38, 42, 0.04);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #0f1b1d;
      --surface: #16262a;
      --surface-2: #1b2e31;
      --border: #274043;
      --text: #e7f1f0;
      --text-muted: #93a9ac;
      --accent: #52d2c8;
      --accent-soft: #163531;
      --error: #ef7266;
      --error-soft: #3a1f1c;
      --warning: #e5ac4c;
      --warning-soft: #3a2c14;
      --info: #8aabe8;
      --info-soft: #1c2740;
      --ok: #5ad39c;
      --ok-soft: #143327;
      --method-get: #6fa8e0;
      --method-post: #5cc99a;
      --method-put: #e5ac4c;
      --method-patch: #b192ea;
      --method-delete: #ef7266;
      --shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #0f1b1d;
    --surface: #16262a;
    --surface-2: #1b2e31;
    --border: #274043;
    --text: #e7f1f0;
    --text-muted: #93a9ac;
    --accent: #52d2c8;
    --accent-soft: #163531;
    --error: #ef7266;
    --error-soft: #3a1f1c;
    --warning: #e5ac4c;
    --warning-soft: #3a2c14;
    --info: #8aabe8;
    --info-soft: #1c2740;
    --ok: #5ad39c;
    --ok-soft: #143327;
    --method-get: #6fa8e0;
    --method-post: #5cc99a;
    --method-put: #e5ac4c;
    --method-patch: #b192ea;
    --method-delete: #ef7266;
    --shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
  }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: "IBM Plex Sans", -apple-system, "Microsoft JhengHei", sans-serif;
    font-size: 14.5px;
    line-height: 1.55;
  }}
  .wrap {{ max-width: 1440px; margin: 0 auto; padding: 40px 24px 80px; }}
  .mono {{ font-family: "IBM Plex Mono", ui-monospace, "SFMono-Regular", monospace; }}
  code, .mono {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 0.92em; }}
  code {{ background: var(--surface-2); padding: 1px 6px; border-radius: 4px; color: var(--text); }}
  code.loc {{ color: var(--text-muted); }}
  code.rule {{ background: transparent; color: var(--text-muted); padding: 0; }}

  header.page-head {{ margin-bottom: 28px; }}
  .eyebrow {{
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--accent); font-weight: 600; margin-bottom: 10px;
  }}
  .env-badge {{
    background: var(--accent-soft); color: var(--accent);
    border-radius: 999px; padding: 2px 10px; font-size: 11px; font-weight: 700;
    letter-spacing: 0.03em;
  }}
  h1 {{ font-size: 26px; font-weight: 600; margin: 0 0 8px; text-wrap: balance; letter-spacing: -0.01em; }}
  .meta-line {{ color: var(--text-muted); font-size: 13px; }}
  .meta-line .mono {{ color: var(--text); }}

  .tiles {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 12px; margin: 24px 0 8px;
  }}
  .tile {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; box-shadow: var(--shadow);
  }}
  .tile .tile-num {{ font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }}
  .tile .tile-label {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}
  .tile-error .tile-num {{ color: var(--error); }}
  .tile-warning .tile-num {{ color: var(--warning); }}
  .tile-info .tile-num {{ color: var(--info); }}
  .tile-ok .tile-num {{ color: var(--ok); }}

  .file-pills {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 20px 0 32px; }}
  .file-pill {{
    display: flex; align-items: center; gap: 8px;
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 6px 10px; font-size: 12.5px; box-shadow: var(--shadow);
  }}
  .file-pill-name {{ font-weight: 600; }}
  .file-pill-status {{ color: var(--text-muted); }}
  .file-pill-ok .file-pill-status {{ color: var(--ok); }}
  .file-pill-warn .file-pill-status {{ color: var(--warning); font-weight: 600; }}
  .file-pill-err .file-pill-status {{ color: var(--error); font-weight: 600; }}
  .file-pill-muted-pill {{ opacity: 0.55; }}

  details.section {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    margin-bottom: 16px; box-shadow: var(--shadow); overflow: hidden;
  }}
  details.section > summary {{
    list-style: none; cursor: pointer; padding: 16px 20px;
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    flex-wrap: wrap;
  }}
  details.section > summary::-webkit-details-marker {{ display: none; }}
  details.section > summary::before {{
    content: "▸"; display: inline-block; margin-right: 10px; color: var(--text-muted);
    transition: transform 0.15s ease;
  }}
  details.section[open] > summary::before {{ transform: rotate(90deg); }}
  .section-title {{ font-weight: 600; font-size: 15.5px; display: flex; align-items: center; }}
  .section-counts {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .count-chip {{
    font-size: 11.5px; font-weight: 700; padding: 2px 9px; border-radius: 999px;
    font-variant-numeric: tabular-nums;
  }}
  .count-chip-error {{ background: var(--error-soft); color: var(--error); }}
  .count-chip-warning {{ background: var(--warning-soft); color: var(--warning); }}
  .count-chip-info {{ background: var(--info-soft); color: var(--info); }}
  .count-chip-ok {{ background: var(--ok-soft); color: var(--ok); }}

  .section-body {{ padding: 0 20px 20px; border-top: 1px solid var(--border); }}
  .section-note {{ color: var(--text-muted); font-size: 13px; margin: 14px 0 10px; }}
  .empty {{ color: var(--ok); font-weight: 600; padding: 16px 0 4px; }}

  .table-scroll {{ overflow-x: auto; margin-top: 12px; }}
  table {{ border-collapse: collapse; width: 100%; table-layout: fixed; font-size: 13px; }}
  th, td {{ padding: 8px 10px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--border); word-break: break-word; overflow-wrap: anywhere; }}
  th {{ color: var(--text-muted); font-weight: 600; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.04em; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  th:nth-child(1), td:nth-child(1) {{ width: 8%; }}
  th:nth-child(2), td:nth-child(2) {{ width: 11%; }}
  th:nth-child(3), td:nth-child(3) {{ width: 20%; }}
  th:nth-child(4), td:nth-child(4) {{ width: 12%; }}
  th:nth-child(5), td:nth-child(5) {{ width: 36%; }}
  th:nth-child(6), td:nth-child(6) {{ width: 13%; }}
  @media (max-width: 640px) {{
    table {{ table-layout: auto; min-width: 640px; }}
    th:nth-child(n), td:nth-child(n) {{ width: auto; }}
  }}

  .chip {{ font-size: 11.5px; font-weight: 700; padding: 2px 8px; border-radius: 6px; white-space: nowrap; }}
  .chip-error {{ background: var(--error-soft); color: var(--error); }}
  .chip-warning {{ background: var(--warning-soft); color: var(--warning); }}
  .chip-info {{ background: var(--info-soft); color: var(--info); }}

  .method {{
    display: inline-block; font-family: "IBM Plex Mono", monospace; font-size: 10.5px; font-weight: 700;
    padding: 1px 6px; border-radius: 4px; margin-right: 6px; color: #fff; letter-spacing: 0.02em;
  }}
  .method-GET {{ background: var(--method-get); }}
  .method-POST {{ background: var(--method-post); }}
  .method-PUT {{ background: var(--method-put); }}
  .method-PATCH {{ background: var(--method-patch); }}
  .method-DELETE {{ background: var(--method-delete); }}

  .muted {{ color: var(--text-muted); }}
  footer.page-foot {{ margin-top: 32px; color: var(--text-muted); font-size: 12px; border-top: 1px solid var(--border); padding-top: 16px; }}
</style>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">

<div class="wrap">
  <header class="page-head">
    <div class="eyebrow">API Review Agent <span class="env-badge">{_esc(env_label)}</span></div>
    <h1>Public Swagger 檢查報告</h1>
    <div class="meta-line">來源 <a class="mono" href="{_esc(swagger_ui_url)}" target="_blank" rel="noopener">{_esc(swagger_ui_url)}</a> ・ 產生時間 <span class="mono">{_esc(summary['generated_at'])}</span></div>
  </header>

  <div class="tiles">
    <div class="tile"><div class="tile-num">{summary['total']}</div><div class="tile-label">總發現數</div></div>
    <div class="tile tile-error"><div class="tile-num">{summary['by_severity'].get('error', 0)}</div><div class="tile-label">Error</div></div>
    <div class="tile tile-warning"><div class="tile-num">{summary['by_severity'].get('warning', 0)}</div><div class="tile-label">Warning</div></div>
    <div class="tile tile-info"><div class="tile-num">{summary['by_severity'].get('info', 0)}</div><div class="tile-label">Info</div></div>
    <div class="tile tile-ok"><div class="tile-num">{summary['num_clean_files']}/{summary['num_real_files']}</div><div class="tile-label">無發現的分頁</div></div>
    {f'<div class="tile {"tile-error" if endpoint_fail_count else "tile-ok"}"><div class="tile-num">{endpoint_pass_count}/{endpoint_total} ({endpoint_pass_count / endpoint_total * 100:.0f}%)</div><div class="tile-label">Endpoint Pass 率（Error + LLM Fail 合計）</div></div>' if endpoint_total else ''}
  </div>

  <div class="file-pills">
{''.join(file_rows)}
  </div>

  {''.join(sections)}

  <footer class="page-foot">
    靜態比對涵蓋 Swagger 定義本身是否完整、一致；其餘各階段的涵蓋範圍見各區塊內的說明。展開／收合各區塊可以聚焦想看的部分。
  </footer>
</div>
"""
