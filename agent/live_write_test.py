"""小範圍的寫入方法（POST/PUT/DELETE）live call 測試。

跟 live_call.py（只做 GET）不一樣：這裡會真的建立、修改、刪除資料，所以刻意縮到最小範圍——
只挑「建立完可以自己刪乾淨」的資源（有 DELETE），不會在 dev 環境留下垃圾測試資料：

    - Chat V2 的對話（run_conversation_crud_test）
    - FAQ V1 的問答集（run_faq_crud_test）以及集合底下的問與答 entry（run_faq_entry_crud_test）
    - Knowledge V3 的知識庫（run_knowledge_crud_test）以及知識庫底下的文件（run_knowledge_document_crud_test，
      需要真的走 Asset V2 的上傳流程，會留下一個無法清除的殘留檔案，見下方函式說明）
    - FedFlow V1 的 flow 執行（run_fedflow_execute_test，沒有 DELETE、不可逆，架構跟其他幾個不同，見下方函式說明）

每一組都是同樣的流程：POST 建立 -> GET 驗證存進去的內容 -> PUT 改名稱/標題 -> GET 驗證真的
改了 -> DELETE 刪除 -> GET 驗證真的刪了（應該 404）。任何一步失敗就停下來、不繼續做後面
會動資料的步驟——寧可留下一筆標題清楚標成 `[agent-test]` 的殘留資料，也不要在不確定前面
步驟結果的情況下繼續刪東西。

FAQ / Knowledge 都需要帳號有對應的 admin 權限（`faq.admin` / `kb.admin`）才能建立，如果測試
帳號沒有這個權限，POST 會回 403——這種情況視為「權限不足、無法測試」，回報成 info 等級的
live_write_skipped，不是硬規則意義上的 bug。
"""

import base64
import json
import mimetypes
import os
import time
import uuid

import requests
from jsonschema import Draft202012Validator

from .live_call import _deref_schema, _documented_statuses

TEST_TITLE = "[agent-test] live-call POST/PUT/DELETE 驗證"
TEST_TITLE_UPDATED = TEST_TITLE + " - updated"

FEDFLOW_EXECUTE_INPUT = "[agent-test] live-call FedFlow execute 驗證"
FEDFLOW_POLL_INTERVAL_SECONDS = 2
FEDFLOW_POLL_MAX_ATTEMPTS = 10
FEDFLOW_TERMINAL_STATES = {"FINISHED", "ERROR", "TIMEOUT", "TERMINATED"}

KNOWLEDGE_DOC_TEST_FILENAME = "[agent-test]-knowledge-doc.txt"
KNOWLEDGE_DOC_TEST_CONTENT = "[agent-test] 用於驗證 Knowledge V3 文件上傳與索引流程，可安全忽略。\n".encode("utf-8")
KNOWLEDGE_DOC_CONTENT_TYPE = "text/plain"
KNOWLEDGE_DOC_INDEX_POLL_INTERVAL_SECONDS = 3
KNOWLEDGE_DOC_INDEX_POLL_MAX_ATTEMPTS = 20
KNOWLEDGE_DOC_DELETE_POLL_INTERVAL_SECONDS = 2
KNOWLEDGE_DOC_DELETE_POLL_MAX_ATTEMPTS = 15
KNOWLEDGE_DOC_INDEX_TERMINAL_STATES = {"completed", "error", "cancelled", "skip", "unknown"}

TRANSCRIPTION_POLL_INTERVAL_SECONDS = 5
TRANSCRIPTION_POLL_MAX_ATTEMPTS = 30
TRANSCRIPTION_TERMINAL_STATUSES = {"completed", "error", "expired"}


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


def _parse_sse_dialect_a(resp):
    """解析 Chat V2/Flowise V3 用的 SSE 方言 A。

    回傳 (last_data, error_event)：正常結束時 error_event 是 None、last_data 是最後一則
    累積出來的 ChatResponse；中途失敗時 error_event 是那個 Error 物件。`: heartbeat` 註解行
    直接忽略，事件用空行分隔，`data:` 永遠單行。
    """
    last_data = None
    error_event = None
    pending_event = None
    # 刻意不用 iter_lines(decode_unicode=True)：requests 會在還沒把網路封包重組成完整一行
    # 之前就先解碼，中文字的 UTF-8 多位元組序列如果剛好被切在兩個封包中間會解碼壞掉，甚至
    # 誤判出多餘的換行，把一則 data: 硬生生切成兩行。改成先用原始 bytes 分行（\n 在 UTF-8
    # 裡永遠是安全的分割點，不會出現在多位元組字元中間），每一行湊齊之後再自己解碼。
    for raw_bytes in resp.iter_lines():
        if raw_bytes is None:
            continue
        try:
            raw_line = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        line = raw_line.rstrip("\r")
        if line == "":
            pending_event = None
            continue
        if line.startswith(":"):
            continue  # heartbeat 或其他註解行，忽略
        if line.startswith("event:"):
            pending_event = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_str = line[len("data:") :].strip()
            try:
                payload = json.loads(data_str)
            except ValueError:
                continue
            if pending_event == "error":
                error_event = payload
            else:
                last_data = payload
    return last_data, error_event


def _decode_jwt_user_id(token):
    """本地解碼 access token 拿出 user_id，不會把 token 送到任何地方。解不出來就回傳 None。"""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("user_id")
    except Exception:  # noqa: BLE001
        return None


def _asset_v2_presign_and_upload(
    session,
    base,
    timeout,
    findings,
    *,
    filename,
    content_type,
    content,
    presign_path="/public/asset/v2/assets:presign",
    presign_spec_label=("asset-v2.yaml", "Asset V2"),
):
    """走 presign + 實際上傳流程，回傳 assetKey；失敗回傳 None（findings 已記錄原因）。

    預設走 Asset V2 的 `/assets:presign`；Asura V1 有自己的 `/transcriptions:presign`，
    回傳的 formData 形狀相同（同一套 S3 相容 storage），可以傳 `presign_path` 覆寫。
    不論走哪個 presign 端點，實際上傳都是同一個共用的 `/asset` 端點。

    成功時會順便記一筆 live_write_permanent_residue：這個 storage 沒有查詢或刪除端點，
    上傳的檔案會永久留著，這是已知且無法避免的殘留。
    """
    presign_filename, presign_group = presign_spec_label
    origin = base[: -len("/api")] if base.endswith("/api") else base

    try:
        resp = session.post(f"{base}{presign_path}", json={"contentType": content_type, "filename": filename}, timeout=timeout)
        if resp.status_code != 200:
            findings.append(_finding("live_write_aborted", "error", presign_filename, presign_group, f"POST presign 沒有回 200（實際 {resp.status_code}）", path=presign_path, method="POST"))
            return None
        body = resp.json()
        asset_key = body.get("assetKey")
        form_data = body.get("formData") or {}
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", presign_filename, presign_group, f"POST presign 失敗：{exc}", path=presign_path, method="POST"))
        return None

    try:
        upload_resp = requests.post(f"{origin}/asset", data=form_data, files={"file": (filename, content, content_type)}, timeout=timeout)
        if not upload_resp.ok:
            findings.append(
                _finding("live_write_aborted", "error", presign_filename, presign_group, f"實際上傳檔案到 storage 失敗（{upload_resp.status_code}）：{upload_resp.text[:300]}", path="/asset", method="POST")
            )
            return None
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", presign_filename, presign_group, f"實際上傳檔案到 storage 失敗：{exc}", path="/asset", method="POST"))
        return None

    findings.append(
        _finding(
            "live_write_permanent_residue",
            "info",
            presign_filename,
            presign_group,
            f"測試上傳的檔案（assetKey={asset_key}）會永久留在 storage，這個 API 沒有提供任何刪除或查詢端點，這是已知且無法避免的殘留",
        )
    )
    return asset_key


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


def run_faq_entry_crud_test(entries, api_base_url, token, timeout=30):
    """測試 FAQ 底下 entry（問與答）的 CRUD。

    entry 一定要掛在某個 faqId 底下才能建立，所以先建一個臨時的父層 FAQ 容器（跟
    run_faq_crud_test 各自獨立、不共用資料）。不論 entry 本身的 CRUD 步驟中途是否有失敗，
    最後都會嘗試把這個臨時容器整個刪掉——刪容器本身是獨立、無歧義的動作（容器只有被我們
    建立過，沒有被中途更新過），順便也會清掉底下任何沒刪乾淨的殘留 entry。
    """
    findings = []
    filename, group = "faq-v1.yaml", "FAQ V1"
    entry_spec = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry_spec:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry_spec["spec"]
    paths = spec.get("paths") or {}
    faqs_path = "/public/faq/v1/faqs"
    faq_item_path_template = "/public/faq/v1/faqs/{faqId}"
    entries_path_template = "/public/faq/v1/faqs/{faqId}/entries"
    entry_item_path_template = "/public/faq/v1/faqs/{faqId}/entries/{entryId}"

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    # Step 0：建立一個臨時的父層 FAQ 容器，entry 要掛在某個 faqId 底下才能測
    # 名稱加隨機後綴：固定名稱如果跟前一次沒清乾淨的殘留容器同名，建立時會撞 409（見
    # _prepare_knowledge_params 的說明），這裡跟那邊用同一套做法避免撞名。
    faq_container_name = TEST_TITLE + f"（entry 測試用容器 {uuid.uuid4().hex[:8]}）"
    try:
        resp = session.post(f"{base}{faqs_path}", json={"faq": {"name": faq_container_name}}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立測試用父層 FAQ 失敗：{exc}", path=faqs_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(
            _finding("live_write_skipped", "info", filename, group, f"測試帳號沒有建立 FAQ 的權限（{resp.status_code}），跳過 entry 寫入測試", path=faqs_path, method="POST")
        )
        return findings

    ok = _check_status_and_schema(op_of(faqs_path, "post"), resp, findings, filename, group, faqs_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(
            _finding("live_write_aborted", "error", filename, group, f"POST 建立測試用父層 FAQ 沒有回 200（實際 {resp.status_code}），中止 entry 寫入測試", path=faqs_path, method="POST")
        )
        return findings

    try:
        faq_id = resp.json()["faq"]["faqId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 faqId，中止 entry 寫入測試：{exc}", path=faqs_path, method="POST"))
        return findings

    entries_path = entries_path_template.replace("{faqId}", faq_id)
    entry_id = None
    entry_item_path = None
    aborted = False

    # Step 1：POST 建立 entry
    try:
        resp = session.post(
            f"{base}{entries_path}",
            json={"entry": {"question": TEST_TITLE, "answer": "[agent-test] 測試用答案"}},
            timeout=timeout,
        )
        ok = _check_status_and_schema(op_of(entries_path_template, "post"), resp, findings, filename, group, entries_path_template, "post")
        if not ok or resp.status_code != 200:
            findings.append(
                _finding("live_write_aborted", "error", filename, group, f"POST 建立 entry 沒有回 200（實際 {resp.status_code}），中止後續步驟", path=entries_path_template, method="POST")
            )
            aborted = True
        else:
            entry_id = resp.json()["entry"]["entryId"]
            entry_item_path = entry_item_path_template.replace("{faqId}", faq_id).replace("{entryId}", entry_id)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立 entry 失敗：{exc}", path=entries_path_template, method="POST"))
        aborted = True
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 entryId，中止後續步驟：{exc}", path=entries_path_template, method="POST"))
        aborted = True

    # Step 2：GET 驗證建立的內容
    if not aborted:
        try:
            resp = session.get(f"{base}{entry_item_path}", timeout=timeout)
            _check_status_and_schema(op_of(entry_item_path_template, "get"), resp, findings, filename, group, entry_item_path_template, "get")
            got = resp.json().get("entry") or {}
            if got.get("question") != TEST_TITLE or got.get("answer") != "[agent-test] 測試用答案":
                findings.append(
                    _finding("live_write_data_mismatch", "error", filename, group, f"GET 回來的內容跟 POST 送出去的不一致：{got}", path=entry_item_path_template, method="GET")
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"GET 驗證建立內容失敗：{exc}", path=entry_item_path_template, method="GET"))

    # Step 3：PUT 整體覆寫更新（question/answer 都要重新帶，帶部分欄位不是 PATCH）
    put_ok = False
    if not aborted:
        try:
            resp = session.put(
                f"{base}{entry_item_path}",
                json={"entry": {"question": TEST_TITLE_UPDATED, "answer": "[agent-test] 測試用答案（已更新）"}},
                timeout=timeout,
            )
            put_ok = _check_status_and_schema(op_of(entry_item_path_template, "put"), resp, findings, filename, group, entry_item_path_template, "put") and resp.status_code == 200
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"PUT 更新 entry 失敗：{exc}", path=entry_item_path_template, method="PUT"))

    # Step 4：GET 驗證真的改了
    if put_ok:
        try:
            resp = session.get(f"{base}{entry_item_path}", timeout=timeout)
            got = resp.json().get("entry") or {}
            if got.get("question") != TEST_TITLE_UPDATED or got.get("answer") != "[agent-test] 測試用答案（已更新）":
                findings.append(
                    _finding(
                        "live_write_data_mismatch",
                        "error",
                        filename,
                        group,
                        f"PUT 回 200，但實際 GET 回來的內容沒有真的更新：{got}",
                        path=entry_item_path_template,
                        method="PUT",
                    )
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"GET 驗證 PUT 結果失敗：{exc}", path=entry_item_path_template, method="GET"))

    # Step 5：DELETE entry（只要有明確的 entry_id，不論前面驗證步驟是否有失敗都清掉）
    if entry_item_path:
        try:
            resp = session.delete(f"{base}{entry_item_path}", timeout=timeout)
            delete_ok = _check_status_and_schema(op_of(entry_item_path_template, "delete"), resp, findings, filename, group, entry_item_path_template, "delete")
        except requests.RequestException as exc:
            findings.append(_finding("live_write_cleanup_failed", "error", filename, group, f"DELETE entry 失敗：{exc}", path=entry_item_path_template, method="DELETE"))
            delete_ok = False

        # Step 6：GET 驗證真的刪了
        if delete_ok:
            try:
                resp = session.get(f"{base}{entry_item_path}", timeout=timeout)
                if resp.status_code != 404:
                    findings.append(
                        _finding(
                            "live_write_cleanup_failed",
                            "error",
                            filename,
                            group,
                            f"DELETE entry 回 200，但 GET 還是拿到 {resp.status_code}（預期 404），entry {entry_id} 可能沒有真的刪除",
                            path=entry_item_path_template,
                            method="DELETE",
                        )
                    )
            except requests.RequestException as exc:
                findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證 entry 刪除結果失敗：{exc}", path=entry_item_path_template, method="GET"))

    # Step 7：不論前面 entry 步驟結果如何，都清掉臨時的父層 FAQ 容器——
    # 容器本身沒被中途更新過，刪它是獨立、無歧義的動作，也會順便清掉任何殘留在裡面的 entry
    faq_item_path = faq_item_path_template.replace("{faqId}", faq_id)
    try:
        resp = session.delete(f"{base}{faq_item_path}", timeout=timeout)
        if resp.status_code != 200:
            findings.append(
                _finding(
                    "live_write_cleanup_failed",
                    "error",
                    filename,
                    group,
                    f"清理測試用父層 FAQ（{faq_id}）失敗，DELETE 回 {resp.status_code}，需要手動清理",
                    path=faq_item_path_template,
                    method="DELETE",
                )
            )
    except requests.RequestException as exc:
        findings.append(
            _finding("live_write_cleanup_failed", "error", filename, group, f"清理測試用父層 FAQ（{faq_id}）失敗：{exc}，需要手動清理", path=faq_item_path_template, method="DELETE")
        )

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(
            _finding("live_write_ok", "info", filename, group, f"entry 的 POST → GET → PUT → GET → DELETE → GET 全部驗證通過，測試容器（{faq_id}）已清除乾淨")
        )

    return findings


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


