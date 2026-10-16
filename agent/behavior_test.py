"""參數行為規則測試：spec 對某個參數寫明了「省略時是什麼／只接受哪些值／什麼情況回哪個錯誤碼」，
就照它說的各打一次，對照實際結果。

跟 live_write_test.py 的差別：那邊驗證「一條正常流程走不走得通」，這邊驗證「spec 寫的每條規則
是不是真的」——例如 spec 寫 `speed` 只收 1、其他值回 400，就真的送 1.5 看是不是 400。
這類落差（FEDGPT-17373、17374）正常流程的測試抓不到，因為正常流程不會去碰那些值。

每條規則是一筆 case：`rule`（spec 怎麼說）、`request`（這次要改哪些參數）、`check`（拿回應判斷，
符合回傳 None，不符合回傳一句「實際怎樣」）。不符合記一筆 `spec_behavior_mismatch`。

這裡全部都是無狀態呼叫或只讀的 GET，不會建立、修改或刪除任何資料。
"""

import json
import os
import re
import time

import requests
from jsonschema import Draft202012Validator

from .config import HELIX_TEST_AUDIO_PATH
from .live_call import _deref_schema
from .live_write_test import (
    ASR_TEST_MODEL,
    OPENAI_TTS_TEST_MODEL,
    _check_status_and_schema,
    _decode_jwt_user_id,
    _finding,
    _is_permission_denied,
    _load_spec_for_test,
    _make_op_of,
    _make_session,
    _response_excerpt,
)


def _request_id_note(resp):
    request_id = resp.headers.get("X-Request-Id")
    return f"（X-Request-Id: {request_id}）" if request_id else ""


def _content_type(resp):
    return (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()


def _reason(resp):
    try:
        return resp.json().get("reason")
    except (ValueError, AttributeError):
        return None


def _expect_status(code, reason=None):
    """預期某個錯誤碼（可再指定 reason）。"""

    def check(resp):
        if resp.status_code != code:
            return f"實際回 {resp.status_code}"
        if reason and _reason(resp) != reason:
            return f"狀態碼對，但 reason 是 {_reason(resp)!r}，不是 {reason!r}"
        return None

    return check


def _expect_ok(content_type, body_check=None):
    """預期 200、指定的 Content-Type，body_check(resp) 再細看內容（符合回傳 None）。"""

    def check(resp):
        if resp.status_code != 200:
            return f"實際回 {resp.status_code}"
        if content_type and _content_type(resp) != content_type:
            return f"回 200，但 Content-Type 是 {_content_type(resp) or '（沒有帶）'}，不是 {content_type}"
        return body_check(resp) if body_check else None

    return check


def _expect_documented(op):
    """spec 沒寫會回什麼錯誤時，至少要回一個 spec 有宣告的狀態碼。"""
    documented = set((op.get("responses") or {}).keys())

    def check(resp):
        if str(resp.status_code) in documented:
            return None
        return f"實際回 {resp.status_code}，spec 這支只宣告了 {sorted(documented)}"

    return check


def _run_cases(findings, session, base, op, filename, group, path, method, cases, timeout, show_body=True):
    """依序打每一筆 case，回傳符合的筆數。

    每個回應也會順便過一次 `_check_status_and_schema`，所以「回 200 但 body 不符 schema」這種
    會另外記成 response_schema_mismatch（例如 embeddings 的 base64）。
    show_body=False 時訊息裡不貼回應內容，只附 X-Request-Id——API key 清單這種回應裡有憑證。
    """
    passed = 0
    for case in cases:
        url = f"{base}{case.get('path', path)}"
        try:
            resp = session.request(method, url, timeout=timeout, **case["request"]())
        except requests.RequestException as exc:
            findings.append(_finding("live_write_error", "error", filename, group, f"{case['rule']}：呼叫失敗：{exc}", path=path, method=method))
            continue
        problem = case["check"](resp)
        if show_body:
            schema_findings = []
            _check_status_and_schema(op, resp, schema_findings, filename, group, path, method)
            for f in schema_findings:
                # 狀態碼沒宣告的話，下面那筆 spec_behavior_mismatch 已經寫了同樣的事，而且多了規則本身的脈絡
                if problem and f["rule"] == "undocumented_status_code":
                    continue
                f["message"] = _shorten_schema_message(f["message"])
                if f["message"] not in {g["message"] for g in findings if g["path"] == path}:  # 同一個問題每筆 case 都會撞到，只記一次
                    findings.append(f)
        if problem is None:
            passed += 1
            continue
        detail = _response_excerpt(resp) if show_body else _request_id_note(resp)
        findings.append(
            _finding(
                "spec_behavior_mismatch",
                "error",
                filename,
                group,
                f"{case['rule']}——{problem}。{detail}",
                path=path,
                method=method,
                location=case.get("location", ""),
            )
        )
    return passed


def _shorten_schema_message(message, limit=300):
    """jsonschema 的訊息會把整個 instance 貼進來（例如 base64 向量、整份逐字稿），報告裡只留重點。"""
    if "is valid under each of" in message:
        head = message.split("不符合 spec 宣告的 schema：")[0]
        return head + "符合 oneOf 的條件不只一個：回應同時符合 oneOf 裡的多個 schema，但 oneOf 要求恰好符合一個（各 schema 沒有用 additionalProperties 或必填欄位區分開）"
    # 很長的字串值（base64 向量之類）換成長度說明，只留後面「is not of type ...」這段真正的原因
    message = re.sub(r"'([^']{80,})'", lambda m: f"<{len(m.group(1))} 字元的字串>", message)
    return message if len(message) <= limit else message[:limit] + "…"


def _finalize(findings, filename, group, message):
    """沒有任何 error 等級的 finding（行為不符、schema 不符、呼叫失敗）時，補一筆 live_write_ok。"""
    if not any(f["severity"] == "error" for f in findings):
        findings.append(_finding("live_write_ok", "info", filename, group, message))


def _probe_skip(findings, session, base, path, method, filename, group, timeout, label, **kwargs):
    """先打一次正常請求，權限不足或這個部署沒開（404）就跳過整組，回傳 True 代表要跳過。"""
    try:
        resp = session.request(method, f"{base}{path}", timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"{label}：先打一次正常請求就失敗：{exc}", path=path, method=method))
        return True
    if _is_permission_denied(resp):
        findings.append(_finding("live_write_skipped", "info", filename, group, f"{label}：測試帳號沒有權限（{resp.status_code}），跳過這組規則", path=path, method=method))
        return True
    if resp.status_code == 404:
        findings.append(_finding("live_write_skipped", "info", filename, group, f"{label}：這個環境回 404（功能沒開或模型不存在），跳過這組規則。{_response_excerpt(resp)}", path=path, method=method))
        return True
    return False


