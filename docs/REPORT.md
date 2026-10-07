# Public Swagger 稽核報告（stg2-vm1）

> 這份檔案由 `run_check.py` 每次執行自動覆寫，請勿手動編輯。完整細節請看同一次執行產生的 `reports/FedGPT-API-Review-*.html`。

- 最後更新：2026-10-07 15:49:53 +0800
- 來源：https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/
- 這次跑的階段：靜態比對、Live GET、LLM 審查

| 整體結果 | Pass | Fail | Endpoint 總數 | Pass 率 |
|---|---|---|---|---|
| **FAIL** | 79 | 6 | 85 | 92.9% |

| 總發現數 | Error | Warning | Info | 無發現的分頁 |
|---|---|---|---|---|
| 145 | 18 | 44 | 83 | 0/10 |

> ⚠️ 這次沒有跑 `--live-write`，只有寫入測試才打得到的已知問題不會反映在 Pass 率裡。

## 執行歷史

| 時間 | 環境 | live-write | Pass 率 | Pass/總數 | Error | Warning | Info |
|---|---|---|---|---|---|---|---|
| 2026-10-07 15:49:53 +0800 | stg2-vm1 | — | 92.9% | 79/85 | 18 | 44 | 83 |
| 2026-10-07 15:18:04 +0800 | stg2-vm1 | ✓ | 80.0% | 68/85 | 39 | 52 | 140 |
| 2026-10-06 18:03:05 +0800 | stg2-vm1 | ✓ | 78.0% | 64/82 | 49 | 48 | 119 |
| 2026-10-05 14:36:36 +0800 | stg2-vm1 | ✓ | 79.3% | 65/82 | 48 | 50 | 116 |
| 2026-10-01 18:13:08 +0800 | stg2-vm1 | ✓ | 77.8% | 63/81 | 49 | 47 | 114 |
| 2026-10-01 10:02:16 +0800 | stg2-vm1 | ✓ | 79.0% | 64/81 | 48 | 47 | 114 |
| 2026-10-01 09:37:51 +0800 | stg2-vm1 | ✓ | 77.8% | 63/81 | 49 | 47 | 114 |
| 2026-09-30 11:41:34 +0800 | stg2-vm1 | ✓ | 77.8% | 63/81 | 45 | 48 | 114 |
| 2026-09-30 11:12:54 +0800 | stg2-vm1 | — | 93.8% | 76/81 | 23 | 39 | 80 |
| 2026-09-29 15:06:44 +0800 | stg2-vm1 | ✓ | 77.8% | 63/81 | 45 | 48 | 124 |
| 2026-09-29 11:43:59 +0800 | stg2-vm1 | — | 93.8% | 76/81 | 19 | 40 | 80 |

## 受檢規格

| 分頁 | 檔案 | 狀態 |
|---|---|---|
| Common | `0.yaml` | 說明用分頁 |
| SSE 串流 | `sse.yaml` | 說明用分頁 |
| Admin V1 | `admin-v1.yaml` | 3 個發現 |
| Asset V2 | `asset-v2.yaml` | 1 個發現 |
| Asura V1 | `asura-v1.yaml` | 7 個發現 |
| Auth V2 | `auth-v2.yaml` | 9 個發現 |
| Chat V2 | `chat-v2.yaml` | 37 個發現 |
| FAQ V1 | `faq-v1.yaml` | 12 個發現 |
| FedFlow V1 | `fedflow-v1.yaml` | 43 個發現 |
| Helix V1 | `helix-v1.yaml` | 7 個發現 |
| Knowledge V3 | `knowledge-v3.yaml` | 13 個發現 |
| OpenAI V1 | `openai-v1.yaml` | 13 個發現 |

## Error（16 筆，依根因合併）

| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |
|---|---|---|---|---|---|---|
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations` | `responses.200.conversations.N` | 實際回應在 conversations.N 不符合 spec 宣告的 schema：'disabled' is a required property | `response_schema_mismatch` | 5 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/faqs` | `responses.200.faqs.N` | 實際回應在 faqs.N 不符合 spec 宣告的 schema：'description' is a required property | `response_schema_mismatch` | 5 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}` | `responses.200.conversation` | 實際回應在 conversation 不符合 spec 宣告的 schema：'disabled' is a required property | `response_schema_mismatch` | 1 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}/messages` | `responses.200.flowMessages.items.N.sseEvents.N.content.role` | 實際回應在 flowMessages.items.N.sseEvents.N.content.role 不符合 spec 宣告的 schema：'' is not one of ['user', 'assistant', 'tool', 'system'] | `response_schema_mismatch` | 5 |

## Warning（42 筆，依根因合併）

| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |
|---|---|---|---|---|---|---|
| 靜態比對 | Chat V2 | POST `/api/public/chat/v2/conversations` | `responses.200.conversation` | 欄位 `conversation` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FAQ V1 | POST `/api/public/faq/v1/faqs` | `responses.200.faq` | 欄位 `faq` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FAQ V1 | POST `/api/public/faq/v1/faqs/{faqId}/entries` | `responses.200.entry` | 欄位 `entry` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ExecutionStreamEvent.messages[].role` | 欄位 `role` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ExecutionStreamEvent.messages[].text` | 欄位 `text` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.PackageLockEntry.name` | 欄位 `name` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.PackageLockEntry.version` | 欄位 `version` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.PackageItem.name` | 欄位 `name` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.PackageItem.version` | 欄位 `version` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.PackageItem.source` | 欄位 `source` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.PackageItem.source` | 這個欄位沒有 example | `missing_example` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.PackageItem.kind` | 這個欄位沒有 example | `missing_example` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.PackageItem.transitive[].name` | 欄位 `name` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.PackageItem.transitive[].version` | 欄位 `version` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ListPackagesResponse.packages` | 欄位 `packages` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ListPackagesResponse.packages[].name` | 欄位 `name` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ListPackagesResponse.packages[].version` | 欄位 `version` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ListPackagesResponse.packages[].source` | 欄位 `source` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ListPackagesResponse.packages[].source` | 這個欄位沒有 example | `missing_example` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ListPackagesResponse.packages[].kind` | 這個欄位沒有 example | `missing_example` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ListPackagesResponse.packages[].transitive[].name` | 欄位 `name` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ListPackagesResponse.packages[].transitive[].version` | 欄位 `version` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ListPackagesResponse.pagination` | 欄位 `pagination` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.InstallPackagesRequest.packages` | 欄位 `packages` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.InstallPackagesResponse.installed` | 欄位 `installed` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.InstallPackagesResponse.installed[].name` | 欄位 `name` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.InstallPackagesResponse.installed[].version` | 欄位 `version` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.InstallPackagesResponse.installed[].source` | 欄位 `source` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.InstallPackagesResponse.installed[].source` | 這個欄位沒有 example | `missing_example` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.InstallPackagesResponse.installed[].kind` | 這個欄位沒有 example | `missing_example` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.InstallPackagesResponse.installed[].transitive[].name` | 欄位 `name` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.InstallPackagesResponse.installed[].transitive[].version` | 欄位 `version` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.UninstallPackagesRequest.packages` | 欄位 `packages` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.UninstallPackagesResponse.uninstalled` | 欄位 `uninstalled` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FedFlow V1 | — | `components.schemas.ResetPackagesResponse.message` | 欄位 `message` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | Knowledge V3 | POST `/api/public/knowledge/v3/knowledges` | `responses.200.knowledge` | 欄位 `knowledge` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | Knowledge V3 | POST `/api/public/knowledge/v3/knowledges/{knowledgeId}/documents` | `responses.200.document` | 欄位 `document` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | OpenAI V1 | — | `components.schemas.UpstreamError.error` | 欄位 `error` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | OpenAI V1 | — | `components.schemas.CreateTranscriptionRequest.file` | 這個欄位沒有 example | `missing_example` | 1 |
| 靜態比對 | OpenAI V1 | — | `components.schemas.ClientConfig.llm` | 欄位 `llm` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | OpenAI V1 | — | `components.schemas.ClientConfig.asr` | 欄位 `asr` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | OpenAI V1 | — | `components.schemas.ClientConfig.tts` | 欄位 `tts` 沒有 description | `missing_property_description` | 1 |

## Info（只列數量）

_無_

## LLM 文字審查

> AI 對 description 清晰度與邏輯一致性的判斷，僅供參考；每次執行結果會有些浮動，diff 時請留意。

Pass 83 支、Fail 2 支。

| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |
|---|---|---|---|---|---|---|
| LLM 審查 | Asura V1 | POST `/api/public/asura/v1/transcriptions` | `[responses.200.progress]` | 描述稱「只有查詢端點會帶這個欄位，建立時的回應沒有」，但該欄位定義在 responses.200（建立請求的回應）中，存在邏輯矛盾。 | `llm_description_issue` | 1 |
| LLM 審查 | Asura V1 | POST `/api/public/asura/v1/transcriptions` | — | 文字審查結果：Fail（1 筆問題，見上方 llm_description_issue） | `llm_review_fail` | 1 |
| LLM 審查 | OpenAI V1 | GET `/api/public/llm/v1/client-config` | `[responses.200.llm.model]` | 描述中提到若未設定代稱請改用 `GET /models` 的第一筆，但端點本身的描述中提到的是 `GET /api/public/llm/v1/fedgpt/v1/models`，路徑不一致，可能導致開發者找不到正確的端點。 | `llm_description_issue` | 1 |
| LLM 審查 | OpenAI V1 | GET `/api/public/llm/v1/client-config` | — | 文字審查結果：Fail（1 筆問題，見上方 llm_description_issue） | `llm_review_fail` | 1 |
