# Qoder2API —— 编译控制面板（Windows / PowerShell，只用系统自带 csc，不需要 .NET SDK）
#
#   powershell -ExecutionPolicy Bypass -File windows\launcher\build.ps1
#
# 产物：
#   windows\launcher\dist\Qoder2API.exe   控制面板（带图标与版本信息）
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent (Split-Path -Parent $scriptDir)   # windows\launcher -> windows -> 项目根
$source = Join-Path $scriptDir 'Qoder2ApiLauncher.cs'
$icon = Join-Path $scriptDir 'Qoder2API.ico'
$dist = Join-Path $scriptDir 'dist'
$out = Join-Path $dist 'Qoder2API.exe'

if (-not (Test-Path -LiteralPath $source)) { throw "找不到源码：$source" }

$cscCandidates = @(
    'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe',
    'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe'
)
$csc = $null
foreach ($c in $cscCandidates) {
    if (Test-Path -LiteralPath $c) { $csc = $c; break }
}
if (-not $csc) { throw '找不到 csc.exe（需要 .NET Framework 4.x，Windows 10/11 自带）。' }

New-Item -ItemType Directory -Force -Path $dist | Out-Null

# 版本号跟 qoder_proxy.py 的 VERSION 走（文件属性里能看到）
$version = '0.0.0'
$proxyPy = Join-Path $root 'qoder_proxy.py'
if (Test-Path -LiteralPath $proxyPy) {
    $match = Select-String -Path $proxyPy -Pattern '^VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($match) { $version = $match.Matches[0].Groups[1].Value }
}
$numeric = $version -replace '[^0-9.]', ''
$parts = @($numeric.Split('.') | Where-Object { $_ -ne '' })
while ($parts.Count -lt 4) { $parts += '0' }
$assemblyVersion = ($parts[0..3] -join '.')

$assemblyInfo = Join-Path $dist 'AssemblyInfo.cs'
$assemblyText = @"
using System.Reflection;
[assembly: AssemblyTitle("Qoder2API")]
[assembly: AssemblyDescription("Qoder2API 控制面板")]
[assembly: AssemblyProduct("Qoder2API")]
[assembly: AssemblyCompany("Qoder2API")]
[assembly: AssemblyVersion("$assemblyVersion")]
[assembly: AssemblyFileVersion("$assemblyVersion")]
"@
[IO.File]::WriteAllText($assemblyInfo, $assemblyText, (New-Object System.Text.UTF8Encoding $true))

$cscArgs = @(
    '/nologo', '/target:winexe', '/optimize+', '/platform:anycpu',
    ('/out:' + $out),
    '/reference:System.dll',
    '/reference:System.Windows.Forms.dll',
    '/reference:System.Drawing.dll',
    '/reference:System.Management.dll',
    '/reference:System.Core.dll'
)
if (Test-Path -LiteralPath $icon) { $cscArgs += ('/win32icon:' + $icon) }
$cscArgs += $source
$cscArgs += $assemblyInfo

Write-Host ("编译 {0}（版本 {1}）" -f $out, $version) -ForegroundColor Cyan
& $csc @cscArgs
if ($LASTEXITCODE -ne 0) { throw "csc 编译失败（退出码 $LASTEXITCODE）。" }
if (-not (Test-Path -LiteralPath $out)) { throw '编译没有生成 exe。' }

$size = (Get-Item -LiteralPath $out).Length
Write-Host ("[OK] 已生成 {0}（{1:N0} 字节）" -f $out, $size) -ForegroundColor Green
Write-Host '     部署到安装目录：windows\deploy.ps1 会把它复制到目标安装目录的根部。'
