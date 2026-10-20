# Qoder2API —— 公共函数（start.ps1 / stop.ps1 / service-tools.ps1 共用）
# 用法：. (Join-Path $scriptDir 'common.ps1')
# 约定：脚本住在 windows\ 子目录，项目根 = 上一级。

# 端口解析：显式参数 > QD_PORT > PORT > 8790（与 qoder_proxy.py 的默认值一致）
function Resolve-QdPort {
    param([int]$Port = 0)
    if ($Port -gt 0) { return $Port }
    foreach ($name in @('QD_PORT', 'PORT')) {
        $raw = [Environment]::GetEnvironmentVariable($name)
        if ($raw) {
            $n = 0
            if ([int]::TryParse($raw.Trim(), [ref]$n) -and $n -gt 0 -and $n -lt 65536) { return $n }
        }
    }
    return 8790
}

# 判断服务是否在运行。先看 pid 文件（本目录脚本写入），再用 /health 兜底
# （兼容旧版 start-qoder-proxy.bat 拉起的实例，它们不写 pid 文件）。
function Get-QdServer {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $info = @{ Running = $false; Pid = $null; Source = $null }

    $pidFile = Join-Path $Root 'qoder-proxy.pid'
    if (Test-Path -LiteralPath $pidFile) {
        $raw = [string](Get-Content -LiteralPath $pidFile -TotalCount 1 -ErrorAction SilentlyContinue)
        $procId = 0
        if ($raw -and [int]::TryParse($raw.Trim(), [ref]$procId)) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
            if ($proc -and $proc.CommandLine -match 'qoder_proxy\.py') {
                $info.Running = $true
                $info.Pid = $procId
                $info.Source = 'pid'
                return $info
            }
        }
    }

    try {
        $resp = Invoke-WebRequest -Uri ("http://127.0.0.1:$Port/health") -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        $json = $resp.Content | ConvertFrom-Json -ErrorAction Stop
        if ($json.service -eq 'qoder-proxy') {
            $owner = $null
            $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($conn) { $owner = $conn.OwningProcess }
            $info.Running = $true
            $info.Pid = $owner
            $info.Source = 'port'
        }
    } catch { }
    return $info
}

# 轮询 /health，返回解析后的 JSON；超时返回 $null。
function Wait-QdHealth {
    param([Parameter(Mandatory = $true)][int]$Port, [int]$TimeoutMs = 15000)
    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri ("http://127.0.0.1:$Port/health") -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { return ($resp.Content | ConvertFrom-Json) }
        } catch { }
        Start-Sleep -Milliseconds 400
    }
    return $null
}

# 精确停止：只停命令行里确实含 qoder_proxy.py 的那一个进程，绝不按名字批量杀。
function Stop-QdServer {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $pidFile = Join-Path $Root 'qoder-proxy.pid'
    $info = Get-QdServer -Root $Root -Port $Port

    if (-not $info.Running) {
        if (Test-Path -LiteralPath $pidFile) {
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        }
        return @{ Stopped = $false; Reason = 'not-running' }
    }
    if (-not $info.Pid) {
        if (Test-Path -LiteralPath $pidFile) {
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        }
        return @{ Stopped = $false; Reason = 'no-pid' }
    }

    $proc = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$info.Pid) -ErrorAction SilentlyContinue
    if (-not $proc -or $proc.CommandLine -notmatch 'qoder_proxy\.py') {
        $cmdline = ''
        if ($proc) { $cmdline = $proc.CommandLine }
        return @{ Stopped = $false; Reason = 'not-ours'; CommandLine = $cmdline }
    }

    Stop-Process -Id $info.Pid -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 25; $i++) {
        if (-not (Get-Process -Id $info.Pid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 200
    }
    if (Test-Path -LiteralPath $pidFile) {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
    return @{ Stopped = $true; Pid = $info.Pid }
}

# 打开看板。DelayMs > 0 时用一个独立隐藏进程延迟打开，等服务器起来再弹浏览器。
function Open-QdDashboard {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$DelayMs = 0
    )
    if ($DelayMs -le 0) {
        Start-Process $Url
        return
    }
    $safeUrl = $Url.Replace("'", "''")
    $body = "Start-Sleep -Milliseconds $DelayMs; Start-Process '$safeUrl'"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-EncodedCommand', $encoded) `
        -WindowStyle Hidden
}