# ---------------------------------------------------------------- B1 OpenAI 語音合成


def run_openai_speech_behavior_test(entries, api_base_url, token, timeout=60):
    """B1：`POST /llm/v1/fedgpt/v1/audio/speech` 的 response_format／speed／voice／model／instructions／input／stream_format。"""
    findings = []
    filename, group = "openai-v1.yaml", "OpenAI V1"
    spec, paths = _load_spec_for_test(entries, filename, group, findings)
    path = "/api/public/llm/v1/fedgpt/v1/audio/speech"
    if spec is None or path not in paths:
        return findings
    op = _make_op_of(spec, paths)(path, "post")
    session = _make_session(token)
    base = api_base_url.rstrip("/")
    required = {"model": OPENAI_TTS_TEST_MODEL, "input": "[agent-test] Behavior check.", "voice": "yating"}

    def body(**overrides):
        return lambda: {"json": {**required, **overrides}}

    def is_riff(want):
        def check(resp):
            starts = resp.content[:4] == b"RIFF"
            if starts != want:
                return f"開頭 4 bytes 是 {resp.content[:4]!r}，{'應該' if want else '不應該'}是 RIFF header"
            return None if resp.content else "音訊內容是空的"

        return check

    def has_done_event(resp):
        text = resp.content.decode("utf-8", errors="replace")
        if "speech.audio.delta" not in text:
            return "SSE 裡沒有任何 speech.audio.delta"
        if "speech.audio.done" not in text:
            return "SSE 最後沒有 speech.audio.done"
        return None

    cases = [
        {"rule": "response_format=pcm 應回 audio/pcm、沒有 header", "request": body(response_format="pcm"), "check": _expect_ok("audio/pcm", is_riff(False)), "location": "requestBody.response_format"},
        {"rule": "response_format=wav 應回 audio/wav、RIFF header", "request": body(response_format="wav"), "check": _expect_ok("audio/wav", is_riff(True)), "location": "requestBody.response_format"},
        {"rule": "response_format 只有 wav／pcm，mp3 應回 400", "request": body(response_format="mp3"), "check": _expect_status(400, "data.validation"), "location": "requestBody.response_format"},
        {"rule": "speed 只收 1，1.5 應回 400", "request": body(speed=1.5), "check": _expect_status(400, "data.validation"), "location": "requestBody.speed"},
        {"rule": "voice 用 OpenAI 的名字（alloy）應回 400", "request": body(voice="alloy"), "check": _expect_status(400, "data.validation"), "location": "requestBody.voice"},
        {"rule": "model 用 OpenAI 的名字（tts-1）應回 400", "request": body(model="tts-1"), "check": _expect_status(400, "data.validation"), "location": "requestBody.model"},
        {"rule": "instructions 不支援，非空應回 400", "request": body(instructions="calm"), "check": _expect_status(400, "data.validation"), "location": "requestBody.instructions"},
        {"rule": "input 上限 4096 字元，4097 字元應回 400", "request": body(input="a" * 4097), "check": _expect_status(400, "data.validation"), "location": "requestBody.input"},
        {"rule": "stream_format=sse 應回 text/event-stream，以 speech.audio.done 結束", "request": body(stream_format="sse"), "check": _expect_ok("text/event-stream", has_done_event), "location": "requestBody.stream_format"},
    ]
    if _probe_skip(findings, session, base, path, "POST", filename, group, timeout, "audio/speech 行為規則", json=required):
        return findings
    passed = _run_cases(findings, session, base, op, filename, group, path, "POST", cases, timeout)
    _finalize(findings, filename, group, f"audio/speech 的 {passed} 條行為規則全部符合 spec")
    return findings


# ---------------------------------------------------------------- B2 OpenAI 語音轉文字


