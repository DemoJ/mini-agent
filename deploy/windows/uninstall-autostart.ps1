#Requires -Version 5.1
<#
.SYNOPSIS
  卸载 mini-agent WebUI 开机自启计划任务。

.EXAMPLE
  .\deploy\windows\uninstall-autostart.ps1
#>

param(
    [string]$TaskName = "mini-agent-webui"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "未找到计划任务: $TaskName（可能已卸载）"
    exit 0
}

# 先尝试停止
try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
} catch {}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "已卸载计划任务: $TaskName" -ForegroundColor Green
