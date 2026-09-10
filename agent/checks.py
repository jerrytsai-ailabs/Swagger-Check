"""Phase 1 靜態比對規則。

只讀 Swagger/OpenAPI 定義本身，不會實際呼叫 API（不做 live call）。
每個 check_* 函式回傳 list of finding dict：
    {
        "rule": str,           # 規則代號
        "severity": str,       # "error" | "warning" | "info"
        "file": str,           # spec 檔名，如 auth-v2.yaml
        "group": str,          # 分頁名稱，如 Auth V2
        "path": str | None,    # endpoint path
        "method": str | None,  # HTTP method（大寫）
        "location": str,       # 在 schema 內的位置（給人看的麵包屑）
        "message": str,
    }
"""

from openapi_spec_validator import OpenAPIV31SpecValidator

from .config import HTTP_METHODS, KNOWN_NON_PUBLIC_EXCEPTIONS

MAX_WALK_DEPTH = 8

_JSON_TYPE_TO_PY = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


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
    }


def _resolve_ref(ref, spec):
    """只處理本檔內的本地 $ref（例如 '#/components/schemas/Error'）。"""
    if not ref.startswith("#/"):
        return None, None
    parts = ref[2:].split("/")
    node = spec
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return None, None
        node = node[p]
    return node, ref


def check_schema_validity(entry):
    """R1: 用 openapi-spec-validator 驗證整份文件的 OpenAPI 3.1 結構合法性。"""
    findings = []
    spec = entry["spec"]
    if not spec:
        return findings
    try:
        validator = OpenAPIV31SpecValidator(spec)
        for err in validator.iter_errors():
            loc = " > ".join(str(p) for p in err.path) if err.path else "(root)"
            findings.append(
                _finding(
                    "schema_validity",
                    "error",
                    entry["filename"],
                    entry["group"],
                    err.message,
                    location=loc,
                )
            )
    except Exception as exc:  # noqa: BLE001 - validator 本身壞掉也要回報，不要整支腳本中斷
        findings.append(
            _finding(
                "schema_validity",
                "error",
                entry["filename"],
                entry["group"],
                f"驗證器執行失敗：{exc}",
            )
        )
    return findings


def check_operation_docs(entry):
    """R2: 每個 operation 要有非空 summary 與 description。"""
    findings = []
    spec = entry["spec"]
    if not spec:
        return findings
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            if not (op.get("summary") or "").strip():
                findings.append(
                    _finding(
                        "missing_summary",
                        "warning",
                        entry["filename"],
                        entry["group"],
                        "這個 endpoint 沒有 summary",
                        path=path,
                        method=method,
                    )
                )
            if not (op.get("description") or "").strip():
                findings.append(
                    _finding(
                        "missing_description",
                        "warning",
                        entry["filename"],
                        entry["group"],
                        "這個 endpoint 沒有 description",
                        path=path,
                        method=method,
                    )
                )
    return findings


def check_public_prefix(entry):
    """R3: 檢查 path 是否遵循 /public/ 命名慣例（已知例外不報）。"""
    findings = []
    spec = entry["spec"]
    if not spec:
        return findings
    for path in (spec.get("paths") or {}).keys():
        if path == "/*":
            continue  # Common / SSE 分頁的佔位路徑
        if path.startswith("/public/"):
            continue
        if (entry["filename"], path) in KNOWN_NON_PUBLIC_EXCEPTIONS:
            continue
        findings.append(
            _finding(
                "non_public_prefix",
                "info",
                entry["filename"],
                entry["group"],
                "這個 path 沒有 /public/ 前綴，確認是否為刻意保留的相容路徑（若是，麻煩在 description 講明原因）",
                path=path,
            )
        )
    return findings


def _walk_schema(node, spec, file, group, path, method, breadcrumb, findings, depth=0, seen_refs=None):
    if seen_refs is None:
        seen_refs = set()
    if depth > MAX_WALK_DEPTH or not isinstance(node, dict):
        return

    if "$ref" in node:
        resolved, ref_str = _resolve_ref(node["$ref"], spec)
        if resolved is None or ref_str in seen_refs:
            return
        seen_refs = seen_refs | {ref_str}
        _walk_schema(resolved, spec, file, group, path, method, breadcrumb, findings, depth, seen_refs)
        return

    schema_type = node.get("type")

    if schema_type == "object" or "properties" in node:
        properties = node.get("properties") or {}
        required = node.get("required") or []
        for name in required:
            if name not in properties:
                findings.append(
                    _finding(
                        "required_field_missing",
                        "error",
                        file,
                        group,
                        f"`required` 裡列了 `{name}`，但 `properties` 沒有定義這個欄位",
                        path=path,
                        method=method,
                        location=breadcrumb,
                    )
                )
        for prop_name, prop_schema in properties.items():
            prop_breadcrumb = f"{breadcrumb}.{prop_name}" if breadcrumb else prop_name
            resolved_prop = prop_schema
            if isinstance(prop_schema, dict) and "$ref" in prop_schema:
                resolved_prop, _ = _resolve_ref(prop_schema["$ref"], spec)
                resolved_prop = resolved_prop or {}
            if isinstance(resolved_prop, dict):
                if not (resolved_prop.get("description") or "").strip():
                    findings.append(
                        _finding(
                            "missing_property_description",
                            "warning",
                            file,
                            group,
                            f"欄位 `{prop_name}` 沒有 description",
                            path=path,
                            method=method,
                            location=prop_breadcrumb,
                        )
                    )
                _check_missing_example(resolved_prop, file, group, path, method, prop_breadcrumb, findings)
                _check_example_type(resolved_prop, file, group, path, method, prop_breadcrumb, findings)
            _walk_schema(prop_schema, spec, file, group, path, method, prop_breadcrumb, findings, depth + 1, seen_refs)

    elif schema_type == "array" and "items" in node:
        _walk_schema(node["items"], spec, file, group, path, method, f"{breadcrumb}[]", findings, depth + 1, seen_refs)

    for combinator in ("allOf", "oneOf", "anyOf"):
        for sub in node.get(combinator) or []:
            _walk_schema(sub, spec, file, group, path, method, breadcrumb, findings, depth + 1, seen_refs)