def run_openai_transcription_behavior_test(entries, api_base_url, token, timeout=120):
    """B2：`POST /llm/v1/fedgpt/v1/audio/transcriptions` 的 response_format 與明確拒絕的參數。"""
    findings = []
    filename, group = "openai-v1.yaml", "OpenAI V1"
    spec, paths = _load_spec_for_test(entries, filename, group, findings)
    path = "/api/public/llm/v1/fedgpt/v1/audio/transcriptions"
    if spec is None or path not in paths or not os.path.exists(HELIX_TEST_AUDIO_PATH):
        return findings
    op = _make_op_of(spec, paths)(path, "post")
    session = requests.Session()  # multipart 要讓 requests 自己帶 boundary
    session.headers.update({"X-Access-Token": token})
    base = api_base_url.rstrip("/")
    with open(HELIX_TEST_AUDIO_PATH, "rb") as fh:
        audio = fh.read()
    audio_name = os.path.basename(HELIX_TEST_AUDIO_PATH)
    verbose_schema = _deref_schema({"$ref": "#/components/schemas/TranscriptionVerbose"}, spec)

    def form(**fields):
        return lambda: {"data": {"model": ASR_TEST_MODEL, "language": "en", **fields}, "files": {"file": (audio_name, audio, "application/octet-stream")}}

    def text_check(pattern, label):
        def check(resp):
            if not resp.text.strip():
                return "內容是空的"
            return None if re.search(pattern, resp.text) else f"內容不是 {label} 格式：{resp.text[:120]!r}"

        return check

    def verbose_check(resp):
        try:
            data = resp.json()
        except ValueError:
            return "回應不是 JSON"
        errors = list(Draft202012Validator(verbose_schema).iter_errors(data))
        if errors:
            return f"不符合 TranscriptionVerbose schema：{errors[0].message[:150]}"
        if data.get("language") != "en":
            return f"spec 寫 language 回顯請求值，送 en 實際回 {data.get('language')!r}"
        return None

    cases = [
        {"rule": "response_format=text 應回 text/plain 純文字逐字稿", "request": form(response_format="text"), "check": _expect_ok("text/plain", text_check(r"\w", "純文字")), "location": "requestBody.response_format"},
        {"rule": "response_format=srt 應回 text/plain 的 SRT（序號從 1、毫秒用逗號）", "request": form(response_format="srt"), "check": _expect_ok("text/plain", text_check(r"\A\s*1\s*\n\d\d:\d\d:\d\d,\d{3} --> ", "SRT")), "location": "requestBody.response_format"},
        {"rule": "response_format=vtt 應回 text/plain 的 WebVTT（WEBVTT 開頭、毫秒用點）", "request": form(response_format="vtt"), "check": _expect_ok("text/plain", text_check(r"\AWEBVTT[\s\S]*\d\d:\d\d\.\d{3} --> ", "WebVTT")), "location": "requestBody.response_format"},
        {"rule": "response_format=verbose_json 應回 TranscriptionVerbose，language 回顯請求值", "request": form(response_format="verbose_json"), "check": _expect_ok("application/json", verbose_check), "location": "requestBody.response_format"},
        {"rule": "temperature 非 0 應明確拒絕回 400", "request": form(temperature="0.5"), "check": _expect_status(400, "data.validation"), "location": "requestBody.temperature"},
        {"rule": "帶了 include[] 應明確拒絕回 400", "request": form(**{"include[]": "logprobs"}), "check": _expect_status(400, "data.validation"), "location": "requestBody.include[]"},
        {"rule": "stream=true 不能搭配 response_format=srt，應回 400", "request": form(stream="true", response_format="srt"), "check": _expect_status(400, "data.validation"), "location": "requestBody.stream"},
        {"rule": "model 用 OpenAI 的名字（whisper-1）應回 404", "request": form(model="whisper-1"), "check": _expect_status(404, "data.not-found"), "location": "requestBody.model"},
    ]
    if _probe_skip(findings, session, base, path, "POST", filename, group, timeout, "audio/transcriptions 行為規則", **form()()):
        return findings
    passed = _run_cases(findings, session, base, op, filename, group, path, "POST", cases, timeout)
    _finalize(findings, filename, group, f"audio/transcriptions 的 {passed} 條行為規則全部符合 spec")
    return findings


# ---------------------------------------------------------------- B3 Embeddings


def run_embeddings_behavior_test(entries, api_base_url, token, timeout=30):
    """B3：`POST /llm/v1/retriever/v1/embeddings` 的 encoding_format 與 dimensions。"""
    findings = []
    filename, group = "openai-v1.yaml", "OpenAI V1"
    spec, paths = _load_spec_for_test(entries, filename, group, findings)
    path = "/api/public/llm/v1/retriever/v1/embeddings"
    if spec is None or path not in paths:
        return findings
    op = _make_op_of(spec, paths)(path, "post")
    session = _make_session(token)
    base = api_base_url.rstrip("/")
    required = {"model": "embedding-v3.0", "input": ["[agent-test] behavior check"]}
    native = {}  # 第一筆（不帶 dimensions）記下原生維度，給後面對照

    def body(**overrides):
        return lambda: {"json": {**required, **overrides}}

    def vector_len(resp):
        try:
            return len(resp.json()["data"][0]["embedding"])
        except Exception:  # noqa: BLE001
            return None

    def record_native(resp):
        native["dim"] = vector_len(resp)
        return None if native["dim"] else "回應裡拿不到 embedding 向量"

    def dims_check(resp):
        got = vector_len(resp)
        if got in (1024, native.get("dim")):
            return None
        return f"送 dimensions=1024，回的向量長度是 {got}（原生維度 {native.get('dim')}）"

    cases = [
        {"rule": "不帶 dimensions 應回模型原生維度", "request": body(), "check": _expect_ok("application/json", record_native), "location": "requestBody.dimensions"},
        {"rule": "dimensions=1024（spec 範例值）應回 1024 維，模型不支援降維就照 spec 說的「不生效」回原生維度", "request": body(dimensions=1024), "check": _expect_ok("application/json", dims_check), "location": "requestBody.dimensions"},
        # 回 200 之後 body 符不符合 schema 由 _run_cases 順便驗（base64 回字串、schema 只宣告陣列 → response_schema_mismatch）
        {"rule": "encoding_format=base64 應回 200", "request": body(encoding_format="base64"), "check": _expect_ok("application/json"), "location": "requestBody.encoding_format"},
    ]
    passed = _run_cases(findings, session, base, op, filename, group, path, "POST", cases, timeout)
    _finalize(findings, filename, group, f"embeddings 的 {passed} 條行為規則全部符合 spec")
    return findings


# ---------------------------------------------------------------- B4 Asura 語音合成

ASURA_ENCODING_CONTENT_TYPES = {
    "LINEAR16": "audio/wav",
    "PCM": "audio/pcm",
    "MP3": "audio/mpeg",
    "OGG_OPUS": "audio/ogg",
    "AAC": "audio/aac",
    "FLAC": "audio/flac",
}


