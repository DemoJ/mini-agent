#Requires -Version 5.1
<#
.SYNOPSIS
  一键本机部署 mini-agent WebUI（venv + 依赖 + 配置 + 开机/登录自启）。

.EXAMPLE
  .\deploy\windows\install-autostart.ps1 -AtLogon          # 登录自启（推荐，无需管理员）
  .\deploy\windows\install-autostart.ps1                    # 开机自启（需管理员）
  .\deploy\windows\install-autostart.ps1 -AtLogon -Port 8080
#>

param(
    [string]$ListenHost = "0.0.0.0",
    [int]$Port = 8000,
    [switch]$AtLogon,
    [string]$TaskName = "mini-agent-webui",
    [string]$PipIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location -LiteralPath $ProjectRoot
Write-Host "项目目录: $ProjectRoot"

# ---------- 1. 找系统 Python ----------
Write-Step "检查 Python"
$sysPython = $null
foreach ($name in @("python", "python3", "py")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) {
        if ($name -eq "py") {
            $sysPython = & py -3 -c "import sys; print(sys.executable)" 2>$null
        } else {
            $ver = & $cmd.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver) {
                $major, $minor = $ver.Split(".")
                if ([int]$major -gt 3 -or ([int]$major -eq 3 -and [int]$minor -ge 10)) {
                    $sysPython = $cmd.Source
                }
            }
        }
        if ($sysPython) { break }
    }
}
if (-not $sysPython) {
    throw "未找到 Python ≥ 3.10。请先安装: https://www.python.org/downloads/ （勾选 Add to PATH）"
}
Write-Host "系统 Python: $sysPython"

# ---------- 2. 创建 / 复用 .venv ----------
Write-Step "准备虚拟环境 .venv"
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $sysPython -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw "创建 .venv 失败"
    }
    Write-Host "已创建 .venv"
} else {
    Write-Host ".venv 已存在，跳过创建"
}

# ---------- 3. 安装依赖 ----------
Write-Step "安装项目依赖"
& $venvPython -m pip install -U pip -i $PipIndex --trusted-host pypi.tuna.tsinghua.edu.cn
& $venvPython -m pip install -e $ProjectRoot -i $PipIndex --trusted-host pypi.tuna.tsinghua.edu.cn
if ($LASTEXITCODE -ne 0) {
    Write-Host "镜像安装失败，改用官方源重试..." -ForegroundColor Yellow
    & $venvPython -m pip install -e $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
}
& $venvPython -c "import fastapi, uvicorn; print('依赖 OK')"
if ($LASTEXITCODE -ne 0) { throw "依赖校验失败" }

# ---------- 4. 配置文件 ----------
Write-Step "检查 config.yaml"
$configPath = Join-Path $ProjectRoot "config.yaml"
$examplePath = Join-Path $ProjectRoot "config.example.yaml"
if (-not (Test-Path -LiteralPath $configPath)) {
    if (-not (Test-Path -LiteralPath $examplePath)) {
        throw "缺少 config.example.yaml"
    }
    Copy-Item -LiteralPath $examplePath -Destination $configPath
    Write-Host "已生成 config.yaml（请稍后在 WebUI 设置页或编辑文件填入 api_key）" -ForegroundColor Yellow
} else {
    Write-Host "config.yaml 已存在"
}

# ---------- 5. 注册计划任务 ----------
Write-Step "注册计划任务 $TaskName"
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已移除旧任务"
}

$webui = Join-Path $ProjectRoot "webui.py"
$argList = "`"$webui`" --host $ListenHost --port $Port"
$action = New-ScheduledTaskAction -Execute $venvPython -Argument $argList -WorkingDirectory $ProjectRoot

if ($AtLogon) {
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $mode = "用户登录自启"
} else {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
    if (-not $isAdmin) {
        throw "开机自启需要管理员权限。请右键「以管理员身份运行」PowerShell，或改用: -AtLogon"
    }
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest
    $mode = "开机自启"
}

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "mini-agent WebUI（$mode）" | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "=== 部署完成 ===" -ForegroundColor Green
Write-Host "模式:     $mode"
Write-Host "任务名:   $TaskName"
Write-Host "Python:   $venvPython"
Write-Host "监听:     ${ListenHost}:${Port}（局域网可访问）"
Write-Host "本机访问: http://127.0.0.1:${Port}"
Write-Host ""
Write-Host "下一步: 浏览器打开本机或局域网 IP 对应端口，在「设置」填入 API Key。"
Write-Host "卸载:   .\deploy\windows\uninstall-autostart.ps1"
Write-Host ""
