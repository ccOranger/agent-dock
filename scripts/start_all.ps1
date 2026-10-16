<#
一键启动开发环境：后端(uvicorn) + 客户前端(5173) + 管理后台(5174)
每个服务在独立 PowerShell 窗口运行，便于分别查看日志 / 单独停止。

用法：
  .\start_all.ps1              # 直接启动三个服务
  .\start_all.ps1 -Install     # 先补齐依赖（后端 venv / 前端 node_modules）再启动
  .\start_all.ps1 -BackendPort 8001   # 换后端端口（前端 vite 代理固定指向 8000，一般不用改）

说明：
  - 前端请用 http://localhost:5173 / :5174 访问（Vite 默认只绑 IPv6 的 ::1，用 127.0.0.1 会连不上）
  - 关闭某个服务的窗口即停止该服务；关闭全部窗口 = 停止整个环境
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [int]$BackendPort = 8000
)

$ErrorActionPreference = 'Stop'

# scripts/ 的上一级即仓库根
$Root      = Split-Path -Parent $PSScriptRoot
$Backend   = Join-Path $Root 'backend'
$Frontend  = Join-Path $Root 'frontend'
$Admin     = Join-Path $Root 'admin-frontend'
$VenvPy    = Join-Path $Backend '.venv\Scripts\python.exe'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "    $msg" -ForegroundColor Yellow }

function New-ServiceWindow($title, $workdir, $command) {
    # 在每个新窗口里先设 UTF-8（Windows GBK 控制台下 hello-agents 打印 emoji 会崩），再切目录执行
    $full = "`$Host.UI.RawUI.WindowTitle='$title'; " +
            "`$env:PYTHONIOENCODING='utf-8'; " +
            "Set-Location -LiteralPath '$workdir'; $command"
    Start-Process powershell -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $full
}

# ---------------- 环境自检 ----------------
if (-not (Test-Path $Backend) -or -not (Test-Path $Frontend) -or -not (Test-Path $Admin)) {
    throw "未找到 backend / frontend / admin-frontend 目录，请确认脚本位于仓库 scripts/ 下：$Root"
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "未找到 npm，请先安装 Node.js >= 18.18 并将其加入 PATH。"
}

# ---------------- 可选：安装依赖 ----------------
if ($Install) {
    Write-Step '安装 / 补齐依赖'

    if (-not (Test-Path $VenvPy)) {
        Write-Warn '后端 .venv 不存在，正在创建虚拟环境并安装 requirements...'
        $sysPy = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $sysPy) { throw '未找到 python，无法创建虚拟环境。' }
        & $sysPy -m venv (Join-Path $Backend '.venv')
        & $VenvPy -m pip install --upgrade pip
        Push-Location $Backend
        try { & $VenvPy -m pip install -r requirements.txt } finally { Pop-Location }
    } else {
        Write-Ok '后端 .venv 已存在，跳过创建'
    }

    foreach ($dir in @($Frontend, $Admin)) {
        if (-not (Test-Path (Join-Path $dir 'node_modules'))) {
            Write-Warn "在 $dir 执行 npm install ..."
            Push-Location $dir
            try { npm install } finally { Pop-Location }
        } else {
            Write-Ok "$(Split-Path -Leaf $dir) 的 node_modules 已存在，跳过"
        }
    }
}
else {
    # 不装依赖时先校验关键文件，给出明确提示
    if (-not (Test-Path $VenvPy)) {
        Write-Warn "后端 .venv 缺失（$VenvPy）。首次运行请加 -Install： .\start_all.ps1 -Install"
    }
    foreach ($dir in @($Frontend, $Admin)) {
        if (-not (Test-Path (Join-Path $dir 'node_modules'))) {
            Write-Warn "$dir 缺 node_modules。首次运行请加 -Install。"
        }
    }
}

# ---------------- 启动三个服务 ----------------
Write-Step "启动后端 uvicorn (端口 $BackendPort)"
New-ServiceWindow 'smart-cs backend' $Backend `
    "& '$VenvPy' -m uvicorn app.main:app --reload --host 0.0.0.0 --port $BackendPort"

Write-Step '启动客户前端 (Vite :5173)'
New-ServiceWindow 'smart-cs frontend' $Frontend 'npm run dev'

Write-Step '启动管理后台 (Vite :5174)'
New-ServiceWindow 'smart-cs admin' $Admin 'npm run dev'

# ---------------- 提示 ----------------
Write-Host ''
Write-Host '三个服务已在独立窗口拉起，稍等片刻待其编译完成：' -ForegroundColor Green
Write-Host "  后端 API   : http://localhost:$BackendPort"
Write-Host '  客户前端   : http://localhost:5173'
Write-Host '  管理后台   : http://localhost:5174'
Write-Host ''
Write-Host '提示：用 localhost 访问（非 127.0.0.1）；关闭对应窗口即停止该服务。' -ForegroundColor Yellow
