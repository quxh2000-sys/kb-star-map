# 知识库星图工作台 · 一行安装（Windows）
#
# 在 PowerShell 里粘贴这一行：
#   $u='https://gitee.com/quxh20000/kb-star-map/raw/master/install.ps1'; $p="$env:TEMP\kbs-setup.ps1"; irm $u -OutFile $p; powershell -NoProfile -ExecutionPolicy Bypass -File $p
#
# 装到 %USERPROFILE%\.kb-star-map，并把 kbs 命令加到用户 PATH。
# 之后任何目录里执行 kbs 即可；不会往笔记库里塞工具。
#
# 注意：本文件必须保存为 UTF-8 带 BOM + CRLF。
#   · PowerShell 5.1 按 ANSI 代码页读 .ps1，无 BOM 的中文会让整个脚本解析失败；
#   · 但带 BOM 的内容经 irm | iex 传入时，BOM 会被当成首行的一部分而报错。
#     所以上面的一行命令是"先存成文件再 -File 运行"，两种问题都避开，
#     顺带也不碰 iex（杀毒软件对 iex 更敏感）。
$ErrorActionPreference = 'Stop'
$ManifestUrl = 'https://gitee.com/quxh20000/kb-star-map/raw/master/dist/update.json'
$Home4Kbs = Join-Path $env:USERPROFILE '.kb-star-map'

function Say($m) { Write-Host $m }
function Die($m) { Write-Host "[错误] $m" -ForegroundColor Red; exit 1 }

Say '=============================================='
Say '  知识库星图工作台 · 安装'
Say '=============================================='

# ---- Python ----
$pyExe = $null
$pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $pyExe = 'py'; $pyArgs = @('-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyExe = 'python' }
if (-not $pyExe) {
    Die "没有找到 Python。请到 https://www.python.org/downloads/ 安装，安装时务必勾选 Add Python to PATH，然后重跑本命令。"
}
Say ("Python: " + ((& $pyExe @pyArgs --version 2>&1) -join " "))

# ---- 取版本 ----
Say ''
Say '正在从 Gitee 获取最新版本…'
try { $info = Invoke-RestMethod -Uri $ManifestUrl -TimeoutSec 30 }
catch { Die ("读取更新清单失败：" + $_.Exception.Message) }
Say ("版本: v" + $info.version)

# ---- 下载并校验 ----
$work = Join-Path $env:TEMP ("kbs-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$zip = Join-Path $work 'pkg.zip'
Say '正在下载…'
try { Invoke-WebRequest -Uri $info.zip_url -OutFile $zip -TimeoutSec 300 -UseBasicParsing }
catch { Die ("下载失败：" + $_.Exception.Message) }
$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($info.sha256 -and $actual -ne $info.sha256.ToLower()) {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    Die ("校验不通过，已中止。期望 " + $info.sha256 + " / 实际 " + $actual)
}
Say ("校验通过（sha256 " + $actual.Substring(0,16) + "…）")

# ---- 安装到用户目录 ----
if (Test-Path $Home4Kbs) {
    $backup = Join-Path $Home4Kbs ("升级备份\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    Copy-Item (Join-Path $Home4Kbs "tools") $backup -Recurse -Force -ErrorAction SilentlyContinue
    Say ("已备份旧版本到 " + $backup)
    Remove-Item (Join-Path $Home4Kbs "tools") -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $Home4Kbs | Out-Null
Expand-Archive -Path $zip -DestinationPath $Home4Kbs -Force
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

# ---- 版本戳 ----
$stamp = [ordered]@{ version = "$($info.version)"; installed_at = (Get-Date -Format s) }
$stampJson = $stamp | ConvertTo-Json -Compress
Set-Content -Path (Join-Path $Home4Kbs 'installed.json') -Value $stampJson -Encoding UTF8

# ---- kbs 命令 ----
$shimLines = @(
    '@echo off',
    'setlocal',
    'set "PY=py -3"',
    'where py >nul 2>nul',
    'if errorlevel 1 set "PY=python"',
    '%PY% "%~dp0kbs.py" %*'
)
$shim = Join-Path $Home4Kbs 'kbs.cmd'
$shimLines -join "`r`n" | Set-Content -Path $shim -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not $userPath) { $userPath = "" }
if ($userPath -notlike "*$Home4Kbs*") {
    $newPath = $userPath.TrimEnd(";") + ";" + $Home4Kbs
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Say ("已把 " + $Home4Kbs + " 加入用户 PATH")
}

Say ''
Say '=============================================='
Say ("  安装完成（v" + $info.version + "）")
Say '=============================================='
Say ("  安装位置：" + $Home4Kbs)
Say ''
Say '  用法（**新开一个** PowerShell 或 CMD 窗口后）：'
Say '    先切到你的笔记库目录：  cd "D:\我的笔记"'
Say '    然后执行：              kbs'
Say ''
Say '    也可以直接指定目录：    kbs "D:\我的笔记"'
Say ''
Say '  其它：'
Say '    kbs update    更新工具'
Say '    kbs check     查看是否有新版本'
Say '    kbs stop      停止本地服务'
Say '    kbs version   查看版本与安装位置'
