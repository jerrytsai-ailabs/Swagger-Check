#!/usr/bin/env bash
# 測試 stg2-vm1 環境：靜態比對 + live call（GET）+ spec diff + LLM 文字審查。
# 用 FEDGPT_STG2_TOKEN（存在 .env，跟 dev 用的 FEDGPT_ACCESS_TOKEN 分開）。
#
# 用法（Mac/Linux）：
#   ./test_stg2.sh                  # 一般跑法（固定含 LLM 文字審查，見下方說明）
#   ./test_stg2.sh --notify-teams   # 額外推到 Teams
#   ./test_stg2.sh --live-write     # 額外跑寫入方法測試
# 後面接的參數會直接轉給 run_check.py，run_check.py 支援的旗標這裡都能用。
# 這是 test_stg2.ps1（Windows PowerShell 版）的對應腳本，邏輯完全一致。
#
# LLM 文字審查已固定寫進下面的指令，每次用這支腳本都會跑（會呼叫真的 LLM API，
# 有實際花費）。想跳過的話不要用這支腳本，改照 README「不透過包好的腳本」那段
# 自己組 run_check.py 指令、不要加 --llm-check。

set -euo pipefail

TOKEN=$(python3 -c "from agent.config import FEDGPT_STG2_TOKEN; print(FEDGPT_STG2_TOKEN)")
if [ -z "$TOKEN" ]; then
  echo "FEDGPT_STG2_TOKEN 沒有設定，請先在 .env 裡加上這一行：FEDGPT_STG2_TOKEN=..." >&2
  exit 1
fi

python3 run_check.py --live --llm-check --base-url https://fedgpt-stg2-vm1.corp.ailabs.tw/swagger/docs --token "$TOKEN" "$@"