_SCALAR_TYPES = {"string", "integer", "number", "boolean"}


def _check_missing_example(schema, file, group, path, method, breadcrumb, findings):
    """跟 PM 討論後新增的規則：純量欄位沒有 example 也要標出來（例如 contentType 這類欄位）。

    一開始以為有 enum 的欄位可以跳過（合法值都列出來了，感覺不需要再給 example），
    但實測對到 PM 講的具體案例（asura-v1.yaml 的 contentType，有 enum 但沒 example）才發現
    enum 列的是「合法值有哪些」，example 給的是「示範怎麼填」，兩者用途不同——enum 不能取代 example。
    """
    declared_type = schema.get("type")
    if not isinstance(declared_type, str) or declared_type not in _SCALAR_TYPES:
        return  # OpenAPI 3.1 允許 type 是 list（如 ["string","null"]）；先不判斷型別聯集，避免誤判
    if "example" not in schema:
        findings.append(
            _finding(
                "missing_example",
                "warning",
                file,
                group,
                "這個欄位沒有 example",
                path=path,
                method=method,
                location=breadcrumb,
            )
        )


def _check_example_type(schema, file, group, path, method, breadcrumb, findings):
    if "example" not in schema:
        return
    declared_type = schema.get("type")
    if not isinstance(declared_type, str) or declared_type not in _JSON_TYPE_TO_PY:
        return  # OpenAPI 3.1 允許 type 是 list（如 ["string","null"]）；型別聯集先不判斷，避免誤判
    example = schema["example"]
    py_type = _JSON_TYPE_TO_PY[declared_type]
    if declared_type == "integer" and isinstance(example, bool):
        matches = False
    elif declared_type in ("integer", "number") and isinstance(example, bool):
        matches = False
    else:
        matches = isinstance(example, py_type)
    if not matches:
        findings.append(
            _finding(
                "example_type_mismatch",
                "warning",
                file,
                group,
                f"`example` 值 {example!r} 看起來不符合宣告的 type `{declared_type}`",
                path=path,
                method=method,
                location=breadcrumb,
            )
        )


def check_schemas(entry):
    """R4/R5/R6：遍歷 components.schemas 與各 operation 的 request/response schema，
    檢查 description 完整性、required 欄位存在性、example 型別是否吻合。
    """
    findings = []
    spec = entry["spec"]
    if not spec:
        return findings

    # components.schemas：共用 schema 只在這裡走一次，避免同一個 $ref 在每個引用點都報一次重複的 finding。
    for name, schema in ((spec.get("components") or {}).get("schemas") or {}).items():
        _walk_schema(schema, spec, entry["filename"], entry["group"], None, None, f"components.schemas.{name}", findings)

    # 各 operation 自己 inline 定義（沒有走 $ref）的 request/response schema。
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            request_schema = (
                ((op.get("requestBody") or {}).get("content") or {})
                .get("application/json", {})
                .get("schema")
            )
            if request_schema and "$ref" not in request_schema:
                _walk_schema(request_schema, spec, entry["filename"], entry["group"], path, method, "requestBody", findings)

            for status, resp in (op.get("responses") or {}).items():
                if not isinstance(resp, dict):
                    continue
                resp_schema = ((resp.get("content") or {}).get("application/json", {})).get("schema")
                if resp_schema and "$ref" not in resp_schema:
                    _walk_schema(
                        resp_schema, spec, entry["filename"], entry["group"], path, method, f"responses.{status}", findings
                    )
    return findings


ALL_CHECKS = [
    check_schema_validity,
    check_operation_docs,
    check_public_prefix,
    check_schemas,
]


def run_all_checks(entries):
    """對 fetch_specs()/load_local_specs() 回傳的每筆 entry 跑完整套規則，回傳攤平後的 findings list。"""
    all_findings = []
    for entry in entries:
        if entry["error"]:
            all_findings.append(
                _finding(
                    "fetch_error",
                    "error",
                    entry["filename"],
                    entry["group"],
                    f"抓取或解析失敗：{entry['error']}",
                )
            )
            continue
        for check_fn in ALL_CHECKS:
            all_findings.extend(check_fn(entry))
    return all_findings