def run_knowledge_document_crud_test(entries, api_base_url, token, timeout=30):
    """測試 Knowledge V3 文件的上傳、索引、刪除。

    文件一定要先走 Asset V2 的 presign + 實際上傳流程拿到 assetKey，才能掛進知識庫，所以
    這裡會真的對 S3 相容 storage 上傳一個幾個 bytes 的極小測試檔案。

    ⚠️ 已知限制（無法避免）：Asset V2 完全沒有查詢或刪除端點（spec 原文明講），所以這裡
    上傳的測試檔案會永久留在 storage 上，沒有任何 API 能清除它——測試前已跟人類確認過
    可以接受這個殘留。

    這裡也會主動驗證一個先前實測發現、疑似與 spec 文件不一致的地方：`DELETE
    /knowledges/{knowledgeId}` 文件寫著「底下的文件會一起消失，而且無法復原」，但據回報實際
    行為是知識庫必須先清空文件才能刪除。做法是在文件還存在時先試著刪一次知識庫容器，把
    實際回應記成一筆 `spec_description_mismatch`（如果真的不一致）或確認一致的 finding，
    而不是只憑口頭轉述。確認文件真的刪除乾淨（輪詢到 404）之後才會走一般的容器清理流程；
    文件沒清乾淨就不再嘗試刪容器（反正會失敗），改回報需要手動清理。
    """
    findings = []
    kb_filename, kb_group = "knowledge-v3.yaml", "Knowledge V3"
    asset_filename, asset_group = "asset-v2.yaml", "Asset V2"

    kb_entry = next((e for e in entries if e.get("filename") == kb_filename and e.get("spec")), None)
    asset_entry = next((e for e in entries if e.get("filename") == asset_filename and e.get("spec")), None)
    if not kb_entry or not asset_entry:
        missing = kb_filename if not kb_entry else asset_filename
        findings.append(_finding("live_write_skipped", "info", missing, kb_group, f"找不到 {missing} 的 spec，跳過寫入測試"))
        return findings

    kb_spec = kb_entry["spec"]
    kb_paths = kb_spec.get("paths") or {}
    asset_spec = asset_entry["spec"]
    asset_paths = asset_spec.get("paths") or {}

    def kb_op(path, method):
        op = kb_paths[path][method]
        op["__spec__"] = kb_spec
        return op

    def asset_op(path, method):
        op = asset_paths[path][method]
        op["__spec__"] = asset_spec
        return op

    knowledges_path = "/public/knowledge/v3/knowledges"
    knowledge_item_path_template = "/public/knowledge/v3/knowledges/{knowledgeId}"
    documents_path_template = "/public/knowledge/v3/knowledges/{knowledgeId}/documents"
    document_item_path_template = "/public/knowledge/v3/knowledges/{knowledgeId}/documents/{documentId}"
    presign_path = "/public/asset/v2/assets:presign"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")
    origin = base[: -len("/api")] if base.endswith("/api") else base

    # Step 0：建立一個臨時的知識庫容器
    # 名稱加隨機後綴，避免跟前一次沒清乾淨的殘留容器撞名（見 _prepare_knowledge_params 的說明；
    # 這支測試本來就只有單一呼叫者，但如果前一次執行的容器卡在索引中很久沒被清掉，一樣會撞 409）。
    knowledge_container_name = TEST_TITLE + f"（document 測試用容器 {uuid.uuid4().hex[:8]}）"
    try:
        resp = session.post(
            f"{base}{knowledges_path}",
            json={"knowledge": {"name": knowledge_container_name, "description": "[agent-test] 用於驗證文件上傳，測試完會刪除"}},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", kb_filename, kb_group, f"POST 建立測試用知識庫失敗：{exc}", path=knowledges_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(
            _finding("live_write_skipped", "info", kb_filename, kb_group, f"測試帳號沒有建立知識庫的權限（{resp.status_code}），跳過文件寫入測試", path=knowledges_path, method="POST")
        )
        return findings

    ok = _check_status_and_schema(kb_op(knowledges_path, "post"), resp, findings, kb_filename, kb_group, knowledges_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(
            _finding("live_write_aborted", "error", kb_filename, kb_group, f"POST 建立測試用知識庫沒有回 200（實際 {resp.status_code}），中止文件寫入測試", path=knowledges_path, method="POST")
        )
        return findings

    try:
        knowledge_id = resp.json()["knowledge"]["knowledgeId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", kb_filename, kb_group, f"POST 回應裡拿不到 knowledgeId，中止文件寫入測試：{exc}", path=knowledges_path, method="POST"))
        return findings

    knowledge_item_path = knowledge_item_path_template.replace("{knowledgeId}", knowledge_id)
    documents_path = documents_path_template.replace("{knowledgeId}", knowledge_id)

    document_id = None
    document_item_path = None
    document_deleted_confirmed = False

    # Step 1：Asset V2 presign，拿上傳憑證
    asset_key = None
    form_data = {}
    try:
        resp = session.post(
            f"{base}{presign_path}",
            json={"contentType": KNOWLEDGE_DOC_CONTENT_TYPE, "filename": KNOWLEDGE_DOC_TEST_FILENAME},
            timeout=timeout,
        )
        ok = _check_status_and_schema(asset_op(presign_path, "post"), resp, findings, asset_filename, asset_group, presign_path, "post")
        if ok and resp.status_code == 200:
            body = resp.json()
            asset_key = body.get("assetKey")
            form_data = body.get("formData") or {}
        else:
            findings.append(
                _finding("live_write_aborted", "error", asset_filename, asset_group, f"POST presign 沒有回 200（實際 {resp.status_code}），中止文件寫入測試", path=presign_path, method="POST")
            )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", asset_filename, asset_group, f"POST presign 失敗：{exc}", path=presign_path, method="POST"))

    # Step 2：實際把檔案上傳到 S3 相容 storage（這支端點不在任何 spec 檔案裡，只能檢查狀態碼，
    # 不透過 session 送出——不需要也不該帶 X-Access-Token，憑證是 formData 裡的簽章）
    if asset_key:
        try:
            upload_resp = requests.post(
                f"{origin}/asset",
                data=form_data,
                files={"file": (KNOWLEDGE_DOC_TEST_FILENAME, KNOWLEDGE_DOC_TEST_CONTENT, KNOWLEDGE_DOC_CONTENT_TYPE)},
                timeout=timeout,
            )
            if not upload_resp.ok:
                findings.append(
                    _finding(
                        "live_write_aborted",
                        "error",
                        asset_filename,
                        asset_group,
                        f"實際上傳檔案到 storage 失敗（{upload_resp.status_code}）：{upload_resp.text[:300]}，中止文件寫入測試",
                        path="/asset",
                        method="POST",
                    )
                )
                asset_key = None
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", asset_filename, asset_group, f"實際上傳檔案到 storage 失敗：{exc}", path="/asset", method="POST"))
            asset_key = None

    # Step 3：把上傳好的檔案掛進知識庫
    if asset_key:
        try:
            resp = session.post(f"{base}{documents_path}", json={"document": {"assetKey": asset_key}}, timeout=timeout)
            ok = _check_status_and_schema(kb_op(documents_path_template, "post"), resp, findings, kb_filename, kb_group, documents_path_template, "post")
            if ok and resp.status_code == 200:
                document_id = resp.json()["document"]["documentId"]
                document_item_path = document_item_path_template.replace("{knowledgeId}", knowledge_id).replace("{documentId}", document_id)
            else:
                findings.append(
                    _finding("live_write_aborted", "error", kb_filename, kb_group, f"POST 掛入文件沒有回 200（實際 {resp.status_code}），中止後續步驟", path=documents_path_template, method="POST")
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", kb_filename, kb_group, f"POST 掛入文件失敗：{exc}", path=documents_path_template, method="POST"))
        except Exception as exc:  # noqa: BLE001
            findings.append(_finding("live_write_aborted", "error", kb_filename, kb_group, f"POST 回應裡拿不到 documentId：{exc}", path=documents_path_template, method="POST"))

    # Step 4：輪詢索引狀態直到終態
    final_state = None
    if document_item_path:
        for _ in range(KNOWLEDGE_DOC_INDEX_POLL_MAX_ATTEMPTS):
            try:
                resp = session.get(f"{base}{document_item_path}", timeout=timeout)
            except requests.RequestException as exc:
                findings.append(_finding("live_write_error", "error", kb_filename, kb_group, f"GET 輪詢索引狀態失敗：{exc}", path=document_item_path_template, method="GET"))
                break
            _check_status_and_schema(kb_op(document_item_path_template, "get"), resp, findings, kb_filename, kb_group, document_item_path_template, "get")
            try:
                doc = resp.json().get("document") or {}
            except ValueError:
                findings.append(_finding("live_write_error", "error", kb_filename, kb_group, "GET 輪詢索引狀態回應不是合法 JSON", path=document_item_path_template, method="GET"))
                break
            final_state = doc.get("displayState")
            if final_state in KNOWLEDGE_DOC_INDEX_TERMINAL_STATES:
                if doc.get("fileName") != KNOWLEDGE_DOC_TEST_FILENAME:
                    findings.append(
                        _finding(
                            "live_write_data_mismatch",
                            "error",
                            kb_filename,
                            kb_group,
                            f"文件的 fileName 是 {doc.get('fileName')!r}，跟上傳時的檔名 {KNOWLEDGE_DOC_TEST_FILENAME!r} 不一致",
                            path=document_item_path_template,
                            method="GET",
                        )
                    )
                break
            time.sleep(KNOWLEDGE_DOC_INDEX_POLL_INTERVAL_SECONDS)
        else:
            findings.append(
                _finding(
                    "live_write_error",
                    "warning",
                    kb_filename,
                    kb_group,
                    f"輪詢 {KNOWLEDGE_DOC_INDEX_POLL_MAX_ATTEMPTS} 次仍未進入索引終態（最後一次 displayState={final_state!r}）",
                    path=document_item_path_template,
                    method="GET",
                )
            )

        if final_state and final_state != "completed":
            findings.append(
                _finding(
                    "live_write_error",
                    "warning",
                    kb_filename,
                    kb_group,
                    f"文件索引結束但 displayState={final_state!r}（非 completed）",
                    path=document_item_path_template,
                    method="GET",
                )
            )

    # Step 4.5：文件還留著的時候，先試著刪知識庫容器一次——藉此實測驗證 spec 對這支端點的
    # 描述（「底下的文件會一起消失，而且無法復原」）是否屬實。這一步的結果不論如何都會記成
    # 一筆 finding，讓「文件寫的行為」跟「實測到的行為」是否一致有明確的證據，而不只是口頭轉述。
    container_already_gone = False
    if document_item_path:
        try:
            resp = session.delete(f"{base}{knowledge_item_path}", timeout=timeout)
            _check_status_and_schema(kb_op(knowledge_item_path_template, "delete"), resp, findings, kb_filename, kb_group, knowledge_item_path_template, "delete")
            if resp.status_code == 200:
                findings.append(
                    _finding(
                        "live_write_ok",
                        "info",
                        kb_filename,
                        kb_group,
                        "文件仍存在時刪除知識庫容器，實際回 200（成功）——這次的實測結果跟 spec 描述的「底下文件會一起消失」一致",
                        path=knowledge_item_path_template,
                        method="DELETE",
                    )
                )
                container_already_gone = True
            else:
                findings.append(
                    _finding(
                        "spec_description_mismatch",
                        "warning",
                        kb_filename,
                        kb_group,
                        f"spec 對 DELETE /knowledges/{{knowledgeId}} 的描述寫「⚠️ 底下的文件會一起消失，而且無法復原」，"
                        f"但實測在文件仍存在時呼叫，回應是 {resp.status_code}（非 200），代表知識庫必須先清空文件才能刪除——"
                        "文件描述的行為與實際行為不一致，建議修正文件說明或修正實作其中一邊",
                        path=knowledge_item_path_template,
                        method="DELETE",
                    )
                )
        except requests.RequestException as exc:
            findings.append(
                _finding("live_write_error", "warning", kb_filename, kb_group, f"實測「文件還在時刪知識庫」這一步失敗：{exc}", path=knowledge_item_path_template, method="DELETE")
            )

    # Step 5：刪除文件，輪詢直到真的消失（GET 404）或卡在 deleting_failed
    if document_item_path and not container_already_gone:
        try:
            resp = session.delete(f"{base}{document_item_path}", timeout=timeout)
            delete_ok = _check_status_and_schema(kb_op(document_item_path_template, "delete"), resp, findings, kb_filename, kb_group, document_item_path_template, "delete")
        except requests.RequestException as exc:
            findings.append(
                _finding("live_write_cleanup_failed", "error", kb_filename, kb_group, f"DELETE 文件失敗：{exc}，documentId={document_id}", path=document_item_path_template, method="DELETE")
            )
            delete_ok = False

        if delete_ok:
            for _ in range(KNOWLEDGE_DOC_DELETE_POLL_MAX_ATTEMPTS):
                try:
                    resp = session.get(f"{base}{document_item_path}", timeout=timeout)
                except requests.RequestException as exc:
                    findings.append(_finding("live_write_error", "warning", kb_filename, kb_group, f"GET 輪詢刪除結果失敗：{exc}", path=document_item_path_template, method="GET"))
                    break
                if resp.status_code == 404:
                    document_deleted_confirmed = True
                    break
                try:
                    doc = resp.json().get("document") or {}
                except ValueError:
                    doc = {}
                if doc.get("displayState") == "deleting_failed":
                    findings.append(
                        _finding(
                            "live_write_cleanup_failed",
                            "error",
                            kb_filename,
                            kb_group,
                            f"文件刪除失敗，displayState=deleting_failed，documentId={document_id} 需要手動清理",
                            path=document_item_path_template,
                            method="DELETE",
                        )
                    )
                    break
                time.sleep(KNOWLEDGE_DOC_DELETE_POLL_INTERVAL_SECONDS)
            else:
                findings.append(
                    _finding(
                        "live_write_cleanup_failed",
                        "error",
                        kb_filename,
                        kb_group,
                        f"輪詢 {KNOWLEDGE_DOC_DELETE_POLL_MAX_ATTEMPTS} 次後文件仍未刪除完成，documentId={document_id} 需要手動確認",
                        path=document_item_path_template,
                        method="DELETE",
                    )
                )

    # Step 6：容器如果在 Step 4.5 就已經刪掉了（spec 描述的 cascade delete 屬實），就不用再刪一次；
    # 否則只有在文件確認清除乾淨後，才嘗試刪除知識庫容器——文件沒刪乾淨就不再嘗試刪容器，直接留給人工處理
    if container_already_gone:
        pass
    elif document_item_path and not document_deleted_confirmed:
        findings.append(
            _finding(
                "live_write_cleanup_failed",
                "error",
                kb_filename,
                kb_group,
                f"文件沒有確認清除乾淨，不嘗試刪除知識庫容器（{knowledge_id}），需要手動清理知識庫與其中的文件",
                path=knowledge_item_path_template,
                method="DELETE",
            )
        )
    else:
        try:
            resp = session.delete(f"{base}{knowledge_item_path}", timeout=timeout)
            if resp.status_code != 200:
                findings.append(
                    _finding(
                        "live_write_cleanup_failed",
                        "error",
                        kb_filename,
                        kb_group,
                        f"清理測試用知識庫（{knowledge_id}）失敗，DELETE 回 {resp.status_code}，需要手動清理",
                        path=knowledge_item_path_template,
                        method="DELETE",
                    )
                )
        except requests.RequestException as exc:
            findings.append(
                _finding(
                    "live_write_cleanup_failed", "error", kb_filename, kb_group, f"清理測試用知識庫（{knowledge_id}）失敗：{exc}，需要手動清理", path=knowledge_item_path_template, method="DELETE"
                )
            )

    if asset_key:
        findings.append(
            _finding(
                "live_write_permanent_residue",
                "info",
                asset_filename,
                asset_group,
                f"測試上傳的檔案（assetKey={asset_key}）會永久留在 storage，Asset V2 沒有提供任何刪除或查詢端點，這是已知且無法避免的殘留",
            )
        )

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(
            _finding("live_write_ok", "info", kb_filename, kb_group, f"文件的上傳 → 索引 → 驗證 → 刪除 → 確認清除全部驗證通過，測試容器（{knowledge_id}）已清除乾淨")
        )

    return findings


def run_fedflow_execute_test(entries, api_base_url, token, timeout=20, flow_id=None):
    """測試 FedFlow 的 POST execute -> GET result 輪詢。

    跟其他三組 CRUD 測試不同：execute 沒有 DELETE、執行了無法復原，所以不即興建立測試資料，
    而是固定打一個已人工確認過「無副作用」的 flow（見 config.FEDFLOW_TEST_FLOW_ID 的說明）。
    這個 flow 目前只存在於 stg2，對其他環境跑會收到 404，此時視為「這個環境沒有可用的測試
    flow」而跳過，回報 info 等級的 live_write_skipped，不當成錯誤。
    """
    from .config import FEDFLOW_TEST_FLOW_ID

    findings = []
    filename, group = "fedflow-v1.yaml", "FedFlow V1"
    flow_id = flow_id or FEDFLOW_TEST_FLOW_ID

    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}
    execute_path_template = "/public/fedflow/v1/flows/{flowId}/execute"
    result_path_template = "/public/fedflow/v1/executions/{executionId}/result"

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")
    execute_path = execute_path_template.replace("{flowId}", flow_id)

    # Step 1：POST 觸發執行
    try:
        resp = session.post(f"{base}{execute_path}", json={"input": FEDFLOW_EXECUTE_INPUT}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 觸發 flow 執行失敗：{exc}", path=execute_path_template, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(
            _finding("live_write_skipped", "info", filename, group, f"測試帳號沒有這個 flow 的執行權限（{resp.status_code}），跳過這組測試", path=execute_path_template, method="POST")
        )
        return findings

    if resp.status_code == 404:
        findings.append(
            _finding(
                "live_write_skipped",
                "info",
                filename,
                group,
                f"flow_id={flow_id} 在這個環境找不到（404），這個測試 flow 只在特定環境（stg2）建立，跳過這組測試",
                path=execute_path_template,
                method="POST",
            )
        )
        return findings

    ok = _check_status_and_schema(op_of(execute_path_template, "post"), resp, findings, filename, group, execute_path_template, "post")
    if not ok or resp.status_code != 200:
        findings.append(
            _finding("live_write_aborted", "error", filename, group, f"POST 觸發執行沒有回 200（實際 {resp.status_code}），中止後續步驟", path=execute_path_template, method="POST")
        )
        return findings

    try:
        execution_id = resp.json()["executionId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 executionId，中止後續步驟：{exc}", path=execute_path_template, method="POST"))
        return findings

    real_result_path = result_path_template.replace("{executionId}", execution_id)

    # Step 2：輪詢結果直到終態
    state = None
    body = None
    for _ in range(FEDFLOW_POLL_MAX_ATTEMPTS):
        try:
            resp = session.get(f"{base}{real_result_path}", timeout=timeout)
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"GET 輪詢執行結果失敗：{exc}", path=result_path_template, method="GET"))
            return findings

        _check_status_and_schema(op_of(result_path_template, "get"), resp, findings, filename, group, result_path_template, "get")
        try:
            body = resp.json()
        except ValueError:
            findings.append(_finding("live_write_error", "error", filename, group, "GET 輪詢執行結果回應不是合法 JSON，中止輪詢", path=result_path_template, method="GET"))
            return findings

        state = body.get("state")
        if state in FEDFLOW_TERMINAL_STATES:
            break
        time.sleep(FEDFLOW_POLL_INTERVAL_SECONDS)
    else:
        findings.append(
            _finding(
                "live_write_error",
                "warning",
                filename,
                group,
                f"輪詢 {FEDFLOW_POLL_MAX_ATTEMPTS} 次（每次間隔 {FEDFLOW_POLL_INTERVAL_SECONDS}s）後仍未進入終態（最後一次 state={state!r}），executionId={execution_id}",
                path=result_path_template,
                method="GET",
            )
        )
        return findings

    if state != "FINISHED":
        findings.append(
            _finding(
                "live_write_error",
                "warning",
                filename,
                group,
                f"flow 執行結束但 state={state!r}（非 FINISHED），executionId={execution_id}，回應內容：{body}",
                path=result_path_template,
                method="GET",
            )
        )
        return findings

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(
            _finding(
                "live_write_ok",
                "info",
                filename,
                group,
                f"POST execute → GET result 輪詢驗證通過，flow 執行成功結束（executionId={execution_id}）",
            )
        )

    return findings


