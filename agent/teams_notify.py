"""把檢查結果摘要推到 Microsoft Teams。

設定方式（Teams 現在的 Incoming Webhook 機制走 Power Automate，不是舊版的 Office 365 Connector）：
  1. 在目標 Teams 頻道：⋯（頻道選單）→ Workflows → 範本選「Post to a channel when a webhook
     request is received」（或類似名稱，各租戶顯示可能略有差異）。
  2. 建立後會拿到一組 HTTP POST URL，設成環境變數 TEAMS_WEBHOOK_URL（或用 --teams-webhook-url 傳入）。
  3. 這支程式送出的是標準 Adaptive Card JSON（見 build_teams_payload）。Power Automate 那邊的
     "When a Teams webhook request is received" 觸發點可以自訂 request body 的 schema——
     如果你們的 flow 定義的 schema 跟這裡送出的不一樣，需要在 flow 裡調整解析方式，
     或者跟我說實際的 schema 長怎樣，我再改送出格式。

介面刻意跟 chat_notify.py 對齊（一樣吃 findings/entries/base_url/report_path），
run_check.py 之後要兩邊都推、或只推其中一邊都很好改。
"""

import requests

from .report import build_summary


def build_teams_payload(findings, entries, base_url, report_path=None):
    summary = build_summary(findings, entries)

    body = [
        {
            "type": "TextBlock",
            "text": "API Review Agent — Public Swagger 靜態檢查完成",
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": f"來源：{base_url}",
            "isSubtle": True,
            "wrap": True,
            "spacing": "None",
        },
        {
            "type": "TextBlock",
            "text": f"時間：{summary['generated_at']}",
            "isSubtle": True,
            "wrap": True,
            "spacing": "None",
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "總發現數", "value": str(summary["total"])},
                {"title": "Error", "value": str(summary["by_severity"].get("error", 0))},
                {"title": "Warning", "value": str(summary["by_severity"].get("warning", 0))},
                {"title": "Info", "value": str(summary["by_severity"].get("info", 0))},
                {"title": "無發現的分頁", "value": f"{summary['num_clean_files']}/{summary['num_real_files']}"},
            ],
        },
    ]

    top_errors = [f for f in findings if f["severity"] == "error"][:5]
    if top_errors:
        lines = []
        for f in top_errors:
            endpoint = f"{f['method']} {f['path']}" if f["path"] else f["file"]
            lines.append(f"- [{f['group']}] {endpoint} — {f['message']}")
        body.append({"type": "TextBlock", "text": "**前幾筆 Error：**", "wrap": True, "spacing": "Medium"})
        body.append({"type": "TextBlock", "text": "\n\n".join(lines), "wrap": True})

    actions = []
    if report_path:
        if report_path.startswith("http://") or report_path.startswith("https://"):
            actions.append({"type": "Action.OpenUrl", "title": "開啟完整報告", "url": report_path})
        else:
            body.append({"type": "TextBlock", "text": f"完整報告：{report_path}", "wrap": True, "spacing": "Medium"})

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if actions:
        card["actions"] = actions

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }


def post_to_teams(webhook_url, payload):
    if not webhook_url:
        raise ValueError("沒有設定 Teams webhook URL（TEAMS_WEBHOOK_URL 或 --teams-webhook-url）")
    resp = requests.post(webhook_url, json=payload, timeout=20)
    resp.raise_for_status()
    return resp
