"""把檢查結果更新到一個固定的 Confluence 頁面，取代綁定在單一 Claude 帳號的 Artifact 連結。

跟 Artifact 不同，Confluence 有公開的 REST API（Cloud REST API v2），這裡直接用
`requests` + 個人的 email/API token 做 Basic Auth 打 API 更新頁面內容，不需要透過
Claude 工具、也不綁定特定人——只要那個人在目標 Confluence space 有編輯權限，用自己的
CONFLUENCE_EMAIL/CONFLUENCE_API_TOKEN 就能更新到同一頁，網址永遠不變。

Confluence 的 storage format 對自訂 CSS/JS 支援有限，這裡用純表格呈現（沒有摺疊分類、
沒有深色模式），不是把 report.py 那份互動式 HTML 原封不動搬過去——換取不依賴 Claude、
不綁定帳號的自動更新能力。
"""

import base64
from html import escape

import requests

from .config import derive_swagger_ui_url
from .report import build_summary, compute_endpoint_pass_fail

_SEVERITY_ORDER = ["error", "warning", "info"]
_SEVERITY_LABEL = {"error": "🔴 Error", "warning": "🟡 Warning", "info": "🔵 Info"}


def _auth_header(email, api_token):
    raw = f"{email}:{api_token}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def _findings_table(findings):
    if not findings:
        return "<p>沒有任何發現。</p>"

    rows = []
    for f in sorted(findings, key=lambda x: _SEVERITY_ORDER.index(x["severity"]) if x["severity"] in _SEVERITY_ORDER else 9):
        endpoint = f"{f['method']} {f['path']}" if f.get("method") and f.get("path") else (f.get("path") or "—")
        rows.append(
            "<tr>"
            f"<td>{_SEVERITY_LABEL.get(f['severity'], escape(f['severity']))}</td>"
            f"<td>{escape(f.get('group') or '—')}<br><code>{escape(f.get('file') or '')}</code></td>"
            f"<td><code>{escape(endpoint)}</code></td>"
            f"<td>{escape(f.get('message') or '')}</td>"
            f"<td><code>{escape(f.get('rule') or '')}</code></td>"
            "</tr>"
        )

    return (
        '<table data-layout="full-width"><thead><tr>'
        "<th>嚴重度</th><th>分頁</th><th>Endpoint</th><th>訊息</th><th>規則</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def build_confluence_body(findings, entries, base_url):
    """把 findings 轉成 Confluence storage format 的頁面內容（一段 HTML 字串）。"""
    summary = build_summary(findings, entries)
    generated_at = summary["generated_at"]
    swagger_ui_url = derive_swagger_ui_url(base_url)

    endpoint_pass_count, endpoint_fail_count, endpoint_total = compute_endpoint_pass_fail(findings)

    by_phase = {}
    for f in findings:
        by_phase.setdefault(f.get("phase", "static"), []).append(f)

    phase_labels = {
        "static": "靜態比對 — Swagger 定義本身",
        "live": "Live Call — 唯讀端點（GET）",
        "live_write": "Live Call — 寫入方法測試（POST/PUT/DELETE）",
        "diff": "Spec Diff — 版本間差異",
        "llm": "LLM 文字審查",
    }

    sections = []
    for phase, label in phase_labels.items():
        phase_findings = by_phase.pop(phase, [])
        sections.append(f"<h2>{escape(label)}（{len(phase_findings)} 筆）</h2>" + _findings_table(phase_findings))
    for phase, phase_findings in by_phase.items():
        sections.append(f"<h2>{escape(phase)}（{len(phase_findings)} 筆）</h2>" + _findings_table(phase_findings))

    endpoint_status_color = "red" if endpoint_fail_count else "green"

    summary_table = (
        "<table><tbody>"
        f'<tr><th>來源</th><td><a href="{escape(swagger_ui_url)}">{escape(swagger_ui_url)}</a></td></tr>'
        f"<tr><th>產生時間</th><td>{escape(generated_at)}</td></tr>"
        f"<tr><th>總發現數</th><td>{summary['total']}</td></tr>"
        f"<tr><th>Error</th><td>{summary['by_severity'].get('error', 0)}</td></tr>"
        f"<tr><th>Warning</th><td>{summary['by_severity'].get('warning', 0)}</td></tr>"
        f"<tr><th>Info</th><td>{summary['by_severity'].get('info', 0)}</td></tr>"
        + (
            f"<tr><th>Endpoint Pass 率</th><td>{endpoint_pass_count}/{endpoint_total}"
            f"（{endpoint_pass_count / endpoint_total * 100:.0f}%，"
            f'<span data-type="status" data-color="{endpoint_status_color}">Fail {endpoint_fail_count} 支</span>，'
            "合併計算 Error 發現與 LLM 審查 Fail）</td></tr>"
            if endpoint_total
            else ""
        )
        + "</tbody></table>"
    )

    return (
        "<p>本頁由 <code>run_check.py --notify-confluence</code> 自動更新，內容是最新一次執行的完整結果，"
        "不需要手動編輯（下次執行會整頁覆寫）。</p>" + summary_table + "".join(sections)
    )


def update_confluence_report(site_url, email, api_token, page_id, findings, entries, base_url):
    """呼叫 Confluence REST API v2 更新 page_id 這頁的內容。回傳更新後的 API 回應 dict。"""
    if not (site_url and email and api_token and page_id):
        raise ValueError("缺少 CONFLUENCE_SITE_URL / CONFLUENCE_EMAIL / CONFLUENCE_API_TOKEN / CONFLUENCE_REPORT_PAGE_ID 其中之一")

    headers = _auth_header(email, api_token)
    api_base = site_url.rstrip("/") + "/wiki/api/v2"

    get_resp = requests.get(f"{api_base}/pages/{page_id}", headers=headers, timeout=20)
    get_resp.raise_for_status()
    current = get_resp.json()

    body_value = build_confluence_body(findings, entries, base_url)

    put_body = {
        "id": str(page_id),
        "status": "current",
        "title": current["title"],
        "spaceId": current["spaceId"],
        "body": {"representation": "storage", "value": body_value},
        "version": {
            "number": current["version"]["number"] + 1,
            "message": "run_check.py --notify-confluence 自動更新",
        },
    }

    put_resp = requests.put(f"{api_base}/pages/{page_id}", headers=headers, json=put_body, timeout=20)
    put_resp.raise_for_status()
    return put_resp.json()