def run_helix_voice_crud_test(entries, api_base_url, token, timeout=60):
    """測試 Helix V1 的 `/voices`（自己管理的獨立聲紋）。

    刻意不測 `/enrollment`——那是「一個帳號只能有一組」的資源，如果測試帳號已經有真實註冊，
    這個 Agent 不應該去動它（也沒有安全的方式先確認「動了會不會影響到人」）。`/voices` 則是
    可以無限建立、也有完整 GET/DELETE 的獨立資源，風險模式跟 Chat/FAQ/Knowledge 一致。

    需要 config.HELIX_TEST_AUDIO_PATH 指到一個本機真的音檔（各機器路徑不同，不寫死進 repo，
    透過環境變數指定）；沒有設定或檔案不存在就跳過測試，不是失敗。

    ⚠️ 這裡也順便驗證一個文件模糊之處：`VoiceCreateInput.audioUris` 要的是「FedGPT 連得到
    的網址」，spec 建議的取得方式是「走 Asset V2 上傳、把回傳的網址填進來」，但 Asset V2
    實際回傳的是 `assetKey`，不是網址——兩者怎麼轉換文件沒講清楚。這裡會直接把 `assetKey`
    當成 `audioUris` 送出去，不論成功或失敗都記錄成一筆 finding，把真實行為攤開來看。
    """
    from .config import HELIX_TEST_AUDIO_PATH

    findings = []
    filename, group = "helix-v1.yaml", "Helix V1"
    asset_filename, asset_group = "asset-v2.yaml", "Asset V2"

    if not HELIX_TEST_AUDIO_PATH or not os.path.isfile(HELIX_TEST_AUDIO_PATH):
        findings.append(_finding("live_write_skipped", "info", filename, group, "沒有設定 HELIX_TEST_AUDIO_PATH 或檔案不存在，跳過 /voices 寫入測試"))
        return findings

    kb_entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    asset_entry = next((e for e in entries if e.get("filename") == asset_filename and e.get("spec")), None)
    if not kb_entry or not asset_entry:
        missing = filename if not kb_entry else asset_filename
        findings.append(_finding("live_write_skipped", "info", missing, group, f"找不到 {missing} 的 spec，跳過寫入測試"))
        return findings

    spec = kb_entry["spec"]
    paths = spec.get("paths") or {}
    asset_spec = asset_entry["spec"]
    asset_paths = asset_spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    def asset_op(path, method):
        op = asset_paths[path][method]
        op["__spec__"] = asset_spec
        return op

    voices_path = "/public/helix/v1/voices"
    voice_item_path_template = "/public/helix/v1/voices/{voiceId}"
    presign_path = "/public/asset/v2/assets:presign"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")
    origin = base[: -len("/api")] if base.endswith("/api") else base

    test_filename = "[agent-test]-" + os.path.basename(HELIX_TEST_AUDIO_PATH)
    content_type = mimetypes.guess_type(HELIX_TEST_AUDIO_PATH)[0] or "audio/aac"
    with open(HELIX_TEST_AUDIO_PATH, "rb") as fh:
        audio_bytes = fh.read()

    # Step 1：Asset V2 presign，拿上傳憑證
    asset_key = None
    form_data = {}
    try:
        resp = session.post(f"{base}{presign_path}", json={"contentType": content_type, "filename": test_filename}, timeout=timeout)
        ok = _check_status_and_schema(asset_op(presign_path, "post"), resp, findings, asset_filename, asset_group, presign_path, "post")
        if ok and resp.status_code == 200:
            body = resp.json()
            asset_key = body.get("assetKey")
            form_data = body.get("formData") or {}
        else:
            findings.append(
                _finding("live_write_aborted", "error", asset_filename, asset_group, f"POST presign 沒有回 200（實際 {resp.status_code}），中止文件寫入測試", path=presign_path, method="POST")
            )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", asset_filename, asset_group, f"POST presign 失敗：{exc}", path=presign_path, method="POST"))

    # Step 2：實際把音檔上傳到 S3 相容 storage
    if asset_key:
        try:
            upload_resp = requests.post(f"{origin}/asset", data=form_data, files={"file": (test_filename, audio_bytes, content_type)}, timeout=timeout)
            if not upload_resp.ok:
                findings.append(
                    _finding(
                        "live_write_aborted",
                        "error",
                        asset_filename,
                        asset_group,
                        f"實際上傳音檔到 storage 失敗（{upload_resp.status_code}）：{upload_resp.text[:300]}，中止測試",
                        path="/asset",
                        method="POST",
                    )
                )
                asset_key = None
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", asset_filename, asset_group, f"實際上傳音檔到 storage 失敗：{exc}", path="/asset", method="POST"))
            asset_key = None

    if not asset_key:
        return findings

    findings.append(
        _finding(
            "live_write_permanent_residue",
            "info",
            asset_filename,
            asset_group,
            f"測試上傳的音檔（assetKey={asset_key}）會永久留在 storage，Asset V2 沒有提供任何刪除或查詢端點，這是已知且無法避免的殘留",
        )
    )

    # Step 3：實測「把 assetKey 直接當 audioUris 送出去」到底成不成立
    try:
        resp = session.post(f"{base}{voices_path}", json={"audioUris": [asset_key]}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立聲紋失敗：{exc}", path=voices_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有建立聲紋的權限（{resp.status_code}），跳過這組測試", path=voices_path, method="POST"))
        return findings

    _check_status_and_schema(op_of(voices_path, "post"), resp, findings, filename, group, voices_path, "post")

    if resp.status_code != 200:
        findings.append(
            _finding(
                "spec_description_mismatch",
                "warning",
                filename,
                group,
                f"spec 建議『Asset V2 上傳後把網址填進來』作為 audioUris，但直接把 assetKey（{asset_key}）當成 audioUris 送出，實際回應是 {resp.status_code}——"
                "代表這兩支 API 之間怎麼銜接，文件沒有講清楚：assetKey 不能（或至少不是能直接這樣）當 audioUris 用",
                path=voices_path,
                method="POST",
            )
        )
        return findings

    try:
        voice_id = resp.json()["voiceId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 voiceId：{exc}", path=voices_path, method="POST"))
        return findings

    findings.append(
        _finding(
            "live_write_ok",
            "info",
            filename,
            group,
            f"意外發現：把 assetKey 直接當 audioUris 送出去，實際成功建立了聲紋（voiceId={voice_id}）——這條路其實是通的，只是文件沒有明講",
            path=voices_path,
            method="POST",
        )
    )

    voice_item_path = voice_item_path_template.replace("{voiceId}", voice_id)

    # Step 4：GET 驗證
    try:
        resp = session.get(f"{base}{voice_item_path}", timeout=timeout)
        _check_status_and_schema(op_of(voice_item_path_template, "get"), resp, findings, filename, group, voice_item_path_template, "get")
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"GET 驗證聲紋失敗：{exc}", path=voice_item_path_template, method="GET"))

    # Step 5：DELETE 清除
    delete_ok = False
    try:
        resp = session.delete(f"{base}{voice_item_path}", timeout=timeout)
        delete_ok = _check_status_and_schema(op_of(voice_item_path_template, "delete"), resp, findings, filename, group, voice_item_path_template, "delete")
    except requests.RequestException as exc:
        findings.append(
            _finding("live_write_cleanup_failed", "error", filename, group, f"DELETE 聲紋失敗：{exc}，voiceId={voice_id} 需要手動清理", path=voice_item_path_template, method="DELETE")
        )

    # Step 6：GET 驗證真的刪了
    if delete_ok:
        try:
            resp = session.get(f"{base}{voice_item_path}", timeout=timeout)
            if resp.status_code != 404:
                findings.append(
                    _finding(
                        "live_write_cleanup_failed",
                        "error",
                        filename,
                        group,
                        f"DELETE 聲紋回 200，但 GET 還是拿到 {resp.status_code}（預期 404），voiceId={voice_id} 可能沒有真的刪除",
                        path=voice_item_path_template,
                        method="DELETE",
                    )
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證聲紋刪除結果失敗：{exc}", path=voice_item_path_template, method="GET"))

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"聲紋的 POST → GET → DELETE → GET 全部驗證通過，測試資料（{voice_id}）已清除乾淨"))

    return findings


def run_auth_apikey_crud_test(entries, api_base_url, token, timeout=30):
    """測試 Auth V2 的 API key（`/apikeys`、`/apikeys:batchDelete`）。

    這支沒有 PUT/更新端點，所以流程是「建立 → GET 清單驗證存在 → 批次刪除 → GET 清單驗證消失」。

    ⚠️ 這把 key 建立後到刪除前的短暫時間內，是一把真的能用來呼叫 API 的有效憑證——這裡不會
    拿它去打任何其他 API，只用來驗證這組端點本身的行為，測完立刻刪除。效期刻意設得很短
    （建立時間 + 1 小時），把暴露窗口縮到最小。

    帳號每人最多 25 把 key，如果建立時剛好卡在配額上限（`400 apikey.quota-exceeded`），視為
    「無法測試」回報 info 等級的 live_write_skipped，不是硬規則意義上的 bug。
    """
    findings = []
    filename, group = "auth-v2.yaml", "Auth V2"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    apikeys_path = "/public/auth/v2/apikeys"
    batch_delete_path = "/public/auth/v2/apikeys:batchDelete"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    # Step 1：建立一把效期很短的 key
    expired_at = int(time.time()) + 3600
    try:
        resp = session.post(f"{base}{apikeys_path}", json={"number": 1, "expiredAt": expired_at}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立 API key 失敗：{exc}", path=apikeys_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有建立 API key 的權限（{resp.status_code}），跳過這組測試", path=apikeys_path, method="POST"))
        return findings

    if resp.status_code == 400:
        try:
            reason = resp.json().get("reason", "")
        except ValueError:
            reason = ""
        if reason == "apikey.quota-exceeded":
            findings.append(_finding("live_write_skipped", "info", filename, group, "測試帳號的 API key 數量已達上限（25 把），跳過這組測試", path=apikeys_path, method="POST"))
            return findings

    ok = _check_status_and_schema(op_of(apikeys_path, "post"), resp, findings, filename, group, apikeys_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 建立 API key 沒有回 200（實際 {resp.status_code}），中止後續步驟", path=apikeys_path, method="POST"))
        return findings

    try:
        created_key = resp.json()["keys"][0]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 key，中止後續步驟：{exc}", path=apikeys_path, method="POST"))
        return findings

    # Step 2：GET 清單，驗證剛建立的 key 存在且 expiredAt 一致
    try:
        resp = session.get(f"{base}{apikeys_path}", params={"limit": 100}, timeout=timeout)
        _check_status_and_schema(op_of(apikeys_path, "get"), resp, findings, filename, group, apikeys_path, "get")
        items = resp.json().get("items") or []
        matched = next((it for it in items if it.get("key") == created_key), None)
        if not matched:
            findings.append(
                _finding("live_write_data_mismatch", "error", filename, group, f"GET 清單裡找不到剛建立的 key（{created_key}）", path=apikeys_path, method="GET")
            )
        elif matched.get("expiredAt") != expired_at:
            findings.append(
                _finding(
                    "live_write_data_mismatch",
                    "error",
                    filename,
                    group,
                    f"清單裡這把 key 的 expiredAt 是 {matched.get('expiredAt')}，跟建立時送出的 {expired_at} 不一致",
                    path=apikeys_path,
                    method="GET",
                )
            )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"GET 驗證建立內容失敗：{exc}", path=apikeys_path, method="GET"))

    # Step 3：批次刪除
    try:
        resp = session.delete(f"{base}{batch_delete_path}", params={"keys": [created_key]}, timeout=timeout)
        delete_ok = _check_status_and_schema(op_of(batch_delete_path, "delete"), resp, findings, filename, group, batch_delete_path, "delete")
        delete_ok = delete_ok and resp.status_code == 200
    except requests.RequestException as exc:
        findings.append(
            _finding("live_write_cleanup_failed", "error", filename, group, f"批次刪除 API key 失敗：{exc}，key={created_key} 需要手動清理", path=batch_delete_path, method="DELETE")
        )
        delete_ok = False

    # Step 4：GET 清單，驗證真的刪了
    if delete_ok:
        try:
            resp = session.get(f"{base}{apikeys_path}", params={"limit": 100}, timeout=timeout)
            items = resp.json().get("items") or []
            if any(it.get("key") == created_key for it in items):
                findings.append(
                    _finding(
                        "live_write_cleanup_failed",
                        "error",
                        filename,
                        group,
                        f"批次刪除回 200，但 GET 清單裡還是找得到這把 key（{created_key}），可能沒有真的刪除",
                        path=apikeys_path,
                        method="GET",
                    )
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證刪除結果失敗：{exc}", path=apikeys_path, method="GET"))

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, "API key 的 POST → GET → 批次 DELETE → GET 全部驗證通過，測試用的 key 已清除乾淨"))

    return findings


def run_llm_embeddings_test(entries, api_base_url, token, timeout=30):
    """測試 LLM V1 的 embeddings（`POST /llm/v1/retriever/v1/embeddings`）。

    這支是無狀態的呼叫——沒有持久化資源，呼叫完就結束，不需要任何清理。
    """
    findings = []
    filename, group = "llm-v1.yaml", "LLM V1"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    embeddings_path = "/public/llm/v1/retriever/v1/embeddings"
    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    test_input = "[agent-test] live-call embeddings 驗證"
    try:
        resp = session.post(f"{base}{embeddings_path}", json={"model": "embedding-v3.0", "input": [test_input]}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST embeddings 失敗：{exc}", path=embeddings_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有呼叫 embeddings 的權限（{resp.status_code}），跳過這組測試", path=embeddings_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(embeddings_path, "post"), resp, findings, filename, group, embeddings_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST embeddings 沒有回 200（實際 {resp.status_code}），中止測試", path=embeddings_path, method="POST"))
        return findings

    try:
        body = resp.json()
        data = body["data"]
        if len(data) != 1:
            findings.append(_finding("live_write_data_mismatch", "error", filename, group, f"送出 1 筆 input，但 data 回來 {len(data)} 筆", path=embeddings_path, method="POST"))
        elif data[0].get("index") != 0 or not data[0].get("embedding"):
            findings.append(
                _finding("live_write_data_mismatch", "error", filename, group, f"回應的 data[0] 內容不合預期：index={data[0].get('index')}，embedding 長度={len(data[0].get('embedding') or [])}", path=embeddings_path, method="POST")
            )
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"解析 embeddings 回應失敗：{exc}", path=embeddings_path, method="POST"))
        return findings

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"embeddings 呼叫成功，向量維度 {len(data[0]['embedding'])}，index 對應正確"))

    return findings


