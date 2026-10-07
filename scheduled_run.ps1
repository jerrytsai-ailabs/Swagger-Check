# 給 Windows 工作排程器呼叫的包裝腳本：每個平日 09:30 自動跑一次 stg2-vm1 完整檢查（含寫入測試）並推 Teams、覆寫 Confluence 報告頁。
# 排程註冊方式見 README「每日定時檢查」；手動跑一次這支也可以拿來確認排程設定沒問題。
#
# 跟直接跑 test_stg2.ps1 的差別：
#   1. 先確認連得到 stg2-vm1（公司內網 / VPN），連不到就直接結束，不會產生一筆無效的執行歷史
#   2. 每次的輸出存到 logs/scheduled-<日期時間>.log——排程在背景跑，看不到畫面

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8  # 不設的話 PowerShell 5.1 會用 Big5 解讀 python 的 UTF-8 輸出，log 裡中文變亂碼

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("scheduled-{0:yyyy-MM-dd-HHmmss}.log" -f (Get-Date))

$targetHost = "fedgpt-stg2-vm1.corp.ailabs.tw"
try {
    Invoke-WebRequest -Uri "https://$targetHost/swagger/" -UseBasicParsing -TimeoutSec 20 | Out-Null
} catch {
    "連不到 $targetHost（沒連公司內網 / VPN？），這次跳過：$($_.Exception.Message)" | Out-File $log -Encoding utf8
    exit 1
}

$ErrorActionPreference = "Continue"  # 讓 python 寫到 stderr 的訊息照樣進 log，不要被當成錯誤中斷
& "$PSScriptRoot\test_stg2.ps1" --live-write --notify-teams --notify-confluence *>&1 | Out-File $log -Encoding utf8
exit $LASTEXITCODE
