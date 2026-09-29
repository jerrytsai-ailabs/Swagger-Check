"""Markdown 版報告：每次執行都覆寫 docs/REPORT.md，放進 git 版控持續更新。

跟 HTML 報告（report.py）吃同一份 findings，但定位不同：HTML 看細節，Markdown 看摘要與變化——
每次 commit 之後 `git diff docs/REPORT.md` 就能看出這次比上次多了/少了哪些問題。為了讓 diff 乾淨：
- 同一個根因只列一行（例如 5 筆 conversations.N 缺 disabled 合併成一行「×5」）
- Info 只列各規則的數量，不逐筆列出
- LLM 文字審查每次結果會浮動，細項放在最後面，不跟其他階段混在一起

執行歷史存在 docs/report-history.json（結構化資料，每次執行 append 一筆），Markdown 裡的歷史表
每次都從這份 JSON 重新產生，不回頭解析 Markdown。
"""

import json
import os
import re
from collections import Counter, OrderedDict

from .config import derive_swagger_ui_url
from .report import _SEVERITY_LABEL, _environment_label, build_summary, compute_endpoint_pass_fail

HISTORY_LIMIT = 30  # 歷史表只顯示最近幾次，JSON 本身完整保留

_PHASE_LABEL = OrderedDict(
    [
        ("static", "靜態比對"),
        ("live", "Live GET"),
        ("live_write", "Live 寫入"),
        ("diff", "Spec diff"),
        ("llm", "LLM 審查"),
    ]
)

# 陣列索引（conversations.0、sseEvents.3）合併成 .N，讓同一個根因的多筆發現歸成一行；
# responses.200 這種是狀態碼不是索引，要保留
_INDEX_RE = re.compile(r"(?<!responses)\.\d+(?=[.\s:：\]]|$)")


def _md(value):
    """表格儲存格用：跳脫 |、把換行壓成空白。"""
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _code(value):
    return f"`{_md(value)}`" if value else "—"


def _normalize(text):
    return _INDEX_RE.sub(".N", text or "")


def _phases_run(findings):
    return [p for p in _PHASE_LABEL if any(f.get("phase", "static") == p for f in findings)]


def _group_findings(findings):
    """依 (phase, file, method, path, rule, 正規化後的 location/message) 合併，保留第一次出現的順序。"""
    groups = OrderedDict()
    for f in findings:
        key = (
            f.get("phase", "static"),
            f.get("group") or "",
            f.get("method") or "",
            _normalize(f.get("path") or ""),
            f["rule"],
            _normalize(f.get("location") or ""),
            _normalize(f.get("message") or ""),
        )
        groups.setdefault(key, 0)
        groups[key] += 1
    return groups


def _grouped_table(findings):
    rows = ["| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |", "|---|---|---|---|---|---|---|"]
    for (phase, group, method, path, rule, location, message), count in _group_findings(findings).items():
        endpoint = f"{method} `{_md(path)}`".strip() if path else "—"
        rows.append(
            f"| {_PHASE_LABEL.get(phase, phase)} | {_md(group)} | {endpoint} | {_code(location)} | {_md(message)} | `{rule}` | {count} |"
        )
    return "\n".join(rows)


def build_history_record(findings, entries, base_url):
    summary = build_summary(findings, entries)
    env_label, _ = _environment_label(base_url)
    passed, failed, total = compute_endpoint_pass_fail(findings)
    return {
        "generated_at": summary["generated_at"],
        "env": env_label,
        "phases": _phases_run(findings),
        "endpoint_pass": passed,
        "endpoint_fail": failed,
        "endpoint_total": total,
        "error": summary["by_severity"].get("error", 0),
        "warning": summary["by_severity"].get("warning", 0),
        "info": summary["by_severity"].get("info", 0),
    }