def run_asura_tts_test(entries, api_base_url, token, timeout=60):
    """測試 Asura V1 的文字轉語音（`POST /asura/v1/speeches:stream`，固定音色版本）。

    無狀態呼叫，回應是音訊二進位資料不是 JSON，不需要清理。這支端點不是每個部署都有，
    沒開的部署會拿到 404，視為「這個環境沒有這個功能」而跳過，不是失敗。

    這裡也順便主動驗證一個 spec 描述：`SpeechInput.audioConfig` 在 spec 裡宣告是選填
    （`required` 只列了 `modelConfig`），先故意不帶 `audioConfig` 打一次，把實際回應記成
    一筆 finding（一致或不一致都記），再補上 `audioConfig` 重打一次驗證真正的合成流程。
    """
    findings = []
    filename, group = "asura-v1.yaml", "Asura V1"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    tts_path = "/public/asura/v1/speeches:stream"
    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    base_body = {
        "input": {"text": "[agent-test] live-call 文字轉語音驗證", "type": "text"},
        # spec 範例值 tts-general-0.0.1 在 stg2 實測不存在（inferno.get-model.failed）；
        # 實際問過才確認 tts-general-1.3.3 才是這個環境真正可用的 TTS 模型版本。
        "modelConfig": {"model": "tts-general-1.3.3", "voice": "yating"},
    }

    # 這個發現是固定記錄，不是靠現場觸發錯誤才報：spec 對 modelConfig.model 給的範例值
    # `tts-general-0.0.1` 實測在 stg2 並不存在（打下去後端回 inferno.get-model.failed，
    # 內部查模型收到 404），而且 spec 完全沒有提供任何端點可以查詢這個部署實際支援的 TTS
    # 模型名稱——不像 Chat V2 有 GET /models 可查。確認可用的版本（`tts-general-1.3.3`）
    # 是額外詢問熟悉部署的人才拿到的，不是文件或 API 能自己查出來的。
    findings.append(
        _finding(
            "spec_description_mismatch",
            "warning",
            filename,
            group,
            "SpeechModelConfig.model 的 spec 範例值 'tts-general-0.0.1' 在 stg2 實測不存在（後端查模型收到 404，reason=inferno.get-model.failed），"
            "且 spec 沒有提供任何端點可查詢這個部署實際支援的 TTS 模型名稱清單（不像 Chat V2 有 GET /models）——正確版本（tts-general-1.3.3）只能問熟悉部署的人才拿得到",
            path=tts_path,
            method="POST",
        )
    )

    # Step 0：故意不帶 audioConfig，實測 spec 說它選填是否屬實
    try:
        probe_resp = session.post(f"{base}{tts_path}", json=base_body, timeout=timeout)
        if probe_resp.status_code == 200:
            findings.append(
                _finding("live_write_ok", "info", filename, group, "不帶 audioConfig 也能成功合成，跟 spec 宣告的選填一致", path=tts_path, method="POST")
            )
        elif probe_resp.status_code not in (404,):
            try:
                probe_msg = probe_resp.json().get("message", "")
            except ValueError:
                probe_msg = ""
            findings.append(
                _finding(
                    "spec_description_mismatch",
                    "warning",
                    filename,
                    group,
                    f"spec 宣告 SpeechInput.audioConfig 是選填（required 只列 modelConfig），但不帶它實際回應是 {probe_resp.status_code}：{probe_msg}——代表伺服器端其實把它當必填",
                    path=tts_path,
                    method="POST",
                )
            )
    except requests.RequestException:
        pass  # 這只是探測性質，失敗不影響下面正式測試

    # 正式測試：帶上 audioConfig，驗證真正的合成流程
    body = dict(base_body, audioConfig={"encoding": "LINEAR16"})
    try:
        resp = session.post(f"{base}{tts_path}", json=body, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 文字轉語音失敗：{exc}", path=tts_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有呼叫文字轉語音的權限（{resp.status_code}），跳過這組測試", path=tts_path, method="POST"))
        return findings

    if resp.status_code == 404:
        findings.append(_finding("live_write_skipped", "info", filename, group, "這個部署沒有啟用文字轉語音（404），跳過這組測試", path=tts_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(tts_path, "post"), resp, findings, filename, group, tts_path, "post")
    if not ok or resp.status_code != 200:
        try:
            resp_reason = resp.json().get("reason", "")
        except ValueError:
            resp_reason = ""
        if resp_reason == "inferno.get-model.failed":
            findings.append(
                _finding(
                    "spec_description_mismatch",
                    "warning",
                    filename,
                    group,
                    f"spec 的 modelConfig.model 範例值 {body['modelConfig']['model']!r} 在這個環境實際上不存在（後端查模型收到 404），"
                    "而且 spec 沒有提供任何端點可以查詢這個部署實際支援的 TTS 模型名稱——不像 Chat V2 有 GET /models，光靠文件組不出保證能用的請求",
                    path=tts_path,
                    method="POST",
                )
            )
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 文字轉語音沒有回 200（實際 {resp.status_code}），中止測試", path=tts_path, method="POST"))
        return findings

    content_type = resp.headers.get("Content-Type", "")
    if "audio" not in content_type:
        findings.append(_finding("live_write_data_mismatch", "error", filename, group, f"回應 Content-Type 是 {content_type!r}，預期是 audio/*", path=tts_path, method="POST"))
    elif len(resp.content) == 0:
        findings.append(_finding("live_write_data_mismatch", "error", filename, group, "回應的音訊內容是空的", path=tts_path, method="POST"))

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"文字轉語音呼叫成功，收到 {len(resp.content)} bytes 的 {content_type} 音訊"))

    return findings


def run_chat_send_message_test(entries, api_base_url, token, timeout=90):
    """測試 Chat V2 送訊息（`POST /chat/v2/chat/normal`）。

    這是真的呼叫一次 LLM，會有實際的 API 用量／成本，也會等模型把整段回答產生完才回應
    （可能要等一段時間）。為了不影響既有的 `run_conversation_crud_test`，這裡自己建立、
    自己刪除一個獨立的臨時對話，不共用資料。
    """
    findings = []
    filename, group = "chat-v2.yaml", "Chat V2"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    conversations_path = "/public/chat/v2/conversations"
    conversation_item_path_template = "/public/chat/v2/conversations/{convId}"
    chat_normal_path = "/public/chat/v2/chat/normal"
    models_path = "/public/chat/v2/models"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    try:
        resp = session.get(f"{base}{models_path}", timeout=timeout)
        resp.raise_for_status()
        models = resp.json().get("models") or []
        if not models:
            findings.append(_finding("live_write_error", "error", filename, group, "GET /models 沒有回傳任何模型，無法建立測試對話", path=models_path, method="GET"))
            return findings
        model_id = models[0]["id"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_error", "error", filename, group, f"取得模型清單失敗：{exc}", path=models_path, method="GET"))
        return findings

    # Step 1：建立臨時對話
    try:
        resp = session.post(f"{base}{conversations_path}", json={"conversation": {"title": TEST_TITLE, "mode": "normal", "model": model_id}}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立測試對話失敗：{exc}", path=conversations_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有建立對話的權限（{resp.status_code}），跳過這組測試", path=conversations_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(conversations_path, "post"), resp, findings, filename, group, conversations_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 建立測試對話沒有回 200（實際 {resp.status_code}），中止測試", path=conversations_path, method="POST"))
        return findings

    try:
        conv_id = resp.json()["conversation"]["convId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 convId，中止測試：{exc}", path=conversations_path, method="POST"))
        return findings

    conversation_item_path = conversation_item_path_template.replace("{convId}", conv_id)

    # Step 2：送一則訊息，真的呼叫一次 LLM
    try:
        resp = session.post(
            f"{base}{chat_normal_path}",
            json={"convId": conv_id, "message": {"text": "[agent-test] live-call chat 送訊息驗證，請用一句話簡短回覆"}},
            timeout=timeout,
        )
        ok = _check_status_and_schema(op_of(chat_normal_path, "post"), resp, findings, filename, group, chat_normal_path, "post")
        if ok and resp.status_code == 200:
            body = resp.json()
            if body.get("convId") != conv_id:
                findings.append(
                    _finding("live_write_data_mismatch", "error", filename, group, f"回應的 convId（{body.get('convId')}）跟送出的 convId（{conv_id}）不一致", path=chat_normal_path, method="POST")
                )
            elif not body.get("messages"):
                findings.append(_finding("live_write_data_mismatch", "error", filename, group, "回應的 messages 是空的，預期至少有一則模型的回覆", path=chat_normal_path, method="POST"))
        else:
            findings.append(
                _finding("live_write_aborted", "error", filename, group, f"POST 送訊息沒有回 200（實際 {resp.status_code}），中止測試（仍會嘗試清理臨時對話）", path=chat_normal_path, method="POST")
            )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 送訊息失敗：{exc}", path=chat_normal_path, method="POST"))

    # Step 3：清掉臨時對話（訊息會隨對話一起清掉，不用另外處理）
    try:
        resp = session.delete(f"{base}{conversation_item_path}", timeout=timeout)
        delete_ok = _check_status_and_schema(op_of(conversation_item_path_template, "delete"), resp, findings, filename, group, conversation_item_path_template, "delete")
    except requests.RequestException as exc:
        findings.append(
            _finding("live_write_cleanup_failed", "error", filename, group, f"DELETE 測試對話失敗：{exc}，convId={conv_id} 需要手動清理", path=conversation_item_path_template, method="DELETE")
        )
        delete_ok = False

    if delete_ok:
        try:
            resp = session.get(f"{base}{conversation_item_path}", timeout=timeout)
            if resp.status_code != 404:
                findings.append(
                    _finding(
                        "live_write_cleanup_failed",
                        "error",
                        filename,
                        group,
                        f"DELETE 測試對話回 200，但 GET 還是拿到 {resp.status_code}（預期 404），convId={conv_id} 可能沒有真的刪除",
                        path=conversation_item_path_template,
                        method="DELETE",
                    )
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證刪除結果失敗：{exc}", path=conversation_item_path_template, method="GET"))

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"送訊息 → 驗證回應 → 刪除臨時對話全部驗證通過（convId={conv_id}）"))

    return findings


def _run_chat_mode_test(entries, api_base_url, token, timeout, *, mode, chat_path, mode_label, prepare_params_fn):
    """建立一個指定 mode 的臨時對話 → 送訊息驗證 ChatResponse → 刪除對話。

    是 run_chat_send_message_test（mode: normal）的通用版本，`knowledge`/`agentic-rag`/
    `faq`/`tabular` 這幾個需要 `params` 的 mode 都共用這個流程。

    prepare_params_fn(session, base, timeout, findings) 負責準備這個 mode 需要的 params
    （建立臨時資源，或查詢帳號名下既有的資源），回傳 (params_list, cleanup_fn)：
    - 準備失敗或判定要跳過時回傳 (None, None)，此時 findings 已經寫好原因，呼叫端直接結束。
    - cleanup_fn() 在測試結束時一定會被呼叫一次，用來清掉 prepare 建立的臨時資源；如果
      prepare 只是查詢既有資源、沒有建立任何東西（例如 tabular），cleanup_fn 可以是 no-op。
    """
    findings = []
    filename, group = "chat-v2.yaml", "Chat V2"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    conversations_path = "/public/chat/v2/conversations"
    conversation_item_path_template = "/public/chat/v2/conversations/{convId}"
    models_path = "/public/chat/v2/models"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    try:
        resp = session.get(f"{base}{models_path}", timeout=timeout)
        resp.raise_for_status()
        models = resp.json().get("models") or []
        if not models:
            findings.append(_finding("live_write_error", "error", filename, group, "GET /models 沒有回傳任何模型，無法建立測試對話", path=models_path, method="GET"))
            return findings
        model_id = models[0]["id"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_error", "error", filename, group, f"取得模型清單失敗：{exc}", path=models_path, method="GET"))
        return findings

    params, cleanup_fn = prepare_params_fn(session, base, timeout, findings)
    if params is None:
        return findings  # prepare_params_fn 已經把跳過或失敗的原因寫進 findings

    conv_id = None
    try:
        # Step 1：建立臨時對話
        try:
            resp = session.post(
                f"{base}{conversations_path}",
                json={"conversation": {"title": TEST_TITLE, "mode": mode, "model": model_id, "params": params}},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立測試對話失敗：{exc}", path=conversations_path, method="POST"))
            return findings

        if _is_permission_denied(resp):
            findings.append(
                _finding("live_write_skipped", "info", filename, group, f"測試帳號沒有這個 mode（{mode_label}）所需資源的 chat 權限（{resp.status_code}），跳過這組測試", path=conversations_path, method="POST")
            )
            return findings

        ok = _check_status_and_schema(op_of(conversations_path, "post"), resp, findings, filename, group, conversations_path, "post")
        if not ok or resp.status_code != 200:
            findings.append(
                _finding("live_write_aborted", "error", filename, group, f"POST 建立 {mode_label} 對話沒有回 200（實際 {resp.status_code}），中止測試", path=conversations_path, method="POST")
            )
            return findings

        try:
            conv_id = resp.json()["conversation"]["convId"]
        except Exception as exc:  # noqa: BLE001
            findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 convId，中止測試：{exc}", path=conversations_path, method="POST"))
            return findings

        conversation_item_path = conversation_item_path_template.replace("{convId}", conv_id)

        # Step 2：送一則訊息，真的呼叫一次 LLM
        try:
            resp = session.post(
                f"{base}{chat_path}",
                json={"convId": conv_id, "message": {"text": f"[agent-test] live-call chat {mode_label} 驗證，請用一句話簡短回覆"}},
                timeout=timeout,
            )
            ok = _check_status_and_schema(op_of(chat_path, "post"), resp, findings, filename, group, chat_path, "post")
            if ok and resp.status_code == 200:
                body = resp.json()
                if body.get("convId") != conv_id:
                    findings.append(
                        _finding("live_write_data_mismatch", "error", filename, group, f"回應的 convId（{body.get('convId')}）跟送出的 convId（{conv_id}）不一致", path=chat_path, method="POST")
                    )
                elif not body.get("messages"):
                    findings.append(_finding("live_write_data_mismatch", "error", filename, group, "回應的 messages 是空的，預期至少有一則模型的回覆", path=chat_path, method="POST"))
            else:
                findings.append(
                    _finding("live_write_aborted", "error", filename, group, f"POST 送訊息（{mode_label}）沒有回 200（實際 {resp.status_code}），中止測試（仍會清理臨時資源）", path=chat_path, method="POST")
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"POST 送訊息（{mode_label}）失敗：{exc}", path=chat_path, method="POST"))

        # Step 3：清掉臨時對話
        try:
            resp = session.delete(f"{base}{conversation_item_path}", timeout=timeout)
            delete_ok = _check_status_and_schema(op_of(conversation_item_path_template, "delete"), resp, findings, filename, group, conversation_item_path_template, "delete")
        except requests.RequestException as exc:
            findings.append(
                _finding("live_write_cleanup_failed", "error", filename, group, f"DELETE 測試對話失敗：{exc}，convId={conv_id} 需要手動清理", path=conversation_item_path_template, method="DELETE")
            )
            delete_ok = False

        if delete_ok:
            try:
                resp = session.get(f"{base}{conversation_item_path}", timeout=timeout)
                if resp.status_code != 404:
                    findings.append(
                        _finding(
                            "live_write_cleanup_failed",
                            "error",
                            filename,
                            group,
                            f"DELETE 測試對話回 200，但 GET 還是拿到 {resp.status_code}（預期 404），convId={conv_id} 可能沒有真的刪除",
                            path=conversation_item_path_template,
                            method="DELETE",
                        )
                    )
            except requests.RequestException as exc:
                findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證刪除結果失敗：{exc}", path=conversation_item_path_template, method="GET"))
    finally:
        # 不論前面步驟結果如何，都嘗試清掉 prepare_params_fn 建立的臨時資源
        cleanup_fn()

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"{mode_label} 模式的送訊息 → 驗證回應 → 刪除臨時對話全部驗證通過（convId={conv_id}）"))

    return findings


