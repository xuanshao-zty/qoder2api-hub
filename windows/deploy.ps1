# Qoder2API —— 把源码目录同步成一个「双击即用」的部署目录（Windows / PowerShell）
#
#   powershell -ExecutionPolicy Bypass -File windows\deploy.ps1
#   powershell -ExecutionPolicy Bypass -File windows\deploy.ps1 -Target 'D:\software\qoder2api'
#
# 目标目录保留自己的 accounts\、usage\、日志与 PID 文件；源码目录的代码/脚本较新时覆盖。
# 目标目录已经在运行时先停一下再部署，部署完成后重新 start 即可。
param(
    [string]$Target = 'D:\software\qoder2api'
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Split-Path -Parent $scriptDir

if (-not (Test-Path -LiteralPath (Join-Path $source 'qoder_proxy.py'))) {
    throw "源码目录不正确：$source"
}
$sourceFull = [IO.Path]::GetFullPath($source).TrimEnd('\')
$targetFull = [IO.Path]::GetFullPath($Target).TrimEnd('\')
if ($sourceFull -eq $targetFull) {
    throw '目标目录不能是源码目录本身。'
}

Write-Host ("源目录  : {0}" -f $sourceFull) -ForegroundColor Cyan
Write-Host ("目标目录: {0}" -f $targetFull) -ForegroundColor Cyan

# 目标在跑就先停，避免覆盖进程正在读的文件时出现奇怪问题（不是必需，但更稳）
$targetStop = Join-Path $targetFull 'windows\stop.ps1'
if (Test-Path -LiteralPath $targetStop) {
    Write-Host '先停止目标目录里可能正在运行的服务...'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $targetStop
}

$excludeDirs = @(
    '.git', '.github', 'tests', '__pycache__', '.pytest_cache', 'backups', 'logs',
    'launcher', 'accounts', 'usage', '_acc', '_use', '.team', '.mimosa', '.runtime',
    '.venv', 'venv', 'node_modules', '.cache', '.mypy_cache', '.ruff_cache',
    'suite-logs', '_shots', '.ssh', '.aws', '.azure'
)
$excludeFiles = @(
    'deploy.ps1',
    '_test_*.py', '_diag_*.py', '_install_umid.py', '_refresh_catalog.py',
    '_verify_models.py', 'qoder-proxy.pid', 'qoder-proxy.log', 'qoder-proxy.err.log',
    'launcher.ini', '*.pid', '*.log', '.env', '.env.*', '.git-credentials',
    '.netrc', '_netrc', '.npmrc', '.pypirc', '*.pfx', '*.p12', '*.key', '*.pem',
    '*.bak', '*.tmp', '*.pdb', 'qoder-accounts-*.json', 'accounts-export*.json',
    'usage.jsonl', 'usage-summary.json', '__*.py'
)
$robocopyArgs = @(
    $sourceFull, $targetFull, '/E', '/XO', '/R:2', '/W:1',
    '/NFL', '/NDL', '/NJH', '/NJS', '/NP',
    '/XD'
) + $excludeDirs + @('/XF') + $excludeFiles

& robocopy @robocopyArgs | Out-Host
$rc = $LASTEXITCODE
# robocopy: 0=无变化 1=有复制 2=有额外 3=1+2 4/5/6=有不一致但可接受 8+=失败
if ($rc -ge 8) {
    throw "robocopy 失败（退出码 $rc）。"
}

$required = @('qoder_proxy.py', 'dashboard.html', 'baseprompt.json', 'windows\start.cmd', 'windows\start.ps1')
foreach ($rel in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $targetFull $rel))) {
        throw "部署结果缺少 $rel"
    }
}

# 控制面板 exe：编译过就同步到安装目录根（双击启动用）
$launcherExe = Join-Path $sourceFull 'windows\launcher\dist\Qoder2API.exe'
if (Test-Path -LiteralPath $launcherExe) {
    Copy-Item -LiteralPath $launcherExe -Destination (Join-Path $targetFull 'Qoder2API.exe') -Force
    Write-Host '[OK] 已同步 Qoder2API.exe（双击启动的控制面板）' -ForegroundColor Green
} else {
    Write-Host '[WARN] 还没编译控制面板：运行 windows\launcher\build.ps1 生成 Qoder2API.exe' -ForegroundColor Yellow
}

Write-Host ''
Write-Host ("[OK] 已部署到 {0}" -f $targetFull) -ForegroundColor Green
Write-Host '     双击 Qoder2API.exe 启动（控制面板 + 自动启动 + 自动打开看板）。'
Write-Host '     也可以双击 windows\start.cmd 前台启动。'
Write-Host '     后台常驻：powershell -ExecutionPolicy Bypass -File windows\service-tools.ps1 start'
Write-Host '     数据目录 accounts\ / usage\ 原样保留。'
