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
- **輸入來源**：Public Swagger 頁面。

---

## 2. Agent 設計

### 2.1 輸入
- Public Swagger spec（透過提供的 Swagger 頁面取得，格式待確認：OpenAPI JSON/YAML 匯出或直接讀取頁面）。

### 2.2 檢查流程（第一階段：靜態比對）
- **本階段只做「靜態比對 Swagger 定義本身的正確性」**，不實際呼叫 API 驗證行為（不做 live call 測試）。
- Live call 驗證（例如實際打 API 驗證回應、狀態碼、向下相容行為）列為**後續階段**，待第一階段穩定後再評估開啟。

### 2.3 輸出格式
雙軌輸出：
1. **Bug 發現 → 自動開 Jira 票**：Agent 檢查出的問題以 Jira issue 形式建立，方便追蹤與指派。
2. **每輪執行 → 產出一份 HTML report**：每次跑完整份 Public Swagger 的檢查後，產出一份總覽用的 HTML 報告（含本輪檢查了哪些 endpoint、通過/失敗數量、對應開出的 Jira 票清單等）。

> 待釐清：Jira 開票的專案/issue type/欄位規則、報告內容的具體欄位與呈現方式，需要在正式設計文件中細化。

---

## 3. 檢查規則 vs. 驗收標準

依原 ticket 的驗收標準，轉譯為 Agent 可自動檢查的靜態比對規則：

| 原始驗收標準 | Agent 靜態檢查規則（第一階段） |
|---|---|
| key 必填與否 | 比對 Swagger 中 `required` 欄位定義是否與實際規格一致（本階段先確認 Swagger 定義本身無矛盾/缺漏，非 live 驗證） |
| key / endpoint 描述是否正確（範圍、範例） | 檢查每個 key、endpoint 是否都有 description，是否有標明合理範圍與範例值 |
| Swagger 上列出的 endpoint 都要存在 | 本階段先確認 Swagger 定義完整、無明顯缺漏/重複（實際 endpoint 是否存在待 live call 階段驗證） |
| 狀態碼 | 檢查 Swagger 是否有定義完整的回應狀態碼（實際狀態碼是否正確待 live call 階段驗證） |
| API 向下相容（自 3.10 起） | **待取得 3.10 版本 Swagger 後**，以 3.10 為 baseline 與目前版本做 diff，找出破壞性變更（本階段尚未啟動，見第 4 節） |

> 待釐清：需要與 James Yang review 確認，哪些規則第一階段就能做到「純靜態比對」、哪些其實仍需要 live call 才能驗證。

---

## 4. 時程

- **一次跑完所有 Public endpoints 需要多久**：待評估（取決於 endpoint 數量與檢查規則複雜度，需在設計階段抓一個初估數字）。
- **是否接入 CI 或每個 sprint 固定跑**：**本階段先不決定，留待後續討論**。第一階段先以人工觸發執行為主。

---

## 5. 3.14 可行性

- Ticket 原始期望：3.14 版本時，Public Swagger 已修正目前已知問題。
- 向下相容 diff（3.10 baseline）**需等拿到 3.10 版本 Swagger 才能開始**，是這條時程上的前置依賴，需要在時程規劃中特別標注。
- 待釐清：3.14 的 code freeze / release 日期，回推 Agent 需要在什麼時間點前產出第一輪完整報告，才來得及讓開發修復。

---

## 6. 風險與所需資源

**風險**
- 第一階段僅做靜態比對，「Swagger 寫得對」不等於「API 實際行為對」，可能與 SI 廠商實際串接經驗有落差，需要在報告中明確標注本階段的檢查邊界，避免被誤解為完整驗收。
- 3.10 版本 Swagger 尚未取得，向下相容檢查目前無法評估工作量與風險。
- 自動開 Jira 票若規則寫得太寬，可能產生大量誤判票，先開成草稿待 Review 後再開卡。

**所需資源**
- Public Swagger 頁面連結（Jerry 提供）。
- 3.10 版本 Swagger（待取得，向下相容檢查的前置依賴）。
- Jira 開票所需的專案權限 / API token。
- 待確認：Agent 執行環境（本機 / CI runner）、開發時程與人力。

---

## 待確認 / Open Questions

1. Public Swagger 頁面的具體連結與格式（OpenAPI JSON/YAML？可否直接下載）— Jerry 提供中。
2. 3.10 版本 Swagger 何時可以取得。
3. Jira 開票的目標 project、issue type、必填欄位規則。
4. HTML report 的呈現內容與對象（給 PJM 看？給開發看？）。
5. Live call 驗證（第二階段）的啟動時機與範圍。
6. CI / 排程整合的時間點，待後續討論再定案。

---

## 下一步

1. 依此草稿確認 Jerry 提供的 Public Swagger 連結，實際看一次規格內容。
2. 補齊第 3 節「檢查規則」的細節定義（哪些規則第一階段可行）。
3. 找 **James Yang** discuss & review，cc Jessica Kao、Winter Deng。
4. Review 後正式謄寫為 Confluence 頁面，回填 ticket [FEDGPT-15990](https://ailabstw.atlassian.net/browse/FEDGPT-15990)。