def _prepare_knowledge_params(session, base, timeout, findings):
    """為 chat/knowledge 與 chat/agenticRag 建立一個臨時知識庫，回傳 (params, cleanup_fn)。"""
    knowledges_path = "/public/knowledge/v3/knowledges"
    filename, group = "chat-v2.yaml", "Chat V2"

    # 名稱要每次呼叫都不同：這個 helper 會被 knowledge/agentic-rag 的一般版與串流版共 4 個測試各呼叫一次，
    # 如果用固定名稱，一旦前一次建立的知識庫卡在索引中沒被清乾淨（stg2 曾發生過），
    # 後面幾次用同名字建立就會撞到 409 conflict，連帶讓其他測試也跳過。
    knowledge_name = TEST_TITLE + f"（chat mode 測試用 {uuid.uuid4().hex[:8]}）"

    try:
        resp = session.post(
            f"{base}{knowledges_path}",
            json={"knowledge": {"name": knowledge_name, "description": "[agent-test] 用於驗證 chat knowledge/agentic-rag mode，測試完會刪除"}},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立測試用知識庫失敗：{exc}", path=knowledges_path, method="POST"))
        return None, None

    if resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 建立測試用知識庫沒有回 200（實際 {resp.status_code}），跳過這組測試", path=knowledges_path, method="POST"))
        return None, None

    try:
        knowledge_id = resp.json()["knowledge"]["knowledgeId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 knowledgeId：{exc}", path=knowledges_path, method="POST"))
        return None, None

    knowledge_item_path = f"{knowledges_path}/{knowledge_id}"
    documents_path = f"{knowledges_path}/{knowledge_id}/documents"
    document_item_path = None

    def cleanup():
        # 實測發現知識庫必須先清空文件才能刪除（見 run_knowledge_document_crud_test 的說明），
        # 所以這裡一定要先確認文件真的刪除乾淨，才嘗試刪除知識庫容器。
        document_deleted = document_item_path is None
        if document_item_path:
            try:
                resp = session.delete(f"{base}{document_item_path}", timeout=timeout)
                if resp.status_code == 200:
                    for _ in range(KNOWLEDGE_DOC_DELETE_POLL_MAX_ATTEMPTS):
                        resp = session.get(f"{base}{document_item_path}", timeout=timeout)
                        if resp.status_code == 404:
                            document_deleted = True
                            break
                        time.sleep(KNOWLEDGE_DOC_DELETE_POLL_INTERVAL_SECONDS)
                    if not document_deleted:
                        findings.append(
                            _finding("live_write_cleanup_failed", "error", filename, group, f"文件（{document_item_path}）刪除後一直沒有真的消失，需要手動確認", path=documents_path, method="DELETE")
                        )
                else:
                    findings.append(
                        _finding("live_write_cleanup_failed", "error", filename, group, f"DELETE 測試文件失敗（{resp.status_code}），需要手動清理", path=documents_path, method="DELETE")
                    )
            except requests.RequestException as exc:
                findings.append(_finding("live_write_cleanup_failed", "error", filename, group, f"DELETE 測試文件失敗：{exc}，需要手動清理", path=documents_path, method="DELETE"))

        if not document_deleted:
            findings.append(
                _finding(
                    "live_write_cleanup_failed",
                    "error",
                    filename,
                    group,
                    f"文件沒有確認清除乾淨，不嘗試刪除知識庫容器（{knowledge_id}），需要手動清理",
                    path=knowledge_item_path,
                    method="DELETE",
                )
            )
            return

        try:
            resp = session.delete(f"{base}{knowledge_item_path}", timeout=timeout)
            if resp.status_code != 200:
                findings.append(
                    _finding("live_write_cleanup_failed", "error", filename, group, f"清理測試用知識庫（{knowledge_id}）失敗，DELETE 回 {resp.status_code}，需要手動清理", path=knowledges_path, method="DELETE")
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_cleanup_failed", "error", filename, group, f"清理測試用知識庫（{knowledge_id}）失敗：{exc}，需要手動清理", path=knowledges_path, method="DELETE"))

    # chat/knowledge 實測發現：空的知識庫（沒有索引好的文件）沒辦法拿去聊天，會回
    # 404 data.not-found（spec 只提到 convId 不存在或引用資源被刪除這兩種成因，沒提到這種）。
    # 所以這裡要素真的上傳一份文件並等索引完成，才能讓 chat/knowledge 測到正常路徑。
    asset_key = _asset_v2_presign_and_upload(
        session, base, timeout, findings, filename="[agent-test]-chat-mode-doc.txt", content_type="text/plain", content=b"[agent-test] used to let chat/knowledge have indexed content to query.\n"
    )
    if not asset_key:
        # 上傳失敗：至少能測 agentic-rag（實測空知識庫也能用），knowledge 那邊會在送訊息時失敗，
        # 但這比完全不測好，且失敗原因會被下面的送訊息步驟如實記錄下來。
        return [knowledge_id], cleanup

    try:
        resp = session.post(f"{base}{documents_path}", json={"document": {"assetKey": asset_key}}, timeout=timeout)
        if resp.status_code != 200:
            findings.append(_finding("live_write_error", "warning", filename, group, f"POST 掛入測試文件沒有回 200（實際 {resp.status_code}），knowledge mode 可能因此沒有索引內容", path=documents_path, method="POST"))
            return [knowledge_id], cleanup
        document_id = resp.json()["document"]["documentId"]
        document_item_path = f"{documents_path}/{document_id}"
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_error", "warning", filename, group, f"POST 掛入測試文件失敗：{exc}，knowledge mode 可能因此沒有索引內容", path=documents_path, method="POST"))
        return [knowledge_id], cleanup

    # 輪詢索引狀態直到終態，讓 chat/knowledge 真的有內容可以查
    for _ in range(KNOWLEDGE_DOC_INDEX_POLL_MAX_ATTEMPTS):
        try:
            resp = session.get(f"{base}{document_item_path}", timeout=timeout)
            display_state = (resp.json().get("document") or {}).get("displayState")
        except Exception:  # noqa: BLE001
            display_state = None
        if display_state in KNOWLEDGE_DOC_INDEX_TERMINAL_STATES:
            if display_state != "completed":
                findings.append(
                    _finding("live_write_error", "warning", filename, group, f"測試文件索引結束但 displayState={display_state!r}（非 completed），knowledge mode 可能查不到內容", path=document_item_path, method="GET")
                )
            break
        time.sleep(KNOWLEDGE_DOC_INDEX_POLL_INTERVAL_SECONDS)
    else:
        findings.append(_finding("live_write_error", "warning", filename, group, "測試文件輪詢逾時仍未完成索引，knowledge mode 可能查不到內容", path=document_item_path, method="GET"))

    return [knowledge_id], cleanup


def _prepare_faq_params(session, base, timeout, findings):
    """為 chat/faq 建立一個臨時 FAQ 容器，回傳 (params, cleanup_fn)。"""
    faqs_path = "/public/faq/v1/faqs"
    filename, group = "chat-v2.yaml", "Chat V2"

    # 名稱加隨機後綴：這個 helper 被 faq 的一般版與串流版兩個測試各呼叫一次，固定名稱在
    # 前一次沒清乾淨時會撞 409（跟 _prepare_knowledge_params 是同一個根因）。
    faq_name = TEST_TITLE + f"（chat mode 測試用 {uuid.uuid4().hex[:8]}）"
    try:
        resp = session.post(f"{base}{faqs_path}", json={"faq": {"name": faq_name}}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立測試用 FAQ 失敗：{exc}", path=faqs_path, method="POST"))
        return None, None

    if resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 建立測試用 FAQ 沒有回 200（實際 {resp.status_code}），跳過這組測試", path=faqs_path, method="POST"))
        return None, None

    try:
        faq_id = resp.json()["faq"]["faqId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 faqId：{exc}", path=faqs_path, method="POST"))
        return None, None

    faq_item_path = f"{faqs_path}/{faq_id}"

    def cleanup():
        try:
            resp = session.delete(f"{base}{faq_item_path}", timeout=timeout)
            if resp.status_code != 200:
                findings.append(
                    _finding("live_write_cleanup_failed", "error", filename, group, f"清理測試用 FAQ（{faq_id}）失敗，DELETE 回 {resp.status_code}，需要手動清理", path=faqs_path, method="DELETE")
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_cleanup_failed", "error", filename, group, f"清理測試用 FAQ（{faq_id}）失敗：{exc}，需要手動清理", path=faqs_path, method="DELETE"))

    return [faq_id], cleanup


def _prepare_tabular_params(session, base, timeout, findings):
    """為 chat/tabular 查詢帳號名下既有的 tabular 資源，沒有就跳過（這支沒有建立端點）。"""
    tabulars_path = "/public/chat/v2/tabulars"
    filename, group = "chat-v2.yaml", "Chat V2"

    try:
        resp = session.get(f"{base}{tabulars_path}", timeout=timeout)
        resp.raise_for_status()
        tabulars = resp.json().get("tabulars") or []
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_error", "error", filename, group, f"GET tabulars 清單失敗：{exc}", path=tabulars_path, method="GET"))
        return None, None

    if not tabulars:
        findings.append(_finding("live_write_skipped", "info", filename, group, "帳號名下沒有任何 tabular 資源（且這支沒有建立端點），跳過 chat/tabular 測試", path=tabulars_path, method="GET"))
        return None, None

    # 固定記錄一筆 spec 缺口：Tabular schema 只有 tabularId/name/description，沒有任何欄位
    # 能判斷這個資源是否「ready for query」。實測發現部分既有 tabular 會在送訊息時回
    # 423 data.locked（「tabular not ready for query」），但 chat/tabular 的文件完全沒提到
    # 這個狀態碼，GET /tabulars 也沒給任何線索——只能實際送出聊天請求才會知道。
    findings.append(
        _finding(
            "spec_description_mismatch",
            "warning",
            filename,
            group,
            "GET /chat/v2/tabulars 回傳的 Tabular schema（tabularId/name/description）沒有任何欄位可以判斷資源是否已就緒可查詢；"
            "實測發現部分既有 tabular 會在 chat/tabular 回 423 data.locked（'tabular not ready for query'），但這個狀態碼完全沒有寫進 chat/tabular 的 spec 文件，也無法事先得知",
            path=tabulars_path,
            method="GET",
        )
    )

    tabular_id = tabulars[0]["tabularId"]
    return [tabular_id], lambda: None  # 不是我們建立的資源，不清理


def run_chat_knowledge_mode_test(entries, api_base_url, token, timeout=90):
    return _run_chat_mode_test(
        entries, api_base_url, token, timeout, mode="knowledge", chat_path="/public/chat/v2/chat/knowledge", mode_label="knowledge", prepare_params_fn=_prepare_knowledge_params
    )


def run_chat_agentic_rag_mode_test(entries, api_base_url, token, timeout=90):
    return _run_chat_mode_test(
        entries, api_base_url, token, timeout, mode="agentic-rag", chat_path="/public/chat/v2/chat/agenticRag", mode_label="agentic-rag", prepare_params_fn=_prepare_knowledge_params
    )


def run_chat_faq_mode_test(entries, api_base_url, token, timeout=90):
    return _run_chat_mode_test(entries, api_base_url, token, timeout, mode="faq", chat_path="/public/chat/v2/chat/faq", mode_label="faq", prepare_params_fn=_prepare_faq_params)


def run_chat_tabular_mode_test(entries, api_base_url, token, timeout=90):
    return _run_chat_mode_test(entries, api_base_url, token, timeout, mode="tabular", chat_path="/public/chat/v2/chat/tabular", mode_label="tabular", prepare_params_fn=_prepare_tabular_params)


def run_admin_apikey_crud_test(entries, api_base_url, token, timeout=30):
    """測試 Admin V1 代管他人 API key 的端點（GET/POST/apikeys:batchDelete）。

    這三支端點全部需要 `user.admin` 權限，一般測試帳號很可能沒有——沒有的話會在第一步的
    GET 就被 403 擋下，直接跳過，不會嘗試繞過或換帳號。

    為了不碰到任何其他真實使用者的資料，這裡固定只對「測試帳號自己的 userId」操作（從
    access token 本地解碼取得，不會送到任何地方）。即使這支端點允許建立永不過期的 key，
    這裡仍刻意帶一個短效期（建立時間 + 1 小時），降低風險。
    """
    findings = []
    filename, group = "admin-v1.yaml", "Admin V1"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    user_id = _decode_jwt_user_id(token)
    if not user_id:
        findings.append(_finding("live_write_skipped", "info", filename, group, "無法從 token 本地解碼出 user_id，跳過這組測試"))
        return findings

    apikeys_by_user_path_template = "/public/admin/v1/apikeys/{userId}"
    apikeys_create_path = "/public/admin/v1/apikeys"
    batch_delete_path = "/public/admin/v1/apikeys:batchDelete"
    apikeys_by_user_path = apikeys_by_user_path_template.replace("{userId}", user_id)

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    # Step 0：確認測試帳號有沒有 user.admin 權限（用自己的 userId 查，不會踩到別人）
    try:
        resp = session.get(f"{base}{apikeys_by_user_path}", params={"limit": 100}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"GET 查詢自己的 admin apikeys 失敗：{exc}", path=apikeys_by_user_path_template, method="GET"))
        return findings

    if resp.status_code == 403:
        findings.append(_finding("live_write_skipped", "info", filename, group, "測試帳號沒有 user.admin 權限，跳過這組測試", path=apikeys_by_user_path_template, method="GET"))
        return findings

    _check_status_and_schema(op_of(apikeys_by_user_path_template, "get"), resp, findings, filename, group, apikeys_by_user_path_template, "get")

    # Step 1：透過 Admin 端點代自己建立一把效期很短的 key
    expired_at = int(time.time()) + 3600
    try:
        resp = session.post(f"{base}{apikeys_create_path}", json={"userId": user_id, "number": 1, "expiredAt": expired_at}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立 API key 失敗：{exc}", path=apikeys_create_path, method="POST"))
        return findings

    if resp.status_code == 403:
        findings.append(_finding("live_write_skipped", "info", filename, group, "測試帳號沒有 user.admin 權限，跳過這組測試", path=apikeys_create_path, method="POST"))
        return findings

    if resp.status_code == 400:
        try:
            reason = resp.json().get("reason", "")
        except ValueError:
            reason = ""
        if reason == "apikey.quota-exceeded":
            findings.append(_finding("live_write_skipped", "info", filename, group, "測試帳號的 API key 數量已達上限（25 把），跳過這組測試", path=apikeys_create_path, method="POST"))
            return findings

    ok = _check_status_and_schema(op_of(apikeys_create_path, "post"), resp, findings, filename, group, apikeys_create_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 建立 API key 沒有回 200（實際 {resp.status_code}），中止後續步驟", path=apikeys_create_path, method="POST"))
        return findings

    try:
        created_key = resp.json()["keys"][0]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 key，中止後續步驟：{exc}", path=apikeys_create_path, method="POST"))
        return findings

    # Step 2：GET 驗證清單裡有這把 key，且 expiredAt 一致
    try:
        resp = session.get(f"{base}{apikeys_by_user_path}", params={"limit": 100}, timeout=timeout)
        items = resp.json().get("items") or []
        matched = next((it for it in items if it.get("key") == created_key), None)
        if not matched:
            findings.append(_finding("live_write_data_mismatch", "error", filename, group, f"GET 清單裡找不到剛建立的 key（{created_key}）", path=apikeys_by_user_path_template, method="GET"))
        elif matched.get("expiredAt") != expired_at:
            findings.append(
                _finding(
                    "live_write_data_mismatch",
                    "error",
                    filename,
                    group,
                    f"清單裡這把 key 的 expiredAt 是 {matched.get('expiredAt')}，跟建立時送出的 {expired_at} 不一致",
                    path=apikeys_by_user_path_template,
                    method="GET",
                )
            )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"GET 驗證建立內容失敗：{exc}", path=apikeys_by_user_path_template, method="GET"))

    # Step 3：批次刪除
    try:
        resp = session.delete(f"{base}{batch_delete_path}", params={"keys": [created_key]}, timeout=timeout)
        delete_ok = _check_status_and_schema(op_of(batch_delete_path, "delete"), resp, findings, filename, group, batch_delete_path, "delete")
        delete_ok = delete_ok and resp.status_code == 200
    except requests.RequestException as exc:
        findings.append(
            _finding("live_write_cleanup_failed", "error", filename, group, f"批次刪除 API key 失敗：{exc}，key={created_key} 需要手動清理", path=batch_delete_path, method="DELETE")
        )
        delete_ok = False

    # Step 4：GET 驗證真的刪了
    if delete_ok:
        try:
            resp = session.get(f"{base}{apikeys_by_user_path}", params={"limit": 100}, timeout=timeout)
            items = resp.json().get("items") or []
            if any(it.get("key") == created_key for it in items):
                findings.append(
                    _finding(
                        "live_write_cleanup_failed",
                        "error",
                        filename,
                        group,
                        f"批次刪除回 200，但 GET 清單裡還是找得到這把 key（{created_key}），可能沒有真的刪除",
                        path=apikeys_by_user_path_template,
                        method="GET",
                    )
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證刪除結果失敗：{exc}", path=apikeys_by_user_path_template, method="GET"))

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(
            _finding("live_write_ok", "info", filename, group, "Admin apikeys 的 POST → GET → 批次 DELETE → GET 全部驗證通過（只操作測試帳號自己的 userId），測試用的 key 已清除乾淨")
        )

    return findings


def run_asura_transcription_test(entries, api_base_url, token, timeout=60):
    """測試 Asura V1 的語音轉文字（presign 上傳 → POST /transcriptions → 輪詢結果）。

    需要 config.HELIX_TEST_AUDIO_PATH 指到一個本機真的音檔；沒有設定就跳過測試（跟 Helix
    voices 測試共用同一個音檔設定，不用另外準備）。

    ⚠️ 已知且無法避免的雙重殘留：這支端點**沒有 DELETE**，轉錄工作的紀錄會永久留著；
    上傳的音檔本身也一樣（走的是 Asura 自己的 presign，一樣沒有刪除或查詢端點）。
    """
    from .config import HELIX_TEST_AUDIO_PATH

    findings = []
    filename, group = "asura-v1.yaml", "Asura V1"

    if not HELIX_TEST_AUDIO_PATH or not os.path.isfile(HELIX_TEST_AUDIO_PATH):
        findings.append(_finding("live_write_skipped", "info", filename, group, "沒有設定 HELIX_TEST_AUDIO_PATH 或檔案不存在，跳過語音轉文字測試"))
        return findings

    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    transcriptions_path = "/public/asura/v1/transcriptions"
    transcription_item_path_template = "/public/asura/v1/transcriptions/{transcriptionId}"
    presign_path = "/public/asura/v1/transcriptions:presign"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    test_filename = "[agent-test]-" + os.path.basename(HELIX_TEST_AUDIO_PATH)
    # Asura 的 presign 限定只收固定幾個 MIME type（跟 Asset V2 不同，那邊不限制），
    # mimetypes.guess_type 對 .aac 猜出的 audio/vnd.dlna.adts 不在清單裡，這裡固定填
    # audio/aac（測試音檔的實際格式），不要用猜的。
    content_type = "audio/aac"
    with open(HELIX_TEST_AUDIO_PATH, "rb") as fh:
        audio_bytes = fh.read()

    asset_key = _asset_v2_presign_and_upload(
        session,
        base,
        timeout,
        findings,
        filename=test_filename,
        content_type=content_type,
        content=audio_bytes,
        presign_path=presign_path,
        presign_spec_label=(filename, group),
    )
    if not asset_key:
        return findings

    # 建立轉錄工作。asr-general-1.3.1 是實測確認可用的版本（spec 範例 asr-general-1.1.0
    # 未必是這個環境實際部署的版本，跟 TTS 那邊遇到的情況一樣，這裡直接用已知可用的版本）
    try:
        resp = session.post(
            f"{base}{transcriptions_path}",
            json={"assetKey": asset_key, "modelConfig": {"model": "asr-general-1.3.1", "lang": "zh"}},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立轉錄工作失敗：{exc}", path=transcriptions_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有建立轉錄工作的權限（{resp.status_code}），跳過這組測試", path=transcriptions_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(transcriptions_path, "post"), resp, findings, filename, group, transcriptions_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 建立轉錄工作沒有回 200（實際 {resp.status_code}），中止測試", path=transcriptions_path, method="POST"))
        return findings

    try:
        transcription_id = resp.json()["transcriptionId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 transcriptionId，中止測試：{exc}", path=transcriptions_path, method="POST"))
        return findings

    transcription_item_path = transcription_item_path_template.replace("{transcriptionId}", transcription_id)

    # 輪詢結果直到終態
    status = None
    for _ in range(TRANSCRIPTION_POLL_MAX_ATTEMPTS):
        try:
            resp = session.get(f"{base}{transcription_item_path}", timeout=timeout)
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"GET 輪詢轉錄結果失敗：{exc}", path=transcription_item_path_template, method="GET"))
            break
        _check_status_and_schema(op_of(transcription_item_path_template, "get"), resp, findings, filename, group, transcription_item_path_template, "get")
        try:
            body = resp.json()
        except ValueError:
            findings.append(_finding("live_write_error", "error", filename, group, "GET 輪詢轉錄結果回應不是合法 JSON，中止輪詢", path=transcription_item_path_template, method="GET"))
            break
        status = body.get("status")
        if status in TRANSCRIPTION_TERMINAL_STATUSES:
            if status == "completed" and not body.get("sentences"):
                findings.append(
                    _finding("live_write_data_mismatch", "error", filename, group, "status 是 completed，但 sentences 是空的", path=transcription_item_path_template, method="GET")
                )
            elif status != "completed":
                findings.append(
                    _finding(
                        "live_write_error",
                        "warning",
                        filename,
                        group,
                        f"轉錄工作結束但 status={status!r}（非 completed），failReason={body.get('failReason')!r}",
                        path=transcription_item_path_template,
                        method="GET",
                    )
                )
            break
        time.sleep(TRANSCRIPTION_POLL_INTERVAL_SECONDS)
    else:
        findings.append(
            _finding(
                "live_write_error",
                "warning",
                filename,
                group,
                f"輪詢 {TRANSCRIPTION_POLL_MAX_ATTEMPTS} 次（每次間隔 {TRANSCRIPTION_POLL_INTERVAL_SECONDS}s）後仍未進入終態（最後一次 status={status!r}），transcriptionId={transcription_id}",
                path=transcription_item_path_template,
                method="GET",
            )
        )

    findings.append(
        _finding(
            "live_write_permanent_residue",
            "info",
            filename,
            group,
            f"這支端點沒有 DELETE，轉錄工作紀錄（transcriptionId={transcription_id}）會永久留著，這是已知且無法避免的殘留",
        )
    )

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"presign → 上傳 → 建立轉錄工作 → 輪詢結果全部驗證通過（transcriptionId={transcription_id}）"))

    return findings


