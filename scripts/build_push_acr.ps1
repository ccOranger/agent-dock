#requires -Version 5.1
<#
.SYNOPSIS
  本地构建 backend 镜像并推送到阿里云 ACR。

.DESCRIPTION
  - 从仓库根 .acr.local 读取 Registry / 用户名 / 密码 / 命名空间 / 仓库名（该文件已被 *.local 忽略）。
  - 密码通过 docker login --password-stdin 传入，不出现在命令行参数里。
  - 默认使用项目版本号作为 tag；构建平台默认 linux/amd64。
  - 构建上下文必须是仓库根（Dockerfile 会 COPY backend/、scripts/、docker/）。
  - 默认启用 registry cache（type=registry,ref=<镜像同仓库>:buildcache,mode=max）：
    pip 依赖层在本地 BuildKit 缓存丢失/换机器时也能从 ACR 命中，代码层变更时不再重传 300+ MB 依赖层。
    为此需 docker-container 驱动的 builder（检测到 docker 驱动会自动 buildx create --use 切换）；
    该驱动下由 buildx 直接 --push，不再走 --load + docker push。
  - -SkipCacheExport 可退回旧的 docker 驱动 --load + push 流程（不读写 registry cache）。

.EXAMPLE
  .\scripts\build_push_acr.ps1

.EXAMPLE
  .\scripts\build_push_acr.ps1 -Tag 0.3.1 -AlsoLatest

.EXAMPLE
  .\scripts\build_push_acr.ps1 -SkipLogin -NoPush

.EXAMPLE
  .\scripts\build_push_acr.ps1 -SkipCacheExport   # 不使用 registry cache（首次推送后才有缓存，调试时可跳过）
#>
[CmdletBinding()]
param(
    [string]$Registry = "",
    [string]$Username = "",
    [string]$Namespace = "",
    [string]$Repository = "",
    [string]$Tag = "",
    [string]$Platform = "linux/amd64",
    [string]$PipIndexUrl = "https://mirrors.aliyun.com/pypi/simple/",
    [switch]$SkipLogin,
    [switch]$NoPush,
    [switch]$AlsoLatest,
    [switch]$UpdateEnv,
    [switch]$DryRun,
    [switch]$NoCache,
    [switch]$SkipCacheExport,
    [switch]$PromptPassword,
    [switch]$InteractiveLogin
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$CredFile = Join-Path $RepoRoot ".acr.local"

# 读取 .acr.local：KEY=VALUE，忽略注释与空行
$cfg = @{}
if (Test-Path -LiteralPath $CredFile) {
    Get-Content -LiteralPath $CredFile -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $cfg[$matches[1]] = $matches[2]
        }
    }
}

function Resolve-Config {
    param(
        [string]$Value,
        [string]$EnvName,
        [string]$Key,
        [string]$Default
    )
    if (-not [string]::IsNullOrWhiteSpace($Value)) { return $Value }
    if ($cfg.ContainsKey($Key) -and -not [string]::IsNullOrWhiteSpace($cfg[$Key])) { return $cfg[$Key] }
    $envValue = [Environment]::GetEnvironmentVariable($EnvName)
    if (-not [string]::IsNullOrWhiteSpace($envValue)) { return $envValue }
    return $Default
}

$Registry = Resolve-Config $Registry "ACR_REGISTRY" "ACR_REGISTRY" "registry.cn-hangzhou.aliyuncs.com"
$Username = Resolve-Config $Username "ACR_USERNAME" "ACR_USERNAME" "your-acr-username"
$Namespace = Resolve-Config $Namespace "ACR_NAMESPACE" "ACR_NAMESPACE" "licc"
$Repository = Resolve-Config $Repository "ACR_REPOSITORY" "ACR_REPOSITORY" "agentdock-backend"

