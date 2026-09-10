# API Review Agent — 需求釐清文件

> 來源：[FEDGPT-15990](https://ailabstw.atlassian.net/browse/FEDGPT-15990) 請 QA 測試 Public API
> 狀態：草稿，待與 James Yang discuss & review 後正式謄寫為 Confluence

## 背景

**不做人工逐一比對，改為建置一個 API Review Agent，自動依 Public Swagger 做檢查**。

---

## 1. 目標與範圍

- **目標**：讓 SI 廠商（SIV）與我們的 PJM 能直接依 Public Swagger 上的資訊正確呼叫 API，不需要額外對照其他文件或猜測行為。
- **Ground truth**：先針對 **Public Swagger** 做檢查，Swagger 本身即為 ground truth；其他文件（API 文件、SDK、範例程式等）皆視為 Swagger 的衍生品，不在檢查基準內。
- **使用者角色**：
  - SI 廠商（依 Swagger 串接）
  - 我們的 PJM（驗收 / 對外溝通窗口）
- **本階段範圍**：**功能正確性檢查為主，壓力/效能測試不在範疇內。**
- **輸入來源**：Public Swagger 頁面 — <https://fedgpt-dev.corp.ailabs.tw/swagger/>（dev 環境，`info.version: latest`，跟得上最新開發進度）。

### 1.1 跟 PM 討論後的結論

- **假設讀者看得懂 API**：文件不用解釋「什麼是 API」這種基本概念，重點放在這支 API 特有的行為、限制、格式規則。
- **正確性優先於可讀性**：寫得漂亮但內容錯，比寫得普通但內容對還糟——規則的嚴重度分級要反映這個順序（結構性錯誤如必填/型別不對 > 純文字缺漏如沒寫 description）。
- **驗收方式＝照手冊上的方式去呼叫 API，看能不能通**：不是憑空猜測 API 行為，而是照 Swagger 文件寫的步驟真的打一次，跟 live call 的設計方向一致。
- **有對外的 API 都要檢查，一支都不能少**：涵蓋現有 12 份 spec 裡所有真實端點；且檢查方法本身也要全面，不能只做讀取類——**live call 要從目前的 GET only 擴大到 POST/PUT/DELETE**（見第 7 節）。

---

## 2. Agent 設計

### 2.1 輸入

實測 <https://fedgpt-dev.corp.ailabs.tw/swagger/> 後確認：這不是單一份 spec，Swagger UI 的 `swagger-initializer.js` 指向 **12 份獨立、自包含的 OpenAPI 3.1.0 YAML**（`$ref` 只指向自己檔案內的 `#/components/...`，沒有跨檔案引用，可直接個別解析）：

| 分頁 | 檔案（`/swagger/docs/` 下） | 性質 |
|---|---|---|
| Common | `0.yaml` | 共通規則說明（錯誤格式、`reason` 對照表），非真實端點 |
| SSE 串流 | `sse.yaml` | 串流格式說明，非真實端點 |
| Admin V1 | `admin-v1.yaml` | 真實端點 |
| Asset V2 | `asset-v2.yaml` | 真實端點 |
| Asura V1 | `asura-v1.yaml` | 真實端點 |
| Auth V2 | `auth-v2.yaml` | 真實端點 |
| Chat V2 | `chat-v2.yaml` | 真實端點 |
| FAQ V1 | `faq-v1.yaml` | 真實端點 |
| FedFlow V1 | `fedflow-v1.yaml` | 真實端點 |
| Helix V1 | `helix-v1.yaml` | 真實端點 |
| Knowledge V3 | `knowledge-v3.yaml` | 真實端點 |
| LLM V1 | `llm-v1.yaml` | 真實端點 |

實際功能端點規模：約 **57 個 path、75 個 operation**（10 個功能分頁加總，不含 Common / SSE 兩個文件用分頁）。

Agent 的輸入方式：直接對這 12 個 URL 發 HTTP GET 抓 YAML，不需要爬 Swagger UI 渲染後的畫面（渲染用的 JS/CSS 資源在沒有 VPN 路由的環境會抓不到，但 spec 本身用一般 HTTP client 直接打得到）。

### 2.2 檢查流程（第一階段：靜態比對）
- **本階段只做「靜態比對 Swagger 定義本身的正確性」**，不實際呼叫 API 驗證行為（不做 live call 測試）。
- Live call 驗證（例如實際打 API 驗證回應、狀態碼、向下相容行為）列為**後續階段**，待第一階段穩定後再評估開啟。

### 2.3 輸出格式
雙軌輸出：
1. **Bug 發現 → 自動開 Jira 票**：Agent 檢查出的問題以 Jira issue 形式建立，方便追蹤與指派。
2. **每輪執行 → 產出一份 HTML report**：每次跑完整份 Public Swagger 的檢查後，產出一份總覽用的 HTML 報告（含本輪檢查了哪些 endpoint、通過/失敗數量、對應開出的 Jira 票清單等）。

> 待釐清：Jira 開票的專案/issue type/欄位規則、報告內容的具體欄位與呈現方式，需要在正式設計文件中細化。

### 2.4 設計原則：Agent 只標缺漏，不代寫內容

Agent 能檢查出「這裡沒寫 description」，但**不負責猜測正確的內容應該是什麼**——尤其牽涉到實際行為的情況（例如某個回應欄位到底包含哪些子欄位），只有懂實作的人確認過才能寫，Agent 亂猜反而可能寫出錯誤文件。Agent 的角色是標出缺口、附上足夠的上下文（哪支 endpoint、哪個欄位、旁邊有沒有類似寫得好的欄位可以參考），由人補上正確內容。實際案例見第 3 節。

---

## 3. 檢查規則 vs. 驗收標準

依原 ticket 的驗收標準，轉譯為 Agent 可自動檢查的靜態比對規則：

| 原始驗收標準 | Agent 靜態檢查規則（第一階段） |
|---|---|
| key 必填與否 | 比對 Swagger 中 `required` 欄位定義是否與實際規格一致（本階段先確認 Swagger 定義本身無矛盾/缺漏，非 live 驗證） |
| key / endpoint 描述是否正確（範圍、範例） | 檢查每個 key、endpoint 是否都有 description；**新增規則**：純量欄位（string/integer/number/boolean）沒有 `example` 也要標出來——跟 PM 討論時發現的具體缺口是 `contentType` 這類欄位缺 example，之後同類欄位都要抓 |
| Swagger 上列出的 endpoint 都要存在 | 本階段先確認 Swagger 定義完整、無明顯缺漏/重複（實際 endpoint 是否存在待 live call 階段驗證） |
| 狀態碼 | 檢查 Swagger 是否有定義完整的回應狀態碼（實際狀態碼是否正確待 live call 階段驗證） |
| API 向下相容（自 3.10 起） | **改為「這次執行 vs. 上次執行」的 spec diff**（見下方 3.2 節）——3.10 版本大概率拿不到，放棄以它為 baseline，改成每次執行都跟上一次存的快照比對，抓破壞性變更 |

> 待釐清：需要與 James Yang review 確認，哪些規則第一階段就能做到「純靜態比對」、哪些其實仍需要 live call 才能驗證。

**補充發現**：實測發現幾乎所有端點都遵循 `/public/{service}/v{n}/...` 的命名慣例，這可以當作「這支算不算 public」的判斷依據之一。但也發現一個例外：`fedflow-v1.yaml` 同時有 `/public/fedflow/v1/flows/summary` 與不帶 `/public/` 前綴的 `/fedflow/v1/flows/summary`，經確認這是**刻意保留的相容路徑**，description 裡有明講「無前綴那條是早期整合留下的相容路徑，新接的整合請用帶 `/public` 前綴的那條」。這代表檢查規則不能只看「有沒有 `/public/` 前綴」，還要能分辨「有註明原因的例外」跟「真的漏標／漏寫」，避免誤判。

### 3.1 案例：description 缺漏的實際影響

實測跑雛形 Agent 對 `chat-v2.yaml` 抓到的一筆真實發現，可以具體說明「key / endpoint 描述是否正確」這條驗收標準在抓什麼。

**有寫（`GET /public/chat/v2/conversations/{convId}` 的回應）**：`conversation` 欄位 `$ref` 到共用的 `Conversation` schema，裡面每個子欄位都有 description、example、必填清單，SI 開發者不用問人就知道每個欄位的意思與限制。

**沒寫（`POST /public/chat/v2/conversations`，建立對話的回應）**：同樣叫 `conversation` 的欄位，卻是 inline 定義、只保證有 `convId`，本身沒有 description。可能會產生三個疑問：這個 `conversation` 跟 GET 回的是不是同一種東西？除了 `convId` 是不是其實還有別的欄位只是沒寫？這是不是文件漏寫的 bug？

**這裡的重點**：Agent 只能標出「這欄位沒有 description」，**沒辦法自己決定該補什麼內容**——因為正確答案取決於後端實際回傳什麼，必須先請熟悉這支 API 的工程師確認實際行為（或找一組帳號實測一次），才能決定是要：

- (a) 改成跟 GET 一樣 `$ref` 到 `Conversation`（如果其實回的就是完整物件）or 
- (b) 保留精簡結構、但補上 description 說明「這是精簡版，只保證有 convId，要完整資訊請改打 GET」（如果本來就設計成只回最小資訊）。

這也是第 2.4 節「Agent 只標缺漏、不代寫內容」原則的具體案例。

### 3.1.1 新規則：純量欄位缺 example

已實作並測試（`agent/checks.py` 的 `_check_missing_example`）。原本以為「有 `enum` 的欄位可以跳過，合法值都列出來了」，但拿 `asura-v1.yaml` 的 `contentType`（有 `enum`、一開始以為沒有 example）去對，才發現這個假設是錯的——`enum` 列的是「合法值有哪些」，`example` 給的是「示範怎麼填」，兩者用途不同，enum 不能取代 example，所以拿掉了這個例外。用單元測試確認邏輯正確（有 example 的不報、沒有的會報）。目前對 dev 環境的即時 spec 跑一輪是 0 筆——這份 spec 剛好在這點上是乾淨的，規則本身留著當之後的防護網。

### 3.2 向下相容改法：跟上次執行比對，不等 3.10

**每次執行都跟上一次存的 spec 快照做 diff**：第一次跑某份 spec 時沒有基準可比，只會存下來；之後每次執行都會抓出跟上次比較的差異，抓完再把這次內容存回去當下次的基準。

已經做出雛形並實測驗證（`agent/spec_diff.py`），會抓：

- endpoint / method 整支被新增或移除
- 欄位從選填變必填（對呼叫方是破壞性變更）、必填變選填
- 欄位被新增／移除
- 欄位型別改變
- enum 合法值被移除（破壞性）或新增
- 宣告的回應狀態碼被移除或新增

驗證方式：手動在快照裡模擬「欄位從選填變必填」跟「新增一個狀態碼」兩種變化，重跑一次，兩筆都被正確抓出來，嚴重度也分得出來（前者 error、後者 info）。


---

## 4. 時程

- **一次跑完所有 Public endpoints 需要多久**：待評估（取決於 endpoint 數量與檢查規則複雜度，需在設計階段抓一個初估數字）。
- **是否接入 CI 或每個 sprint 固定跑**：**本階段先不決定，留待後續討論**。第一階段先以人工觸發執行為主。

---

## 5. 3.14 可行性

- Ticket 原始期望：3.14 版本時，Public Swagger 已修正目前已知問題。
- 向下相容 diff 已改成「跟上次執行比對」（見 3.2 節），不再卡 3.10 版本，可以馬上開始用——但代表 3.14 之前的變化只有從我們開始固定執行之後才抓得到，之前的破壞性變更不會被回溯抓出來。
- 待釐清：3.14 的 code freeze / release 日期，回推 Agent 需要在什麼時間點前產出第一輪完整報告，才來得及讓開發修復；以及要多久固定跑一次才不會漏掉兩次執行之間的變化（見第 4 節）。

---

## 6. 風險與所需資源

**風險**
- 第一階段僅做靜態比對，「Swagger 寫得對」不等於「API 實際行為對」，可能與 SI 廠商實際串接經驗有落差，需要在報告中明確標注本階段的檢查邊界，避免被誤解為完整驗收（live call 已補上一部分，但目前只涵蓋 GET）。
- 向下相容改成跟上次執行比對後，兩次執行間隔太久會讓中間的變化被壓成一大包、抓不出是哪次壞的，需要固定節奏執行（見第 4 節）。
- 自動開 Jira 票若規則寫得太寬，可能產生大量誤判票，先開成草稿待 Review 後再開卡。

**所需資源**
- Public Swagger 頁面連結（已確認）。
- Jira 開票所需的專案權限 / API token。
- 待確認：Agent 執行環境（本機 / CI runner）、開發時程與人力。

---

## 7. 擴大 Live Call 到寫入方法（POST/PUT/DELETE）

跟 PM 討論後拍板：「有對外的 API 都要檢查」不只是指涵蓋所有 endpoint，也指檢查方法本身要全面，不能只做唯讀。目前 `agent/live_call.py` 只打 GET，這節記錄擴大範圍前需要先解決的問題（**尚未開始實作**）。

**跟唯讀 GET 的差異**：GET 沒有副作用，打錯了頂多多讀一次；POST/PUT/DELETE 會真的在 dev 環境建立、修改、刪除資料，出錯的代價完全不同，不能照抄現在的做法直接套用。

**開始實作前要先定案的事**：
- **測試資料隔離**：要不要用一個專門的測試帳號/命名慣例（例如標題都以 `[agent-test]` 開頭），方便事後辨識、清理，也避免跟真人使用者的資料混在一起。
- **清理策略**：每輪跑完是不是要把自己建立的資料刪掉？如果 DELETE 本身也在測試範圍內，刪除動作要怎麼驗證又不留下垃圾資料。
- **跨 endpoint 的先後依賴**：像 Chat V2 要「建立對話 → 查詢 → 更新標題 → （目前沒有刪除端點）」串成一條流程，才測得到 PUT；FedFlow、Knowledge 等其他分頁也要各自盤點適合的流程順序。
- **哪些 endpoint 先做**：建議先挑「有對應 GET 可以驗證結果」的 POST/PUT（例如建立後可以馬上 GET 回來比對），比「打了看不到結果」的更容易驗證，先從這類開始。

---

## 待確認 / Open Questions

1. 已確認：<https://fedgpt-dev.corp.ailabs.tw/swagger/>，OpenAPI 3.1.0 YAML，共 12 份可直接 HTTP GET 下載。
**新待確認**：dev 環境（`version: latest`）是否等同 Nina 要的「Public Swagger」本人？跟正式環境對外曝露的版本是否有落差、要不要改盯正式環境。
2.「跟上次執行比對」取代 3.10 baseline（見 3.2 節）。
3. Jira 開票的目標 project、issue type、必填欄位規則。
4. HTML report 的呈現內容與對象（給 PJM 看？給開發看？）。
5. ~~Live call 驗證（第二階段）的啟動時機與範圍~~ — 已啟動 GET 部分並實測 dev 環境，抓到 3 類真實的文件/行為落差（`Conversation.disabled` 必填但實際回應沒有、`mode` 出現未宣告的值 `assistant`、`FAQ.description` 必填但實際缺漏）。**新待確認**：PM 已拍板要擴大到 POST/PUT/DELETE（見第 7 節），但測試資料隔離/清理策略還沒定案，這點要先講好才能動工。
6. CI / 排程整合的時間點——現在有了「跟上次執行比對」的機制後，這點變得更重要：要多久固定跑一次，才不會讓兩次執行之間累積太多變化，待後續討論再定案。

---

## 下一步

1. 補齊第 3 節「檢查規則」的細節定義（哪些規則第一階段可行）。
2. 找 **James Yang** discuss & review，cc Jessica Kao、Winter Deng。
3. Review 後正式謄寫為 Confluence 頁面，回填 ticket [FEDGPT-15990](https://ailabstw.atlassian.net/browse/FEDGPT-15990)。