def run_asura_neartime_token_test(entries, api_base_url, token, timeout=30):
    """測試 Asura V1 的即時轉錄連線 token（`POST /neartime/token`）。

    只驗證「拿連線憑證」這個契約本身（回應的 token/path 格式），不會真的連上 WebSocket
    做即時辨識——那需要另一套完全不同的用戶端架構，跟 HTTP request/response 測試不是同一回事。
    這支端點也不是每個部署都有，沒開的部署會拿到 404，視為「這個環境沒有這個功能」而跳過。
    """
    findings = []
    filename, group = "asura-v1.yaml", "Asura V1"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    neartime_path = "/public/asura/v1/neartime/token"
    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    try:
        resp = session.post(f"{base}{neartime_path}", json={"modelConfig": {"model": "asr-general-1.3.1", "lang": "zh"}}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 取得即時轉錄 token 失敗：{exc}", path=neartime_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有這個權限（{resp.status_code}），跳過這組測試", path=neartime_path, method="POST"))
        return findings

    if resp.status_code == 404:
        findings.append(_finding("live_write_skipped", "info", filename, group, "這個部署沒有啟用即時轉錄（404），跳過這組測試", path=neartime_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(neartime_path, "post"), resp, findings, filename, group, neartime_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 取得即時轉錄 token 沒有回 200（實際 {resp.status_code}），中止測試", path=neartime_path, method="POST"))
        return findings

    try:
        body = resp.json()
        if not body.get("token") or not body.get("path"):
            findings.append(_finding("live_write_data_mismatch", "error", filename, group, f"回應缺少 token 或 path：{body}", path=neartime_path, method="POST"))
        elif body["token"] not in body["path"]:
            findings.append(
                _finding(
                    "spec_description_mismatch",
                    "warning",
                    filename,
                    group,
                    f"spec 描述 path 已經帶好 token（'{body['token']}' 應該出現在 path 裡），但實際 path（{body['path']}）沒有包含這個 token",
                    path=neartime_path,
                    method="POST",
                )
            )
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"解析回應失敗：{exc}", path=neartime_path, method="POST"))
        return findings

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, "取得即時轉錄連線 token 成功，回應格式（token/path）驗證通過（未實際連線 WebSocket）"))

    return findings


def run_chat_normal_stream_test(entries, api_base_url, token, timeout=90):
    """測試 Chat V2 送訊息的串流版本（`POST /chat/v2/chat/normal:stream`，SSE 方言 A）。

    自己建立、自己刪除一個獨立的臨時對話，不共用資料。這是真的呼叫一次 LLM，會有實際的
    API 用量／成本。只測 `normal` 模式代表這個 SSE 解析架構本身；其他模式（knowledge/
    agentic-rag/faq/tabular）的 `:stream` 版本共用同一套方言，架構驗證過就不重複測。
    """
    findings = []
    filename, group = "chat-v2.yaml", "Chat V2"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    conversations_path = "/public/chat/v2/conversations"
    conversation_item_path_template = "/public/chat/v2/conversations/{convId}"
    chat_stream_path = "/public/chat/v2/chat/normal:stream"
    models_path = "/public/chat/v2/models"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    try:
        resp = session.get(f"{base}{models_path}", timeout=timeout)
        resp.raise_for_status()
        models = resp.json().get("models") or []
        if not models:
            findings.append(_finding("live_write_error", "error", filename, group, "GET /models 沒有回傳任何模型，無法建立測試對話", path=models_path, method="GET"))
            return findings
        model_id = models[0]["id"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_error", "error", filename, group, f"取得模型清單失敗：{exc}", path=models_path, method="GET"))
        return findings

    # Step 1：建立臨時對話
    try:
        resp = session.post(f"{base}{conversations_path}", json={"conversation": {"title": TEST_TITLE, "mode": "normal", "model": model_id}}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立測試對話失敗：{exc}", path=conversations_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有建立對話的權限（{resp.status_code}），跳過這組測試", path=conversations_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(conversations_path, "post"), resp, findings, filename, group, conversations_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 建立測試對話沒有回 200（實際 {resp.status_code}），中止測試", path=conversations_path, method="POST"))
        return findings

    try:
        conv_id = resp.json()["conversation"]["convId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 convId，中止測試：{exc}", path=conversations_path, method="POST"))
        return findings

    conversation_item_path = conversation_item_path_template.replace("{convId}", conv_id)

    # Step 2：送一則訊息，SSE 串流回應
    try:
        resp = session.post(
            f"{base}{chat_stream_path}",
            json={"convId": conv_id, "message": {"text": "[agent-test] live-call chat 串流驗證，請用一句話簡短回覆"}},
            timeout=timeout,
            stream=True,
        )
        if resp.status_code != 200:
            findings.append(
                _finding("live_write_aborted", "error", filename, group, f"POST 串流送訊息沒有回 200（實際 {resp.status_code}），中止測試（仍會清理臨時對話）", path=chat_stream_path, method="POST")
            )
        else:
            content_type = resp.headers.get("Content-Type", "")
            if "text/event-stream" not in content_type:
                findings.append(
                    _finding("live_write_data_mismatch", "error", filename, group, f"回應 Content-Type 是 {content_type!r}，預期是 text/event-stream", path=chat_stream_path, method="POST")
                )
            last_data, error_event = _parse_sse_dialect_a(resp)
            if error_event:
                findings.append(
                    _finding("live_write_aborted", "error", filename, group, f"串流中途收到 event: error：{error_event}", path=chat_stream_path, method="POST")
                )
            elif not last_data:
                findings.append(_finding("live_write_data_mismatch", "error", filename, group, "串流結束但沒有收到任何 event: data", path=chat_stream_path, method="POST"))
            else:
                if last_data.get("convId") != conv_id:
                    findings.append(
                        _finding(
                            "live_write_data_mismatch", "error", filename, group, f"最後一則事件的 convId（{last_data.get('convId')}）跟送出的 convId（{conv_id}）不一致", path=chat_stream_path, method="POST"
                        )
                    )
                elif not last_data.get("messages"):
                    findings.append(_finding("live_write_data_mismatch", "error", filename, group, "最後一則事件的 messages 是空的，預期至少有一則模型的回覆", path=chat_stream_path, method="POST"))
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 串流送訊息失敗：{exc}", path=chat_stream_path, method="POST"))

    # Step 3：清掉臨時對話
    try:
        resp = session.delete(f"{base}{conversation_item_path}", timeout=timeout)
        delete_ok = _check_status_and_schema(op_of(conversation_item_path_template, "delete"), resp, findings, filename, group, conversation_item_path_template, "delete")
    except requests.RequestException as exc:
        findings.append(
            _finding("live_write_cleanup_failed", "error", filename, group, f"DELETE 測試對話失敗：{exc}，convId={conv_id} 需要手動清理", path=conversation_item_path_template, method="DELETE")
        )
        delete_ok = False

    if delete_ok:
        try:
            resp = session.get(f"{base}{conversation_item_path}", timeout=timeout)
            if resp.status_code != 404:
                findings.append(
                    _finding(
                        "live_write_cleanup_failed",
                        "error",
                        filename,
                        group,
                        f"DELETE 測試對話回 200，但 GET 還是拿到 {resp.status_code}（預期 404），convId={conv_id} 可能沒有真的刪除",
                        path=conversation_item_path_template,
                        method="DELETE",
                    )
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證刪除結果失敗：{exc}", path=conversation_item_path_template, method="GET"))

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"串流送訊息 → 解析累積內容 → 刪除臨時對話全部驗證通過（convId={conv_id}）"))

    return findings


def _run_chat_mode_stream_test(entries, api_base_url, token, timeout, *, mode, chat_stream_path, mode_label, prepare_params_fn):
    """`_run_chat_mode_test` 的串流版本：建立臨時對話 → SSE 方言 A 送訊息 → 刪除對話。

    `knowledge`/`agentic-rag`/`faq`/`tabular` 這幾個 mode 的 `:stream` 端點都共用這個流程，
    prepare_params_fn 的用法跟 `_run_chat_mode_test` 完全一致（可以直接共用同一批函式）。
    """
    findings = []
    filename, group = "chat-v2.yaml", "Chat V2"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    conversations_path = "/public/chat/v2/conversations"
    conversation_item_path_template = "/public/chat/v2/conversations/{convId}"
    models_path = "/public/chat/v2/models"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    try:
        resp = session.get(f"{base}{models_path}", timeout=timeout)
        resp.raise_for_status()
        models = resp.json().get("models") or []
        if not models:
            findings.append(_finding("live_write_error", "error", filename, group, "GET /models 沒有回傳任何模型，無法建立測試對話", path=models_path, method="GET"))
            return findings
        model_id = models[0]["id"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_error", "error", filename, group, f"取得模型清單失敗：{exc}", path=models_path, method="GET"))
        return findings

    params, cleanup_fn = prepare_params_fn(session, base, timeout, findings)
    if params is None:
        return findings

    conv_id = None
    try:
        try:
            resp = session.post(
                f"{base}{conversations_path}",
                json={"conversation": {"title": TEST_TITLE, "mode": mode, "model": model_id, "params": params}},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立測試對話失敗：{exc}", path=conversations_path, method="POST"))
            return findings

        if _is_permission_denied(resp):
            findings.append(
                _finding("live_write_skipped", "info", filename, group, f"測試帳號沒有這個 mode（{mode_label}）所需資源的 chat 權限（{resp.status_code}），跳過這組測試", path=conversations_path, method="POST")
            )
            return findings

        ok = _check_status_and_schema(op_of(conversations_path, "post"), resp, findings, filename, group, conversations_path, "post")
        if not ok or resp.status_code != 200:
            findings.append(
                _finding("live_write_aborted", "error", filename, group, f"POST 建立 {mode_label} 對話沒有回 200（實際 {resp.status_code}），中止測試", path=conversations_path, method="POST")
            )
            return findings

        try:
            conv_id = resp.json()["conversation"]["convId"]
        except Exception as exc:  # noqa: BLE001
            findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 convId，中止測試：{exc}", path=conversations_path, method="POST"))
            return findings

        conversation_item_path = conversation_item_path_template.replace("{convId}", conv_id)

        # 送一則訊息，SSE 串流回應
        try:
            resp = session.post(
                f"{base}{chat_stream_path}",
                json={"convId": conv_id, "message": {"text": f"[agent-test] live-call chat {mode_label} 串流驗證，請用一句話簡短回覆"}},
                timeout=timeout,
                stream=True,
            )
            if resp.status_code != 200:
                findings.append(
                    _finding(
                        "live_write_aborted",
                        "error",
                        filename,
                        group,
                        f"POST 串流送訊息（{mode_label}）沒有回 200（實際 {resp.status_code}），中止測試（仍會清理臨時資源）",
                        path=chat_stream_path,
                        method="POST",
                    )
                )
            else:
                content_type = resp.headers.get("Content-Type", "")
                if "text/event-stream" not in content_type:
                    findings.append(
                        _finding("live_write_data_mismatch", "error", filename, group, f"回應 Content-Type 是 {content_type!r}，預期是 text/event-stream", path=chat_stream_path, method="POST")
                    )
                last_data, error_event = _parse_sse_dialect_a(resp)
                if error_event:
                    findings.append(_finding("live_write_aborted", "error", filename, group, f"串流中途收到 event: error：{error_event}", path=chat_stream_path, method="POST"))
                elif not last_data:
                    findings.append(_finding("live_write_data_mismatch", "error", filename, group, "串流結束但沒有收到任何 event: data", path=chat_stream_path, method="POST"))
                else:
                    if last_data.get("convId") != conv_id:
                        findings.append(
                            _finding(
                                "live_write_data_mismatch",
                                "error",
                                filename,
                                group,
                                f"最後一則事件的 convId（{last_data.get('convId')}）跟送出的 convId（{conv_id}）不一致",
                                path=chat_stream_path,
                                method="POST",
                            )
                        )
                    elif not last_data.get("messages"):
                        findings.append(
                            _finding("live_write_data_mismatch", "error", filename, group, "最後一則事件的 messages 是空的，預期至少有一則模型的回覆", path=chat_stream_path, method="POST")
                        )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"POST 串流送訊息（{mode_label}）失敗：{exc}", path=chat_stream_path, method="POST"))

        # 清掉臨時對話
        try:
            resp = session.delete(f"{base}{conversation_item_path}", timeout=timeout)
            delete_ok = _check_status_and_schema(op_of(conversation_item_path_template, "delete"), resp, findings, filename, group, conversation_item_path_template, "delete")
        except requests.RequestException as exc:
            findings.append(
                _finding("live_write_cleanup_failed", "error", filename, group, f"DELETE 測試對話失敗：{exc}，convId={conv_id} 需要手動清理", path=conversation_item_path_template, method="DELETE")
            )
            delete_ok = False

        if delete_ok:
            try:
                resp = session.get(f"{base}{conversation_item_path}", timeout=timeout)
                if resp.status_code != 404:
                    findings.append(
                        _finding(
                            "live_write_cleanup_failed",
                            "error",
                            filename,
                            group,
                            f"DELETE 測試對話回 200，但 GET 還是拿到 {resp.status_code}（預期 404），convId={conv_id} 可能沒有真的刪除",
                            path=conversation_item_path_template,
                            method="DELETE",
                        )
                    )
            except requests.RequestException as exc:
                findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證刪除結果失敗：{exc}", path=conversation_item_path_template, method="GET"))
    finally:
        cleanup_fn()

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"{mode_label} 模式的串流送訊息 → 解析累積內容 → 刪除臨時對話全部驗證通過（convId={conv_id}）"))

    return findings


def run_chat_knowledge_stream_test(entries, api_base_url, token, timeout=90):
    return _run_chat_mode_stream_test(
        entries, api_base_url, token, timeout, mode="knowledge", chat_stream_path="/public/chat/v2/chat/knowledge:stream", mode_label="knowledge", prepare_params_fn=_prepare_knowledge_params
    )


def run_chat_agentic_rag_stream_test(entries, api_base_url, token, timeout=90):
    return _run_chat_mode_stream_test(
        entries, api_base_url, token, timeout, mode="agentic-rag", chat_stream_path="/public/chat/v2/chat/agenticRag:stream", mode_label="agentic-rag", prepare_params_fn=_prepare_knowledge_params
    )


def run_chat_faq_stream_test(entries, api_base_url, token, timeout=90):
    return _run_chat_mode_stream_test(
        entries, api_base_url, token, timeout, mode="faq", chat_stream_path="/public/chat/v2/chat/faq:stream", mode_label="faq", prepare_params_fn=_prepare_faq_params
    )


def run_chat_tabular_stream_test(entries, api_base_url, token, timeout=90):
    return _run_chat_mode_stream_test(
        entries, api_base_url, token, timeout, mode="tabular", chat_stream_path="/public/chat/v2/chat/tabular:stream", mode_label="tabular", prepare_params_fn=_prepare_tabular_params
    )


def run_llm_visual_completions_test(entries, api_base_url, token, timeout=60):
    """測試 LLM V1 的圖片對話（`POST /llm/v1/visual/v1/chat/completions`）。

    無狀態呼叫，不需要清理。用一個公開、穩定的測試圖片網址（httpbin.org 的靜態測試圖）——
    如果部署環境對外連線有限制、連不到這個網址，這裡會回報成失敗，但那其實是環境限制，
    不是這支 API 本身的問題，回報訊息裡會註明這個可能性。
    """
    findings = []
    filename, group = "llm-v1.yaml", "LLM V1"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    visual_path = "/public/llm/v1/visual/v1/chat/completions"
    test_image_url = "https://httpbin.org/image/jpeg"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    try:
        resp = session.post(
            f"{base}{visual_path}",
            json={
                "model": "vlm-v3.11",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "[agent-test] live-call visual completions 驗證，請用一句話簡短描述這張圖片"},
                            {"type": "image_url", "image_url": {"url": test_image_url}},
                        ],
                    }
                ],
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST visual completions 失敗：{exc}", path=visual_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有呼叫 visual completions 的權限（{resp.status_code}），跳過這組測試", path=visual_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(visual_path, "post"), resp, findings, filename, group, visual_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(
            _finding(
                "live_write_aborted",
                "error",
                filename,
                group,
                f"POST visual completions 沒有回 200（實際 {resp.status_code}），中止測試——如果原因跟抓不到測試圖片網址（{test_image_url}）有關，可能是部署環境對外連線限制，不是 API 本身的問題",
                path=visual_path,
                method="POST",
            )
        )
        return findings

    try:
        body = resp.json()
        choices = body.get("choices") or []
        if not choices or not (choices[0].get("message") or {}).get("content"):
            findings.append(_finding("live_write_data_mismatch", "error", filename, group, "回應的 choices 是空的，或第一個 choice 沒有 message.content", path=visual_path, method="POST"))
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"解析 visual completions 回應失敗：{exc}", path=visual_path, method="POST"))
        return findings

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, "visual completions 呼叫成功，收到模型對測試圖片的描述回應"))

    return findings