def run_asura_speech_behavior_test(entries, api_base_url, token, timeout=60):
    """B4：`POST /asura/v1/speeches:stream` 的 audioConfig.encoding（決定 Content-Type）與 speed 範圍。"""
    findings = []
    filename, group = "asura-v1.yaml", "Asura V1"
    spec, paths = _load_spec_for_test(entries, filename, group, findings)
    path = "/api/public/asura/v1/speeches:stream"
    if spec is None or path not in paths:
        return findings
    op = _make_op_of(spec, paths)(path, "post")
    session = _make_session(token)
    base = api_base_url.rstrip("/")
    required = {"input": {"text": "[agent-test] 行為規則驗證", "type": "text"}, "modelConfig": {"model": "tts-general-1.3.3", "voice": "yating"}}

    def body(**audio_config):
        return lambda: {"json": {**required, "audioConfig": audio_config}}

    # spec 自己的不一致：encoding 的說明列了 6 種 Content-Type，但 200 的 content 只宣告 audio/wav。
    # 這是寫法問題、不需要打 API 就看得出來，跟下面「實際回什麼」分開記。
    declared = set(((op.get("responses") or {}).get("200") or {}).get("content", {}).keys())
    undeclared = sorted(set(ASURA_ENCODING_CONTENT_TYPES.values()) - declared)
    if undeclared:
        findings.append(
            _finding(
                "spec_description_mismatch",
                "warning",
                filename,
                group,
                f"audioConfig.encoding 的說明寫回應 Content-Type 隨 encoding 變（6 種），但 200 的 content 只宣告了 {sorted(declared)}，少了 {undeclared}",
                path=path,
                method="POST",
                location="responses.200.content",
            )
        )

    cases = [
        {
            "rule": f"encoding={enc} 應回 {ctype}",
            "request": body(encoding=enc),
            "check": _expect_ok(ctype, lambda resp: None if resp.content else "音訊內容是空的"),
            "location": "requestBody.audioConfig.encoding",
        }
        for enc, ctype in ASURA_ENCODING_CONTENT_TYPES.items()
    ] + [
        {"rule": "speed 下限 0.25，0.1 應回 400", "request": body(encoding="LINEAR16", speed=0.1), "check": _expect_status(400), "location": "requestBody.audioConfig.speed"},
        {"rule": "speed 上限 4，5 應回 400", "request": body(encoding="LINEAR16", speed=5), "check": _expect_status(400), "location": "requestBody.audioConfig.speed"},
    ]
    if _probe_skip(findings, session, base, path, "POST", filename, group, timeout, "speeches:stream 行為規則", **body(encoding="LINEAR16")()):
        return findings
    passed = _run_cases(findings, session, base, op, filename, group, path, "POST", cases, timeout)
    _finalize(findings, filename, group, f"speeches:stream 的 {passed} 條行為規則全部符合 spec")
    return findings


# ---------------------------------------------------------------- B5 分頁與排序（只讀）


def _find_value(obj, key, depth=0):
    """在 JSON 裡找第一個叫 key 的字串值。"""
    if depth > 5:
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get(key), str):
            return obj[key]
        values = obj.values()
    elif isinstance(obj, list):
        values = obj[:10]
    else:
        return None
    for value in values:
        found = _find_value(value, key, depth + 1)
        if found:
            return found
    return None


def _expect_page(list_key, max_items, echo_limit=True):
    """不帶 limit 時，最多 max_items 筆，回應的 limit 欄位也應該是預設值。"""

    def check(resp):
        try:
            data = resp.json()
        except ValueError:
            return "回應不是 JSON"
        items = data.get(list_key) or []
        if len(items) > max_items:
            return f"不帶 limit 回了 {len(items)} 筆，超過預設的 {max_items}"
        if echo_limit and data.get("limit") != max_items:
            return f"回應的 limit 欄位是 {data.get('limit')!r}，不是預設的 {max_items}"
        return None

    return _expect_ok("application/json", check)


def _expect_order(list_key, ascending):
    def check(resp):
        try:
            stamps = [it.get("createdAt") for it in resp.json().get(list_key) or []]
        except ValueError:
            return "回應不是 JSON"
        stamps = [s for s in stamps if isinstance(s, (int, float))]
        if len(stamps) < 2:
            return None  # 筆數不夠看不出順序，不算不符
        ok = stamps == sorted(stamps, reverse=not ascending)
        return None if ok else f"createdAt 不是{'由舊到新' if ascending else '由新到舊'}：{stamps[:5]}"

    return _expect_ok("application/json", check)


