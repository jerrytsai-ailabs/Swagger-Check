# Public Swagger 稽核報告（stg2-vm1）

> 這份檔案由 `run_check.py` 每次執行自動覆寫，請勿手動編輯。完整細節請看同一次執行產生的 `reports/FedGPT-API-Review-*.html`。

- 最後更新：2026-10-14 09:35:26 +0800
- 來源：https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/
- 這次跑的階段：靜態比對、Live GET、Live 寫入、LLM 審查

| 整體結果 | Pass | Fail | Endpoint 總數 | Pass 率 |
|---|---|---|---|---|
| **FAIL** | 73 | 12 | 85 | 85.9% |

| 總發現數 | Error | Warning | Info | 無發現的分頁 |
|---|---|---|---|---|
| 194 | 24 | 48 | 122 | 0/10 |

## 執行歷史

| 時間 | 環境 | live-write | Pass 率 | Pass/總數 | Error | Warning | Info |
|---|---|---|---|---|---|---|---|
| 2026-10-14 09:35:26 +0800 | stg2-vm1 | ✓ | 85.9% | 73/85 | 24 | 48 | 122 |
| 2026-10-13 09:35:43 +0800 | stg2-vm1 | ✓ | 85.9% | 73/85 | 24 | 48 | 132 |
| 2026-10-12 14:23:53 +0800 | stg2-vm1 | ✓ | 85.9% | 73/85 | 21 | 48 | 122 |
| 2026-10-12 09:34:54 +0800 | stg2-vm1 | ✓ | 82.4% | 70/85 | 40 | 49 | 121 |
| 2026-10-08 09:34:20 +0800 | stg2-vm1 | ✓ | 78.8% | 67/85 | 44 | 52 | 117 |
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
| Admin V1 | `admin-v1.yaml` | 4 個發現 |
| Asset V2 | `asset-v2.yaml` | 9 個發現 |
| Asura V1 | `asura-v1.yaml` | 18 個發現 |
| Auth V2 | `auth-v2.yaml` | 12 個發現 |
| Chat V2 | `chat-v2.yaml` | 49 個發現 |
| FAQ V1 | `faq-v1.yaml` | 14 個發現 |
| FedFlow V1 | `fedflow-v1.yaml` | 44 個發現 |
| Helix V1 | `helix-v1.yaml` | 12 個發現 |
| Knowledge V3 | `knowledge-v3.yaml` | 17 個發現 |
| OpenAI V1 | `openai-v1.yaml` | 15 個發現 |

## Error（24 筆，依根因合併）

| 階段 | 分頁 | Endpoint | 位置 | 問題 | 規則 | 筆數 |
|---|---|---|---|---|---|---|
| Live GET | Chat V2 | GET `/api/public/chat/v2/faqs` | `responses.200.faqs.N` | 實際回應在 faqs.N 不符合 spec 宣告的 schema：'description' is a required property | `response_schema_mismatch` | 5 |
| Live GET | Chat V2 | GET `/api/public/chat/v2/conversations/{convId}/messages` | `responses.200.flowMessages.items.N.sseEvents.N.content.role` | 實際回應在 flowMessages.items.N.sseEvents.N.content.role 不符合 spec 宣告的 schema：'' is not one of ['user', 'assistant', 'tool', 'system'] | `response_schema_mismatch` | 5 |
| Live 寫入 | Knowledge V3 | DELETE `/api/public/knowledge/v3/knowledges/{knowledgeId}` | — | 實際回傳 423，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '403', '404']）。回應內容：{"service":"grpc-knowledge","reason":"data.locked","message":"cannot delete kb with 1 documents; delete them first"}（X-Request-Id: <id>） | `undocumented_status_code` | 1 |
| Live 寫入 | Helix V1 | POST `/api/public/helix/v1/voices` | — | 實際回傳 502，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '400']）。回應內容：{"service":"","reason":"","message":"502: {\"detail\":{\"error\":\"AUDIO_FETCH_FAILED\",\"message\":\"audio_uri must be an absolute path or http(s) URL, got: 'tmp/<id>'\"}}"}（X-Request-Id: <id>） | `undocumented_status_code` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/normal` | `messages.N.humanInLoop` | 實際回應在 messages.N.humanInLoop 不符合 spec 宣告的 schema：None is not of type 'object' | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/knowledge` | `messages.N.humanInLoop` | 實際回應在 messages.N.humanInLoop 不符合 spec 宣告的 schema：None is not of type 'object' | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/agenticRag` | `messages.N.humanInLoop` | 實際回應在 messages.N.humanInLoop 不符合 spec 宣告的 schema：None is not of type 'object' | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/faq` | `messages.N.humanInLoop` | 實際回應在 messages.N.humanInLoop 不符合 spec 宣告的 schema：None is not of type 'object' | `response_schema_mismatch` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/tabular` | — | 實際回傳 423，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '400', '403', '404', '410']）。回應內容：{"service":"grpc-knowledge","reason":"data.locked","message":"tabular not ready for query"}（X-Request-Id: <id>） | `undocumented_status_code` | 1 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/tabular` | — | POST 送訊息（tabular）沒有回 200（實際 423），中止測試（仍會清理臨時資源） | `live_write_aborted` | 1 |
| Live 寫入 | Asura V1 | GET `/api/public/asura/v1/transcriptions/{transcriptionId}` | `sentences.N` | 實際回應在 sentences.N 不符合 spec 宣告的 schema：'sentiment' is a required property | `response_schema_mismatch` | 3 |
| Live 寫入 | Chat V2 | POST `/api/public/chat/v2/chat/tabular:stream` | — | 串流中途收到 event: error：{'service': 'grpc-knowledge', 'reason': 'data.locked', 'message': 'tabular not ready for query'} | `live_write_aborted` | 1 |
| Live 寫入 | Helix V1 | POST `/api/public/helix/v1/enrollment` | — | 實際回傳 500，但 spec 裡這支 endpoint 沒有宣告這個狀態碼（宣告的有：['200', '400', '409']）。回應內容：{"service":"","reason":"","message":"500: Internal Server Error"}（X-Request-Id: <id>） | `undocumented_status_code` | 1 |
| Live 寫入 | Helix V1 | POST `/api/public/helix/v1/enrollment` | — | POST 註冊聲紋沒有回 200（實際 500）。回應內容：{"service":"","reason":"","message":"500: Internal Server Error"}（X-Request-Id: <id>），中止測試 | `live_write_aborted` | 1 |