# 默认 tag 取 backend/pyproject.toml 的 version
if ([string]::IsNullOrWhiteSpace($Tag)) {
    $pyproject = Join-Path $RepoRoot "backend/pyproject.toml"
    if (Test-Path -LiteralPath $pyproject) {
        $m = Select-String -LiteralPath $pyproject -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($m) { $Tag = $m.Matches[0].Groups[1].Value }
    }
}
if ([string]::IsNullOrWhiteSpace($Tag)) { $Tag = "latest" }

$AppPort = [Environment]::GetEnvironmentVariable("APP_PORT")
if ([string]::IsNullOrWhiteSpace($AppPort)) { $AppPort = "8000" }

$Password = $null
$PasswordSource = "未读取"
if ($cfg.ContainsKey("ACR_PASSWORD") -and -not [string]::IsNullOrWhiteSpace($cfg["ACR_PASSWORD"])) {
    $Password = $cfg["ACR_PASSWORD"]
    $PasswordSource = ".acr.local"
}
else {
    $Password = [Environment]::GetEnvironmentVariable("ACR_PASSWORD")
    if (-not [string]::IsNullOrWhiteSpace($Password)) { $PasswordSource = "环境变量" }
}

if ($PromptPassword) {
    $secure = Read-Host "请输入 ACR 密码（输入不回显）" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $Password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $PasswordSource = "本次手动输入"
}

if (-not $DryRun -and -not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 docker 命令，请先安装并启动 Docker Desktop。"
}
if ($Namespace -notmatch '^[a-z0-9][a-z0-9._-]*$') {
    throw "ACR 命名空间只能包含小写字母、数字、点、下划线、短横线：$Namespace"
}
if ($Repository -notmatch '^[a-z0-9][a-z0-9._-]*$') {
    throw "ACR 仓库名只能包含小写字母、数字、点、下划线、短横线：$Repository"
}
if ($Tag -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$') {
    throw "镜像 tag 不合法：$Tag"
}

function Invoke-Docker {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$DockerArgs)
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($DockerArgs -join ' ') 失败（退出码 $LASTEXITCODE）"
    }
}

$Image = "$Registry/$Namespace/$Repository`:$Tag"

if ($DryRun) {
    Write-Host "Registry   : $Registry"
    Write-Host "Username   : $Username"
    Write-Host "Namespace  : $Namespace"
    Write-Host "Repository : $Repository"
    Write-Host "Tag        : $Tag"
    Write-Host "Image      : $Image"
    Write-Host "Platform   : $Platform"
    Write-Host "PipIndex   : $PipIndexUrl"
    Write-Host "Password   : $(if ([string]::IsNullOrWhiteSpace($Password)) { '未读取（运行时会提示输入）' } else { "已读取（来源：$PasswordSource，不显示）" })"
    Write-Host "DryRun     : 仅校验配置，不构建/不推送"
    return
}

