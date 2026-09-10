"""Spec diff —— 沒有 3.10 版本可以當 baseline，改成「這次跟上次執行比較」。

第一次跑某份 spec 時沒有東西可比，只會把當下內容存成基準；
之後每次執行都會先跟上次存的基準比對，抓出：
    - endpoint / method 整支被新增或移除
    - 欄位從選填變必填（對呼叫方是破壞性變更）、必填變選填
    - 欄位被新增／移除
    - 欄位型別改變
    - enum 合法值被移除（破壞性）或新增
    - 宣告的回應狀態碼被移除或新增
比對完，不管有沒有變化，都會把這次的內容存回去當作下次比對的基準。
"""

import os

from .config import HTTP_METHODS

SNAPSHOT_DIR_DEFAULT = "spec_snapshots"


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
        "phase": "diff",
    }


def _load_snapshot(snapshot_dir, filename):
    import yaml

    path = os.path.join(snapshot_dir, filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh.read())


def _save_snapshot(snapshot_dir, filename, raw_text):
    os.makedirs(snapshot_dir, exist_ok=True)
    with open(os.path.join(snapshot_dir, filename), "w", encoding="utf-8") as fh:
        fh.write(raw_text)


def _diff_schema(old_schema, new_schema, file, group, path, method, breadcrumb, findings, depth=0):
    if depth > 6 or not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
        return

    # $ref 只比對指到哪裡變了沒，不在這裡展開比對內容——避免同一個共用 schema 在每個引用點都report 一次。
    if "$ref" in old_schema or "$ref" in new_schema:
        if old_schema.get("$ref") != new_schema.get("$ref"):
            findings.append(
                _finding(
                    "schema_ref_changed",
                    "warning",
                    file,
                    group,
                    f"參照的 schema 變了：{old_schema.get('$ref')} → {new_schema.get('$ref')}",
                    path=path,
                    method=method,
                    location=breadcrumb,
                )
            )
        return

    old_required = set(old_schema.get("required") or [])
    new_required = set(new_schema.get("required") or [])
    for name in new_required - old_required:
        findings.append(
            _finding(
                "field_became_required",
                "error",
                file,
                group,
                f"欄位 `{name}` 從選填變成必填——既有呼叫方如果沒帶這個欄位，會開始被拒絕",
                path=path,
                method=method,
                location=breadcrumb,
            )
        )
    for name in old_required - new_required:
        findings.append(
            _finding(
                "field_became_optional",
                "info",
                file,
                group,
                f"欄位 `{name}` 從必填變成選填",
                path=path,
                method=method,
                location=breadcrumb,
            )
        )

    old_props = old_schema.get("properties") or {}
    new_props = new_schema.get("properties") or {}
    for name in set(old_props) - set(new_props):
        findings.append(
            _finding(
                "field_removed",
                "warning",
                file,
                group,
                f"欄位 `{name}` 被移除了，既有依賴這個欄位的呼叫方可能受影響",
                path=path,
                method=method,
                location=f"{breadcrumb}.{name}",
            )
        )
    for name in set(new_props) - set(old_props):
        findings.append(
            _finding(
                "field_added",
                "info",
                file,
                group,
                f"新增了欄位 `{name}`",
                path=path,
                method=method,
                location=f"{breadcrumb}.{name}",
            )
        )

    for name in set(old_props) & set(new_props):
        old_p, new_p = old_props[name], new_props[name]
        if not isinstance(old_p, dict) or not isinstance(new_p, dict):
            continue
        old_type, new_type = old_p.get("type"), new_p.get("type")
        if old_type and new_type and old_type != new_type:
            findings.append(
                _finding(
                    "field_type_changed",
                    "error",
                    file,
                    group,
                    f"欄位 `{name}` 的型別從 `{old_type}` 改成 `{new_type}`",
                    path=path,
                    method=method,
                    location=f"{breadcrumb}.{name}",
                )
            )
        old_enum, new_enum = old_p.get("enum"), new_p.get("enum")
        if old_enum and new_enum:
            removed_values = set(old_enum) - set(new_enum)
            added_values = set(new_enum) - set(old_enum)
            if removed_values:
                findings.append(
                    _finding(
                        "enum_value_removed",
                        "error",
                        file,
                        group,
                        f"欄位 `{name}` 的合法值被移除：{sorted(removed_values)}——原本送這些值的呼叫方會開始被拒絕",
                        path=path,
                        method=method,
                        location=f"{breadcrumb}.{name}",
                    )
                )
            if added_values:
                findings.append(
                    _finding(
                        "enum_value_added",
                        "info",
                        file,
                        group,
                        f"欄位 `{name}` 新增了合法值：{sorted(added_values)}",
                        path=path,
                        method=method,
                        location=f"{breadcrumb}.{name}",
                    )
                )
        _diff_schema(old_p, new_p, file, group, path, method, f"{breadcrumb}.{name}", findings, depth + 1)


