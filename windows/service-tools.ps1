# Qoder2API —— 后台服务管理（Windows / PowerShell）
#
#   powershell -ExecutionPolicy Bypass -File windows\service-tools.ps1 start    后台启动（日志写项目根目录）
#   powershell -ExecutionPolicy Bypass -File windows\service-tools.ps1 stop     停止
#   powershell -ExecutionPolicy Bypass -File windows\service-tools.ps1 status   查看状态
#   powershell -ExecutionPolicy Bypass -File windows\service-tools.ps1 restart  重启
#
# 与同目录 start.ps1 的区别：start.ps1 是前台运行（关窗口即停），本脚本用
# Start-Process 拉起独立进程，关掉终端也不影响；日志落在
# qoder-proxy.log 与 qoder-proxy.err.log。
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'status',

    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
# 项目根在上一级（qoder_proxy.py、accounts、dashboard.html 所在处）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $scriptDir
Set-Location $root

. (Join-Path $scriptDir 'common.ps1')
. (Join-Path $scriptDir 'python-env.ps1')

$Port = Resolve-QdPort -Port $Port
$outLog = Join-Path $root 'qoder-proxy.log'
$errLog = Join-Path $root 'qoder-proxy.err.log'
$pidFile = Join-Path $root 'qoder-proxy.pid'

function Show-QdStatus {
    $running = Get-QdServer -Root $root -Port $Port
    if ($running.Running) {
        Write-Host ("Qoder2API 运行中：PID {0}（来源：{1}）" -f $running.Pid, $running.Source) -ForegroundColor Green
        Write-Host ("看板：http://127.0.0.1:{0}/   API：http://127.0.0.1:{0}/v1" -f $Port)
        $health = Wait-QdHealth -Port $Port -TimeoutMs 3000
        if ($health) {
            Write-Host ("健康检查：ok  version={0}  accounts={1}  api_key_required={2}" -f `
                $health.version, $health.accounts, $health.api_key_required)
        } else {
            Write-Host '健康检查：/health 没有应答（服务可能还在启动）' -ForegroundColor Yellow
        }
    } else {
        Write-Host ("Qoder2API 未运行（端口 $Port）") -ForegroundColor Yellow
    }

    if (Test-Path -LiteralPath $outLog) {
        Write-Host ("最近日志：{0}" -f $outLog)
        Get-Content -LiteralPath $outLog -Tail 5 -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host ("  " + $_) }
    }
    if (Test-Path -LiteralPath $errLog) {
        $errTail = Get-Content -LiteralPath $errLog -Tail 3 -ErrorAction SilentlyContinue
        if ($errTail) {
            Write-Host ("错误日志尾部：{0}" -f $errLog) -ForegroundColor DarkYellow
            $errTail | ForEach-Object { Write-Host ("  " + $_) }
        }
    }
}

function Start-QdBackground {
    $running = Get-QdServer -Root $root -Port $Port
    if ($running.Running) {
        Write-Host ("已经在运行（PID {0}），无需重复启动。" -f $running.Pid) -ForegroundColor Yellow
        Write-Host ("看板：http://127.0.0.1:{0}/" -f $Port)
        return
    }

    $python = Get-QdPython -Root $root
    if (-not $python) {
        Write-Host '[ERROR] 没有找到可用的 Python（3.9+）。' -ForegroundColor Red
        Write-Host '        安装 Python 3.9+，或把便携运行时放到项目根目录的 python\ 下。'
        return
    }

    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    # 用一张临时 .bat 把「python + 输出重定向」交给 cmd 执行。
    # 不能直接 Start-Process -RedirectStandardOutput：Python 会继承调用者的
    # stdout 句柄，在 `powershell -File service-tools.ps1 start | ...` 这类
    # 管道里调用时，管道永远等不到 EOF（命令看起来「卡住」）。经 cmd 转一手后，
    # Python 只继承 cmd 自己的文件句柄，父进程的管道可以正常关闭。
    $batPath = Join-Path ([IO.Path]::GetTempPath()) 'qoder2api-background.bat'
    $batLines = @(
        '@echo off',
        ('cd /d "' + $root + '"'),
        ('"' + $python + '" "' + (Join-Path $root 'qoder_proxy.py') + '" --port ' + $Port +
            ' --pid-file "' + $pidFile + '" > "' + $outLog + '" 2> "' + $errLog + '"')
    )
    [IO.File]::WriteAllLines($batPath, $batLines, [Text.Encoding]::Default)

    $null = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $batPath) `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru

    $health = Wait-QdHealth -Port $Port -TimeoutMs 15000
    if ($health) {
        $info = Get-QdServer -Root $root -Port $Port
        Write-Host ("已启动：PID {0}，版本 {1}，日志 {2}" -f $info.Pid, $health.version, $outLog) -ForegroundColor Green
        Write-Host ("看板：http://127.0.0.1:{0}/" -f $Port)
    } else {
        Write-Host '启动失败或超时，日志尾部：' -ForegroundColor Red
        Get-Content -LiteralPath $errLog -Tail 20 -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host ("  " + $_) }
        Get-Content -LiteralPath $outLog -Tail 10 -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host ("  " + $_) }
    }
}

function Stop-QdBackground {
    $result = Stop-QdServer -Root $root -Port $Port
    if ($result.Stopped) {
        Write-Host ("已停止 PID {0}。" -f $result.Pid) -ForegroundColor Green
    } elseif ($result.Reason -eq 'not-running') {
        Write-Host ("没有在运行的 Qoder2API（端口 $Port）。")
    } elseif ($result.Reason -eq 'not-ours') {
        Write-Host '占着端口的进程不是 qoder_proxy.py，拒绝停止。' -ForegroundColor Yellow
        if ($result.CommandLine) { Write-Host ("  " + $result.CommandLine) }
    } else {
        Write-Host ("停止失败：{0}" -f $result.Reason) -ForegroundColor Red
    }
}

switch ($Action) {
    'start'   { Start-QdBackground }
    'stop'    { Stop-QdBackground }
    'restart' { Stop-QdBackground; Start-Sleep -Milliseconds 800; Start-QdBackground }
    'status'  { Show-QdStatus }
}
