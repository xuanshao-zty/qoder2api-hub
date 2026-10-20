# Qoder2API —— 本机前台启动（Windows / PowerShell）
# 双击 windows\start.cmd 即可；关掉这个窗口 = 停止服务（与 WorkBuddyManager 一致）。
param(
    [Parameter(Position = 0)]
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
Set-Location $root

. (Join-Path $scriptDir 'common.ps1')
. (Join-Path $scriptDir 'python-env.ps1')

$Port = Resolve-QdPort -Port $Port
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$pidFile = Join-Path $root 'qoder-proxy.pid'
$script = Join-Path $root 'qoder_proxy.py'

$running = Get-QdServer -Root $root -Port $Port
if ($running.Running) {
    Write-Host "[INFO] Qoder2API 已经在运行（PID $($running.Pid)）。" -ForegroundColor Yellow
    Write-Host "       看板: http://127.0.0.1:$Port/"
    Write-Host "       先运行 windows\stop.cmd 再启动，或直接刷新看板。"
    if ($env:QD_NO_BROWSER -ne '1') {
        Open-QdDashboard -Url "http://127.0.0.1:$Port/"
    }
    exit 0
}

$python = Get-QdPython -Root $root
if (-not $python) {
    Write-Host '[ERROR] 没有找到可用的 Python（3.9+）。' -ForegroundColor Red
    Write-Host '        1) 安装 Python 3.9+ 并勾选 Add to PATH；或'
    Write-Host '        2) 把便携运行时放到项目根目录的 python\ 下；或'
    Write-Host '        3) 直接运行: python qoder_proxy.py --port 8790'
    exit 1
}

if (-not (Test-Path -LiteralPath $script)) {
    Write-Host "[ERROR] 找不到 qoder_proxy.py：$script" -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host '===========================================================' -ForegroundColor Cyan
Write-Host '  Qoder2API' -ForegroundColor Cyan
Write-Host ''
Write-Host ("  API      : http://127.0.0.1:{0}/v1" -f $Port)
Write-Host ("  Dashboard: http://127.0.0.1:{0}/" -f $Port)
Write-Host ''
Write-Host ("  Python   : {0}" -f $python)
Write-Host ''
Write-Host '  保持这个窗口打开；关闭窗口即停止服务（Ctrl+C 也可以）。'
Write-Host '  后台常驻：powershell -ExecutionPolicy Bypass -File windows\service-tools.ps1 start'
Write-Host '===========================================================' -ForegroundColor Cyan
Write-Host ''

if ($env:QD_NO_BROWSER -ne '1') {
    Open-QdDashboard -Url "http://127.0.0.1:$Port/" -DelayMs 2500
}

# 前台运行：控制台就是进程生命周期，关窗口会连同服务一起结束。
$exitCode = 0
try {
    & $python $script --port $Port --pid-file $pidFile
    $exitCode = $LASTEXITCODE
} finally {
    Write-Host ''
    if ($exitCode -ne 0) {
        Write-Host "[INFO] 服务已退出（代码 $exitCode）。" -ForegroundColor Yellow
    } else {
        Write-Host '[INFO] 服务已停止。' -ForegroundColor Green
    }
}
exit $exitCode