def _diff_operation(old_op, new_op, file, group, path, method, findings):
    old_statuses = set((old_op.get("responses") or {}).keys())
    new_statuses = set((new_op.get("responses") or {}).keys())
    for s in old_statuses - new_statuses:
        findings.append(
            _finding(
                "response_status_removed",
                "warning",
                file,
                group,
                f"狀態碼 `{s}` 的說明被移除了，原本依賴它判斷結果的呼叫方可能受影響",
                path=path,
                method=method,
            )
        )
    for s in new_statuses - old_statuses:
        findings.append(
            _finding("response_status_added", "info", file, group, f"新增了狀態碼 `{s}` 的說明", path=path, method=method)
        )

    old_req_schema = (((old_op.get("requestBody") or {}).get("content") or {}).get("application/json", {})).get("schema")
    new_req_schema = (((new_op.get("requestBody") or {}).get("content") or {}).get("application/json", {})).get("schema")
    if old_req_schema and new_req_schema:
        _diff_schema(old_req_schema, new_req_schema, file, group, path, method, "requestBody", findings)

    for status, new_resp in (new_op.get("responses") or {}).items():
        old_resp = (old_op.get("responses") or {}).get(status)
        if not isinstance(old_resp, dict) or not isinstance(new_resp, dict):
            continue
        old_schema = ((old_resp.get("content") or {}).get("application/json", {})).get("schema")
        new_schema = ((new_resp.get("content") or {}).get("application/json", {})).get("schema")
        if old_schema and new_schema:
            _diff_schema(old_schema, new_schema, file, group, path, method, f"responses.{status}", findings)


def run_spec_diff(entries, snapshot_dir=SNAPSHOT_DIR_DEFAULT):
    findings = []
    for entry in entries:
        if entry["error"] or not entry.get("is_real") or not entry.get("spec"):
            continue

        old_spec = _load_snapshot(snapshot_dir, entry["filename"])
        new_spec = entry["spec"]

        if old_spec is None:
            findings.append(
                _finding(
                    "diff_baseline_saved",
                    "info",
                    entry["filename"],
                    entry["group"],
                    "第一次看到這份 spec，先存成比對基準，下次執行才會顯示差異",
                )
            )
        else:
            old_paths = old_spec.get("paths") or {}
            new_paths = new_spec.get("paths") or {}

            for p in set(old_paths) - set(new_paths):
                findings.append(
                    _finding("endpoint_removed", "error", entry["filename"], entry["group"], f"endpoint `{p}` 整支被移除了", path=p)
                )
            for p in set(new_paths) - set(old_paths):
                findings.append(
                    _finding("endpoint_added", "info", entry["filename"], entry["group"], f"新增了 endpoint `{p}`", path=p)
                )

            for p in set(old_paths) & set(new_paths):
                old_item, new_item = old_paths[p], new_paths[p]
                if not isinstance(old_item, dict) or not isinstance(new_item, dict):
                    continue
                old_methods = {m for m in old_item if m.lower() in HTTP_METHODS}
                new_methods = {m for m in new_item if m.lower() in HTTP_METHODS}

                for m in old_methods - new_methods:
                    findings.append(
                        _finding("method_removed", "error", entry["filename"], entry["group"], f"`{m.upper()} {p}` 被移除了", path=p, method=m)
                    )
                for m in new_methods - old_methods:
                    findings.append(
                        _finding("method_added", "info", entry["filename"], entry["group"], f"新增了 `{m.upper()} {p}`", path=p, method=m)
                    )
                for m in old_methods & new_methods:
                    _diff_operation(old_item[m], new_item[m], entry["filename"], entry["group"], p, m, findings)

        _save_snapshot(snapshot_dir, entry["filename"], entry["raw_text"])

    return findings
