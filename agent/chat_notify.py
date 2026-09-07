"""把檢查結果摘要推到 Google Chat（用 Space 的 Incoming Webhook）。

設定方式：
  1. 在目標 Google Chat Space：Space 名稱旁選單 → Apps & integrations → Webhooks → 新增一個。
  2. 複製產生的 URL，設成環境變數 GOOGLE_CHAT_WEBHOOK_URL（或用 --webhook-url 傳入）。

之後若要換到 Microsoft Teams，只要另外寫一個 teams_notify.py、輸出一樣的 summary dict 給它組 Adaptive Card 即可，
run_check.py 呼叫的介面不用變。
"""

import requests

from .report import build_summary


def build_chat_message(findings, entries, base_url, report_path=None):
    summary = build_summary(findings, entries)
    lines = [
        "*API Review Agent — Public Swagger 靜態檢查完成*",
        f"來源：{base_url}",
        f"時間：{summary['generated_at']}",
        "",
        f"總發現數：*{summary['total']}*"
        f"（Error {summary['by_severity'].get('error', 0)}"
        f" / Warning {summary['by_severity'].get('warning', 0)}"
        f" / Info {summary['by_severity'].get('info', 0)}）",
        f"無發現的分頁：{summary['num_clean_files']}/{summary['num_real_files']}",
    ]

    top_errors = [f for f in findings if f["severity"] == "error"][:5]
    if top_errors:
        lines.append("")
        lines.append("*前幾筆 Error：*")
        for f in top_errors:
            endpoint = f"{f['method']} {f['path']}" if f["path"] else f["file"]
            lines.append(f"• [{f['group']}] {endpoint} — {f['message']}")

    if report_path:
        lines.append("")
        lines.append(f"完整報告：{report_path}")

    return "\n".join(lines)


def post_to_google_chat(webhook_url, message_text):
    if not webhook_url:
        raise ValueError("沒有設定 Google Chat webhook URL（GOOGLE_CHAT_WEBHOOK_URL 或 --webhook-url）")
    resp = requests.post(webhook_url, json={"text": message_text}, timeout=20)
    resp.raise_for_status()
    return resp