def run_helix_enrollment_test(entries, api_base_url, token, timeout=60):
    """測試 Helix V1 的 `/enrollment`（帳號自己的聲紋，一個帳號限一組）。

    這個資源的限制跟 `/voices` 完全不同：**一個帳號只能有一組**，而且已知主要測試帳號
    有真實註冊，不能拿來測（會被 409 擋下，就算沒被擋也絕不該碰）。所以這裡固定使用一個
    獨立的、確認過從沒註冊過聲紋的專用測試帳號（`config.HELIX_ENROLLMENT_TEST_TOKEN`），
    完全不會用到參數傳進來的 `token`（那是主要測試帳號的，留著只是為了跟其他 run_* 函式
    的呼叫介面一致，方便 run_check.py 統一迭代呼叫）——沒有設定這個環境變數就跳過測試，
    不是失敗。

    即使用的是專用帳號，第一步仍然會先 `GET` 確認真的還沒註冊過，不會假設環境變數設定
    正確就直接動手：如果這個帳號不知何故已經有註冊，一樣直接跳過、不嘗試刪除重建。
    """
    from .config import HELIX_ENROLLMENT_TEST_TOKEN, HELIX_TEST_AUDIO_PATH

    findings = []
    filename, group = "helix-v1.yaml", "Helix V1"

    if not HELIX_ENROLLMENT_TEST_TOKEN:
        findings.append(_finding("live_write_skipped", "info", filename, group, "沒有設定 HELIX_ENROLLMENT_TEST_TOKEN，跳過 /enrollment 測試（不會用主要測試帳號測這個端點）"))
        return findings

    if not HELIX_TEST_AUDIO_PATH or not os.path.isfile(HELIX_TEST_AUDIO_PATH):
        findings.append(_finding("live_write_skipped", "info", filename, group, "沒有設定 HELIX_TEST_AUDIO_PATH 或檔案不存在，跳過 /enrollment 測試"))
        return findings

    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    enrollment_path = "/public/helix/v1/enrollment"

    session = requests.Session()
    session.headers.update({"X-Access-Token": HELIX_ENROLLMENT_TEST_TOKEN, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    # Step 0：確認這個專用測試帳號真的還沒註冊過，不假設環境變數設定正確
    try:
        resp = session.get(f"{base}{enrollment_path}", timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"GET 查詢註冊狀態失敗：{exc}", path=enrollment_path, method="GET"))
        return findings

    ok = _check_status_and_schema(op_of(enrollment_path, "get"), resp, findings, filename, group, enrollment_path, "get")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"GET 查詢註冊狀態沒有回 200（實際 {resp.status_code}），中止測試", path=enrollment_path, method="GET"))
        return findings

    try:
        already_enrolled = resp.json().get("enrolled")
    except ValueError:
        findings.append(_finding("live_write_aborted", "error", filename, group, "GET 查詢註冊狀態回應不是合法 JSON，中止測試", path=enrollment_path, method="GET"))
        return findings

    if already_enrolled:
        findings.append(
            _finding("live_write_skipped", "info", filename, group, "這個專用測試帳號已經有註冊（enrolled: true），不符合預期，直接跳過、不嘗試刪除重建", path=enrollment_path, method="GET")
        )
        return findings

    # Step 1：presign + 上傳音檔
    test_filename = "[agent-test]-" + os.path.basename(HELIX_TEST_AUDIO_PATH)
    content_type = "audio/aac"
    with open(HELIX_TEST_AUDIO_PATH, "rb") as fh:
        audio_bytes = fh.read()

    asset_key = _asset_v2_presign_and_upload(session, base, timeout, findings, filename=test_filename, content_type=content_type, content=audio_bytes)
    if not asset_key:
        return findings

    # Step 2：註冊聲紋
    try:
        resp = session.post(f"{base}{enrollment_path}", json={"audioKeys": [asset_key]}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 註冊聲紋失敗：{exc}", path=enrollment_path, method="POST"))
        return findings

    if resp.status_code == 409:
        findings.append(
            _finding(
                "live_write_skipped",
                "info",
                filename,
                group,
                "POST 註冊回 409（data.unique，帳號已註冊過）——跟 Step 0 的 GET 結果矛盾，可能是併發註冊，直接跳過不處理",
                path=enrollment_path,
                method="POST",
            )
        )
        return findings

    ok = _check_status_and_schema(op_of(enrollment_path, "post"), resp, findings, filename, group, enrollment_path, "post")
    if not ok or resp.status_code != 200:
        if "MULTIPLE_SPEAKERS" in resp.text:
            findings.append(
                _finding(
                    "live_write_skipped",
                    "info",
                    filename,
                    group,
                    f"測試音檔偵測到多位說話者（{resp.text[:200]}），/enrollment 需要單一穩定說話者的錄音，這是合理的業務驗證、不是 API 問題——需要換一段單人語音的測試音檔才能完整測試這條路徑",
                    path=enrollment_path,
                    method="POST",
                )
            )
            return findings
        try:
            error_body = resp.json()
            if resp.status_code == 400 and not error_body.get("service") and not error_body.get("reason"):
                findings.append(
                    _finding(
                        "spec_description_mismatch",
                        "warning",
                        filename,
                        group,
                        f"這個 400 回應的 service/reason 都是空字串（message={error_body.get('message')!r}），不符合 Error schema 說明的『reason 是必填、程式請用這個欄位做分支』——這種錯誤沒辦法照文件教的方式判斷成因",
                        path=enrollment_path,
                        method="POST",
                    )
                )
        except ValueError:
            pass
        findings.append(
            _finding("live_write_aborted", "error", filename, group, f"POST 註冊聲紋沒有回 200（實際 {resp.status_code}）：{resp.text[:300]}，中止測試", path=enrollment_path, method="POST")
        )
        return findings

    try:
        voice_id = resp.json()["voiceId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 voiceId：{exc}", path=enrollment_path, method="POST"))
        return findings

    # Step 3：GET 驗證註冊成功
    try:
        resp = session.get(f"{base}{enrollment_path}", timeout=timeout)
        body = resp.json()
        if not body.get("enrolled"):
            findings.append(_finding("live_write_data_mismatch", "error", filename, group, f"註冊成功後 GET 卻回 enrolled: {body.get('enrolled')}", path=enrollment_path, method="GET"))
        elif body.get("voiceId") != voice_id:
            findings.append(
                _finding("live_write_data_mismatch", "error", filename, group, f"GET 回來的 voiceId（{body.get('voiceId')}）跟 POST 回應的（{voice_id}）不一致", path=enrollment_path, method="GET")
            )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"GET 驗證註冊結果失敗：{exc}", path=enrollment_path, method="GET"))

    # Step 4：刪除，恢復成未註冊狀態
    delete_ok = False
    try:
        resp = session.delete(f"{base}{enrollment_path}", timeout=timeout)
        delete_ok = _check_status_and_schema(op_of(enrollment_path, "delete"), resp, findings, filename, group, enrollment_path, "delete")
        delete_ok = delete_ok and resp.status_code == 200
    except requests.RequestException as exc:
        findings.append(_finding("live_write_cleanup_failed", "error", filename, group, f"DELETE 刪除註冊失敗：{exc}，voiceId={voice_id} 需要手動清理", path=enrollment_path, method="DELETE"))

    # Step 5：GET 驗證恢復成未註冊
    if delete_ok:
        try:
            resp = session.get(f"{base}{enrollment_path}", timeout=timeout)
            body = resp.json()
            if body.get("enrolled"):
                findings.append(
                    _finding("live_write_cleanup_failed", "error", filename, group, f"DELETE 回 200，但 GET 還是 enrolled: true，voiceId={voice_id} 可能沒有真的清除", path=enrollment_path, method="GET")
                )
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "warning", filename, group, f"GET 驗證刪除結果失敗：{exc}", path=enrollment_path, method="GET"))

    findings.append(
        _finding(
            "live_write_permanent_residue",
            "info",
            filename,
            group,
            f"spec 明講送進來的錄音會被永久保存，即使已經 DELETE 恢復成未註冊狀態，底層錄音本身是否真的清除文件沒有講清楚——這是已知且無法確認的潛在殘留",
        )
    )

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"專用測試帳號的 POST 註冊 → GET 驗證 → DELETE → GET 驗證清空全部驗證通過（voiceId={voice_id}）"))

    return findings


def run_asura_speech_zero_shot_test(entries, api_base_url, token, timeout=60):
    """測試 Asura V1 的零樣本語音克隆（`POST /asura/v1/speeches:zero-shot`）。

    需要 config.HELIX_TEST_AUDIO_PATH 指到一個本機真的音檔，當作音色範本上傳（跟 Helix
    voices / Asura 轉錄測試共用同一個音檔設定）；沒有設定就跳過測試，不是失敗。

    無狀態呼叫，回應是音訊二進位資料不是 JSON，不需要清理這支端點本身；但上傳範本音檔
    走的是 Asura 自己的 `/transcriptions:presign`（spec 明講這支 presign 同時服務轉錄與
    這裡的音色範本），一樣沒有刪除或查詢端點，會留一筆永久殘留的 finding。

    這個部署可能沒開這支端點（跟固定音色版一樣，404 視為「環境沒這個功能」而跳過）。
    modelConfig.model 沿用 run_asura_tts_test 已經確認在這個環境可用的 tts-general-1.3.3；
    如果零樣本版本其實要用不同的模型名稱而失敗，會記成新的 spec 落差，不會亂猜其他名字。
    `promptText`（音檔的逐字稿）是選填欄位，這裡沒有精確逐字稿就不填，不影響呼叫成不成功，
    只影響合成音色像不像範本，不在這支 API 正確性測試的範圍內。
    """
    from .config import HELIX_TEST_AUDIO_PATH

    findings = []
    filename, group = "asura-v1.yaml", "Asura V1"

    if not HELIX_TEST_AUDIO_PATH or not os.path.isfile(HELIX_TEST_AUDIO_PATH):
        findings.append(_finding("live_write_skipped", "info", filename, group, "沒有設定 HELIX_TEST_AUDIO_PATH 或檔案不存在，跳過零樣本語音克隆測試"))
        return findings

    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    zero_shot_path = "/public/asura/v1/speeches:zero-shot"
    presign_path = "/public/asura/v1/transcriptions:presign"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    # Step 0：故意不帶 promptVoiceUrl/promptVoiceAssetKey，實測 spec 沒把它們標必填是否屬實
    # （ZeroShotSpeechInput.input 的 required 只列了 text）。
    try:
        probe_resp = session.post(
            f"{base}{zero_shot_path}",
            json={"input": {"text": "[agent-test] 探測用，不帶語音範本"}, "modelConfig": {"model": "tts-general-1.3.3"}},
            timeout=timeout,
        )
        if probe_resp.status_code not in (200, 404):
            try:
                probe_msg = probe_resp.json().get("message", "")
            except ValueError:
                probe_msg = ""
            findings.append(
                _finding(
                    "spec_description_mismatch",
                    "warning",
                    filename,
                    group,
                    f"spec 的 ZeroShotSpeechInput.input 只把 text 標成必填，promptVoiceUrl/promptVoiceAssetKey 兩個都是選填，"
                    f"但兩個都不帶實際回應是 {probe_resp.status_code}：{probe_msg}——代表伺服器端其實兩者擇一是必填",
                    path=zero_shot_path,
                    method="POST",
                )
            )
    except requests.RequestException:
        pass  # 這只是探測性質，失敗不影響下面正式測試

    test_filename = "[agent-test]-zero-shot-prompt-" + os.path.basename(HELIX_TEST_AUDIO_PATH)
    content_type = "audio/aac"  # 跟轉錄測試一樣：mimetypes 猜出來的 MIME type 不在 Asura presign 收的清單裡
    with open(HELIX_TEST_AUDIO_PATH, "rb") as fh:
        audio_bytes = fh.read()

    asset_key = _asset_v2_presign_and_upload(
        session,
        base,
        timeout,
        findings,
        filename=test_filename,
        content_type=content_type,
        content=audio_bytes,
        presign_path=presign_path,
        presign_spec_label=(filename, group),
    )
    if not asset_key:
        return findings

    body = {
        "input": {"text": "[agent-test] live-call 零樣本語音克隆驗證", "type": "text", "promptVoiceAssetKey": asset_key},
        "modelConfig": {"model": "tts-general-1.3.3"},
    }

    try:
        resp = session.post(f"{base}{zero_shot_path}", json=body, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 零樣本語音克隆失敗：{exc}", path=zero_shot_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有呼叫零樣本語音克隆的權限（{resp.status_code}），跳過這組測試", path=zero_shot_path, method="POST"))
        return findings

    if resp.status_code == 404:
        findings.append(_finding("live_write_skipped", "info", filename, group, "這個部署沒有啟用零樣本語音克隆（404），跳過這組測試", path=zero_shot_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(zero_shot_path, "post"), resp, findings, filename, group, zero_shot_path, "post")
    if not ok or resp.status_code != 200:
        try:
            resp_reason = resp.json().get("reason", "")
        except ValueError:
            resp_reason = ""
        if resp_reason == "inferno.get-model.failed":
            findings.append(
                _finding(
                    "spec_description_mismatch",
                    "warning",
                    filename,
                    group,
                    f"零樣本語音克隆用固定音色版已經驗證可用的模型（{body['modelConfig']['model']!r}），這裡卻查不到模型（{resp_reason}）——"
                    "代表兩支 TTS 端點的模型清單不是共用的，spec 沒有講清楚這點，也沒有端點可以查零樣本版本實際支援的模型名稱",
                    path=zero_shot_path,
                    method="POST",
                )
            )
        elif resp_reason == "data.unhandled" and resp.status_code == 500:
            # 手動診斷過（不是每次自動測試都重跑，成本較高）：拿掉 promptVoiceAssetKey 只送
            # text 會回乾淨的 400（value length must be at least 1 characters，代表 spec 沒
            # 標必填其實是必填）；帶對的話不管填 promptVoiceAssetKey（走 spec 建議的
            # transcriptions:presign 流程）還是填一個已經驗證能用的真實 promptVoiceUrl（同一份
            # 檔案用同一個 assetKey 打 /transcriptions 可以正常解析、成功建立轉錄工作），兩種
            # 格式都同樣回這個被吞掉細節的 500——代表壞的不是 assetKey 對不對、模型名稱對不對，
            # 是這支端點處理「帶了語音範本」這條路徑本身有問題，比較像後端真的有 bug，不是文件寫錯。
            findings.append(
                _finding(
                    "spec_description_mismatch",
                    "warning",
                    filename,
                    group,
                    "帶 promptVoiceAssetKey（照 spec 建議走 transcriptions:presign 上傳）一律回 500 "
                    "{\"service\":\"http-gateway\",\"reason\":\"data.unhandled\",\"message\":\"unexpected status code: 400\"}"
                    "——代表 http-gateway 轉呼叫上游時收到 400，但沒有處理這個狀態碼的邏輯，直接包成看不出原因的 500。"
                    "手動排查過：同一個 assetKey 打 /transcriptions 可以正常解析成真實網址並成功建立轉錄工作，"
                    "改填那個已驗證能連得到的 promptVoiceUrl 給這支端點一樣回同一個 500——不是 assetKey 或模型名稱的問題，"
                    "是這支端點處理語音範本輸入的路徑本身壞了，比較像後端 bug 而不是文件寫錯",
                    path=zero_shot_path,
                    method="POST",
                )
            )
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 零樣本語音克隆沒有回 200（實際 {resp.status_code}）：{resp.text[:300]}，中止測試", path=zero_shot_path, method="POST"))
        return findings

    content_type_resp = resp.headers.get("Content-Type", "")
    if "audio" not in content_type_resp:
        findings.append(_finding("live_write_data_mismatch", "error", filename, group, f"回應 Content-Type 是 {content_type_resp!r}，預期是 audio/*", path=zero_shot_path, method="POST"))
    elif len(resp.content) == 0:
        findings.append(_finding("live_write_data_mismatch", "error", filename, group, "回應的音訊內容是空的", path=zero_shot_path, method="POST"))

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"零樣本語音克隆呼叫成功，收到 {len(resp.content)} bytes 的 {content_type_resp} 音訊"))

    return findings


