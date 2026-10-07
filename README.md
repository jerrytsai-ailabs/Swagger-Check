# API Review Agent

自動檢查 FedGPT Public Swagger 的工具，對應 [FEDGPT-15990](https://ailabstw.atlassian.net/browse/FEDGPT-15990)（請 QA 測試 Public API）。以 **Public Swagger 本身為 ground truth**，目標是讓 SI 廠商與 PJM 只看 Swagger 就能正確呼叫 API。

一次執行會做這些事（可自由組合）：靜態檢查 spec 本身、實際呼叫 API 驗證回應、追蹤 spec 隨時間的變化、用 LLM 審查文件寫得清不清楚，最後產出 HTML 與 Markdown 報告，並可推播到 Teams / Google Chat / Confluence。

需求釐清、設計決策與開發過程的紀錄見 [docs/background.md](docs/background.md)。

---

## 快速開始

```bash
git clone <repo>
cd SwaggerAgent
pip install -r requirements.txt
```
Mac／Linux 上如果 `python`/`pip` 不是 Python 3，改用 `python3`/`pip3`（下面所有指令同理）。

### 設定 `.env`

在專案根目錄建立 `.env`（已在 `.gitignore`，不會進版控），依需要填入：

| 變數 | 用途 | 沒設定時 |
|---|---|---|
| `FEDGPT_ACCESS_TOKEN` | 打 **dev** 環境用的 access token | live 相關階段無法執行 |
| `FEDGPT_STG2_TOKEN` | 打 **stg2-vm1** 環境用的 access token（兩環境帳號不互通） | `test_stg2` 腳本無法執行 |
| `FEDFLOW_TEST_FLOW_ID` | `--live-write` 測 FedFlow execute 用的 flow | 預設 stg2-vm1 上已確認無副作用的 flow "Second"（`212913c58069778f5f399a3f466d7ffc`），其他環境會跳過 |
| `HELIX_TEST_AUDIO_PATH` | Helix 聲紋與 Asura 語音相關測試用的音檔（要單人語音） | 使用 `test-data/harvard.aac`；檔案不存在就跳過這幾組測試 |
| `HELIX_ENROLLMENT_TEST_TOKEN` | 測 Helix `/enrollment` 用的獨立帳號 token（一個帳號只能註冊一次，不能用主要帳號） | 跳過 |
| `AUTH_LOGIN_TEST_USERNAME` / `AUTH_LOGIN_TEST_PASSWORD` | 測 Auth V2 login / logout 用的**次要帳號**（避免動到主要帳號的 token） | 跳過 |
| `TEAMS_WEBHOOK_URL` | `--notify-teams` 推播（Teams → Workflows →「When a Teams webhook request is received」） | 無法推播 |
| `GOOGLE_CHAT_WEBHOOK_URL` | `--notify` 推播（Space → Apps & integrations → Webhooks） | 無法推播 |
| `REPORT_LINK_URL` | 推播卡片「開啟完整報告」的連結，建議設成 Confluence 報告頁 | 退回本機報告的檔案路徑 |
| `CONFLUENCE_EMAIL` / `CONFLUENCE_API_TOKEN` | `--notify-confluence` 認證，**每個人用自己的**（[申請 API token](https://id.atlassian.com/manage-profile/security/api-tokens)） | 無法更新 Confluence |
| `CONFLUENCE_REPORT_PAGE_ID` / `CONFLUENCE_SITE_URL` | 要覆寫的 Confluence 頁面（目前是 `469303303`）與站台 | 站台預設 `https://ailabstw.atlassian.net` |

測試用的檔案（目前是語音測試用的 `harvard.aac`）統一放在專案根目錄的 `test-data/`。

### Token 過期怎麼辦

token 會過期，帳號在別處登出也會被撤銷。知道帳密的話可以自己換一組新的，把回應裡的 `token` 貼回 `.env`：

```bash
curl -X POST https://fedgpt-stg2-vm1.corp.ailabs.tw/api/public/auth/v2/fedgpt/login \
  -H "Content-Type: application/json" \
  -d '{"authKey": "<帳號>", "authSecret": "<密碼>"}'
```
dev 環境把網域換成 `fedgpt-dev.corp.ailabs.tw`；LDAP 帳號改打 `/api/public/auth/v2/ldap/login`。

---

## 常用指令

**stg2-vm1 用包好的腳本**（固定帶 `--live --llm-check`，自動吃 `FEDGPT_STG2_TOKEN`，後面接的參數會轉給 `run_check.py`）：

```powershell
# Windows
.\test_stg2.ps1                              # 日常檢查：不動任何資料，但會跑 LLM 審查（有呼叫成本）
.\test_stg2.ps1 --notify-teams               # 同上 + 推 Teams
.\test_stg2.ps1 --live-write --notify-teams  # 完整驗證：含寫入測試（會動到 stg2-vm1 資料，見下方注意事項）
```
```bash
# Mac / Linux（第一次先 chmod +x test_stg2.sh）
./test_stg2.sh --live-write --notify-teams
```

**直接用 `run_check.py`**：

```bash
python run_check.py                                   # 只做靜態檢查 + spec diff（不需要 token），預設打 dev
python run_check.py --base-url https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/docs --token <stg2-vm1 token> --live --llm-check
```

| 旗標 | 作用 |
|---|---|
| `--base-url <url>` | 要檢查的 Swagger docs 目錄，預設 dev（`https://fedgpt-dev.corp.ailabs.tw/swagger/docs`） |
| `--token <token>` | 覆寫 `FEDGPT_ACCESS_TOKEN` |
| `--live` | 對所有 GET 端點做 live call（唯讀） |
| `--live-write` | 加跑 POST/PUT/DELETE 寫入測試 ⚠️ |
| `--llm-check` | LLM 文字審查（較慢，有呼叫成本） |
| `--no-diff` | 不做 spec diff，也不更新快照 |
| `--diff-against <版本>` | 強制跟指定版本的快照比對（例如 `v3.12`） |
| `--local <資料夾>` | 離線模式，讀本機 YAML 不連網 |
| `--notify-teams` / `--notify` / `--notify-confluence` | 跑完推播到 Teams / Google Chat / 覆寫 Confluence 報告頁 |
| `--output-dir` / `--docs-dir` / `--snapshot-dir` | HTML 報告（預設 `reports/`）、Markdown 報告（預設 `docs/`）、spec 快照（預設 `spec_snapshots/`）的位置 |

`--live`/`--live-write`/`--llm-check` 開跑前會先確認 token 有效，失效就直接中止並印出原因，不會產生一整排誤導性的 401 結果。

**每日定時檢查（Windows）**：`scheduled_run.ps1` 會先確認連得到 stg2-vm1（連不到就跳過，不留無效的執行歷史），再跑 `test_stg2.ps1 --live-write --notify-teams --notify-confluence`（Confluence 需要 `.env` 裡的 `CONFLUENCE_EMAIL` / `CONFLUENCE_API_TOKEN`），輸出存到 `logs/scheduled-<日期時間>.log`。註冊成平日 09:30 的排程（用自己的帳號、登入時才跑；錯過時間會在開機後補跑）：

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scheduled_run.ps1`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "SwaggerAgent stg2-vm1 daily" -Action $action -Trigger $trigger -Settings $settings
```

想立刻試跑：`Start-ScheduledTask "SwaggerAgent stg2-vm1 daily"`；停用：`Disable-ScheduledTask "SwaggerAgent stg2-vm1 daily"`。每天都會更新 `docs/REPORT.md` 與執行歷史（只保留最近 30 筆），寫入測試每次約留下 10 筆刪不掉的資料。

---

## 檢查內容

檢查對象是 Swagger UI 載入的 **12 份 OpenAPI 3.1.0 YAML**（`/swagger/docs/*.yaml`）：Common、SSE 串流兩份是說明文件，另外 10 份是功能分頁（Admin、Asset、Asura、Auth、Chat、FAQ、FedFlow、Helix、Knowledge、LLM），目前共 **61 個 path、81 個 operation**。所有端點路徑都以 `/api/public/...` 開頭（[FEDGPT-16706](https://ailabstw.atlassian.net/browse/FEDGPT-16706) 之後），`servers.url` 是 `/`。

| 階段 | 旗標 | 檢查什麼 | 程式 |
|---|---|---|---|
| 靜態檢查 | （預設） | schema 合法性、endpoint / 欄位缺 description、純量欄位缺 example、example 型別不符、路徑不是 `/api/public/` 開頭（有白名單例外）、SSE 說明文件指到的端點是否存在 | [checks.py](agent/checks.py) |
| Live GET | `--live` | 實際打所有 GET 端點：狀態碼有沒有在 spec 宣告過、回應是否符合宣告的 schema | [live_call.py](agent/live_call.py) |
| Live 寫入 | `--live-write` | 30 組「建立 → 驗證 → 修改 → 驗證 → 刪除 → 驗證」測試，涵蓋 51 個寫入端點中的 46 個 | [live_write_test.py](agent/live_write_test.py) |
| Spec diff | （預設） | 跟上次存的快照比：端點增刪、選填↔必填、欄位增刪、型別改變、enum 值增刪、狀態碼增刪 | [spec_diff.py](agent/spec_diff.py) |
| LLM 審查 | `--llm-check` | 每支 endpoint 的說明是否含糊（warning）或互相矛盾（error） | [llm_check.py](agent/llm_check.py) |

**設計原則：Agent 只標缺漏，不代寫內容。** 例如它能指出「這個欄位沒有 description」，但正確內容取決於後端實際行為，要由熟悉實作的人確認後補上。

### 寫入測試尚未涵蓋的端點

| 端點 | 為什麼還沒測 |
|---|---|
| FedFlow `POST/DELETE /packages/{language}`、`POST /packages/{language}/reset` | 套件裝卸作用在**整站共用**的執行環境，會影響所有 flow，需要先確定安全的測試方式 |
| OpenAI V1 `POST /fedgpt/v1/audio/transcriptions`、`POST /fedgpt/v1/audio/speech` | 新增的 OpenAI 相容端點，尚未補測試 |

### Spec diff 的比對基準

快照依 spec 的 `info.version` 分資料夾存放（`spec_snapshots/<version>/`）。目前 dev 回 `latest`、stg2-vm1 回 `v3.13`。
- 該版本已有快照 → 跟它比，比完用這次的內容覆蓋，當下次的基準。
- 第一次看到這個版本 → 自動改跟本機版號最接近的舊版本比（報告會出現 `diff_baseline_cross_version` 提示）。
- `--diff-against` 可指定基準；想保留某個時間點的基準，先把資料夾複製一份（例如 `spec_snapshots/latest-20260924/`）。
- spec diff 只比結構，description 這類純文字修改不會出現在結果裡。

### Pass / Fail 怎麼判定

- **LLM 審查**：一支 endpoint 只要有任何一筆 `llm_description_issue` 就是 Fail，完全沒有才是 Pass。由程式從 issue 清單推導，不是另外叫 LLM 下結論。
- **Endpoint Pass 率**（報告最上方）：以 endpoint 為單位，符合任一條件就算 Fail——LLM 審查判 Fail，或任何階段對它回報至少一筆 Error（例如 `response_schema_mismatch`、`undocumented_status_code`）。只計算有被某個階段點名過的 endpoint。
- ⚠️ Pass 率反映的是**這次實際跑到的範圍**：沒加 `--live-write` 時，只有寫入測試才打得到的問題不會被算進來，Pass 率會明顯偏高。

---

## 報告與通知

| 輸出 | 位置 | 說明 |
|---|---|---|
| HTML 報告 | `reports/FedGPT-API-Review-<環境>-yyyy-mm-dd.html` | 每次產生一份（同一天第二次起檔名加上時分秒），細節最完整（可摺疊、深色模式）。單一檔案、不含任何 token，可以直接傳給別人用瀏覽器開。只在本機，不進版控 |
| Markdown 報告 | [docs/REPORT.md](docs/REPORT.md) + `docs/report-history.json` | 每次覆寫，**要 commit 進 git**。用 `git diff docs/REPORT.md` 看這次跟上次差在哪；內含執行歷史表。同一根因合併成一行、Info 只列數量、LLM 細項放最後（LLM 每次結果會有些浮動） |
| Confluence | [API Review Agent — 最新報告](https://ailabstw.atlassian.net/wiki/spaces/FEDGPT/pages/469303303/API+Review+Agent) | `--notify-confluence` 整頁覆寫，網址不變；用各自的 Atlassian API token，不綁定特定人 |
| Teams / Google Chat | 推播卡片 | 摘要 + 報告連結（`REPORT_LINK_URL`）。設了連結不會自動同步內容，要搭配 `--notify-confluence` |

---

## 注意事項

**`--live-write` 會真的建立、修改、刪除 live 環境的資料。** 測試資料標題都以 `[agent-test]` 開頭，測完會自動清除，但有幾個例外：
- **永久殘留**：Knowledge V3 文件、Asura TTS / 轉錄上傳的檔案會永久留在 storage（沒有刪除端點），Asura 轉錄的工作紀錄也沒有 DELETE。這是已知且接受的殘留。
- **無法回溯**：FedFlow execute 會真的執行一次 flow，換 flow 前務必先確認它的實際行為。
- **有成本**：Chat V2 送訊息相關測試會真的呼叫 LLM。
- **中途中斷會留下資料**：網路斷線或程式 crash 時，已建立的資源可能來不及清除。事後請用 GET 列出各資源，找標題含 `[agent-test]` 的手動處理；網路不穩時結果也不可信，建議整輪重跑。
- Knowledge 文件索引有時非常慢，刪除會回 `423 data.locked`，等索引完成後再刪即可。

---

## 目前已知的 spec 問題

最新狀況以 [docs/REPORT.md](docs/REPORT.md) 為準。以下是反覆出現、需要負責人確認的問題（2026-09-29 stg2-vm1 v3.13）：

| 分頁 | 問題 |
|---|---|
| Chat V2 | `Conversation.disabled`、`Faq.description`、`guardian.biasScore`/`hallucinationScore` 在 schema 標必填，實際回應沒有；`humanInLoop` 宣告 object、實際是 `null`；`sseEvents.content.role` 出現空字串。**佔所有 Error 的大宗**，需要確認是 spec 標過頭還是實作未完成 |
| Chat V2 | `chat/tabular` 對部分 tabular 回未宣告的 `423`（沒有欄位可以判斷是否 ready for query）；`chat/knowledge` 對無索引內容的知識庫回 404 |
| Asura V1 | `transcriptions` 建立工作回 `500`（2026-09-29 起）；`speeches:zero-shot` 照 spec 的做法回 `500`；zero-shot 的 `promptVoiceUrl`/`promptVoiceAssetKey` 實際是二擇一必填；TTS 的 `audioConfig` 實際必填、`model` 範例值不存在 |
| Helix V1 | `enrollment` 穩定回 `500`；`voices` 用 Asset V2 的 `assetKey` 當 `audioUris` 會回 `502` |
| Knowledge V3 | `DELETE /knowledges/{id}` 文件寫「底下文件一起消失」，實際要先清空文件，否則回未宣告的 `423` |
| Auth V2 | `logout` 敘述提到 401，但 `responses` 只宣告 200 |
| FedFlow V1 | 執行結果的 `state` 出現未宣告的 `PENDING` |
| OpenAI V1 | `retriever/v1/embeddings` 的 404 說明疑似從 400 複製貼上（LLM 審查） |

---

## 待決事項

- **自動開 Jira 票**：原始需求之一，尚未實作（目標 project、issue type、欄位規則未定）。
- **固定執行節奏 / 接 CI**：spec diff 是「跟上次執行比」，間隔太久會把多次變化壓成一包。
- **要盯哪個環境**：dev（`latest`，跟最新開發）與 stg2-vm1（`v3.13`，跟發布版本）之外，是否要改看正式環境對外的版本。