def run_pagination_behavior_test(entries, api_base_url, token, timeout=30):
    """B5：列表類 GET 的 limit／page 範圍、預設筆數、order 排序方向。只讀，不動資料。"""
    findings = []
    session = _make_session(token)
    base = api_base_url.rstrip("/")

    def get(**params):
        return lambda: {"params": params}

    groups = []

    # admin：別人的（這裡是自己的）API key 清單
    admin_file, admin_group = "admin-v1.yaml", "Admin V1"
    user_id = _decode_jwt_user_id(token)
    spec, paths = _load_spec_for_test(entries, admin_file, admin_group, findings)
    template = "/api/public/admin/v1/apikeys/{userId}"
    if spec and template in paths and user_id:
        groups.append(
            (admin_file, admin_group, spec, paths, template, template.replace("{userId}", user_id), False, [
                {"rule": "limit 上限 100，101 應回 400 data.validation", "request": get(limit=101), "check": _expect_status(400, "data.validation"), "location": "parameters.limit"},
                {"rule": "limit 下限 1，0 應回 400 data.validation", "request": get(limit=0), "check": _expect_status(400, "data.validation"), "location": "parameters.limit"},
                {"rule": "page 從 1 開始，0 應回 400 data.validation", "request": get(page=0), "check": _expect_status(400, "data.validation"), "location": "parameters.page"},
                {"rule": "不帶 limit 預設 10 筆", "request": get(), "check": _expect_page("items", 10), "location": "parameters.limit"},
                {"rule": "order=created_at 應由舊到新", "request": get(order="created_at", limit=100), "check": _expect_order("items", True), "location": "parameters.order"},
                {"rule": "不帶 order 預設 -created_at，應由新到舊", "request": get(limit=100), "check": _expect_order("items", False), "location": "parameters.order"},
            ])
        )

    # auth：自己的 API key 清單（spec 沒寫範圍、沒宣告 400，只測預設與排序）
    auth_file, auth_group = "auth-v2.yaml", "Auth V2"
    spec, paths = _load_spec_for_test(entries, auth_file, auth_group, findings)
    auth_path = "/api/public/auth/v2/apikeys"
    if spec and auth_path in paths:
        groups.append(
            (auth_file, auth_group, spec, paths, auth_path, auth_path, False, [
                {"rule": "不帶 limit 預設 10 筆", "request": get(), "check": _expect_page("items", 10), "location": "parameters.limit"},
                {"rule": "order=created_at 應由舊到新", "request": get(order="created_at", limit=100), "check": _expect_order("items", True), "location": "parameters.order"},
                {"rule": "不帶 order 預設 -created_at，應由新到舊", "request": get(limit=100), "check": _expect_order("items", False), "location": "parameters.order"},
            ])
        )

    # knowledge：知識庫的文件清單，知識庫從清單裡挑第一個
    kb_file, kb_group = "knowledge-v3.yaml", "Knowledge V3"
    spec, paths = _load_spec_for_test(entries, kb_file, kb_group, findings)
    template = "/api/public/knowledge/v3/knowledges/{knowledgeId}/documents"
    if spec and template in paths:
        knowledge_id = None
        try:
            resp = session.get(f"{base}/api/public/knowledge/v3/knowledges", params={"limit": 10}, timeout=timeout)
            knowledge_id = _find_value(resp.json(), "knowledgeId") if resp.ok else None
        except (requests.RequestException, ValueError):
            pass
        if knowledge_id:
            groups.append(
                (kb_file, kb_group, spec, paths, template, template.replace("{knowledgeId}", knowledge_id), True, [
                    {"rule": "limit 上限 100，101 應回 400 data.validation", "request": get(limit=101), "check": _expect_status(400, "data.validation"), "location": "parameters.limit"},
                    {"rule": "page 從 1 開始，0 應回 400 data.validation", "request": get(page=0), "check": _expect_status(400, "data.validation"), "location": "parameters.page"},
                    {"rule": "不帶 limit 預設 10 筆", "request": get(), "check": _expect_page("documents", 10), "location": "parameters.limit"},
                ])
            )
        else:
            findings.append(_finding("live_write_skipped", "info", kb_file, kb_group, "測試帳號看不到任何知識庫，跳過文件清單的分頁規則", path=template, method="GET"))

    # chat：對話清單與訊息清單
    chat_file, chat_group = "chat-v2.yaml", "Chat V2"
    spec, paths = _load_spec_for_test(entries, chat_file, chat_group, findings)
    conv_path = "/api/public/chat/v2/conversations"
    if spec and conv_path in paths:
        conv_op = _make_op_of(spec, paths)(conv_path, "get")
        groups.append(
            (chat_file, chat_group, spec, paths, conv_path, conv_path, True, [
                {"rule": "limit 是必填，不帶時應回 spec 有宣告的狀態碼", "request": get(), "check": _expect_documented(conv_op), "location": "parameters.limit"},
                {"rule": "query 上限 200 字，201 字時應回 spec 有宣告的狀態碼", "request": get(limit=5, query="a" * 201), "check": _expect_documented(conv_op), "location": "parameters.query"},
            ])
        )
        msg_template = "/api/public/chat/v2/conversations/{convId}/messages"
        conv_id = None
        try:
            # 挑一個至少有 2 則訊息的對話，才看得出排序
            resp = session.get(f"{base}{conv_path}", params={"limit": 10}, timeout=timeout)
            for conv in (resp.json().get("conversations") or []) if resp.ok else []:
                cid = conv.get("convId")
                r = session.get(f"{base}{msg_template.replace('{convId}', cid)}", params={"limit": 2}, timeout=timeout) if cid else None
                if r is not None and r.ok and len(r.json().get("messages") or []) >= 2:
                    conv_id = cid
                    break
        except (requests.RequestException, ValueError):
            pass
        if conv_id and msg_template in paths:
            groups.append(
                (chat_file, chat_group, spec, paths, msg_template, msg_template.replace("{convId}", conv_id), True, [
                    {"rule": "order=created_at 應由舊到新", "request": get(limit=20, order="created_at"), "check": _expect_order("messages", True), "location": "parameters.order"},
                    {"rule": "不帶 order 預設 -created_at，應由新到舊", "request": get(limit=20), "check": _expect_order("messages", False), "location": "parameters.order"},
                ])
            )
        else:
            findings.append(_finding("live_write_skipped", "info", chat_file, chat_group, "找不到有 2 則以上訊息的對話，跳過訊息清單的排序規則", path=msg_template, method="GET"))

    for filename, group, spec, paths, template, real_path, show_body, cases in groups:
        op = _make_op_of(spec, paths)(template, "get")
        if _probe_skip(findings, session, base, real_path, "GET", filename, group, timeout, f"{template} 分頁規則", params={"limit": 1} if "conversations" in template else {}):
            continue
        for case in cases:
            case["path"] = real_path
        passed = _run_cases(findings, session, base, op, filename, group, template, "GET", cases, timeout, show_body=show_body)
        if not any(f["severity"] == "error" and f["path"] == template for f in findings):
            findings.append(_finding("live_write_ok", "info", filename, group, f"{template} 的 {passed} 條分頁／排序規則全部符合 spec", path=template, method="GET"))
    return findings


# ---------------------------------------------------------------- 不帶 token 的 401

NO_TOKEN_FAKE_ID = "agent-test-nonexistent"
# 漏了驗證的話，這幾支就算 path 參數是假的也可能真的執行（reset 不吃 body、會清掉共用環境的套件），所以不打
NO_TOKEN_EXCLUDED = {
    ("POST", "/api/public/fedflow/v1/packages/{language}"),
    ("DELETE", "/api/public/fedflow/v1/packages/{language}"),
    ("POST", "/api/public/fedflow/v1/packages/{language}/reset"),
}