## Warning（48 筆，依根因合併）

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
| Live 寫入 | Knowledge V3 | DELETE `/api/public/knowledge/v3/knowledges/{knowledgeId}` | — | spec 對 DELETE /knowledges/{knowledgeId} 的描述寫「⚠️ 底下的文件會一起消失，而且無法復原」，但實測在文件仍存在時呼叫，回應是 423（非 200），代表知識庫必須先清空文件才能刪除——文件描述的行為與實際行為不一致，建議修正文件說明或修正實作其中一邊 | `spec_description_mismatch` | 1 |
| Live 寫入 | Helix V1 | POST `/api/public/helix/v1/voices` | — | spec 建議『Asset V2 上傳後把網址填進來』作為 audioUris，但直接把 assetKey（tmp/<id>）當成 audioUris 送出，實際回應是 502——代表這兩支 API 之間怎麼銜接，文件沒有講清楚：assetKey 不能（或至少不是能直接這樣）當 audioUris 用 | `spec_description_mismatch` | 1 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/speeches:stream` | — | SpeechModelConfig.model 的 spec 範例值 'tts-general-0.0.1' 在 stg2-vm1 實測不存在（後端查模型收到 404，reason=inferno.get-model.failed），且 spec 沒有提供任何端點可查詢這個部署實際支援的 TTS 模型名稱清單（不像 Chat V2 有 GET /models）——正確版本（tts-general-1.3.3）只能問熟悉部署的人才拿得到 | `spec_description_mismatch` | 1 |
| Live 寫入 | Asura V1 | POST `/api/public/asura/v1/speeches:stream` | — | spec 宣告 SpeechInput.audioConfig 是選填（required 只列 modelConfig），但不帶它實際回應是 400：Key: 'createRequest.AudioConfig' Error:Field validation for 'AudioConfig' failed on the 'required' tag——代表伺服器端其實把它當必填 | `spec_description_mismatch` | 1 |
| Live 寫入 | Chat V2 | GET `/api/public/chat/v2/tabulars` | — | GET /chat/v2/tabulars 回傳的 Tabular schema（tabularId/name/description）沒有任何欄位可以判斷資源是否已就緒可查詢；實測發現部分既有 tabular 會在 chat/tabular 回 423 data.locked（'tabular not ready for query'），但這個狀態碼完全沒有寫進 chat/tabular 的 spec 文件，也無法事先得知 | `spec_description_mismatch` | 2 |

## Info（只列數量）

| 規則 | 筆數 |
|---|---|
| `live_write_ok` | 24 |
| `live_write_permanent_residue` | 11 |
| `live_write_skipped` | 2 |

## LLM 文字審查

> AI 對 description 清晰度與邏輯一致性的判斷，僅供參考；每次執行結果會有些浮動，diff 時請留意。

Pass 85 支、Fail 0 支。
