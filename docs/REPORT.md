# Public Swagger 稽核報告（stg2）

> 這份檔案由 `run_check.py` 每次執行自動覆寫，請勿手動編輯。完整細節請看同一次執行產生的 `reports/report-*.html`。

- 最後更新：2026-09-30 11:41:34 +0800
- 來源：https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/
- 這次跑的階段：靜態比對、Live GET、Live 寫入、LLM 審查

| 整體結果 | Pass | Fail | Endpoint 總數 | Pass 率 |
|---|---|---|---|---|
| **FAIL** | 63 | 18 | 81 | 77.8% |

| 總發現數 | Error | Warning | Info | 無發現的分頁 |
|---|---|---|---|---|
| 207 | 45 | 48 | 114 | 0/10 |

## 執行歷史

| 時間 | 環境 | live-write | Pass 率 | Pass/總數 | Error | Warning | Info |
|---|---|---|---|---|---|---|---|
| 2026-09-30 11:41:34 +0800 | stg2 | ✓ | 77.8% | 63/81 | 45 | 48 | 114 |
| 2026-09-30 11:12:54 +0800 | stg2 | — | 93.8% | 76/81 | 23 | 39 | 80 |
| 2026-09-29 15:06:44 +0800 | stg2 | ✓ | 77.8% | 63/81 | 45 | 48 | 124 |
| 2026-09-29 11:43:59 +0800 | stg2 | — | 93.8% | 76/81 | 19 | 40 | 80 |

## 受檢規格

| 分頁 | 檔案 | 狀態 |
|---|---|---|
| Common | `0.yaml` | 說明用分頁 |
| SSE 串流 | `sse.yaml` | 說明用分頁 |
| Admin V1 | `admin-v1.yaml` | 4 個發現 |
| Asset V2 | `asset-v2.yaml` | 9 個發現 |
| Asura V1 | `asura-v1.yaml` | 18 個發現 |
| Auth V2 | `auth-v2.yaml` | 12 個發現 |
| Chat V2 | `chat-v2.yaml` | 64 個發現 |
| FAQ V1 | `faq-v1.yaml` | 14 個發現 |
| FedFlow V1 | `fedflow-v1.yaml` | 41 個發現 |
| Helix V1 | `helix-v1.yaml` | 12 個發現 |
| Knowledge V3 | `knowledge-v3.yaml` | 17 個發現 |
| LLM V1 | `llm-v1.yaml` | 14 個發現 |

## Error（42 筆，依根因合併）

| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |
|---|---|---|---|---|---|---|
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations` | `responses.200.conversations.N` | 實際回應在 conversations.N 不符合 spec 宣告的 schema：'disabled' is a required property | `response_schema_mismatch` | 5 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/faqs` | `responses.200.faqs.N` | 實際回應在 faqs.N 不符合 spec 宣告的 schema：'description' is a required property | `response_schema_mismatch` | 5 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}` | `responses.200.conversation` | 實際回應在 conversation 不符合 spec 宣告的 schema：'disabled' is a required property | `response_schema_mismatch` | 1 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}/messages` | `responses.200.flowMessages.items.N.sseEvents.N.content.role` | 實際回應在 flowMessages.items.N.sseEvents.N.content.role 不符合 spec 宣告的 schema：'' is not one of ['user', 'assistant', 'tool', 'system'] | `response_schema_mismatch` | 5 |
| Live 寫入 | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}` | `conversation` | 實際回應在 conversation 不符合 spec 宣告的 schema：'disabled' is a required property | `response_schema_mismatch` | 1 |
| Live 寫入 | Knowledge V3 | DELETE `/api/public/knowledge/v3/knowledges/{knowledgeId}` | — | 實際回傳 423，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '403', '404']） | `undocumented_status_code` | 1 |
| Live 寫入 | FedFlow V1 | GET `/api/public/fedflow/v1/executions/{executionId}/result` | `state` | 實際回應在 state 不符合 spec 宣告的 schema：'PENDING' is not one of ['INPROGRESS', 'FINISHED', 'ERROR', 'TERMINATED', 'TIMEOUT', 'STOPPED'] | `response_schema_mismatch` | 1 |
| Live 寫入 | Helix V1 | POST `/api/public/helix/v1/voices` | — | 實際回傳 502，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '400']） | `undocumented_status_code` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/normal` | `messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'biasScore' is a required property | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/normal` | `messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'hallucinationScore' is a required property | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/normal` | `messages.N.humanInLoop` | 實際回應在 messages.N.humanInLoop 不符合 spec 宣告的 schema：None is not of type 'object' | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/knowledge` | `messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'biasScore' is a required property | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/knowledge` | `messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'hallucinationScore' is a required property | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/knowledge` | `messages.N.humanInLoop` | 實際回應在 messages.N.humanInLoop 不符合 spec 宣告的 schema：None is not of type 'object' | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/agenticRag` | `messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'biasScore' is a required property | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/agenticRag` | `messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'hallucinationScore' is a required property | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/agenticRag` | `messages.N.humanInLoop` | 實際回應在 messages.N.humanInLoop 不符合 spec 宣告的 schema：None is not of type 'object' | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/faq` | `messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'biasScore' is a required property | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/faq` | `messages.N.guardian` | 實際回應在 messages.N.guardian 不符合 spec 宣告的 schema：'hallucinationScore' is a required property | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/faq` | `messages.N.humanInLoop` | 實際回應在 messages.N.humanInLoop 不符合 spec 宣告的 schema：None is not of type 'object' | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/tabular` | — | 實際回傳 423，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '400', '403', '404', '410']） | `undocumented_status_code` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/tabular` | — | POST 送訊息（tabular）沒有回 200（實際 423），中止測試（仍會清理臨時資源） | `live_write_aborted` | 1 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/transcriptions` | — | 實際回傳 500，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '400']） | `undocumented_status_code` | 1 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/transcriptions` | — | POST 建立轉錄工作沒有回 200（實際 500），中止測試 | `live_write_aborted` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/tabular:stream` | — | 串流中途收到 event: error：{'service': 'grpc-knowledge', 'reason': 'data.locked', 'message': 'tabular not ready for query'} | `live_write_aborted` | 1 |
| Live 寫入 | Helix V1 | POST `/api/public/helix/v1/enrollment` | — | 實際回傳 500，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '400', '409']） | `undocumented_status_code` | 1 |
| Live 寫入 | Helix V1 | POST `/api/public/helix/v1/enrollment` | — | POST 註冊聲紋沒有回 200（實際 500）：{"service":"","reason":"","message":"500: Internal Server Error"} ，中止測試 | `live_write_aborted` | 1 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/speeches:zero-shot` | — | 實際回傳 500，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '400', '404']） | `undocumented_status_code` | 1 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/speeches:zero-shot` | — | POST 零樣本語音克隆沒有回 200（實際 500）：{"service":"http-gateway","reason":"data.unhandled","message":"unexpected status code: 400"} ，中止測試 | `live_write_aborted` | 1 |
| Live 寫入 | Auth V2 | POST `/api/public/auth/v2/logout` | — | 實際回傳 401，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200']） | `undocumented_status_code` | 1 |

## Warning（47 筆，依根因合併）

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
| Live 寫入 | Knowledge V3 | DELETE `/api/public/knowledge/v3/knowledges/{knowledgeId}` | — | spec 對 DELETE /knowledges/{knowledgeId} 的描述寫「⚠️ 底下的文件會一起消失，而且無法復原」，但實測在文件仍存在時呼叫，回應是 423（非 200），代表知識庫必須先清空文件才能刪除——文件描述的行為與實際行為不一致，建議修正文件說明或修正實作其中一邊 | `spec_description_mismatch` | 1 |
| Live 寫入 | Helix V1 | POST `/api/public/helix/v1/voices` | — | spec 建議『Asset V2 上傳後把網址填進來』作為 audioUris，但直接把 assetKey（tmp/yuBRH0j5XjeGabp_Wz64k）當成 audioUris 送出，實際回應是 502——代表這兩支 API 之間怎麼銜接，文件沒有講清楚：assetKey 不能（或至少不是能直接這樣）當 audioUris 用 | `spec_description_mismatch` | 1 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/speeches:stream` | — | SpeechModelConfig.model 的 spec 範例值 'tts-general-0.N.1' 在 stg2 實測不存在（後端查模型收到 404，reason=inferno.get-model.failed），且 spec 沒有提供任何端點可查詢這個部署實際支援的 TTS 模型名稱清單（不像 Chat V2 有 GET /models）——正確版本（tts-general-1.N.3）只能問熟悉部署的人才拿得到 | `spec_description_mismatch` | 1 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/speeches:stream` | — | spec 宣告 SpeechInput.audioConfig 是選填（required 只列 modelConfig），但不帶它實際回應是 400：Key: 'createRequest.AudioConfig' Error:Field validation for 'AudioConfig' failed on the 'required' tag——代表伺服器端其實把它當必填 | `spec_description_mismatch` | 1 |
| Live 寫入 | Chat V2 | GET `/api/public/chat/v2/tabulars` | — | GET /chat/v2/tabulars 回傳的 Tabular schema（tabularId/name/description）沒有任何欄位可以判斷資源是否已就緒可查詢；實測發現部分既有 tabular 會在 chat/tabular 回 423 data.locked（'tabular not ready for query'），但這個狀態碼完全沒有寫進 chat/tabular 的 spec 文件，也無法事先得知 | `spec_description_mismatch` | 2 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/speeches:zero-shot` | — | spec 的 ZeroShotSpeechInput.input 只把 text 標成必填，promptVoiceUrl/promptVoiceAssetKey 兩個都是選填，但兩個都不帶實際回應是 400：failed to validate request; validation error: key: value length must be at least 1 characters——代表伺服器端其實兩者擇一是必填 | `spec_description_mismatch` | 1 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/speeches:zero-shot` | — | 帶 promptVoiceAssetKey（照 spec 建議走 transcriptions:presign 上傳）一律回 500 {"service":"http-gateway","reason":"data.unhandled","message":"unexpected status code: 400"}——代表 http-gateway 轉呼叫上游時收到 400，但沒有處理這個狀態碼的邏輯，直接包成看不出原因的 500。手動排查過：同一個 assetKey 打 /transcriptions 可以正常解析成真實網址並成功建立轉錄工作，改填那個已驗證能連得到的 promptVoiceUrl 給這支端點一樣回同一個 500——不是 assetKey 或模型名稱的問題，是這支端點處理語音範本輸入的路徑本身壞了，比較像後端 bug 而不是文件寫錯 | `spec_description_mismatch` | 1 |

## Info（只列數量）

| 規則 | 筆數 |
|---|---|
| `live_write_ok` | 22 |
| `live_write_permanent_residue` | 10 |
| `live_write_skipped` | 2 |

## LLM 文字審查

> AI 對 description 清晰度與邏輯一致性的判斷，僅供參考；每次執行結果會有些浮動，diff 時請留意。

Pass 80 支、Fail 1 支。

| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |
|---|---|---|---|---|---|---|
| LLM 審查 | LLM V1 | POST `/api/public/llm/v1/retriever/v1/embeddings` | `[responses.400]` | 描述內容與標籤不符。標籤為 400 (Bad Request)，但描述中提到「文字端點的參數錯誤有兩種格式」，且表格內容與 404 的描述完全一致，應為複製貼上錯誤。 | `llm_description_issue` | 1 |
| LLM 審查 | LLM V1 | POST `/api/public/llm/v1/retriever/v1/embeddings` | `[responses.404]` | 描述內容與標籤不符。標籤為 404 (Not Found)，但描述內容卻在說明「參數錯誤」以及「擋下來的是誰」的邏輯，這與 400 的描述完全相同，且與 404 的語意矛盾。 | `llm_description_issue` | 1 |
| LLM 審查 | LLM V1 | POST `/api/public/llm/v1/retriever/v1/embeddings` | `[responses.401]` | 描述中提到「本站台自己擋下來的錯誤」，但隨後又提到「三種都要能處理」，且表格中包含「上游 vLLM」的錯誤，這與 401 (Unauthorized) 的定義不符，且與 400/404 的錯誤處理邏輯混淆。 | `llm_description_issue` | 1 |
| LLM 審查 | LLM V1 | POST `/api/public/llm/v1/retriever/v1/embeddings` | — | 文字審查結果：Fail（3 筆問題，見上方 llm_description_issue） | `llm_review_fail` | 1 |
