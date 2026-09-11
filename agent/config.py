import os

from dotenv import load_dotenv

load_dotenv()  # 從專案根目錄的 .env 讀取本機密鑰（.env 不進 git，見 .gitignore）

DEFAULT_BASE_URL = "https://fedgpt-dev.corp.ailabs.tw/swagger/docs"

# (group name, filename, is_real_endpoints)
# Common / SSE 是說明用分頁，本身沒有要檢查的真實 endpoint，僅用來取共用的 Error schema 等定義。
SPEC_FILES = [
    ("Common", "0.yaml", False),
    ("SSE 串流", "sse.yaml", False),
    ("Admin V1", "admin-v1.yaml", True),
    ("Asset V2", "asset-v2.yaml", True),
    ("Asura V1", "asura-v1.yaml", True),
    ("Auth V2", "auth-v2.yaml", True),
    ("Chat V2", "chat-v2.yaml", True),
    ("FAQ V1", "faq-v1.yaml", True),
    ("FedFlow V1", "fedflow-v1.yaml", True),
    ("Helix V1", "helix-v1.yaml", True),
    ("Knowledge V3", "knowledge-v3.yaml", True),
    ("LLM V1", "llm-v1.yaml", True),
]

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}

# 允許不帶 /public/ 前綴、但已知是刻意保留的相容路徑（file, path）。
# 目前只發現 fedflow-v1.yaml 這一條，spec 內文有明講原因。新增例外前請先確認 description 有沒有講清楚。
KNOWN_NON_PUBLIC_EXCEPTIONS = {
    ("fedflow-v1.yaml", "/fedflow/v1/flows/summary"),
}

GOOGLE_CHAT_WEBHOOK_URL = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL", "")
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")

REQUEST_TIMEOUT_SECONDS = 20

# --- Live call（第二階段，目前只做 GET）---
# Token 是各環境各自獨立的（帳號資料庫沒有共用），一個環境一個變數，用 --token 或這裡切換。
FEDGPT_ACCESS_TOKEN = os.environ.get("FEDGPT_ACCESS_TOKEN", "")  # dev
FEDGPT_STG2_TOKEN = os.environ.get("FEDGPT_STG2_TOKEN", "")  # stg2


def derive_api_base_url(swagger_docs_base_url):
    """從 swagger docs 的 base URL 推回實際打 API 要用的 origin + /api。

    例：https://fedgpt-dev.corp.ailabs.tw/swagger/docs -> https://fedgpt-dev.corp.ailabs.tw/api
    （對應每份 spec 裡 servers[0].url: /api 這個相對路徑）
    """
    origin = swagger_docs_base_url.split("/swagger/")[0]
    return origin.rstrip("/") + "/api"