def run_helix_voice_search_by_audio_test(entries, api_base_url, token, timeout=1800):
    """測試 Helix V1 的 `POST /helix/v1/voices:searchByAudio`（用一段錄音搜尋既有聲紋）。

    需要先有至少一個 voiceId 可以搜尋（`voiceIds` 是必填且不能是空陣列）。這裡跟
    `run_helix_voice_crud_test` 用同一套流程臨時建一個聲紋：Asset V2 presign → 上傳 →
    把 assetKey 直接當 audioUris 送給 `POST /voices`。**這步本身有已知的不穩定性**（同一份
    assetKey 有時候能用、有時候回 502），如果這裡失敗就直接跳過整組測試，不會另外想辦法
    繞過去，避免把不穩定的建立流程誤判成 searchByAudio 本身的問題。

    spec 說這支端點是同步的，語音辨識 + 聲紋比對整條跑完才回應，伺服器端每一步上限 30
    分鐘——這裡的 timeout 給到 30 分鐘只是不要因為 client 太早放棄而誤判失敗，測試音檔本身
    很短，實際應該幾秒到一分鐘內就回來。不要求真的比對到人（`matches` 是空陣列也算正常），
    只驗證回應格式符合 schema。

    測完會 DELETE 掉臨時建立的聲紋，確認清除乾淨。
    """
    findings = []
    filename, group = "helix-v1.yaml", "Helix V1"
    asset_filename, asset_group = "asset-v2.yaml", "Asset V2"

    from .config import HELIX_TEST_AUDIO_PATH

    if not HELIX_TEST_AUDIO_PATH or not os.path.isfile(HELIX_TEST_AUDIO_PATH):
        findings.append(_finding("live_write_skipped", "info", filename, group, "沒有設定 HELIX_TEST_AUDIO_PATH 或檔案不存在，跳過 voices:searchByAudio 測試"))
        return findings

    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    voices_path = "/public/helix/v1/voices"
    voice_item_path_template = "/public/helix/v1/voices/{voiceId}"
    search_path = "/public/helix/v1/voices:searchByAudio"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    test_filename = "[agent-test]-searchByAudio-" + os.path.basename(HELIX_TEST_AUDIO_PATH)
    content_type = "audio/aac"
    with open(HELIX_TEST_AUDIO_PATH, "rb") as fh:
        audio_bytes = fh.read()

    # Step 1：建立一個臨時聲紋當搜尋目標（沿用 run_helix_voice_crud_test 的做法）
    asset_key = _asset_v2_presign_and_upload(session, base, timeout, findings, filename=test_filename, content_type=content_type, content=audio_bytes)
    if not asset_key:
        return findings

    try:
        resp = session.post(f"{base}{voices_path}", json={"audioUris": [asset_key]}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 建立聲紋失敗：{exc}", path=voices_path, method="POST"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有建立聲紋的權限（{resp.status_code}），跳過這組測試", path=voices_path, method="POST"))
        return findings

    if resp.status_code != 200:
        findings.append(
            _finding(
                "live_write_skipped",
                "info",
                filename,
                group,
                f"建立測試用聲紋失敗（{resp.status_code}），這是已知的 assetKey/audioUris 銜接不穩定問題（見 run_helix_voice_crud_test 的說明），跳過 searchByAudio 測試",
                path=voices_path,
                method="POST",
            )
        )
        return findings

    try:
        voice_id = resp.json()["voiceId"]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 回應裡拿不到 voiceId：{exc}", path=voices_path, method="POST"))
        return findings

    voice_item_path = voice_item_path_template.replace("{voiceId}", voice_id)

    def cleanup():
        try:
            resp = session.delete(f"{base}{voice_item_path}", timeout=timeout)
            if resp.status_code != 200:
                findings.append(_finding("live_write_cleanup_failed", "error", filename, group, f"清理測試用聲紋（{voice_id}）失敗，DELETE 回 {resp.status_code}，需要手動清理", path=voice_item_path, method="DELETE"))
        except requests.RequestException as exc:
            findings.append(_finding("live_write_cleanup_failed", "error", filename, group, f"清理測試用聲紋（{voice_id}）失敗：{exc}，需要手動清理", path=voice_item_path, method="DELETE"))

    # Step 2：searchByAudio——用同一份音檔的 assetKey 當 audioUri，搜尋剛剛建立的 voiceId
    try:
        resp = session.post(f"{base}{search_path}", json={"audioUri": asset_key, "voiceIds": [voice_id]}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST voices:searchByAudio 失敗：{exc}", path=search_path, method="POST"))
        cleanup()
        return findings

    if resp.status_code != 200:
        findings.append(
            _finding(
                "live_write_aborted",
                "error",
                filename,
                group,
                f"POST voices:searchByAudio 沒有回 200（實際 {resp.status_code}）：{resp.text[:300]}，中止測試（仍會清理臨時聲紋）",
                path=search_path,
                method="POST",
            )
        )
        cleanup()
        return findings

    ok = _check_status_and_schema(op_of(search_path, "post"), resp, findings, filename, group, search_path, "post")
    if ok:
        try:
            body = resp.json()
            sentence_speaker_ids = {s.get("speakerId") for s in (body.get("sentences") or [])}
            speaker_ids = {s.get("speakerId") for s in (body.get("speakers") or [])}
            missing = sentence_speaker_ids - speaker_ids
            if missing:
                findings.append(
                    _finding(
                        "spec_description_mismatch",
                        "warning",
                        filename,
                        group,
                        f"spec 說 sentences 裡出現過的每個 speakerId 都保證會出現在 speakers 裡，但實測有 {sorted(missing)} 沒對到",
                        path=search_path,
                        method="POST",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            findings.append(_finding("live_write_aborted", "error", filename, group, f"解析 voices:searchByAudio 回應失敗：{exc}", path=search_path, method="POST"))

    cleanup()

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"searchByAudio 呼叫成功、回應格式驗證通過，臨時聲紋（{voice_id}）已清除乾淨"))

    return findings


def run_llm_chat_completions_test(entries, api_base_url, token, timeout=60):
    """測試 LLM V1 的文字對話（`POST /llm/v1/fedgpt/v1/chat/completions`，OpenAI 相容）。

    先 `GET /llm/v1/fedgpt/v1/models` 拿模型清單：**第一筆是代稱**（解到本部署當下的預設
    模型），優先用它；spec 提到代稱在模型只有部分 replica 就緒時可能回 404，這時改用清單
    第二筆（帶版號的模型 ID）重試一次——這是文件自己建議的容錯方式，不是亂猜。

    無狀態呼叫，不需要清理；跟 Chat V2 的測試一樣，這支會有真實的 LLM API 用量／成本，
    所以 `max_tokens` 特意設小。
    """
    findings = []
    filename, group = "llm-v1.yaml", "LLM V1"
    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    models_path = "/public/llm/v1/fedgpt/v1/models"
    completions_path = "/public/llm/v1/fedgpt/v1/chat/completions"

    session = requests.Session()
    session.headers.update({"X-Access-Token": token, "Content-Type": "application/json"})
    base = api_base_url.rstrip("/")

    try:
        resp = session.get(f"{base}{models_path}", timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"GET 模型清單失敗：{exc}", path=models_path, method="GET"))
        return findings

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有讀取模型清單的權限（{resp.status_code}），跳過這組測試", path=models_path, method="GET"))
        return findings

    if resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"GET 模型清單沒有回 200（實際 {resp.status_code}），跳過這組測試", path=models_path, method="GET"))
        return findings

    try:
        model_ids = [m["id"] for m in resp.json().get("data") or []]
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"解析模型清單失敗：{exc}", path=models_path, method="GET"))
        return findings

    if not model_ids:
        findings.append(_finding("live_write_skipped", "info", filename, group, "模型清單是空的（沒有任何模型上線），跳過這組測試", path=models_path, method="GET"))
        return findings

    candidates = [model_ids[0]] + ([model_ids[1]] if len(model_ids) > 1 else [])
    body = {
        "model": None,
        "messages": [{"role": "user", "content": "[agent-test] live-call chat completions 驗證，請用一句話回覆收到了"}],
        "max_tokens": 16,
    }

    resp = None
    for model_id in candidates:
        body["model"] = model_id
        try:
            resp = session.post(f"{base}{completions_path}", json=body, timeout=timeout)
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"POST chat completions 失敗：{exc}", path=completions_path, method="POST"))
            return findings
        if resp.status_code != 404:
            break
        # 代稱在模型只有部分 replica 就緒時會回 404，spec 建議這時改用帶版號的模型 ID 重試——
        # 只在清單第一筆（代稱）失敗時重試一次，不無限重試。

    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"測試帳號沒有呼叫 chat completions 的權限（{resp.status_code}），跳過這組測試", path=completions_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(completions_path, "post"), resp, findings, filename, group, completions_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(
            _finding(
                "live_write_aborted",
                "error",
                filename,
                group,
                f"POST chat completions 沒有回 200（實際 {resp.status_code}，用的模型是 {body['model']!r}），中止測試",
                path=completions_path,
                method="POST",
            )
        )
        return findings

    try:
        choices = resp.json().get("choices") or []
        if not choices or not (choices[0].get("message") or {}).get("content"):
            findings.append(_finding("live_write_data_mismatch", "error", filename, group, "回應的 choices 是空的，或第一個 choice 沒有 message.content", path=completions_path, method="POST"))
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"解析 chat completions 回應失敗：{exc}", path=completions_path, method="POST"))
        return findings

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, f"chat completions 呼叫成功（model={body['model']}）"))

    return findings


def run_auth_login_logout_test(entries, api_base_url, token, timeout=30):
    """測試 Auth V2 的 `POST /fedgpt/login` + `POST /logout`。

    這兩支的風險跟其他 --live-write 端點不一樣：login 會產生新的 access token，logout 會讓
    某個 token 失效。固定用 `config.AUTH_LOGIN_TEST_USERNAME`/`AUTH_LOGIN_TEST_PASSWORD` 指定
    的次要帳號（不是傳進來的 `token` 參數——那是主要測試帳號的，這裡刻意不用），登入後立刻用
    剛拿到的新 token 登出，全程不會動到主要測試帳號正在用的 token。沒有設定帳密就跳過測試。

    刻意不測「密碼打錯」的情境：spec 有連續失敗鎖定帳號的機制（`429 auth.locked`），如果排程
    重複執行這個測試，累積的錯誤嘗試可能真的把這個次要帳號鎖住，風險大於測試價值。
    """
    from .config import AUTH_LOGIN_TEST_PASSWORD, AUTH_LOGIN_TEST_USERNAME

    findings = []
    filename, group = "auth-v2.yaml", "Auth V2"

    if not AUTH_LOGIN_TEST_USERNAME or not AUTH_LOGIN_TEST_PASSWORD:
        findings.append(_finding("live_write_skipped", "info", filename, group, "沒有設定 AUTH_LOGIN_TEST_USERNAME/AUTH_LOGIN_TEST_PASSWORD，跳過 login/logout 測試"))
        return findings

    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    login_path = "/public/auth/v2/fedgpt/login"
    logout_path = "/public/auth/v2/logout"
    base = api_base_url.rstrip("/")

    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    try:
        resp = session.post(f"{base}{login_path}", json={"authKey": AUTH_LOGIN_TEST_USERNAME, "authSecret": AUTH_LOGIN_TEST_PASSWORD}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 登入失敗：{exc}", path=login_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(login_path, "post"), resp, findings, filename, group, login_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 登入沒有回 200（實際 {resp.status_code}），中止測試", path=login_path, method="POST"))
        return findings

    try:
        new_token = resp.json()["token"]
        if not new_token:
            raise ValueError("token 是空的")
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"登入回應裡拿不到有效的 token：{exc}", path=login_path, method="POST"))
        return findings

    logout_session = requests.Session()
    logout_session.headers.update({"X-Access-Token": new_token, "Content-Type": "application/json"})

    try:
        resp = logout_session.post(f"{base}{logout_path}", timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST 登出失敗：{exc}，剛登入的 token 可能沒有失效，需要留意", path=logout_path, method="POST"))
        return findings

    ok = _check_status_and_schema(op_of(logout_path, "post"), resp, findings, filename, group, logout_path, "post")
    if not ok or resp.status_code != 200 or not resp.json().get("success"):
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST 登出沒有成功（{resp.status_code}），剛登入的 token 可能沒有失效，需要留意", path=logout_path, method="POST"))
        return findings

    # 驗證真的失效：再用同一個 token 打一次 logout。spec 的敘述說這時候會拿到
    # 401 auth.invalid-auth，但這支端點的 responses 只正式宣告了 200，401 沒有列在裡面——
    # _check_status_and_schema 遇到未宣告的狀態碼會自動記一筆 undocumented_status_code，
    # 這裡另外驗證 reason 內容符不符合敘述。
    try:
        resp = logout_session.post(f"{base}{logout_path}", timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "warning", filename, group, f"驗證登出後 token 是否真的失效時失敗：{exc}", path=logout_path, method="POST"))
    else:
        _check_status_and_schema(op_of(logout_path, "post"), resp, findings, filename, group, logout_path, "post")
        if resp.status_code == 401:
            try:
                reason = resp.json().get("reason", "")
            except ValueError:
                reason = ""
            if reason != "auth.invalid-auth":
                findings.append(
                    _finding(
                        "spec_description_mismatch",
                        "warning",
                        filename,
                        group,
                        f"spec 敘述說登出後再用同一個 token 的 reason 會是 auth.invalid-auth，實測是 {reason!r}",
                        path=logout_path,
                        method="POST",
                    )
                )
        else:
            findings.append(
                _finding(
                    "spec_description_mismatch",
                    "warning",
                    filename,
                    group,
                    f"spec 敘述說登出後再用同一個 token 會拿到 401 auth.invalid-auth，實測是 {resp.status_code}——token 可能沒有真的失效",
                    path=logout_path,
                    method="POST",
                )
            )

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, "登入 → 登出 → 驗證 token 真的失效全部驗證通過（次要測試帳號，未動到主要測試帳號的 token）"))

    return findings


def run_auth_ldap_login_test(entries, api_base_url, token, timeout=30):
    """測試 Auth V2 的 `POST /ldap/login`。

    先打 `GET /providers` 確認這個部署有沒有啟用、設定好 LDAP 登入，沒有的話直接跳過，
    不會真的對 LDAP 登入端點發送任何請求。

    `config.AUTH_LOGIN_TEST_USERNAME`/`AUTH_LOGIN_TEST_PASSWORD` 這組次要帳號已確認只給
    `fedgpt` provider 用，不是 LDAP 帳密——這裡仍然會實際打一次 `ldap/login`，但預期會被
    `401 auth.invalid-auth` 擋下來，那視為「這組帳密不適用 LDAP」而正常跳過，不是失敗；
    只打一次、不重試，避免影響到這個 LDAP 測試網域帳號自己的鎖定策略。
    """
    from .config import AUTH_LOGIN_TEST_PASSWORD, AUTH_LOGIN_TEST_USERNAME

    findings = []
    filename, group = "auth-v2.yaml", "Auth V2"

    if not AUTH_LOGIN_TEST_USERNAME or not AUTH_LOGIN_TEST_PASSWORD:
        findings.append(_finding("live_write_skipped", "info", filename, group, "沒有設定 AUTH_LOGIN_TEST_USERNAME/AUTH_LOGIN_TEST_PASSWORD，跳過 ldap/login 測試"))
        return findings

    entry = next((e for e in entries if e.get("filename") == filename and e.get("spec")), None)
    if not entry:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"找不到 {filename} 的 spec，跳過寫入測試"))
        return findings

    spec = entry["spec"]
    paths = spec.get("paths") or {}

    def op_of(path, method):
        op = paths[path][method]
        op["__spec__"] = spec
        return op

    providers_path = "/public/auth/v2/providers"
    login_path = "/public/auth/v2/ldap/login"
    logout_path = "/public/auth/v2/logout"
    base = api_base_url.rstrip("/")

    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    try:
        resp = session.get(f"{base}{providers_path}", timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"GET providers 失敗：{exc}", path=providers_path, method="GET"))
        return findings

    if resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"GET providers 沒有回 200（實際 {resp.status_code}），跳過這組測試", path=providers_path, method="GET"))
        return findings

    try:
        providers = resp.json().get("items") or []
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"解析 providers 清單失敗：{exc}", path=providers_path, method="GET"))
        return findings

    ldap_provider = next((p for p in providers if p.get("provider") == "ldap"), None)
    if not ldap_provider or not ldap_provider.get("isValid"):
        findings.append(_finding("live_write_skipped", "info", filename, group, "這個部署沒有啟用/設定好 LDAP 登入（GET providers 查不到有效的 ldap），跳過測試", path=login_path, method="POST"))
        return findings

    try:
        resp = session.post(f"{base}{login_path}", json={"authKey": AUTH_LOGIN_TEST_USERNAME, "authSecret": AUTH_LOGIN_TEST_PASSWORD}, timeout=timeout)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"POST LDAP 登入失敗：{exc}", path=login_path, method="POST"))
        return findings

    if resp.status_code == 401:
        try:
            reason = resp.json().get("reason", "")
        except ValueError:
            reason = ""
        if reason == "auth.invalid-auth":
            findings.append(
                _finding("live_write_skipped", "info", filename, group, "測試帳號不是有效的 LDAP 帳密（這組帳密只給 fedgpt provider 用），跳過這組測試", path=login_path, method="POST")
            )
            return findings

    ok = _check_status_and_schema(op_of(login_path, "post"), resp, findings, filename, group, login_path, "post")
    if not ok or resp.status_code != 200:
        findings.append(_finding("live_write_aborted", "error", filename, group, f"POST LDAP 登入沒有回 200（實際 {resp.status_code}），中止測試", path=login_path, method="POST"))
        return findings

    try:
        new_token = resp.json()["token"]
        if not new_token:
            raise ValueError("token 是空的")
    except Exception as exc:  # noqa: BLE001
        findings.append(_finding("live_write_aborted", "error", filename, group, f"LDAP 登入回應裡拿不到有效的 token：{exc}", path=login_path, method="POST"))
        return findings

    logout_session = requests.Session()
    logout_session.headers.update({"X-Access-Token": new_token, "Content-Type": "application/json"})
    try:
        logout_resp = logout_session.post(f"{base}{logout_path}", timeout=timeout)
        if logout_resp.status_code != 200 or not logout_resp.json().get("success"):
            findings.append(
                _finding("live_write_cleanup_failed", "error", filename, group, f"登出剛才 LDAP 登入拿到的 token 失敗（{logout_resp.status_code}），需要留意這個 token 還有效", path=logout_path, method="POST")
            )
    except requests.RequestException as exc:
        findings.append(_finding("live_write_cleanup_failed", "error", filename, group, f"登出剛才 LDAP 登入拿到的 token 失敗：{exc}，需要留意這個 token 還有效", path=logout_path, method="POST"))

    if not any(f["rule"].startswith("live_write_") and f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, "LDAP 登入成功並已登出清除，測試通過"))

    return findings
