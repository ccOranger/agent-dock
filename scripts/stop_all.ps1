<#
一键关停 start_all.ps1 启动的开发环境（后端 + 客户前端 + 管理后台）。

两种关停方式叠加，尽量不留孤儿进程：
  1) 按窗口标题：匹配 start_all.ps1 设置的标题(默认 smart-cs*)，用 taskkill /T /F
     连同其子进程(python / uvicorn / node / vite)一起结束，同时关掉那些终端窗口；
  2) 按端口：对上述没覆盖到、仍在监听 8000/5173/5174 的进程做兜底清理。

用法：
  .\stop_all.ps1                       # 关默认端口 8000/5173/5174 + 标题 smart-cs*
  .\stop_all.ps1 -Ports 8001           # 只按指定端口兜底清理
  .\stop_all.ps1 -TitlePattern 'smart-cs backend*'   # 只关后端
  .\stop_all.ps1 -WhatIf               # 预演：只打印将要结束的进程，不实际执行
#>
[CmdletBinding()]
param(
    [int[]] $Ports = @(8000, 5173, 5174),
    [string] $TitlePattern = 'smart-cs*',
    [switch] $WhatIf
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg)  { Write-Host "    $msg" -ForegroundColor Yellow }

# 结束一个进程及其整棵子进程树（/T 连同子进程，/F 强制）
function Stop-ProcessTree([int]$targetPid) {
    if ($targetPid -le 4) { return }   # 0=Idle, 4=System，绝不能碰
    if ($WhatIf) {
        $p = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
        Write-Warn ("[WhatIf] 将结束 PID {0} ({1})" -f $targetPid, ($p.ProcessName))
        return
    }
    # taskkill 自带进程树终止，比 Stop-Process 更适合连带 python/node 子进程
    & taskkill /T /F /PID $targetPid 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok "已结束 PID $targetPid (含子进程)" }
}

$killed = New-Object System.Collections.Generic.HashSet[int]

# ---------------- 1. 按窗口标题关停（会关掉终端窗口并清理进程树） ----------------
Write-Step "按窗口标题匹配 '$TitlePattern' 关停"
$titleHits = Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -and $_.MainWindowTitle -like $TitlePattern }
if (-not $titleHits) {
    Write-Warn '没有匹配到带标题的服务窗口（可能已关闭，或标题不含该前缀）'
}
foreach ($proc in $titleHits) {
    Write-Host "    命中窗口: [$($proc.MainWindowTitle)] PID=$($proc.Id)"
    if ($killed.Add($proc.Id)) { Stop-ProcessTree $proc.Id }
}

# ---------------- 2. 按端口兜底清理（捕获脱离窗口的孤儿监听进程） ----------------
Write-Step "按端口兜底清理: $($Ports -join ', ')"
foreach ($port in $Ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { Write-Ok "端口 $port 空闲"; continue }
    foreach ($own in ($conns.OwningProcess | Sort-Object -Unique)) {
        $procName = (Get-Process -Id $own -ErrorAction SilentlyContinue).ProcessName
        Write-Host "    端口 $port 被占用: PID=$own ($procName)"
        if ($killed.Add($own)) { Stop-ProcessTree $own }
    }
}

# ---------------- 复核：端口是否已释放 ----------------
Write-Step '复核端口占用'
Start-Sleep -Milliseconds 400
$stillBusy = @()
foreach ($port in $Ports) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($c) { $stillBusy += $port }
}
if ($stillBusy.Count -eq 0) {
    Write-Ok '全部目标端口已释放，环境已停止'
} else {
    Write-Warn ("以下端口仍被占用: {0}（可能有非本脚本启动的进程，或需重试）" -f ($stillBusy -join ', '))
}
