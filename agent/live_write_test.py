"""小範圍的寫入方法（POST/PUT/DELETE）live call 測試。

跟 live_call.py（只做 GET）不一樣：這裡會真的建立、修改、刪除資料，所以刻意縮到最小範圍——
只挑「建立完可以自己刪乾淨」的資源（有 DELETE），不會在 dev 環境留下垃圾測試資料：

    - Chat V2 的對話（run_conversation_crud_test）
    - FAQ V1 的問答集（run_faq_crud_test）
    - Knowledge V3 的知識庫（run_knowledge_crud_test）

每一組都是同樣的流程：POST 建立 -> GET 驗證存進去的內容 -> PUT 改名稱/標題 -> GET 驗證真的
改了 -> DELETE 刪除 -> GET 驗證真的刪了（應該 404）。任何一步失敗就停下來、不繼續做後面
會動資料的步驟——寧可留下一筆標題清楚標成 `[agent-test]` 的殘留資料，也不要在不確定前面
步驟結果的情況下繼續刪東西。

FAQ / Knowledge 都需要帳號有對應的 admin 權限（`faq.admin` / `kb.admin`）才能建立，如果測試
帳號沒有這個權限，POST 會回 403——這種情況視為「權限不足、無法測試」，回報成 info 等級的
live_write_skipped，不是硬規則意義上的 bug。
"""

import requests
from jsonschema import Draft202012Validator

from .live_call import _deref_schema, _documented_statuses

TEST_TITLE = "[agent-test] live-call POST/PUT/DELETE 驗證"
TEST_TITLE_UPDATED = TEST_TITLE + " - updated"


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
        "phase": "live_write",
    }


def _check_status_and_schema(op, resp, findings, file, group, path, method):
    """跟 live_call.py 的 _check_response 邏輯一致：狀態碼有沒有宣告過、body 符不符合 schema。"""
    status_str = str(resp.status_code)
    documented = _documented_statuses(op)
    if status_str not in documented and "default" not in documented:
        findings.append(
            _finding(
                "undocumented_status_code",
                "error",
                file,
                group,
                f"實際回傳 {status_str}，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：{sorted(documented)}）",
                path=path,
                method=method,
            )
        )
        return False

    resp_spec = (op.get("responses") or {}).get(status_str) or (op.get("responses") or {}).get("default")
    schema = ((resp_spec or {}).get("content") or {}).get("application/json", {}).get("schema")
    if schema and "json" in resp.headers.get("Content-Type", ""):
        try:
            body = resp.json()
        except ValueError:
            findings.append(_finding("response_not_json", "warning", file, group, "回應不是合法 JSON", path=path, method=method))
            return True
        deref = _deref_schema(schema, op["__spec__"])
        validator = Draft202012Validator(deref)
        for err in list(validator.iter_errors(body))[:5]:
            loc = ".".join(str(p) for p in err.path) or "(root)"
            findings.append(
                _finding(
                    "response_schema_mismatch",
                    "error",
                    file,
                    group,
                    f"實際回應在 {loc} 不符合 spec 宣告的 schema：{err.message}",
                    path=path,
                    method=method,
                    location=loc,
                )
            )
    return True


def _is_permission_denied(resp):
    if resp.status_code not in (401, 403):
        return False
    try:
        reason = resp.json().get("reason", "")
    except ValueError:
        return False
    return "auth." in reason