def run_no_token_behavior_test(entries, api_base_url, token, timeout=20):
    """每支需要驗證的端點都不帶憑證打一次，驗證 Common 分頁「認證與授權」寫的規則：
    完全沒帶憑證回 401、reason 是 auth.no-auth、body 是標準的錯誤格式。

    大部分端點的 responses 沒有逐支列 401，是統一寫在 Common 分頁，所以這裡把 Common 當成宣告，
    不會因為「這支沒列 401」而報錯。有逐支列 401 的（OpenAI V1、部分 Auth V2）就對它自己宣告的 schema 驗，
    其餘對該檔案的 components.schemas.Error 驗。

    不帶 token、不送 body，path 參數一律換成不存在的假 ID：萬一某支真的漏了驗證，最壞也只會
    拿到 400／404，不會動到真實資料。token 參數不使用（介面跟其他測試一致）。
    """
    findings = []
    session = requests.Session()  # 刻意不用 _make_session：不能帶任何憑證
    base = api_base_url.rstrip("/")
    by_file = {}
    for entry in entries:
        spec = entry.get("spec")
        if not entry.get("is_real") or not spec:
            continue
        global_security = spec.get("security")
        error_schema = ((spec.get("components") or {}).get("schemas") or {}).get("Error")
        for path, item in (spec.get("paths") or {}).items():
            for method, op in item.items():
                method_upper = method.upper()
                if not isinstance(op, dict) or method_upper not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                security = op.get("security", global_security)
                if not security or security == [{}] or (method_upper, path) in NO_TOKEN_EXCLUDED:
                    continue  # 本來就不用驗證（providers、登入），或刻意不打
                filename, group = entry["filename"], entry["group"]
                stats = by_file.setdefault((filename, group), [0, 0])
                stats[1] += 1
                real_path = re.sub(r"\{[^}]+\}", NO_TOKEN_FAKE_ID, path)
                try:
                    resp = session.request(method_upper, f"{base}{real_path}", timeout=timeout, allow_redirects=False)
                except requests.RequestException as exc:
                    findings.append(_finding("live_write_error", "error", filename, group, f"不帶 token 呼叫失敗：{exc}", path=path, method=method_upper))
                    continue
                problem = None
                if resp.status_code != 401:
                    problem = f"實際回 {resp.status_code}"
                else:
                    try:
                        body = resp.json()
                    except ValueError:
                        body = None
                    resp_401 = (op.get("responses") or {}).get("401") or {}
                    declared = (((resp_401.get("content") or {}).get("application/json")) or {}).get("schema")
                    schema = declared or error_schema
                    # 端點自己的 401 說明寫了 reason 的話以它為準（例如 apikeys:self 只收 X-Api-Key，沒帶時寫明是 auth.invalid-auth）
                    expected_reasons = set(re.findall(r"auth\.[a-z-]+", resp_401.get("description") or "")) or {"auth.no-auth"}
                    if body is None:
                        problem = "回 401，但 body 不是 JSON"
                    elif body.get("reason") not in expected_reasons:
                        problem = f"回 401，但 reason 是 {body.get('reason')!r}，不是 {' / '.join(sorted(expected_reasons))}"
                    elif schema:
                        errors = list(Draft202012Validator(_deref_schema(schema, spec)).iter_errors(body))
                        if errors:
                            loc = ".".join(str(p) for p in errors[0].path) or "(root)"
                            problem = f"回 401，但 body 在 {loc} 不符合錯誤格式的 schema：{_shorten_schema_message(errors[0].message, 150)}"
                if problem:
                    findings.append(
                        _finding(
                            "spec_behavior_mismatch",
                            "error",
                            filename,
                            group,
                            f"Common 分頁寫完全沒帶憑證回 401 auth.no-auth——不帶 token 呼叫，{problem}。{_response_excerpt(resp)}",
                            path=path,
                            method=method_upper,
                            location="security",
                        )
                    )
                else:
                    stats[0] += 1
    for (filename, group), (passed, total) in sorted(by_file.items()):
        if passed == total:
            findings.append(_finding("live_write_ok", "info", filename, group, f"不帶 token 的 {total} 支端點都正確回 401 auth.no-auth"))
    return findings


# ---------------------------------------------------------------- API key scope（受限 key）

APIKEYS_PATH = "/api/public/auth/v2/apikeys"
APIKEYS_SELF_PATH = "/api/public/auth/v2/apikeys:self"
APIKEYS_BATCH_DELETE_PATH = "/api/public/auth/v2/apikeys:batchDelete"
SCOPE_TEST_NOTE = "[agent-test] scope 驗證，測完會刪除"
# 矩陣裡不打的端點：漏了 scope 檢查的話會真的生效（套件、登出、刪除帳號唯一的聲紋註冊），
# 或是會把測試用的 key 自己刪掉（apikeys:self，另外單獨驗）
SCOPE_MATRIX_EXCLUDED = NO_TOKEN_EXCLUDED | {
    ("DELETE", APIKEYS_SELF_PATH),
    ("POST", "/api/public/auth/v2/logout"),
    ("DELETE", "/api/public/helix/v1/enrollment"),
}


def _scopes_for(method, path):
    """依 ApikeyScope 說明的對照表，回傳哪些 scope 開放這支端點（空集合＝沒有任何 scope 開放）。

    對照表只列 /api/public/ 底下的入口；無前綴的相容路徑（目前只有 /api/fedflow/v1/flows/summary）
    spec 明寫「不接受任何受限 key」。
    """
    if not path.startswith("/api/public/"):
        return set()
    if path.startswith("/api/public/llm/v1/"):
        return {"openai"}
    if path.startswith("/api/public/knowledge/v3/"):
        return {"kb:read"} if method == "GET" else {"kb:write"}
    if path.startswith("/api/public/fedflow/v1/") and "/packages/" not in path:
        return {"flow:execute"}
    if method == "POST" and path == "/api/public/asset/v2/assets:presign":
        return {"asset:upload"}
    return set()


