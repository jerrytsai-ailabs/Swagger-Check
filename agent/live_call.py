"""Phase 2（部分）：live call —— 目前只打 GET，不碰任何有副作用的 method。

流程：
  1. 先打所有「不需要 path 參數」的 GET，把回應存起來，順便掃出可以重複利用的資源 ID
     （例如從 conversations 列表拿到一個真的 convId）。
  2. 再打「需要 path 參數」的 GET：能從第 1 步的回應找到對應 ID 就用真的 ID 呼叫，
     找不到就退而求其次用該參數在 spec 上宣告的 example 值（大概率會拿到 404，
     但至少能驗證路由存在、錯誤回應的格式對不對）；兩者都沒有就跳過並記錄原因。
  3. 每一次呼叫都檢查：
       - 實際狀態碼是否有在 spec 的 responses 裡宣告過
       - 如果該狀態碼有宣告 application/json 的 schema，把回應內容拿去對這個 schema 做驗證

不會發送任何 POST/PUT/PATCH/DELETE，也不會跟隨 redirect（避免意外打到外部的預簽 URL）。
"""

import copy

import requests
from jsonschema import Draft202012Validator

from .checks import _resolve_ref  # 沿用同一套本地 $ref 解析邏輯
from .config import HTTP_METHODS, REQUEST_TIMEOUT_SECONDS

MAX_DEREF_DEPTH = 10


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
        "phase": "live",
    }


def _resolve_param(param, spec):
    if isinstance(param, dict) and "$ref" in param:
        resolved, _ = _resolve_ref(param["$ref"], spec)
        return resolved or {}
    return param


def _deref_schema(schema, spec, seen=None, depth=0):
    """回傳把本地 $ref 展開後的 schema 深拷貝，供 jsonschema 驗證用。"""
    if depth > MAX_DEREF_DEPTH or not isinstance(schema, dict):
        return schema
    seen = seen or set()

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen:
            return {}  # 避免自我參照的 schema 造成無限遞迴
        resolved, _ = _resolve_ref(ref, spec)
        if resolved is None:
            return {}
        return _deref_schema(resolved, spec, seen | {ref}, depth + 1)

    result = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {k: _deref_schema(v, spec, seen, depth + 1) for k, v in value.items()}
        elif key == "items":
            result[key] = _deref_schema(value, spec, seen, depth + 1)
        elif key in ("allOf", "oneOf", "anyOf") and isinstance(value, list):
            result[key] = [_deref_schema(v, spec, seen, depth + 1) for v in value]
        else:
            result[key] = value
    return result


def collect_get_operations(entries):
    """列出所有真實 spec 檔案裡的 GET operation，附上 path 參數清單。"""
    ops = []
    for entry in entries:
        if not entry.get("is_real") or not entry.get("spec"):
            continue
        spec = entry["spec"]
        for path, item in (spec.get("paths") or {}).items():
            if not isinstance(item, dict) or "get" not in item:
                continue
            op = item["get"]
            raw_params = (op.get("parameters") or []) + (item.get("parameters") or [])
            path_params = [
                p for p in (_resolve_param(rp, spec) for rp in raw_params) if p.get("in") == "path"
            ]
            ops.append({"entry": entry, "spec": spec, "path": path, "op": op, "path_params": path_params})
    return ops


