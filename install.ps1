# 知识库星图工作台 · 一行安装（Windows）
#
# 在 PowerShell 里粘贴这一行：
#   $u='https://gitee.com/quxh20000/kb-star-map/raw/master/install.ps1'; $p="$env:TEMP\kbs-setup.ps1"; irm $u -OutFile $p; powershell -NoProfile -ExecutionPolicy Bypass -File $p
#
# 不用 zip：从 Gitee 逐文件拉取并逐个校验 sha256。下载逻辑复用 tools/bootstrap.py
#（安装与更新共用同一份实现，避免多处维护）。
#
# 注意：本文件必须保存为 UTF-8 带 BOM + CRLF。
#   · PowerShell 5.1 按 ANSI 代码页读 .ps1，无 BOM 的中文会让整个脚本解析失败；
#   · 但带 BOM 的内容经 irm | iex 传入时，BOM 会被当成首行的一部分而报错。
#     所以上面的一行命令是"先存成文件再 -File 运行"。
$ErrorActionPreference = 'Stop'
$RawBase = 'https://gitee.com/quxh20000/kb-star-map/raw/master'
$ManifestUrl = "$RawBase/dist/files.json"
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

# ---- 取引导器 ----
$work = Join-Path $env:TEMP ("kbs-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$bootstrap = Join-Path $work "bootstrap.py"
Say ''
Say '正在获取引导器…'
try { Invoke-WebRequest -Uri "$RawBase/tools/bootstrap.py" -OutFile $bootstrap -TimeoutSec 60 -UseBasicParsing }
catch { Die ("获取引导器失败：" + $_.Exception.Message) }

# ---- 旧版先备份，只留最近 3 份 ----
if (Test-Path $Home4Kbs) {
    $backup = Join-Path $Home4Kbs ("升级备份\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    Copy-Item (Join-Path $Home4Kbs "tools") $backup -Recurse -Force -ErrorAction SilentlyContinue
    Say ("已备份旧版本到 " + $backup)
    Get-ChildItem (Join-Path $Home4Kbs "升级备份") -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -Skip 3 |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    # 清空重装：逐文件覆盖不会删掉「新版已移除」的文件，旧残留会一直躺着。
    # 保留用户自己的东西：备份、更新源、设置。
    Get-ChildItem -Path $Home4Kbs -Force |
        Where-Object { $_.Name -notin @('升级备份', '更新配置.yaml', '设置.yaml') } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

# ---- 安装 ----
Say ''
Say '--- 开始安装 ---'
& $pyExe @pyArgs $bootstrap --manifest $ManifestUrl --raw-base $RawBase --target $Home4Kbs
if ($LASTEXITCODE -ne 0) { Die "安装未完成，请看上面的提示。" }
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

# ---- 更新源配置（属于工具本身；已有就不覆盖）----
$cfgPath = Join-Path $Home4Kbs "更新配置.yaml"
if (-not (Test-Path $cfgPath)) {
    $cfg = @(
        '# 知识库星图工作台 · 更新源（只有主动检查更新时才会联网）',
        'manifest_url: "' + $ManifestUrl + '"',
        'repo: ""',
        'api_base: "https://api.github.com"',
        'auto_check: false',
        'timeout: 15'
    ) -join "`n"
    [IO.File]::WriteAllText($cfgPath, $cfg, (New-Object Text.UTF8Encoding $false))
}

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

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if ($userPath -notlike "*$Home4Kbs*") {
    $newPath = $userPath.TrimEnd(";") + ";" + $Home4Kbs
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Say ("已把 " + $Home4Kbs + " 加入用户 PATH")
}

$stamp = Join-Path $Home4Kbs "installed.json"
$version = "?"
if (Test-Path $stamp) {
    try { $version = (Get-Content $stamp -Raw -Encoding UTF8 | ConvertFrom-Json).version } catch { }
}
Say ''
Say '=============================================='
Say ("  安装完成（v" + $version + "）")
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

# ---------- 装完直接进浏览器（像桌面端 Agent 那样）----------
if ($env:KBS_NO_LAUNCH -eq '1') {
    Say ''
    Say '已跳过启动（KBS_NO_LAUNCH=1）。随时执行： kbs "你的笔记库"'
    exit 0
}

$vault = $env:KBS_VAULT
if (-not $vault) {
    # 不做任何自动搜索：扫到大家目录会卡很久，而且猜错了更麻烦。
    # 只弹文件夹选择框，让用户明确指定。
    try {
        Add-Type -AssemblyName System.Windows.Forms | Out-Null
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = '请选择你的笔记库（装着 .md 笔记的那一层文件夹）'
        $dlg.ShowNewFolderButton = $false
        if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $vault = $dlg.SelectedPath }
    } catch {
        $vault = ''
    }
}

if ($vault -and (Test-Path $vault -PathType Container)) {
    Say ''
    Say ("正在生成星图并打开浏览器：" + $vault)
    Say '（这个窗口就是本地管理服务，关掉它即停止；Ctrl+C 也可以）'
    Say ''
    if ($env:KBS_NO_OPEN -eq '1') {
        & (Join-Path $Home4Kbs 'kbs.cmd') 'port' $vault   # 起服务但不自动开浏览器
    } else {
        & (Join-Path $Home4Kbs 'kbs.cmd') $vault
    }
    exit $LASTEXITCODE
}

Say ''
Say '已安装完成。要打开星图，执行： kbs "你的笔记库"'