Push-Location $RepoRoot
try {
    Write-Host "==> 检查 Docker 引擎" -ForegroundColor Cyan
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        $ctx = (& docker context show 2>$null)
        throw "Docker 引擎不可用（当前 context: $ctx）。请打开 Docker Desktop，等待 Engine running；如仍失败，执行 docker context use desktop-linux 后重试。"
    }

    if (-not $SkipLogin) {
        if ($InteractiveLogin) {
            Write-Host "==> 登录 ACR：$Registry（用户名：$Username）" -ForegroundColor Cyan
            Write-Host "    使用 Docker 原生交互式密码输入；请输入你手动登录成功时的那个密码。"
            & docker login --username $Username $Registry
            if ($LASTEXITCODE -ne 0) {
                throw "docker login 失败：请确认 Registry、用户名和密码；也可先手动 docker login 后使用 -SkipLogin。"
            }
            $Password = $null
        }
        else {
            if ([string]::IsNullOrWhiteSpace($Password)) {
                $secure = Read-Host "请输入 ACR 密码（输入不回显）" -AsSecureString
                $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
                try {
                    $Password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
                }
                finally {
                    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
                }
            }
            if ([string]::IsNullOrWhiteSpace($Password)) {
                throw "ACR 密码为空，无法登录。"
            }

            Write-Host "==> 登录 ACR：$Registry（用户名：$Username）" -ForegroundColor Cyan
            $dockerCmd = Get-Command docker -ErrorAction Stop
            $dockerExe = $dockerCmd.Source
            if ([string]::IsNullOrWhiteSpace($dockerExe)) { $dockerExe = $dockerCmd.Path }
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = $dockerExe
            $psi.Arguments = "login --username `"$Username`" --password-stdin `"$Registry`""
            $psi.UseShellExecute = $false
            $psi.RedirectStandardInput = $true
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
            $psi.CreateNoWindow = $true
            $loginProc = [System.Diagnostics.Process]::Start($psi)
            $loginExit = 1
            $loginOut = ""
            $loginErr = ""
            try {
                # 直接写入、不追加换行：PowerShell 管道会给原生 stdin 补 \r\n，可能被 Docker 当成密码的一部分
                $loginProc.StandardInput.Write($Password)
                $loginProc.StandardInput.Close()
                $loginOut = $loginProc.StandardOutput.ReadToEnd()
                $loginErr = $loginProc.StandardError.ReadToEnd()
                $loginProc.WaitForExit()
                $loginExit = $loginProc.ExitCode
            }
            finally {
                if (-not $loginProc.HasExited) { $loginProc.Kill() }
                $loginProc.Dispose()
            }
            if ($loginOut) { Write-Host $loginOut.TrimEnd() }
            if ($loginErr) { Write-Host $loginErr.TrimEnd() -ForegroundColor DarkGray }
            if ($loginExit -ne 0) {
                throw "docker login 失败：ACR 拒绝了用户名或密码。请改用 -InteractiveLogin 手动输入密码，或先手动 docker login 后使用 -SkipLogin。"
            }
            $Password = $null
        }
    }

    # registry cache：让 pip 依赖层在本地 BuildKit 缓存丢失/换机器时也能从 ACR 命中，
    # 代码层变更时 push 只传代码层。注意：默认的 docker 驱动不支持 type=registry 导出，
    # 需要 docker-container 驱动的 builder（下面会自动创建并切换）。
    $CacheRef = "$Registry/$Namespace/$Repository`:buildcache"

    $buildxInspect = (& docker buildx inspect 2>$null) -join "`n"
    $drv = 'docker'
    if ($buildxInspect -match '(?m)^Driver:\s*(\S+)') { $drv = $matches[1] }
    $useLoad = ($drv -eq 'docker')
    if (-not $SkipCacheExport -and $useLoad) {
        Write-Host "==> 当前 buildx builder 驱动为 docker，切换/创建 docker-container 驱动的专用 builder 以支持 registry cache" -ForegroundColor Cyan
        # 先复用已有的 agentdock-builder（buildx create 对重名 builder 会报错，必须幂等）
        $existing = (& docker buildx inspect agentdock-builder 2>$null) -join "`n"
        if ($existing -match '(?m)^Driver:\s*docker-container') {
            Invoke-Docker buildx use agentdock-builder
        }
        else {
            Invoke-Docker buildx create --name agentdock-builder --driver docker-container --use
        }
        $drv = 'docker-container'
        $useLoad = $false
    }

    Write-Host "==> 构建镜像：$Image" -ForegroundColor Cyan
    Write-Host "    平台：$Platform"
    Write-Host "    pip 源：$PipIndexUrl"
    Write-Host "    构建缓存：$CacheRef（驱动：$drv）"
    # 阿里云 ACR 对 BuildKit 默认附加的 OCI attestation（application/vnd.oci.empty.v1+json）
    # 兼容性不好；关闭 provenance/SBOM。docker 驱动下用 --load 生成 Docker 格式镜像后再 push；
    # docker-container 驱动下由 buildx 直接 --push（缓存在构建时同步进 registry，push 阶段仅确认可见性）。
    $buildArgs = @(
        "buildx", "build",
        "--platform", $Platform,
        "--provenance=false",
        "--sbom=false",
        "--build-arg", "PIP_INDEX_URL=$PipIndexUrl",
        "-f", "docker/backend.Dockerfile",
        "-t", $Image
    )
    if ($useLoad) { $buildArgs += "--load" }
    if ($NoCache) { $buildArgs += "--no-cache" }
    if (-not $SkipCacheExport) {
        $buildArgs += "--cache-from"
        $buildArgs += "type=registry,ref=$CacheRef"
        $buildArgs += "--cache-to"
        $buildArgs += "type=registry,ref=$CacheRef,mode=max"
    }
    if (-not $useLoad) {
        # docker-container 驱动下构建产物默认留在 builder 内部：
        # 要推送用 --push；-NoPush 时用 --output type=docker 导入本地 docker image store
        if ($NoPush) { $buildArgs += "--output"; $buildArgs += "type=docker" } else { $buildArgs += "--push" }
    }
    $buildArgs += "."
    Invoke-Docker @buildArgs

    try {
        $sizeBytes = [long](& docker image inspect $Image --format "{{.Size}}")
        Write-Host ("    镜像大小：{0:N2} GB" -f ($sizeBytes / 1GB)) -ForegroundColor DarkGray
    }
    catch {
        # --push 直接推送时本地无镜像可 inspect，跳过
    }

    if (-not $NoPush) {
        if ($useLoad) {
            Write-Host "==> 推送镜像：$Image" -ForegroundColor Cyan
            Write-Host "    首次推送约 1.5 GB，按 ACR 带宽可能需要几分钟。"
            Invoke-Docker push $Image
        }
        else {
            Write-Host "==> 镜像已由 buildx 直接推送：$Image" -ForegroundColor Cyan
        }

        if ($AlsoLatest) {
            $LatestImage = "$Registry/$Namespace/$Repository`:latest"
            Write-Host "==> 同步 latest：$LatestImage" -ForegroundColor Cyan
            if ($useLoad) { Invoke-Docker tag $Image $LatestImage }
            Invoke-Docker push $LatestImage
        }
    }

    if ($UpdateEnv) {
        $envPath = Join-Path $RepoRoot ".env"
        if (Test-Path -LiteralPath $envPath) {
            $lines = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
            $found = $false
            $newLines = foreach ($line in $lines) {
                if ($line -match '^\s*BACKEND_IMAGE\s*=') {
                    $found = $true
                    "BACKEND_IMAGE=$Image"
                }
                else {
                    $line
                }
            }
            $newLines = @($newLines)
            if (-not $found) { $newLines += "BACKEND_IMAGE=$Image" }
            $text = ($newLines -join "`r`n") + "`r`n"
            [System.IO.File]::WriteAllText($envPath, $text, (New-Object System.Text.UTF8Encoding($false)))
            Write-Host "==> 已更新根 .env：BACKEND_IMAGE=$Image" -ForegroundColor Green
        }
        else {
            Write-Warning "未找到 $envPath，跳过 -UpdateEnv。"
        }
    }

    Write-Host ""
    if ($NoPush) {
        Write-Host "构建完成（未推送）：$Image" -ForegroundColor Green
    }
    else {
        Write-Host "推送完成：$Image" -ForegroundColor Green
    }
    Write-Host ""
    Write-Host "服务器端根 .env 需要写入："
    Write-Host "  BACKEND_IMAGE=$Image" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "服务器端执行："
    Write-Host "  docker login $Registry"
    Write-Host "  docker compose -f docker-compose.external.yml pull backend"
    Write-Host "  docker compose -f docker-compose.external.yml up -d --no-build backend"
    Write-Host "  curl -fsS http://127.0.0.1:$AppPort/health"
}
catch {
    Write-Host ""
    Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}