def load_history(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(path, history):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(history, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _pass_rate(passed, total):
    return f"{passed / total * 100:.1f}%" if total else "—"


def _verdict(failed, total):
    if not total:
        return "未評估"
    return "**FAIL**" if failed else "**PASS**"


def render_markdown(findings, entries, base_url, history):
    summary = build_summary(findings, entries)
    env_label, _ = _environment_label(base_url)
    passed, failed, total = compute_endpoint_pass_fail(findings)
    phases = _phases_run(findings)

    lines = [
        f"# Public Swagger 稽核報告（{env_label}）",
        "",
        "> 這份檔案由 `run_check.py` 每次執行自動覆寫，請勿手動編輯。完整細節請看同一次執行產生的 `reports/report-*.html`。",
        "",
        f"- 最後更新：{summary['generated_at']}",
        f"- 來源：{derive_swagger_ui_url(base_url)}",
        f"- 這次跑的階段：{'、'.join(_PHASE_LABEL[p] for p in phases)}",
        "",
        "| 整體結果 | Pass | Fail | Endpoint 總數 | Pass 率 |",
        "|---|---|---|---|---|",
        f"| {_verdict(failed, total)} | {passed} | {failed} | {total} | {_pass_rate(passed, total)} |",
        "",
        "| 總發現數 | Error | Warning | Info | 無發現的分頁 |",
        "|---|---|---|---|---|",
        f"| {summary['total']} | {summary['by_severity'].get('error', 0)} | {summary['by_severity'].get('warning', 0)} "
        f"| {summary['by_severity'].get('info', 0)} | {summary['num_clean_files']}/{summary['num_real_files']} |",
        "",
    ]
    if "live_write" not in phases:
        lines += [
            "> ⚠️ 這次沒有跑 `--live-write`，只有寫入測試才打得到的已知問題不會反映在 Pass 率裡。",
            "",
        ]

    # 執行歷史
    lines += [
        "## 執行歷史",
        "",
        "| 時間 | 環境 | live-write | Pass 率 | Pass/總數 | Error | Warning | Info |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rec in reversed(history[-HISTORY_LIMIT:]):
        lines.append(
            f"| {rec['generated_at']} | {rec['env']} | {'✓' if 'live_write' in rec.get('phases', []) else '—'} "
            f"| {_pass_rate(rec['endpoint_pass'], rec['endpoint_total'])} | {rec['endpoint_pass']}/{rec['endpoint_total']} "
            f"| {rec['error']} | {rec['warning']} | {rec['info']} |"
        )
    lines.append("")

    # 受檢規格
    lines += ["## 受檢規格", "", "| 分頁 | 檔案 | 狀態 |", "|---|---|---|"]
    for e in entries:
        n = summary["by_file"].get(e["filename"], 0)
        if e["error"]:
            status = "抓取失敗"
        elif not e["is_real"]:
            status = "說明用分頁"
        else:
            status = "沒問題" if n == 0 else f"{n} 個發現"
        lines.append(f"| {_md(e['group'])} | `{e['filename']}` | {status} |")
    lines.append("")

    non_llm = [f for f in findings if f.get("phase") != "llm"]
    llm = [f for f in findings if f.get("phase") == "llm"]

    for sev in ("error", "warning"):
        items = [f for f in non_llm if f["severity"] == sev]
        lines += [f"## {_SEVERITY_LABEL[sev]}（{len(items)} 筆，依根因合併）", ""]
        lines += [_grouped_table(items) if items else "_無_", ""]

    info_counts = Counter(f["rule"] for f in non_llm if f["severity"] == "info")
    lines += ["## Info（只列數量）", ""]
    if info_counts:
        lines += ["| 規則 | 筆數 |", "|---|---|"]
        lines += [f"| `{rule}` | {count} |" for rule, count in sorted(info_counts.items())]
    else:
        lines.append("_無_")
    lines.append("")

    if llm:
        llm_issues = [f for f in llm if f["rule"] not in ("llm_review_pass",)]
        llm_pass = sum(1 for f in llm if f["rule"] == "llm_review_pass")
        llm_fail = sum(1 for f in llm if f["rule"] == "llm_review_fail")
        lines += [
            "## LLM 文字審查",
            "",
            "> AI 對 description 清晰度與邏輯一致性的判斷，僅供參考；每次執行結果會有些浮動，diff 時請留意。",
            "",
            f"Pass {llm_pass} 支、Fail {llm_fail} 支。",
            "",
        ]
        if llm_issues:
            lines += [_grouped_table(llm_issues), ""]

    return "\n".join(lines)


def write_markdown_report(findings, entries, base_url, docs_dir="docs"):
    """寫 docs/REPORT.md 並把這次執行 append 到 docs/report-history.json，回傳 REPORT.md 的路徑。"""
    history_path = os.path.join(docs_dir, "report-history.json")
    history = load_history(history_path)
    history.append(build_history_record(findings, entries, base_url))
    save_history(history_path, history)

    report_path = os.path.join(docs_dir, "REPORT.md")
    with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_markdown(findings, entries, base_url, history))
    return report_path
