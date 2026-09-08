import html
from collections import Counter
from datetime import datetime, timezone

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_SEVERITY_LABEL = {"error": "Error", "warning": "Warning", "info": "Info"}


def _esc(value):
    return html.escape(str(value)) if value is not None else ""


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


def _render_findings_table(findings_sorted):
    rows = []
    for f in findings_sorted:
        endpoint = f"{f['method']} {f['path']}" if f["path"] else "-"
        rows.append(
            f"""<tr class="sev-{_esc(f['severity'])}">
  <td class="sev">{_esc(_SEVERITY_LABEL.get(f['severity'], f['severity']))}</td>
  <td>{_esc(f['group'])}<br/><span class="muted">{_esc(f['file'])}</span></td>
  <td>{_esc(endpoint)}</td>
  <td>{_esc(f['rule'])}</td>
  <td>{_esc(f['location'])}</td>
  <td>{_esc(f['message'])}</td>
</tr>"""
        )
    return f"""<table>
<thead><tr><th>嚴重度</th><th>分頁</th><th>Endpoint</th><th>規則</th><th>位置</th><th>訊息</th></tr></thead>
<tbody>
{''.join(rows) if rows else '<tr><td colspan="6">沒有發現任何問題 🎉</td></tr>'}
</tbody>
</table>"""


def render_html(findings, entries, base_url):
    summary = build_summary(findings, entries)
    findings_sorted = sorted(
        findings, key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 9), f["file"], f["path"] or "")
    )
    static_findings = [f for f in findings_sorted if f.get("phase", "static") == "static"]
    live_findings = [f for f in findings_sorted if f.get("phase") == "live"]
    diff_findings = [f for f in findings_sorted if f.get("phase") == "diff"]
    has_live = any(f.get("phase") == "live" for f in findings)
    has_diff = any(f.get("phase") == "diff" for f in findings)

    file_rows = []
    for e in entries:
        n = summary["by_file"].get(e["filename"], 0)
        status = "抓取失敗" if e["error"] else ("沒問題" if n == 0 else f"{n} 個發現")
        css = "err" if e["error"] else ("ok" if n == 0 else "warn")
        file_rows.append(
            f"""<tr>
  <td>{_esc(e['group'])}</td>
  <td><code>{_esc(e['filename'])}</code></td>
  <td class="{css}">{_esc(status)}</td>
</tr>"""
        )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<title>API Review Agent 報告</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif; margin: 24px; color: #1f2328; background: #fff; }}
  h1 {{ font-size: 20px; }}
  .muted {{ color: #6b7280; font-size: 12px; }}
  .cards {{ display: flex; gap: 16px; margin: 16px 0 24px; flex-wrap: wrap; }}
  .card {{ border: 1px solid #d0d7de; border-radius: 8px; padding: 12px 16px; min-width: 120px; }}
  .card .num {{ font-size: 24px; font-weight: 700; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 32px; font-size: 13px; }}
  th, td {{ border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; vertical-align: top; }}
  th {{ background: #f6f8fa; }}
  tr.sev-error td.sev {{ color: #b3261e; font-weight: 700; }}
  tr.sev-warning td.sev {{ color: #9a6700; font-weight: 700; }}
  tr.sev-info td.sev {{ color: #57606a; }}
  td.ok {{ color: #1a7f37; }}
  td.warn {{ color: #9a6700; }}
  td.err {{ color: #b3261e; }}
  code {{ background: #f6f8fa; padding: 1px 4px; border-radius: 4px; }}
</style>
</head>
<body>
<h1>API Review Agent — Public Swagger 靜態檢查報告</h1>
<p class="muted">來源：{_esc(base_url)} ｜ 產生時間：{_esc(summary['generated_at'])}</p>

<div class="cards">
  <div class="card"><div class="num">{summary['total']}</div><div>總發現數</div></div>
  <div class="card"><div class="num">{summary['by_severity'].get('error', 0)}</div><div>Error</div></div>
  <div class="card"><div class="num">{summary['by_severity'].get('warning', 0)}</div><div>Warning</div></div>
  <div class="card"><div class="num">{summary['by_severity'].get('info', 0)}</div><div>Info</div></div>
  <div class="card"><div class="num">{summary['num_clean_files']}/{summary['num_real_files']}</div><div>無發現的分頁</div></div>
</div>

<h2>各分頁狀態</h2>
<table>
<thead><tr><th>分頁</th><th>檔案</th><th>狀態</th></tr></thead>
<tbody>
{''.join(file_rows)}
</tbody>
</table>

<h2>靜態比對發現（{len(static_findings)} 筆，Error 優先）</h2>
{_render_findings_table(static_findings)}

{f'''<h2>Live call 發現（{len(live_findings)} 筆，Error 優先）</h2>
{_render_findings_table(live_findings)}''' if has_live else ''}

{f'''<h2>與上次執行的差異（{len(diff_findings)} 筆）</h2>
{_render_findings_table(diff_findings)}''' if has_diff else ''}

<p class="muted">
靜態比對涵蓋 Swagger 定義本身是否完整、一致。
{"Live call 目前只打 GET，且只檢查「狀態碼有沒有在 spec 裡宣告過」與「回應內容符不符合宣告的 schema」，不含 POST/PUT/DELETE。" if has_live else "本輪未執行 live call（只做了靜態比對）。"}
{"「與上次執行的差異」比對的是這次抓到的 spec 跟上一次執行存的基準，不是跟 3.10 版本比（目前拿不到 3.10）。" if has_diff else ""}
</p>
</body>
</html>
"""