def _scan_for_ids(obj, wanted_names, pool, depth=0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in wanted_names and key not in pool and isinstance(value, (str, int)):
                pool[key] = value
            _scan_for_ids(value, wanted_names, pool, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:5]:  # 列表只看前幾筆就夠了，不用整份掃
            _scan_for_ids(item, wanted_names, pool, depth + 1)


def _documented_statuses(op):
    return set((op.get("responses") or {}).keys())


def _check_response(entry, path, op, status_code, resp, findings):
    file, group = entry["filename"], entry["group"]
    documented = _documented_statuses(op)
    status_str = str(status_code)

    if status_str not in documented and "default" not in documented:
        findings.append(
            _finding(
                "undocumented_status_code",
                "error",
                file,
                group,
                f"實際回傳 {status_str}，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：{sorted(documented)}）",
                path=path,
                method="GET",
            )
        )
        return

    resp_spec = (op.get("responses") or {}).get(status_str) or (op.get("responses") or {}).get("default")
    schema = ((resp_spec or {}).get("content") or {}).get("application/json", {}).get("schema")
    if not schema:
        return  # 這個狀態碼沒宣告 json schema（例如 307 redirect），不用比對內容

    content_type = resp.headers.get("Content-Type", "")
    if "json" not in content_type:
        return

    try:
        body = resp.json()
    except ValueError:
        findings.append(
            _finding(
                "response_not_json",
                "warning",
                file,
                group,
                f"狀態碼 {status_str} 的 Content-Type 是 {content_type}，但 body 沒辦法解析成 JSON",
                path=path,
                method="GET",
            )
        )
        return

    deref = _deref_schema(schema, entry["spec"])
    validator = Draft202012Validator(deref)
    errors = sorted(validator.iter_errors(body), key=lambda e: list(e.path))
    for err in errors[:5]:  # 同一個回應的錯誤太多時只列前幾個，避免洗版
        loc = ".".join(str(p) for p in err.path) or "(root)"
        findings.append(
            _finding(
                "response_schema_mismatch",
                "error",
                file,
                group,
                f"實際回應在 {loc} 不符合 spec 宣告的 schema：{err.message}",
                path=path,
                method="GET",
                location=f"responses.{status_str}.{loc}",
            )
        )


def run_live_get_checks(entries, api_base_url, token, timeout=REQUEST_TIMEOUT_SECONDS):
    findings = []
    if not token:
        findings.append(
            _finding("live_call_skipped", "info", "-", "-", "沒有設定 FEDGPT_ACCESS_TOKEN，跳過整個 live call 階段")
        )
        return findings

    ops = collect_get_operations(entries)
    session = requests.Session()
    session.headers.update({"X-Access-Token": token})

    def call(path):
        url = api_base_url.rstrip("/") + path
        try:
            return session.get(url, timeout=timeout, allow_redirects=False), None
        except requests.RequestException as exc:
            return None, str(exc)

    no_param_ops = [o for o in ops if not o["path_params"]]
    param_ops = [o for o in ops if o["path_params"]]

    wanted_names = {p["name"] for o in param_ops for p in o["path_params"] if p.get("name")}
    id_pool = {}

    for o in no_param_ops:
        resp, err = call(o["path"])
        if err:
            findings.append(
                _finding("live_call_error", "error", o["entry"]["filename"], o["entry"]["group"], f"呼叫失敗：{err}", path=o["path"], method="GET")
            )
            continue
        _check_response(o["entry"], o["path"], o["op"], resp.status_code, resp, findings)
        if "json" in resp.headers.get("Content-Type", ""):
            try:
                _scan_for_ids(resp.json(), wanted_names, id_pool)
            except ValueError:
                pass

    for o in param_ops:
        filled_path = o["path"]
        skip_reason = None
        for p in o["path_params"]:
            name = p.get("name")
            value = id_pool.get(name)
            if value is None:
                value = (p.get("schema") or {}).get("example")
            if value is None:
                skip_reason = f"缺少可用的測試值：{name}（既沒有從其他回應拿到真實 ID，spec 上這個參數也沒有 example）"
                break
            filled_path = filled_path.replace("{" + name + "}", str(value))

        if skip_reason:
            findings.append(
                _finding(
                    "live_call_skipped",
                    "info",
                    o["entry"]["filename"],
                    o["entry"]["group"],
                    skip_reason,
                    path=o["path"],
                    method="GET",
                )
            )
            continue

        resp, err = call(filled_path)
        if err:
            findings.append(
                _finding("live_call_error", "error", o["entry"]["filename"], o["entry"]["group"], f"呼叫失敗：{err}", path=o["path"], method="GET")
            )
            continue
        _check_response(o["entry"], o["path"], o["op"], resp.status_code, resp, findings)

    return findings
