# Qoder2API —— 停止本机服务（Windows / PowerShell）
# 只停命令行里确实含 qoder_proxy.py 的那个进程；不会误杀其它 Python。
# 用法：windows\stop.cmd [端口]
param(
    [Parameter(Position = 0)]
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
Set-Location $root

. (Join-Path $scriptDir 'common.ps1')

$Port = Resolve-QdPort -Port $Port
$result = Stop-QdServer -Root $root -Port $Port

if ($result.Stopped) {
    Write-Host "[OK] 已停止 PID $($result.Pid)（端口 $Port）。" -ForegroundColor Green
} elseif ($result.Reason -eq 'not-running') {
    Write-Host "[INFO] 没有在运行的 Qoder2API（端口 $Port）。"
} elseif ($result.Reason -eq 'no-pid') {
    Write-Host '[WARN] 服务在运行但拿不到 PID，请手动结束对应进程。' -ForegroundColor Yellow
} elseif ($result.Reason -eq 'not-ours') {
    Write-Host '[WARN] 占着端口的进程不是 qoder_proxy.py，拒绝停止。' -ForegroundColor Yellow
    if ($result.CommandLine) { Write-Host ("       " + $result.CommandLine) }
} else {
    Write-Host "[WARN] 停止失败：$($result.Reason)" -ForegroundColor Yellow
}
