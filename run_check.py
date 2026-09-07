"""API Review Agent — Phase 1 雛形。

跑一輪 Public Swagger 靜態檢查，產出 HTML report，並可選擇性推播摘要到 Google Chat。

用法範例：
    python run_check.py
    python run_check.py --local swagger-spec
    python run_check.py --notify
    python run_check.py --base-url https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/docs
"""

import argparse
import os
import sys
from datetime import datetime

from agent.checks import run_all_checks
from agent.chat_notify import build_chat_message, post_to_google_chat
from agent.config import DEFAULT_BASE_URL, GOOGLE_CHAT_WEBHOOK_URL
from agent.fetch import fetch_specs, load_local_specs
from agent.report import render_html


def main():
    parser = argparse.ArgumentParser(description="Public Swagger 靜態檢查")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Swagger docs 目錄的 base URL")
    parser.add_argument("--local", metavar="DIR", help="不對外抓取，改用本機資料夾裡的 YAML（離線測試用）")
    parser.add_argument("--output-dir", default="reports", help="HTML report 輸出資料夾")
    parser.add_argument("--notify", action="store_true", help="把摘要推到 Google Chat")
    parser.add_argument("--webhook-url", default=GOOGLE_CHAT_WEBHOOK_URL, help="覆寫 GOOGLE_CHAT_WEBHOOK_URL")
    args = parser.parse_args()

    if args.local:
        print(f"[離線模式] 從 {args.local} 讀取 spec ...")
        entries = load_local_specs(args.local)
        source_label = f"local:{os.path.abspath(args.local)}"
    else:
        print(f"從 {args.base_url} 抓取 spec ...")
        entries = fetch_specs(args.base_url)
        source_label = args.base_url

    for e in entries:
        status = f"失敗（{e['error']}）" if e["error"] else "OK"
        print(f"  - {e['group']:12s} {e['filename']:20s} {status}")

    print("執行靜態檢查規則 ...")
    findings = run_all_checks(entries)
    print(f"共 {len(findings)} 筆發現")

    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = os.path.join(args.output_dir, f"report-{timestamp}.html")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(render_html(findings, entries, source_label))
    print(f"HTML report 已寫入：{os.path.abspath(report_path)}")

    if args.notify:
        message = build_chat_message(findings, entries, source_label, report_path=os.path.abspath(report_path))
        try:
            post_to_google_chat(args.webhook_url, message)
            print("已推播摘要到 Google Chat")
        except Exception as exc:  # noqa: BLE001
            print(f"推播到 Google Chat 失敗：{exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
