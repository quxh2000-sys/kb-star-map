#!/bin/bash
# 知识库星图工作台 · 一行安装（macOS / Linux）
#
#   curl -fsSL https://gitee.com/quxh20000/kb-star-map/raw/master/install.sh | bash
#
# 不用 zip：从 Gitee 逐文件拉取并逐个校验 sha256。下载逻辑复用 tools/bootstrap.py
#（安装与更新共用同一份实现，避免多处维护）。
set -e

RAW_BASE="https://gitee.com/quxh20000/kb-star-map/raw/master"
MANIFEST_URL="$RAW_BASE/dist/files.json"
HOME_DIR="$HOME/.kb-star-map"

say() { printf '%s\n' "$*"; }
die() { printf '[错误] %s\n' "$*" >&2; exit 1; }

say '=============================================='
say '  知识库星图工作台 · 安装'
say '=============================================='

PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then PY="$cand"; break; fi
  fi
done
[ -n "$PY" ] || die "没有找到 Python 3.9+。macOS 可执行 xcode-select --install，或到 python.org 安装。"
say "Python: $("$PY" --version 2>&1)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

say ''
say '正在获取引导器…'
"$PY" - "$RAW_BASE/tools/bootstrap.py" "$WORK/bootstrap.py" <<'PYEOF'
import sys, urllib.request
url, dest = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, headers={"User-Agent": "kbs-installer"})
with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as fh:
    fh.write(r.read())
PYEOF

# 旧版先备份：出问题能退回去。只留最近 3 份。
if [ -d "$HOME_DIR" ]; then
  BACKUP="$HOME_DIR/升级备份/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BACKUP"
  cp -R "$HOME_DIR/tools" "$BACKUP/" 2>/dev/null || true
  say "已备份旧版本到 $BACKUP"
  ls -1dt "$HOME_DIR/升级备份"/*/ 2>/dev/null | tail -n +4 | while read -r old; do rm -rf "$old"; done
  # 清空重装：逐文件覆盖不会删掉「新版已移除」的文件，旧残留会一直躺着。
  # 保留用户自己的东西：备份、更新源、设置。
  find "$HOME_DIR" -mindepth 1 -maxdepth 1 \
    ! -name '升级备份' ! -name '更新配置.yaml' ! -name '设置.yaml' \
    -exec rm -rf {} + 2>/dev/null || true
fi

say ''
say '--- 开始安装 ---'
"$PY" "$WORK/bootstrap.py" --manifest "$MANIFEST_URL" --raw-base "$RAW_BASE" --target "$HOME_DIR" \
  || die "安装未完成，请看上面的提示。"

# 更新源配置属于工具本身；已有就不覆盖，免得冲掉用户改过的
if [ ! -f "$HOME_DIR/更新配置.yaml" ]; then
  cat > "$HOME_DIR/更新配置.yaml" <<YAMLEOF
# 知识库星图工作台 · 更新源（只有主动检查更新时才会联网）
manifest_url: "$MANIFEST_URL"
repo: ""
api_base: "https://api.github.com"
auto_check: false
timeout: 15
YAMLEOF
fi

# kbs 启动器
cat > "$HOME_DIR/kbs" <<'SHEOF'
#!/bin/bash
# 安装器会把本脚本软链到 ~/.local/bin/kbs。通过软链调用时 BASH_SOURCE 指向软链本身，
# 直接用它的目录会找不到同目录的 kbs.py——所以先解析符号链接（macOS 的 readlink 没有 -f）。
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  TARGET="$(readlink "$SOURCE")"
  case "$TARGET" in
    /*) SOURCE="$TARGET" ;;
    *) SOURCE="$(dirname "$SOURCE")/$TARGET" ;;
  esac
done
DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
exec python3 "$DIR/kbs.py" "$@"
SHEOF
chmod +x "$HOME_DIR/kbs"

# 放进 PATH
LINKED=""
for dir in "$HOME/.local/bin" "/usr/local/bin"; do
  if [ -d "$dir" ] && [ -w "$dir" ]; then
    ln -sf "$HOME_DIR/kbs" "$dir/kbs" && LINKED="$dir/kbs" && break
  fi
done

VERSION="$("$PY" -c 'import json,sys,pathlib; print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")).get("version","?"))' "$HOME_DIR/installed.json" 2>/dev/null || echo "?")"

say ''
say '=============================================='
say "  安装完成（v${VERSION}）"
say '=============================================='
say "  安装位置：$HOME_DIR"
if [ -n "$LINKED" ]; then
  say "  命令已就绪：$LINKED"
else
  say "  未找到可写的 PATH 目录。请把这一行加到 ~/.zshrc："
  say "      export PATH=\"$HOME_DIR:\$PATH\""
fi
say ''
say '  用法：'
say '    cd 到你的笔记库目录，然后执行：  kbs'
say '    或直接指定目录：                kbs "$HOME/Documents/我的笔记"'
say ''
say '  其它：'
say '    kbs update    更新工具'
say '    kbs check     查看是否有新版本'
say '    kbs stop      停止本地服务'
say '    kbs version   查看版本与安装位置'

# ---------- 装完直接进浏览器（像桌面端 Agent 那样）----------
if [ "${KBS_NO_LAUNCH:-}" = "1" ]; then
  say ''
  say '已跳过启动（KBS_NO_LAUNCH=1）。随时执行： kbs "你的笔记库"'
  exit 0
fi

VAULT="${KBS_VAULT:-}"
if [ -z "$VAULT" ]; then
  # 不做任何自动搜索：扫到大家目录会卡很久，而且猜错了更麻烦。
  # 只弹原生文件夹选择框，让用户明确指定。
  if command -v osascript >/dev/null 2>&1; then
    # curl | bash 时 stdin 是脚本本身，所以必须用图形弹窗而不是 read
    VAULT="$(osascript \
      -e 'try' \
      -e 'POSIX path of (choose folder with prompt "请选择你的笔记库（装着 .md 笔记的那一层文件夹）")' \
      -e 'on error' \
      -e 'return ""' \
      -e 'end try' 2>/dev/null || true)"
  else
    printf '笔记库路径（装着 .md 笔记的文件夹，留空跳过）：'
    read -r VAULT < /dev/tty || VAULT=""
  fi
fi

if [ -n "$VAULT" ] && [ -d "$VAULT" ]; then
  say ''
  say "正在生成星图并打开浏览器：$VAULT"
  say '（这个窗口就是本地管理服务，关掉它即停止；Ctrl+C 也可以）'
  say ''
  if [ "${KBS_NO_OPEN:-}" = "1" ]; then
    exec "$HOME_DIR/kbs" port "$VAULT"     # 起服务但不自动开浏览器
  fi
  exec "$HOME_DIR/kbs" "$VAULT"
fi

say ''
say '已安装完成。要打开星图，执行： kbs "你的笔记库"'
