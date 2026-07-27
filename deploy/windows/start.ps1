#Requires -Version 5.1
# 前台启动（自动用 .venv；没有则提示先跑 install）
param(
    [string]$ListenHost = "0.0.0.0",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location -LiteralPath $ProjectRoot

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "尚未部署。请先执行: .\deploy\windows\install-autostart.ps1 -AtLogon" -ForegroundColor Yellow
    exit 1
}

Write-Host "启动 mini-agent WebUI: http://${ListenHost}:${Port}"
& $venvPython webui.py --host $ListenHost --port $Port
