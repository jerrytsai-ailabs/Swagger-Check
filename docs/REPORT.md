# Public Swagger 稽核報告（stg2）

> 這份檔案由 `run_check.py` 每次執行自動覆寫，請勿手動編輯。完整細節請看同一次執行產生的 `reports/report-*.html`。

- 最後更新：2026-09-29 11:43:59 +0800
- 來源：https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/
- 這次跑的階段：靜態比對、Live GET、LLM 審查

| 整體結果 | Pass | Fail | Endpoint 總數 | Pass 率 |
|---|---|---|---|---|
| **FAIL** | 76 | 5 | 81 | 93.8% |

| 總發現數 | Error | Warning | Info | 無發現的分頁 |
|---|---|---|---|---|
| 139 | 19 | 40 | 80 | 0/10 |

> ⚠️ 這次沒有跑 `--live-write`，只有寫入測試才打得到的已知問題不會反映在 Pass 率裡。

## 執行歷史

| 時間 | 環境 | live-write | Pass 率 | Pass/總數 | Error | Warning | Info |
|---|---|---|---|---|---|---|---|
| 2026-09-29 11:43:59 +0800 | stg2 | — | 93.8% | 76/81 | 19 | 40 | 80 |

## 受檢規格

| 分頁 | 檔案 | 狀態 |
|---|---|---|
| Common | `0.yaml` | 說明用分頁 |
| SSE 串流 | `sse.yaml` | 說明用分頁 |
| Admin V1 | `admin-v1.yaml` | 3 個發現 |
| Asset V2 | `asset-v2.yaml` | 1 個發現 |
| Asura V1 | `asura-v1.yaml` | 6 個發現 |
| Auth V2 | `auth-v2.yaml` | 8 個發現 |
| Chat V2 | `chat-v2.yaml` | 37 個發現 |
| FAQ V1 | `faq-v1.yaml` | 12 個發現 |
| FedFlow V1 | `fedflow-v1.yaml` | 39 個發現 |
| Helix V1 | `helix-v1.yaml` | 7 個發現 |
| Knowledge V3 | `knowledge-v3.yaml` | 13 個發現 |
| LLM V1 | `llm-v1.yaml` | 11 個發現 |

## Error（16 筆，依根因合併）

| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |
|---|---|---|---|---|---|---|
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations` | `responses.200.conversations.N` | 實際回應在 conversations.N 不符合 spec 宣告的 schema：'disabled' is a required property | `response_schema_mismatch` | 5 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/faqs` | `responses.200.faqs.N` | 實際回應在 faqs.N 不符合 spec 宣告的 schema：'description' is a required property | `response_schema_mismatch` | 5 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}` | `responses.200.conversation` | 實際回應在 conversation 不符合 spec 宣告的 schema：'disabled' is a required property | `response_schema_mismatch` | 1 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}/messages` | `responses.200.messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'biasScore' is a required property | `response_schema_mismatch` | 2 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}/messages` | `responses.200.messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'hallucinationScore' is a required property | `response_schema_mismatch` | 2 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}/messages` | `responses.200.messages.N.humanInLoop` | 實際回應在 messages.N.humanInLoop 不符合 spec 宣告的 schema：None is not of type 'object' | `response_schema_mismatch` | 1 |

## Warning（39 筆，依根因合併）

| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |
|---|---|---|---|---|---|---|
| 靜態比對 | Chat V2 | POST `/api/public/chat/v2/conversations` | `responses.200.conversation` | 欄位 `conversation` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FAQ V1 | POST `/api/public/faq/v1/faqs` | `responses.200.faq` | 欄位 `faq` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | FAQ V1 | POST `/api/public/faq/v1/faqs/{faqId}/entries` | `responses.200.entry` | 欄位 `entry` 沒有 description | `missing_property_description` | 1 |
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
| 靜態比對 | LLM V1 | — | `components.schemas.UpstreamError.error` | 欄位 `error` 沒有 description | `missing_property_description` | 1 |
| 靜態比對 | LLM V1 | — | `components.schemas.CreateTranscriptionRequest.file` | 這個欄位沒有 example | `missing_example` | 1 |
| 靜態比對 | SSE 串流 | POST `/chat/v2/chat/agent:stream` | — | SSE 說明文件把 `POST /chat/v2/chat/agent:stream`（分頁：Flowise V3）列為串流端點，但在該分頁的 spec 裡找不到這支端點，可能已改名、搬家，或分頁本身沒有被抓取，文件需要更新 | `sse_doc_endpoint_not_found` | 1 |
| 靜態比對 | SSE 串流 | GET `/chat/v2/chat/agent/{convId}/stream` | — | SSE 說明文件把 `GET /chat/v2/chat/agent/{convId}/stream`（分頁：Flowise V3）列為串流端點，但在該分頁的 spec 裡找不到這支端點，可能已改名、搬家，或分頁本身沒有被抓取，文件需要更新 | `sse_doc_endpoint_not_found` | 1 |

## Info（只列數量）

_無_

## LLM 文字審查

> AI 對 description 清晰度與邏輯一致性的判斷，僅供參考；每次執行結果會有些浮動，diff 時請留意。

Pass 80 支、Fail 1 支。

| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |
|---|---|---|---|---|---|---|
| LLM 審查 | LLM V1 | POST `/api/public/llm/v1/retriever/v1/embeddings` | `[responses.400]` | 描述內容與標籤不符。標籤為 400 (Bad Request)，但描述中提到「文字端點的參數錯誤有兩種格式」，且表格內容與 404 的描述完全一致，應為複製貼上錯誤。 | `llm_description_issue` | 1 |
| LLM 審查 | LLM V1 | POST `/api/public/llm/v1/retriever/v1/embeddings` | `[responses.404]` | 描述內容與標籤不符。標籤為 404 (Not Found)，但描述內容卻在說明「參數錯誤」以及「擋下來的是誰」的邏輯，這與 400 的描述完全相同，且與 404 的語意矛盾。 | `llm_description_issue` | 1 |
| LLM 審查 | LLM V1 | POST `/api/public/llm/v1/retriever/v1/embeddings` | `[responses.401]` | 描述中提到「本站台自己擋下來的錯誤」，但隨後又提到「三種都要能處理」，且表格中包含「上游 vLLM」的錯誤，這與 401 (Unauthorized) 的定義不符，且與 400/404 的錯誤處理邏輯混淆。 | `llm_description_issue` | 1 |
| LLM 審查 | LLM V1 | POST `/api/public/llm/v1/retriever/v1/embeddings` | — | 文字審查結果：Fail（3 筆問題，見上方 llm_description_issue） | `llm_review_fail` | 1 |
