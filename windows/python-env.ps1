# Qoder2API —— Python 解释器解析（供其他脚本 dot-source 使用）
# 查找顺序与 start-qoder-proxy.bat 一致：
#   1) 便携运行时 python\python.exe（打包 zip / 以后内置运行时用）
#   2) .runtime\python\python.exe（与 WorkBuddyManager 的目录约定对齐）
#   3) PATH 上的 python、py 启动器
#   4) Codex 自带的运行时
# 只接受 Python >= 3.9。

function Test-QdPython {
    param([string]$Exe)
    if (-not $Exe) { return $false }

    $target = $Exe
    if (-not (Test-Path -LiteralPath $Exe)) {
        $cmd = Get-Command $Exe -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $cmd) { return $false }
        $target = $cmd.Source
    }

    & $target -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Get-QdPython {
    param([Parameter(Mandatory = $true)][string]$Root)

    foreach ($rel in @('python\python.exe', '.runtime\python\python.exe')) {
        $candidate = Join-Path $Root $rel
        if (Test-Path -LiteralPath $candidate) {
            if (Test-QdPython -Exe $candidate) { return $candidate }
        }
    }

    foreach ($name in @('python', 'py')) {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($cmd -and (Test-QdPython -Exe $cmd.Source)) { return $cmd.Source }
    }

    $codexRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes'
    if (Test-Path -LiteralPath $codexRoot) {
        $hits = Get-ChildItem -Path $codexRoot -Filter 'python.exe' -Recurse -File -ErrorAction SilentlyContinue
        foreach ($hit in $hits) {
            if (Test-QdPython -Exe $hit.FullName) { return $hit.FullName }
        }
    }

    return $null
}