def run_apikey_scope_behavior_test(entries, api_base_url, token, timeout=20):
    """受限 API key（帶 scopes 建立）的行為：

    1. 建立 key 時 spec 寫明會拒絕的值（scopes 空陣列／重複／不在列舉、note 超過 128 字、expiredAt 的四條規則）
       都要回 400；萬一後端沒擋、真的建出 key，記下來一起清掉。
    2. 建 3 把臨時受限 key：A = [openai, kb:read]、B = [kb:write]、C = [flow:execute, asset:upload]（5 種 scope 都涵蓋），確認清單回顯的 scopes／note 跟建立時一致。
    3. 每把 key 各自把所有需要驗證的端點打一輪（矩陣）：scope 有開放的不能回 403 apikey.scope-forbidden，
       沒開放的要回 403 apikey.scope-forbidden，message 要寫這支接受哪些 scope（沒有就是 none），body 符合錯誤格式。
       POST／PUT 一律送空物件、path 參數換成假 ID——scope 沒生效的話最壞也只會拿到 400／404，不會建立資料。
    4. 開放的端點用正常參數打一次要回 200（含 OpenAI V1 的 Authorization: Bearer 寫法）。
    5. 用 key 自己呼叫 apikeys:self 刪除，之後同一把 key 立刻失效（401）；沒確認刪掉的用 access token 批次補刪。

    另外靜態比對每支端點 403 說明裡寫的 scope 跟 ApikeyScope 對照表有沒有矛盾。key 是憑證，訊息只印最後 4 碼。
    """
    findings = []
    filename, group = "auth-v2.yaml", "Auth V2"
    auth_spec, auth_paths = _load_spec_for_test(entries, filename, group, findings)
    if auth_spec is None or APIKEYS_SELF_PATH not in auth_paths:
        return findings
    session = _make_session(token)
    base = api_base_url.rstrip("/")
    created = []  # 這個測試建出來的所有 key（含不該建成功卻建成功的），最後統一確認清除

    def label(key):
        return f"…{key[-4:]}"

    def create_key(**body):
        payload = {"number": 1, "note": SCOPE_TEST_NOTE, **body}
        resp = session.post(f"{base}{APIKEYS_PATH}", json=payload, timeout=timeout)
        keys = []
        if resp.status_code == 200:
            try:
                keys = resp.json().get("keys") or []
            except ValueError:
                pass
            created.extend(keys)
        return resp, keys

    def key_items():
        r = session.get(f"{base}{APIKEYS_PATH}", params={"limit": 100}, timeout=timeout)
        return {it.get("key"): it for it in (r.json().get("items") or [])}

    try:
        # ---- 1. 建立時的驗證規則
        now = int(time.time())
        create_rules = [
            ("scopes 不能是空陣列", {"expiredAt": now + 3600, "scopes": []}, "data.validation"),
            ("scopes 不能有重複值", {"expiredAt": now + 3600, "scopes": ["kb:read", "kb:read"]}, "data.validation"),
            ("scopes 不能有不在列舉內的值", {"expiredAt": now + 3600, "scopes": ["agent-test:unknown"]}, "data.validation"),
            ("note 最多 128 字", {"expiredAt": now + 3600, "note": "a" * 129}, "data.validation"),
            ("expiredAt 必填", {}, "apikey.expiry-invalid"),
            ("expiredAt 不能是過去的時間", {"expiredAt": now - 60}, "apikey.expiry-invalid"),
            ("expiredAt 不能超過現在起算約一年", {"expiredAt": now + 400 * 86400}, "apikey.expiry-invalid"),
            ("expiredAt 填 0 不代表永不過期，會被當成沒填而拒絕", {"expiredAt": 0}, "apikey.expiry-invalid"),
        ]
        for rule, body, reason in create_rules:
            resp, keys = create_key(**body)
            if resp.status_code == 400 and "quota-exceeded" in resp.text:
                findings.append(_finding("live_write_skipped", "info", filename, group, "測試帳號的 API key 數量已達上限（25 把），跳過 scope 測試", path=APIKEYS_PATH, method="POST"))
                return findings
            if resp.status_code != 400 or _reason(resp) != reason:
                extra = f"，建出了 {len(keys)} 把 key（會在測試結束時刪除）" if keys else ""
                findings.append(
                    _finding("spec_behavior_mismatch", "error", filename, group, f"{rule}，應回 400 {reason}——實際回 {resp.status_code}（reason={_reason(resp)!r}）{extra}。{_request_id_note(resp)}", path=APIKEYS_PATH, method="POST", location="requestBody")
                )

        # ---- 2. 建兩把受限 key
        scope_sets = {"A": ["openai", "kb:read"], "B": ["kb:write"], "C": ["flow:execute", "asset:upload"]}
        keys = {}
        for name, scopes in scope_sets.items():
            resp, new_keys = create_key(expiredAt=now + 3600, scopes=scopes)
            if resp.status_code == 400 and "quota-exceeded" in resp.text:
                findings.append(_finding("live_write_skipped", "info", filename, group, "測試帳號的 API key 數量已達上限（25 把），跳過 scope 測試", path=APIKEYS_PATH, method="POST"))
                return findings
            if resp.status_code != 200 or not new_keys:
                findings.append(_finding("live_write_aborted", "error", filename, group, f"建立受限 key（scopes={scopes}）沒有成功（{resp.status_code}），中止 scope 測試。{_request_id_note(resp)}", path=APIKEYS_PATH, method="POST"))
                return findings
            keys[name] = new_keys[0]
        items = key_items()
        for name, scopes in scope_sets.items():
            item = items.get(keys[name]) or {}
            if sorted(item.get("scopes") or []) != sorted(scopes) or item.get("note") != SCOPE_TEST_NOTE:
                findings.append(
                    _finding("spec_behavior_mismatch", "error", filename, group, f"清單裡 key {label(keys[name])} 的 scopes={item.get('scopes')!r}、note={item.get('note')!r}，跟建立時送的 scopes={scopes}、note 不一致", path=APIKEYS_PATH, method="GET")
                )

        # ---- 3. 矩陣：每把 key 打一輪所有需要驗證的端點
        scope_session = requests.Session()  # 不帶 access token，只用 X-Api-Key
        stats = {name: [0, 0] for name in scope_sets}
        for entry in entries:
            spec = entry.get("spec")
            if not entry.get("is_real") or not spec:
                continue
            efile, egroup = entry["filename"], entry["group"]
            global_security = spec.get("security")
            error_schema = ((spec.get("components") or {}).get("schemas") or {}).get("Error")
            for path, item in (spec.get("paths") or {}).items():
                for method, op in item.items():
                    method_upper = method.upper()
                    if not isinstance(op, dict) or method_upper not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                        continue
                    security = op.get("security", global_security)
                    if not security or security == [{}] or (method_upper, path) in SCOPE_MATRIX_EXCLUDED:
                        continue
                    allowed_by = _scopes_for(method_upper, path)
                    resp_403 = (op.get("responses") or {}).get("403") or {}
                    # 靜態：這支 403 說明裡寫的 scope 跟對照表矛盾的話記下來（說明沒提 scope 的不算，Common 分頁有統一寫）
                    doc_scopes = set(re.findall(r"`([a-z]+:[a-z-]+|openai)`", resp_403.get("description") or "")) & {"openai", "kb:read", "kb:write", "flow:execute", "asset:upload"}
                    # 無前綴路徑的 403 說明會順帶提到「帶 /public 的那條要 flow:execute」，不拿來比
                    if doc_scopes and doc_scopes != allowed_by and path.startswith("/api/public/"):
                        findings.append(
                            _finding("spec_description_mismatch", "warning", efile, egroup, f"這支的 403 說明寫需要 {sorted(doc_scopes)}，但 ApikeyScope 對照表推出來是 {sorted(allowed_by) or '沒有任何 scope'}", path=path, method=method_upper, location="responses.403")
                        )
                    real_path = re.sub(r"\{[^}]+\}", NO_TOKEN_FAKE_ID, path)
                    for name, scopes in scope_sets.items():
                        stats[name][1] += 1
                        should_allow = bool(allowed_by & set(scopes))
                        kwargs = {"json": {}} if method_upper in ("POST", "PUT", "PATCH") else {}
                        try:
                            resp = scope_session.request(method_upper, f"{base}{real_path}", headers={"X-Api-Key": keys[name]}, timeout=timeout, allow_redirects=False, **kwargs)
                        except requests.RequestException as exc:
                            findings.append(_finding("live_write_error", "error", efile, egroup, f"用受限 key {name} 呼叫失敗：{exc}", path=path, method=method_upper))
                            continue
                        blocked = resp.status_code == 403 and _reason(resp) == "apikey.scope-forbidden"
                        problem = None
                        if should_allow and blocked:
                            problem = f"key {name}（scopes={scopes}）有開放這支（{sorted(allowed_by)}），卻回 403 apikey.scope-forbidden"
                        elif not should_allow and not blocked:
                            problem = f"key {name}（scopes={scopes}）沒有開放這支（需要 {sorted(allowed_by) or '沒有任何 scope 開放'}），應回 403 apikey.scope-forbidden，實際回 {resp.status_code}（reason={_reason(resp)!r}）"
                        elif blocked:
                            try:
                                body = resp.json()
                            except ValueError:
                                body = {}
                            message = body.get("message") or ""
                            wanted = sorted(allowed_by) or ["none"]
                            if not all(w in message for w in wanted):
                                problem = f"回 403 apikey.scope-forbidden，但 message 沒寫這支接受的 scope（應提到 {', '.join(wanted)}）：{message[:120]!r}"
                            else:
                                schema = ((((resp_403.get("content") or {}).get("application/json")) or {}).get("schema")) or error_schema
                                errors = list(Draft202012Validator(_deref_schema(schema, spec)).iter_errors(body)) if schema else []
                                if errors:
                                    problem = f"回 403，但 body 不符合錯誤格式的 schema：{_shorten_schema_message(errors[0].message, 150)}"
                        if problem:
                            findings.append(
                                _finding("spec_behavior_mismatch", "error", efile, egroup, f"{problem}。{_response_excerpt(resp)}", path=path, method=method_upper, location="security / ApikeyScope")
                            )
                        else:
                            stats[name][0] += 1

        # ---- 4. 開放的端點用正常參數要回 200（含 Bearer 寫法）
        positive = [
            ("key A（kb:read）列出知識庫", "/api/public/knowledge/v3/knowledges", {"X-Api-Key": keys["A"]}, {"limit": 1}),
            ("key A（openai）列出模型", "/api/public/llm/v1/fedgpt/v1/models", {"X-Api-Key": keys["A"]}, {}),
            ("key A 改用 Authorization: Bearer 列出模型", "/api/public/llm/v1/fedgpt/v1/models", {"Authorization": f"Bearer {keys['A']}"}, {}),
            ("key C（flow:execute）列出 flow", "/api/public/fedflow/v1/flows/summary", {"X-Api-Key": keys["C"]}, {}),
        ]
        for rule, path, headers, params in positive:
            resp = scope_session.get(f"{base}{path}", headers=headers, params=params, timeout=timeout)
            if resp.status_code != 200:
                findings.append(_finding("spec_behavior_mismatch", "error", filename, group, f"{rule}，應回 200——實際回 {resp.status_code}。{_response_excerpt(resp)}", path=path, method="GET", location="security"))

        # ---- 5. 用 key 自己刪除，之後立刻失效
        for name in scope_sets:
            resp = scope_session.delete(f"{base}{APIKEYS_SELF_PATH}", headers={"X-Api-Key": keys[name]}, timeout=timeout)
            if resp.status_code != 200:
                findings.append(_finding("spec_behavior_mismatch", "error", filename, group, f"受限 key {name} 呼叫 apikeys:self 刪除自己，應回 200——實際回 {resp.status_code}。{_response_excerpt(resp)}", path=APIKEYS_SELF_PATH, method="DELETE"))
                continue
            after = scope_session.get(f"{base}/api/public/knowledge/v3/knowledges", headers={"X-Api-Key": keys[name]}, params={"limit": 1}, timeout=timeout)
            if after.status_code != 401:
                findings.append(_finding("spec_behavior_mismatch", "error", filename, group, f"spec 寫刪除後立刻失效，但 key {name} 刪除後再呼叫回 {after.status_code}，不是 401。{_request_id_note(after)}", path=APIKEYS_SELF_PATH, method="DELETE"))

        for name, (passed, total) in stats.items():
            if passed == total:
                findings.append(_finding("live_write_ok", "info", filename, group, f"受限 key {name}（scopes={scope_sets[name]}）打 {total} 支端點，放行與擋下都符合 ApikeyScope 對照表"))
    except (requests.RequestException, ValueError) as exc:
        findings.append(_finding("live_write_error", "error", filename, group, f"scope 測試中途失敗：{exc}", path=APIKEYS_PATH))
    finally:
        # 清理：清單裡還找得到的（apikeys:self 沒刪掉、或不該建成功卻建成功的）用 access token 批次刪
        if created:
            try:
                remaining = [k for k in created if k in key_items()]
                if remaining:
                    session.delete(f"{base}{APIKEYS_BATCH_DELETE_PATH}", params={"keys": remaining}, timeout=timeout)
                    still = [k for k in remaining if k in key_items()]
                    if still:
                        raise RuntimeError(f"批次刪除後仍找得到 {', '.join(label(k) for k in still)}")
            except Exception as exc:  # noqa: BLE001
                findings.append(_finding("live_write_cleanup_failed", "error", filename, group, f"清除 scope 測試建立的 key 失敗：{exc}，這些 key 一小時內才會過期，需要手動清理", path=APIKEYS_BATCH_DELETE_PATH, method="DELETE"))
    _finalize(findings, filename, group, "API key scope 的建立規則、放行／擋下矩陣、Bearer 寫法、自我刪除後失效全部符合 spec，測試用的 key 已清除")
    return findings
