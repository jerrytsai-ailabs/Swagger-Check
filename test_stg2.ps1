# 測試 stg2-vm1 環境：靜態比對 + live call（GET）+ spec diff。
# 用 FEDGPT_STG2_TOKEN（存在 .env，跟 dev 用的 FEDGPT_ACCESS_TOKEN 分開）。
#
# 用法：
#   .\test_stg2.ps1                  # 一般跑法
#   .\test_stg2.ps1 --notify-teams   # 額外推到 Teams
#   .\test_stg2.ps1 --llm-check      # 額外跑 LLM 文字審查
# 後面接的參數會直接轉給 run_check.py，run_check.py 支援的旗標這裡都能用。

$ErrorActionPreference = "Stop"

$token = python -c "from agent.config import FEDGPT_STG2_TOKEN; print(FEDGPT_STG2_TOKEN)"
if (-not $token) {
    Write-Error "FEDGPT_STG2_TOKEN 沒有設定，請先在 .env 裡加上這一行：FEDGPT_STG2_TOKEN=..."
    exit 1
}

python run_check.py --live --base-url https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/docs --token $token @args
