# API Review Agent — 需求釐清文件

> 來源：[FEDGPT-15990](https://ailabstw.atlassian.net/browse/FEDGPT-15990) 請 QA 測試 Public API
> 狀態：草稿，待與 James Yang discuss & review 後正式謄寫為 Confluence

### 這是什麼
一個檢查 FedGPT Public Swagger 的小工具：靜態比對 spec 本身、對 GET/POST/PUT/DELETE 端點做 live call、追蹤 spec 隨時間的變化、用 LLM 審查文件寫得清不清楚，最後產出一份 HTML 報告。

### 安裝
```bash
git clone <repo>
cd SwaggerAgent
pip install -r requirements.txt
```
Mac／Linux 上如果系統的 `python`/`pip` 沒有指到 Python 3，改用 `python3`/`pip3`（下面所有指令同理，把 `python` 換成 `python3`）。

### 設定 `.env`
在專案根目錄建立 `.env`（已加進 `.gitignore`，不會進版控），依需要填入：

| 變數 | 用途 | 從哪裡拿 |
|---|---|---|
| `FEDGPT_ACCESS_TOKEN` | live call 打 **dev** 環境要用的 token | 跟熟悉 dev 帳號的人要一組 access token |
| `FEDGPT_STG2_TOKEN` | live call 打 **stg2** 環境要用的 token | 同上，但要 stg2 環境的帳號——**兩邊 token 不能互通**，各環境帳號資料庫是分開的 |
| `FEDFLOW_TEST_FLOW_ID` | `--live-write` 測 FedFlow execute 要打的 flow id | 選填，預設是 stg2 上一個已人工確認「無副作用」的 flow（`212913c58069778f5f399a3f466d7ffc`，名稱 "Second"）。這個 flow 只存在於 stg2，對其他環境跑會直接跳過（回 404 視為無可用測試 flow） |
| `HELIX_TEST_AUDIO_PATH` | `--live-write` 測 Helix 聲紋（`/voices`、`/voices:searchByAudio`）與 Asura 語音相關端點（語音轉文字、零樣本語音克隆）要用的本機音檔路徑 | 選填，指到一個本機真的音檔（測試中用的是 .aac）。沒有設定就跳過這幾組測試，不是失敗；路徑含空格或特殊字元時記得用引號包起來（`.env` 也會被 bash `source` 讀到，格式要兼顧） |
| `HELIX_ENROLLMENT_TEST_TOKEN` | `--live-write` 測 Helix `/enrollment` 要用的獨立測試帳號 token | 選填，`/enrollment` 是「一個帳號只能有一組」的資源，不能用主要測試帳號測，要一個確認過從沒註冊過聲紋的獨立測試帳號 token。沒有設定就跳過這組測試 |
| `AUTH_LOGIN_TEST_USERNAME` / `AUTH_LOGIN_TEST_PASSWORD` | `--live-write` 測 Auth V2 的 `fedgpt/login`、`ldap/login`、`logout` 要用的次要帳號帳密 | 選填，固定用一個次要帳號（不是主要測試帳號），因為登入會產生新 token、登出會讓某個 token 失效，不想動到主要測試帳號正在用的 token。沒有設定就跳過這幾組測試 |
| `GOOGLE_CHAT_WEBHOOK_URL` | `--notify` 推播用 | 目標 Google Chat Space → Apps & integrations → Webhooks |
| `TEAMS_WEBHOOK_URL` | `--notify-teams` 推播用 | Teams 頻道/對話 → Workflows → 建一個 "When a Teams webhook request is received" 的 flow |
| `REPORT_LINK_URL` | `--notify`/`--notify-teams` 推播卡片裡「開啟完整報告」連到的網址 | 選填，建議設成下面 `CONFLUENCE_REPORT_PAGE_ID` 那頁的網址（用 `--notify-confluence` 更新內容，網址不變）。沒設的話會退回本機報告檔案的絕對路徑（只有拿得到那台機器的人看得到） |
| `CONFLUENCE_SITE_URL` | `--notify-confluence` 要打的 Confluence 站台 | 選填，預設 `https://ailabstw.atlassian.net` |
| `CONFLUENCE_REPORT_PAGE_ID` | `--notify-confluence` 要更新的固定頁面 ID | 選填，目前指到 [FEDGPT-15990] 底下的「API Review Agent — 最新報告」子頁（`469303303`），每次執行整頁覆寫 |
| `CONFLUENCE_EMAIL` / `CONFLUENCE_API_TOKEN` | `--notify-confluence` 認證用 | 選填，**每個人要用自己的**：email 是你的 Atlassian 帳號，API token 在 <https://id.atlassian.com/manage-profile/security/api-tokens> 自己申請一組。只要在目標 space 有編輯權限就能更新，不綁定特定人或特定 Claude 帳號 |

### Token 過期怎麼辦

上面這幾個 token（`FEDGPT_ACCESS_TOKEN`、`FEDGPT_STG2_TOKEN`、`HELIX_ENROLLMENT_TEST_TOKEN`）都會過期，或是帳號在別處登出就會被撤銷。**如果知道那個帳號的帳號密碼，可以自己打登入端點換一組新的**，不用等別人給：

```bash
curl -X POST https://fedgpt-stg2-vm1.corp.ailabs.tw/api/public/auth/v2/fedgpt/login \
  -H "Content-Type: application/json" \
  -d '{"authKey": "<帳號>", "authSecret": "<密碼>"}'
```
（打 dev 環境就把 base URL 換成 `https://fedgpt-dev.corp.ailabs.tw/api`；LDAP 帳號改打 `/public/auth/v2/ldap/login`。）回應的 `token` 欄位就是新的 access token，複製貼到 `.env` 對應的變數裡蓋掉舊的就好。

`AUTH_LOGIN_TEST_USERNAME`/`AUTH_LOGIN_TEST_PASSWORD` 這組次要帳號本身沒有另外的帳號管理介面——密碼是多少、要不要換都是團隊內部自行決定，這個工具不負責建立或改密碼，**只要把目前實際有效的帳號密碼更新進 `.env` 就好**，不需要動到程式碼。

### 基本用法
```bash
python run_check.py                    # 只做靜態比對 + 跟上次執行的 spec diff（不需要 token）
python run_check.py --local swagger-spec   # 離線模式，吃本機存的 YAML 快照，不連網
python run_check.py --live              # 加上對 GET 端點的 live call（唯讀，需要 token）
python run_check.py --live-write        # 加上 POST/PUT/DELETE 測試（見下方警語）
python run_check.py --llm-check         # 加上 LLM 文字審查（會呼叫真的 LLM API，較慢；評分標準見下方說明）
python run_check.py --notify            # 跑完推播摘要到 Google Chat
python run_check.py --notify-teams      # 跑完推播摘要到 Microsoft Teams
python run_check.py --notify-confluence # 跑完把完整結果更新到固定的 Confluence 頁面（不綁定特定人的帳號）
python run_check.py --no-diff           # 不跟快照比對（預設會比對並更新快照）
python run_check.py --diff-against v3.10  # 強制跟指定版本的快照比對，而不是自動選最接近的版本
python run_check.py --base-url https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/docs --token <stg2 token>
```
以上旗標可以自由組合。想固定測 stg2 的話，直接用包好的腳本——Windows 用 [test_stg2.ps1](test_stg2.ps1)，Mac／Linux 用 [test_stg2.sh](test_stg2.sh)（邏輯完全一致，兩者互為對應）：
```powershell
# Windows (PowerShell)
.\test_stg2.ps1                  # 等同 --live --llm-check，指向 stg2，自動吃 FEDGPT_STG2_TOKEN
.\test_stg2.ps1 --notify-teams   # 後面接的參數都會轉給 run_check.py
```
```bash
# Mac / Linux
chmod +x test_stg2.sh              # 第一次用要先給執行權限
./test_stg2.sh                     # 等同 --live --llm-check，指向 stg2，自動吃 FEDGPT_STG2_TOKEN
./test_stg2.sh --notify-teams      # 後面接的參數都會轉給 run_check.py
```
`test_stg2.ps1`/`test_stg2.sh` 已固定把 `--llm-check` 寫進指令裡，**每次用這兩支腳本都會跑 LLM 文字審查**（見下方「LLM 文字審查怎麼評分」），不需要另外加旗標；想跳過的話要改用「不透過包好的腳本」那種自己組 `run_check.py` 指令的方式，不要加 `--llm-check`。

⚠️ **`--live-write` 會真的建立、修改、刪除資料**，目前涵蓋 30 組測試（完整清單見第 7 節）。設計上測完會自動清除，建立的測試資料標題都會標成 `[agent-test] ...` 方便辨識，但這終究是會動到 live 環境資料的操作，跑之前想清楚指向的是哪個環境。幾個例外要特別注意：
- **Knowledge V3 文件、Asura TTS/transcriptions 測試**會實際走 Asset V2 或 Asura 自己的上傳流程，上傳的測試檔案**永久留在 storage、無法清除**（這幾支 API 本身沒有查詢或刪除端點）——這是已知且接受的殘留，不是 bug。Asura transcriptions 連工作紀錄本身也沒有 DELETE，一樣永久留著。
- **FedFlow execute 測試**是真的觸發一次 flow 執行，**沒有回溯機制**。預設打的是 stg2 上一個已人工確認無副作用的 flow，換一顆 flow 前務必先確認清楚它的實際行為。
- **Chat V2 相關測試**（送訊息、四個 mode、串流版本）會真的呼叫一次 LLM，有實際的 API 用量／成本。

### 常用指令速查

**完整驗證（stg2，含全部 30 組 `--live-write` + LLM 文字審查）**——目前涵蓋範圍最完整的跑法：
```powershell
# Windows
.\test_stg2.ps1 --live-write --notify-teams
```
```bash
# Mac / Linux
./test_stg2.sh --live-write --notify-teams
```

**只想看有沒有變化，不用整組寫入測試**——不會建立/刪除任何真實資料，比加 `--live-write` 快很多，適合日常快速確認（但一樣會跑 LLM 文字審查，仍有其呼叫成本與耗時，見上方說明）：
```powershell
# Windows
.\test_stg2.ps1
```
```bash
# Mac / Linux
./test_stg2.sh
```

**不透過包好的腳本，自己指定 token**（Windows／Mac／Linux 通用，把 `python` 換成 `python3` 即可）：
```bash
python run_check.py --base-url https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/docs --token <stg2 token> --live-write
```

**跟指定的舊版本比對 spec 差異**（而不是自動選最接近的版本，見 3.2 節）：
```bash
python run_check.py --diff-against v3.10
```

**不透過 `test_stg2` 腳本、直接用 `run_check.py` 時要自己手動加 `--llm-check`**（腳本才是固定內建，直接呼叫 `run_check.py` 不會自動加）：
```bash
python run_check.py --live-write --llm-check --notify-teams
```

### LLM 文字審查怎麼評分

`--llm-check`（見 [llm_check.py](agent/llm_check.py)）除了原本就有的「揪出具體問題」（描述含糊不清、或跟同一支 endpoint 裡別的描述互相矛盾），每支有寫 description 的 endpoint 還會額外拿到一個 **1-5 分的清晰度分數**（`llm_clarity_score` finding，info 等級），當作輔助參考：

- **5 分**：完全清楚，沒有任何問題
- **3 分**：堪用，但有一些含糊不清的地方
- **1 分**：混亂/矛盾到會擋住串接
- （2、4 分沒有明講定義，由 LLM 自己內插）

幾個重要限制：
- 這是 LLM 主觀判斷出來的分數，**不是用公式算出來的**（不是「issue 數 ÷ 描述數」之類），同一份文件換一次措辭、換一個模型版本，分數可能會飄動，不適合拿來做嚴格的長期趨勢比較。
- Prompt 有特別要求「就算給高分也要照樣列出具體問題」，避免 LLM 為了給漂亮分數而少報問題——分數是**補充摘要**，不是拿來取代下面列出的具體 issue。
- 分數只看「清不清楚、有沒有矛盾」，不評估文件寫得美不美、格式好不好，也不管有沒有寫 description（完全沒寫的欄位由 `checks.py` 的靜態規則抓，不會拿來影響 LLM 分數）。

`--live`/`--live-write`/`--llm-check` 開始跑之前，會先打一支輕量端點確認 `--token` 有沒有過期或被撤銷（`check_token_valid`，見 [live_call.py](agent/live_call.py)）；驗證失敗會直接印出清楚的錯誤訊息並中止，不會往下跑一堆誤導性的結果。這是因為 token 失效時，如果沒有這個檢查，後面每支端點都會各自回報一個「回傳 401 但 spec 沒宣告」的 `undocumented_status_code`，報告會出現一整排看起來像是各自獨立的 spec 問題，其實共同原因只有一個。

### 報告在哪裡看
每次執行都會在 `reports/`（已加進 `.gitignore`）產生一份帶時間戳的 HTML 檔案，直接用瀏覽器開就能看。這個資料夾只在本機，不會自動分享出去。

**線上共用連結：Confluence（推薦）**——加上 `--notify-confluence` 就會把這次完整結果更新到 `CONFLUENCE_REPORT_PAGE_ID` 那頁固定的 Confluence 頁面（[API Review Agent — 最新報告](https://ailabstw.atlassian.net/wiki/spaces/FEDGPT/pages/469303303/API+Review+Agent)），網址永遠不變。這是直接打 **Confluence 公開的 REST API**（`agent/confluence_report.py`），不透過 Claude，也**不綁定任何人的帳號**——每個人在 `.env` 填自己的 `CONFLUENCE_EMAIL`/`CONFLUENCE_API_TOKEN`，只要在目標 space 有編輯權限就能更新到同一頁，這點跟下面的 Claude Artifact 不一樣。呈現上是純表格（沒有摺疊分類、深色模式），不是完整搬運 `reports/` 那份互動式 HTML。

```bash
python run_check.py --live-write --notify-confluence --notify-teams
```

**線上共用連結：Claude Artifact（舊做法，會綁定發布者的 Claude 帳號）**——透過 Claude Code session 手動發布成一個連結（用 Artifact 工具）。**這個連結只有發布它的那個 Claude 帳號能更新**，如果之後會有不同人輪流跑這個工具、更新同一份報告，不建議用這個，改用上面的 Confluence 方式。

**推播卡片裡的報告連結**：設定 `REPORT_LINK_URL` 後，`--notify`/`--notify-teams` 的卡片會改成連到那個網址（建議設成 Confluence 頁面的網址），不會再顯示只有本機看得到的檔案路徑。⚠️ 不管連到 Confluence 還是 Artifact，`REPORT_LINK_URL` 本身都**不會自動觸發同步**——要嘛跑的時候有加對應的 `--notify-confluence`（會自動同步），要嘛是走 Artifact 那條路、每次都要手動 publish 一次，兩者不能只設網址就期待內容自動跟上。

---

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

**每次執行都跟快照做 diff**：第一次看到某個版本時沒有基準可比，只會存下來；之後每次執行都會抓出跟基準比較的差異，抓完再把這次內容存回去當下次的基準。

已經做出雛形並實測驗證（`agent/spec_diff.py`），會抓：

- endpoint / method 整支被新增或移除
- 欄位從選填變必填（對呼叫方是破壞性變更）、必填變選填
- 欄位被新增／移除
- 欄位型別改變
- enum 合法值被移除（破壞性）或新增
- 宣告的回應狀態碼被移除或新增

驗證方式：手動在快照裡模擬「欄位從選填變必填」跟「新增一個狀態碼」兩種變化，重跑一次，兩筆都被正確抓出來，嚴重度也分得出來（前者 error、後者 info）。

**快照依版本號分資料夾**：`spec_snapshots/<version>/<檔名>.yaml`，版本號直接讀每份 spec 自己的 `info.version`。實測發現這個欄位在不同環境行為不一樣：dev 環境（`fedgpt-dev`）固定回 `"latest"`（跟得上最新開發進度，本來就沒有版號概念）；**stg2 環境會回真的版號**（實測當下是 `v3.12`），代表 stg2 是跟著特定 sprint release 走的。

比對基準的選擇邏輯：
- 目前版本本地端已經有快照 → 跟它比（等同「跟上次執行這個版本時比」）。
- 目前版本是本地端第一次看到（例如剛切到新版本）→ **自動改跟本地端已存過、版號最接近的舊版本比**，而不是直接當「沒東西可比」，這樣版本切換的當下就能立刻抓到破壞性變更。
- 也可以用 `--diff-against v3.10` 明確指定要跟哪個版本比（該版本本地端沒有快照的話會報 warning，不會硬比）。

不管跟誰比，這次抓到的內容一律存回「目前版本」自己的資料夾，不會覆蓋掉其他版本的快照。


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

跟 PM 討論後拍板：「有對外的 API 都要檢查」不只是指涵蓋所有 endpoint，也指檢查方法本身要全面，不能只做唯讀。`agent/live_call.py` 只打 GET；寫入方法的測試在 `agent/live_write_test.py`，透過 `--live-write` 啟用。

**測試資料隔離／清理策略（已定案並實作）**：所有測試資料的名稱/標題都以 `[agent-test]` 開頭方便辨識；每組測試都是「建立 → GET 驗證 → 修改 → GET 驗證 → 刪除 → GET 驗證真的刪了」的完整閉環，只要有明確的資源 ID 就會嘗試清乾淨，不論中途驗證步驟是否有失敗；只有在建立本身就失敗、拿不到 ID 的情況下才會整組放棄（因為根本沒建立任何東西）。

**目前已涵蓋（30 組，全部驗證通過或正常跳過；46 個公開寫入端點全數涵蓋，無刻意排除項目）**：
- Chat V2 對話（`run_conversation_crud_test`）、送訊息（`run_chat_send_message_test`）、四個資源型 mode 的一次性與串流版本（`run_chat_{knowledge,agentic_rag,faq,tabular}_mode_test` / `run_chat_{knowledge,agentic_rag,faq,tabular}_stream_test`）、`normal` 的串流版本（`run_chat_normal_stream_test`）——全部 9 個都是 SSE 方言 A
- FAQ V1 問答集容器（`run_faq_crud_test`）與底下的問與答 entry（`run_faq_entry_crud_test`，entry 掛在臨時建立的父層容器下測試）
- Knowledge V3 知識庫容器（`run_knowledge_crud_test`）與底下的文件（`run_knowledge_document_crud_test`，會真的走 Asset V2 上傳流程，見上方使用說明的警語）
- FedFlow V1 的 flow 執行（`run_fedflow_execute_test`，沒有 DELETE、無法復原，固定打一個已人工確認安全的 flow，見 `FEDFLOW_TEST_FLOW_ID`）
- Helix V1 的獨立聲紋 `/voices`（`run_helix_voice_crud_test`）、用錄音搜尋既有聲紋 `/voices:searchByAudio`（`run_helix_voice_search_by_audio_test`，需要先臨時建一個聲紋當搜尋目標，`/voices` 建立本身有已知的不穩定性，失敗就直接跳過整組）與帳號自己的 `/enrollment`（`run_helix_enrollment_test`，需要 `HELIX_ENROLLMENT_TEST_TOKEN` 指到一個從沒註冊過聲紋的獨立測試帳號，不會碰主要測試帳號的真實註冊）——三支都需要 `HELIX_TEST_AUDIO_PATH` 指到本機真的音檔
- Auth V2 的 API key CRUD（`run_auth_apikey_crud_test`）、`fedgpt/login` + `logout`（`run_auth_login_logout_test`，固定用 `AUTH_LOGIN_TEST_USERNAME`/`AUTH_LOGIN_TEST_PASSWORD` 指定的次要帳號，不會動到主要測試帳號的 token；刻意不測密碼打錯，避免累積失敗次數觸發帳號鎖定）、`ldap/login`（`run_auth_ldap_login_test`，先查 `GET /providers` 確認有沒有開，這組次要帳號已知只給 `fedgpt` 用，預期以 401 正常跳過）；以及 Admin V1（`run_admin_apikey_crud_test`，只操作測試帳號自己的 userId，不碰其他使用者）的 API key CRUD
- LLM V1 的 embeddings（`run_llm_embeddings_test`）、visual completions（`run_llm_visual_completions_test`，圖片網址用固定的公開測試圖，部署環境若限制對外連線可能因此失敗——那是環境限制不是 API 問題）與直接的 chat completions（`run_llm_chat_completions_test`，先 `GET /models` 拿代稱，回 404 時照文件建議改用帶版號的模型 ID 重試一次）
- Asura V1 的文字轉語音（`run_asura_tts_test`）、零樣本語音克隆（`run_asura_speech_zero_shot_test`，目前這個環境上這支端點有 bug，見下方）、語音轉文字（`run_asura_transcription_test`，沒有 DELETE，工作紀錄永久留存）、即時轉錄連線 token（`run_asura_neartime_token_test`，只驗證取得憑證的契約，不實際連 WebSocket）

**測試過程中意外發現的 spec 正確性問題**（都已記錄成報告裡的 `spec_description_mismatch`、`response_schema_mismatch` 或 `undocumented_status_code` finding）：
- `DELETE /knowledge/v3/knowledges/{knowledgeId}` 文件寫「底下的文件會一起消失」，實測要先清空文件才能刪除（回 423，且未宣告這個狀態碼）
- `POST /helix/v1/voices` 的 `audioUris` 文件建議走 Asset V2 上傳取網址，但 Asset V2 給的是 `assetKey` 不是網址，直接送出去實際回 502（未宣告）
- `FedFlow` 執行結果的 `state` 出現未宣告的 `PENDING`
- Asura TTS 的 `SpeechModelConfig.model` 範例值在 stg2 不存在，且沒有任何端點可查詢正確的模型名稱清單
- Asura TTS 的 `audioConfig` 文件寫選填，實測是必填
- `chat/knowledge` 對一個沒有索引內容的知識庫送訊息會回 404，但文件的 404 成因列表沒提到這種情況；且與 `agentic-rag`「同一套 params 規則」的說法不同，`agentic-rag` 對空知識庫沒有這個限制
- `GET /chat/v2/tabulars` 沒有任何欄位可以判斷資源是否「ready for query」，實測部分既有資源會在送訊息時回 423（未宣告）
- `POST /helix/v1/enrollment` 拒絕多說話者音檔時（`MULTIPLE_SPEAKERS`），回應的 `service`／`reason` 都是空字串，不符合 Error schema「`reason` 必填、程式請用它做分支」的說明
- `POST /helix/v1/enrollment` 換成確認過的單人語音音檔後，`MULTIPLE_SPEAKERS` 這關過了，但緊接著穩定回 **500 Internal Server Error**（`{"service":"","reason":"","message":"500: Internal Server Error"}`），spec 沒有宣告這個狀態碼；重試一次結果一樣，不是偶發。跟音檔是不是單人無關（同一份音檔的 Asura 轉錄測試完全成功），比較像後端處理這支端點時有未預期的例外，是後端 bug 而非文件或測試音檔的問題
- `POST /asura/v1/speeches:zero-shot` 的 `ZeroShotSpeechInput.input` 只把 `text` 標必填，`promptVoiceUrl`/`promptVoiceAssetKey` 都是選填，但兩個都不帶實際回 400，代表伺服器端其實兩者擇一是必填
- `POST /asura/v1/speeches:zero-shot` 照 spec 建議的做法（走 `transcriptions:presign` 上傳、把 `assetKey` 填進 `promptVoiceAssetKey`）一律回 500（`http-gateway`／`data.unhandled`／`unexpected status code: 400`，代表上游回了 400 但這層沒處理、直接包成看不出原因的 500）。手動排查過排除是 assetKey 或模型名稱的問題：同一個 assetKey 打 `/transcriptions` 完全正常，換成已驗證能連得到的真實網址填 `promptVoiceUrl` 一樣回同一個 500——這支端點處理語音範本輸入的路徑本身壞了，比較像後端 bug 而非文件問題
- `POST /auth/v2/logout` 的敘述文字寫「註銷後再用同一個 token 會拿到 401 auth.invalid-auth」，實測行為跟敘述一致，但正式的 `responses` 只宣告了 `200`，401 沒有列進去——文件的敘述段落跟正式的狀態碼清單對不上
- `GET /chat/v2/conversations`／`GET /chat/v2/conversations/{convId}` 的 `Conversation` schema把 `disabled` 標成必填，但 stg2 實際回應完全沒有這個欄位
- `GET /chat/v2/faqs` 的 `Faq` schema 把 `description` 標成必填，但 stg2 實際回應完全沒有這個欄位
- 送訊息四個 mode（`agenticRag`／`faq`／`knowledge`／`normal`）回應裡的 `guardian` 物件，schema 把 `biasScore`／`hallucinationScore` 都標成必填，但 stg2 實際回應都沒有這兩個欄位
- 同上四個 mode 回應裡的 `humanInLoop` 欄位，schema 宣告型別是 `object`，但 stg2 實際回應是 `null`
- 以上四點目前看起來比較像是 spec 標必填標過頭（這幾個欄位可能設計上就是選填/條件性才會出現，例如 `guardian` 分數可能只有開啟防護功能才有），還是後端功能還沒做完，需要熟悉 Chat V2 guardian／humanInLoop 實作的人確認實際設計意圖，Agent 這邊只能標出「宣告與實際不符」，無法判斷哪邊才是對的

**工具本身的 bug（非 spec 問題，記錄下來避免以後重踩）**：`_parse_sse_dialect_a` 一開始用 `resp.iter_lines(decode_unicode=True)` 解析 SSE 串流，`requests` 這個參數會在網路封包還沒重組成完整一行前就先解碼，中文字的 UTF-8 多位元組序列被切在封包邊界時會解碼壞掉、把一則 `data:` 誤判成兩行，導致 `chat/faq:stream`、`chat/tabular:stream` 這兩個「回應剛好含中文」的測試一直收不到任何 `event: data`。修正是改讀原始 bytes 再自己用 `\n` 分行（UTF-8 裡 `\n` 永遠是安全的分割點）、每行湊齊後才解碼。

**營運上的注意事項（不是程式或 spec 問題）**：
- 曾經遇過 Knowledge V3 文件的索引長時間停在 `doing` 不動、`DELETE` 回 `423 data.locked`（`"workflow already running"`），一度以為是 workflow 卡死，兩筆 `[agent-test]` 開頭的知識庫/文件清不掉。**後來追蹤到不是卡死，是索引跑得非常慢**——同一批文件事後查詢已經變成 `displayState: "completed"`，從建立到真的完成索引中間隔了將近 1 小時 45 分鐘。比較像是 stg2 索引服務有嚴重的排隊/延遲問題，不是 workflow 真的壞掉；再遇到的話，只要等夠久（不是等幾分鐘，是等一兩個小時）通常就能正常刪除，不需要手動介入。
- 前一項的延遲間接暴露了測試程式碼自己的問題：好幾支測試用**固定字串**當臨時資源的名稱（如「...（chat mode 測試用）」），如果前一次執行建立的資源因為上面這個延遲還沒被清乾淨，下一次執行用同樣名稱建立會撞 `409 conflict`，連帶讓那組測試被跳過。修法是幫這些固定名稱都加上一段隨機後綴（`uuid.uuid4().hex[:8]`），讓同名容器不再互相卡到；已經套用到 Knowledge/FAQ 兩個 chat-mode 用的臨時容器，以及 FAQ entry 測試、Knowledge document 測試各自用的臨時容器。

**尚未涵蓋**：目前已知的公開端點全部涵蓋，沒有刻意排除的項目。

---

## 待確認 / Open Questions

1. 已確認：<https://fedgpt-dev.corp.ailabs.tw/swagger/>，OpenAPI 3.1.0 YAML，共 12 份可直接 HTTP GET 下載。
**新待確認**：dev 環境（`version: latest`）是否等同 Nina 要的「Public Swagger」本人？跟正式環境對外曝露的版本是否有落差、要不要改盯正式環境。
2.「跟上次執行比對」取代 3.10 baseline（見 3.2 節）。
3. Jira 開票的目標 project、issue type、必填欄位規則。
4. HTML report 的呈現內容與對象（給 PJM 看？給開發看？）。
5. 已啟動 GET 部分並實測 dev/stg2 環境，也已擴大到 POST/PUT/DELETE（見第 7 節，30 組寫入測試全部驗證通過或正常跳過，46 個公開寫入端點全數涵蓋，涵蓋 Chat/FAQ/Knowledge/FedFlow/Helix/Auth/Admin/LLM/Asura，含四個資源型 mode 的一次性與串流版本、Helix 用錄音搜尋聲紋、LLM 直接 chat completions、Auth V2 的 login/logout，以及用獨立測試帳號測到的 Helix `/enrollment`）。目前已知的公開端點全部涵蓋，沒有刻意排除的項目。累積抓到的真實文件/行為落差、以及營運上的注意事項（token 撤銷、知識庫索引異常緩慢、測試資源固定命名撞名）列在第 7 節。
6. CI / 排程整合的時間點——現在有了「跟上次執行比對」的機制後，這點變得更重要：要多久固定跑一次，才不會讓兩次執行之間累積太多變化，待後續討論再定案。

---

## 下一步

1. 補齊第 3 節「檢查規則」的細節定義（哪些規則第一階段可行）。
2. Review 後正式謄寫為 Confluence 頁面，回填 ticket [FEDGPT-15990](https://ailabstw.atlassian.net/browse/FEDGPT-15990)。
