"""Phase：拿 spec 裡的 description 文字給 LLM 看，抓「寫得清不清楚」跟「有沒有邏輯矛盾」。

跟 checks.py 的硬規則不一樣——硬規則只能抓「有沒有寫」，這裡抓的是「寫得好不好」，例如：
    - 同一個欄位的說明前後矛盾（例如同時說「必填」又說「可省略」）
    - 敘述含糊、看完還是不知道該填什麼

用的是 FedGPT 自己的 LLM API（llm-v1.yaml 那個分頁），一個 operation 打一次（把這個 endpoint
自己的 summary/description，以及 request/response 裡每個有寫 description 的欄位，一次打包
成一個 prompt），比照每個欄位都打一次更省呼叫次數，也讓模型能一次看到同一支 endpoint 裡
所有描述、才抓得到「兩個欄位互相矛盾」這種需要對照著看的問題。

只挑「有寫 description 的」來送——完全沒寫的欄位已經被 checks.py 的 missing_description /
missing_property_description 抓過了，不需要 LLM 重複判斷「有沒有寫」。

這個 phase 會呼叫真的 LLM API（有 token 成本、也需要 FEDGPT_ACCESS_TOKEN），所以是 opt-in
（--llm-check），不會預設跑。
"""

import json

import requests

from .checks import _resolve_ref  # 沿用同一套本地 $ref 解析邏輯
from .config import HTTP_METHODS, REQUEST_TIMEOUT_SECONDS

MAX_WALK_DEPTH = 6
LLM_MODEL = "Default"  # 代稱，解到部署當下的預設模型，不用因為換模型改這裡

_SYSTEM_PROMPT = (
    "You are reviewing descriptions from a public REST API's OpenAPI spec, written for API "
    "integrators. Each description below is labeled with where it comes from. Judge ONLY two "
    "things per description: "
    "(1) is it genuinely unclear or ambiguous to a competent developer trying to use this field, "
    "(2) does it logically contradict another description in this same list (or itself). "
    "Do NOT flag: missing descriptions (not your job), style, formatting, tone, or descriptions "
    "that are merely terse but unambiguous. Only flag real problems a careful reader would hit. "
    "Respond with exactly one JSON object, nothing else: "
    '{"issues": [{"location": "<the label>", "severity": "error"|"warning", "message": "<why, in Traditional Chinese>"}]}. '
    'Use "error" only for direct logical contradictions. Use "warning" for genuine ambiguity. '
    'If there is nothing to flag, respond {"issues": []}.'
)


def _finding(rule, severity, file, group, message, path=None, method=None, location=""):
    return {
        "rule": rule,
        "severity": severity,
        "file": file,
        "group": group,
        "path": path,
        "method": method.upper() if method else None,
        "location": location,
        "message": message,
        "phase": "llm",
    }


def _collect_descriptions(node, spec, breadcrumb, out, depth=0, seen_refs=None):
    """蒐集 (breadcrumb, description) pairs，只收非空的 description。"""
    seen_refs = seen_refs or set()
    if depth > MAX_WALK_DEPTH or not isinstance(node, dict):
        return

    if "$ref" in node:
        resolved, ref_str = _resolve_ref(node["$ref"], spec)
        if resolved is None or ref_str in seen_refs:
            return
        _collect_descriptions(resolved, spec, breadcrumb, out, depth, seen_refs | {ref_str})
        return

    desc = (node.get("description") or "").strip()
    if desc and breadcrumb:
        out.append((breadcrumb, desc))

    properties = node.get("properties") or {}
    for prop_name, prop_schema in properties.items():
        prop_breadcrumb = f"{breadcrumb}.{prop_name}" if breadcrumb else prop_name
        _collect_descriptions(prop_schema, spec, prop_breadcrumb, out, depth + 1, seen_refs)

    if node.get("type") == "array" and "items" in node:
        _collect_descriptions(node["items"], spec, f"{breadcrumb}[]", out, depth + 1, seen_refs)

    for combinator in ("allOf", "oneOf", "anyOf"):
        for sub in node.get(combinator) or []:
            _collect_descriptions(sub, spec, breadcrumb, out, depth + 1, seen_refs)


def _collect_operation_descriptions(op, spec):
    """一支 operation 自己的 summary/description，加上 request/response schema 裡的欄位說明。"""
    items = []

    op_desc = (op.get("description") or "").strip()
    if op_desc:
        items.append(("(endpoint 本身)", op_desc))

    request_schema = (
        ((op.get("requestBody") or {}).get("content") or {}).get("application/json", {}).get("schema")
    )
    if request_schema:
        _collect_descriptions(request_schema, spec, "requestBody", items)

    for status, resp in (op.get("responses") or {}).items():
        if not isinstance(resp, dict):
            continue
        resp_schema = ((resp.get("content") or {}).get("application/json", {})).get("schema")
        if resp_schema:
            _collect_descriptions(resp_schema, spec, f"responses.{status}", items)

    return items


def _call_llm(api_base_url, token, prompt_items, timeout):
    labeled = "\n\n".join(f"[{label}]\n{text}" for label, text in prompt_items)
    resp = requests.post(
        f"{api_base_url.rstrip('/')}/public/llm/v1/fedgpt/v1/chat/completions",
        headers={"X-Access-Token": token, "Content-Type": "application/json"},
        json={
            "model": LLM_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": labeled},
            ],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    issues = parsed.get("issues")
    if not isinstance(issues, list):
        raise ValueError(f"預期 'issues' 是 list，實際拿到：{parsed!r}")
    return issues


def run_llm_checks(entries, api_base_url, token, timeout=REQUEST_TIMEOUT_SECONDS * 3):
    findings = []
    if not token:
        findings.append(_finding("llm_check_skipped", "info", "-", "-", "沒有設定 FEDGPT_ACCESS_TOKEN，跳過整個 LLM 檢查階段"))
        return findings

    for entry in entries:
        if not entry.get("is_real") or not entry.get("spec"):
            continue
        spec = entry["spec"]
        for path, item in (spec.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            for method, op in item.items():
                if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                    continue
                prompt_items = _collect_operation_descriptions(op, spec)
                if not prompt_items:
                    continue
                try:
                    issues = _call_llm(api_base_url, token, prompt_items, timeout)
                except Exception as exc:  # noqa: BLE001 - LLM 呼叫/解析失敗要回報，不能讓一支 endpoint 中斷整輪
                    findings.append(
                        _finding(
                            "llm_call_failed",
                            "warning",
                            entry["filename"],
                            entry["group"],
                            f"LLM 檢查失敗：{exc}",
                            path=path,
                            method=method,
                        )
                    )
                    continue
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    severity = issue.get("severity") if issue.get("severity") in ("error", "warning") else "warning"
                    findings.append(
                        _finding(
                            "llm_description_issue",
                            severity,
                            entry["filename"],
                            entry["group"],
                            issue.get("message", "(LLM 沒有給訊息)"),
                            path=path,
                            method=method,
                            location=str(issue.get("location", "")),
                        )
                    )

    return findings