def _run_simple_crud_test(
    entries,
    api_base_url,
    token,
    timeout,
    *,
    filename,
    group,
    resource_label,
    create_path,
    item_path_template,
    wrapper_key,
    id_field,
    name_field,
    extra_create_fields=None,
    prerequisite_fn=None,
):
    """跑一輪 POST -> GET -> PUT -> GET -> DELETE -> GET，回傳 findings。"""
    findings = []
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec  # 借用 op dict 夾帶 spec 參照，方便 schema 解析時用（測試腳本內部用，不影響 spec 本身語意）
        return op

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    extra_fields = dict(extra_create_fields or {})
    if prerequisite_fn:
        prereq = prerequisite_fn(session, base, timeout, findings, filename, group)
        if prereq is None:
            return findings  # prerequisite_fn 已經把失敗原因寫進 findings
        extra_fields.update(prereq)

    # Step 1：POST 建立
    try:
        resp = session.post(
            f"{base}{create_path}",
            json={wrapper_key: {name_field: TEST_TITLE, **extra_fields}},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立{resource_label}失敗：{exc}", path=create_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(
            _finding(
                "live_write_skipped",
                "info",
                filename,
                group,
                f"測試帳號沒有建立{resource_label}的權限（{resp.status_code}），跳過這組測試",
                path=create_path,
                method="POST",
            )
        )
        return findings

    ok = _check_status_and_schema(op_of(create_path, "post"), resp, findings, filename, group, create_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(
            _finding(
                "live_write_aborted",
                "error",
                filename,
                group,
                f"POST 建立{resource_label}沒有回 200（實際 {resp.status_code}），中止後續步驟",
                path=create_path,
                method="POST",
            )
        )
        return findings

    try:
        resource_id = resp.json()[wrapper_key][id_field]
    except Exception as exc:  # noqa: BLE001
        findings.append(
            _finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 {id_field}，中止後續步驟：{exc}", path=create_path, method="POST")
        )
        return findings

    real_path = item_path_template.replace("{" + id_field + "}", resource_id)

    # Step 2：GET 驗證建立的內容
    try:
        resp = session.get(f"{base}{real_path}", timeout=timeout)
        _check_status_and_schema(op_of(item_path_template, "get"), resp, findings, filename, group, item_path_template, "get")
        got_name = (resp.json().get(wrapper_key) or {}).get(name_field)
        if got_name != TEST_TITLE:
            findings.append(
                _finding(
                    "live_write_data_mismatch",
                    "error",
                    filename,
                    group,
                    f"GET 回來的 {name_field} 是 {got_name!r}，跟 POST 送出去的 {TEST_TITLE!r} 不一致",
                    path=item_path_template,
                    method="GET",
                )
            )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"GET 驗證建立內容失敗：{exc}", path=item_path_template, method="GET"))

    # Step 3：PUT 改名稱/標題
    try:
        resp = session.put(
            f"{base}{real_path}",
            json={wrapper_key: {name_field: TEST_TITLE_UPDATED, **extra_fields}},
            timeout=timeout,
        )
        put_ok = _check_status_and_schema(op_of(item_path_template, "put"), resp, findings, filename, group, item_path_template, "put")
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"PUT 更新失敗：{exc}", path=item_path_template, method="PUT"))
        put_ok = False

    # Step 4：GET 驗證真的改了
    if put_ok:
        try:
            resp = session.get(f"{base}{real_path}", timeout=timeout)
            got_name = (resp.json().get(wrapper_key) or {}).get(name_field)
            if got_name != TEST_TITLE_UPDATED:
                findings.append(
                    _finding(
                        "live_write_data_mismatch",
                        "error",
                        filename,
                        group,
                        f"PUT 回 200，但實際 GET 回來的 {name_field} 還是 {got_name!r}，沒有真的改成 {TEST_TITLE_UPDATED!r}",
                        path=item_path_template,
                        method="PUT",
                    )
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"GET 驗證 PUT 結果失敗：{exc}", path=item_path_template, method="GET"))

    # Step 5：DELETE 清乾淨
    try:
        resp = session.delete(f"{base}{real_path}", timeout=timeout)
        delete_ok = _check_status_and_schema(op_of(item_path_template, "delete"), resp, findings, filename, group, item_path_template, "delete")
    except requests.RequestException as exc:
        findings.append(
            _finding(
                "live_write_cleanup_failed",
                "error",
                filename,
                group,
                f"DELETE 失敗，測試資料 {resource_id}（{name_field}：{TEST_TITLE_UPDATED}）可能還留在 dev 環境，需要手動清理：{exc}",
                path=item_path_template,
                method="DELETE",
            )
        )
        delete_ok = False

    # Step 6：GET 驗證真的刪了
    if delete_ok:
        try:
            resp = session.get(f"{base}{real_path}", timeout=timeout)
            if resp.status_code != 404:
                findings.append(
                    _finding(
                        "live_write_cleanup_failed",
                        "error",
                        filename,
                        group,
                        f"DELETE 回 200，但 GET 還是拿到 {resp.status_code}（預期 404），測試資料 {resource_id} 可能沒有真的刪除",
                        path=item_path_template,
                        method="DELETE",
                    )
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證刪除結果失敗：{exc}", path=item_path_template, method="GET"))

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(
            _finding(
                "live_write_ok",
                "info",
                filename,
                group,
                f"POST → GET → PUT → GET → DELETE → GET 全部驗證通過，測試資料（{resource_id}）已清除乾淨",
            )
        )

    return findings


def _chat_prerequisite(session, base, timeout, findings, filename, group):
    """Chat V2 建立對話需要先取一個有效的 model id。"""
    try:
        resp = session.get(f"{base}/public/chat/v2/models", timeout=timeout)
        resp.raise_for_status()
        models = resp.json().get("models") or []
        if not models:
            findings.append(
                _finding("live_write_error", "error", filename, group, "GET /models 沒有回傳任何模型，無法建立測試對話", path="/public/chat/v2/models", method="GET")
            )
            return None
        return {"mode": "normal", "model": models[0]["id"]}
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_error", "error", filename, group, f"取得模型清單失敗：{exc}", path="/public/chat/v2/models", method="GET"))
        return None


def run_conversation_crud_test(entries, api_base_url, token, timeout=30):
    return _run_simple_crud_test(
        entries,
        api_base_url,
        token,
        timeout,
        filename="chat-v2.yaml",
        group="Chat V2",
        resource_label="對話",
        create_path="/public/chat/v2/conversations",
        item_path_template="/public/chat/v2/conversations/{convId}",
        wrapper_key="conversation",
        id_field="convId",
        name_field="title",
        prerequisite_fn=_chat_prerequisite,
    )


def run_faq_crud_test(entries, api_base_url, token, timeout=30):
    return _run_simple_crud_test(
        entries,
        api_base_url,
        token,
        timeout,
        filename="faq-v1.yaml",
        group="FAQ V1",
        resource_label="問答集",
        create_path="/public/faq/v1/faqs",
        item_path_template="/public/faq/v1/faqs/{faqId}",
        wrapper_key="faq",
        id_field="faqId",
        name_field="name",
    )


def run_knowledge_crud_test(entries, api_base_url, token, timeout=30):
    return _run_simple_crud_test(
        entries,
        api_base_url,
        token,
        timeout,
        filename="knowledge-v3.yaml",
        group="Knowledge V3",
        resource_label="知識庫",
        create_path="/public/knowledge/v3/knowledges",
        item_path_template="/public/knowledge/v3/knowledges/{knowledgeId}",
        wrapper_key="knowledge",
        id_field="knowledgeId",
        name_field="name",
        extra_create_fields={"description": "[agent-test] 用於驗證 live call CRUD，測試完會自動刪除"},
    )
