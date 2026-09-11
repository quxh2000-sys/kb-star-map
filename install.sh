#!/bin/bash
# 知识库星图工作台 · 一行安装（macOS / Linux）
#
#   curl -fsSL https://gitee.com/quxh20000/kb-star-map/raw/master/install.sh | bash
#
# 装到 ~/.kb-star-map，并把 kbs 命令放进 PATH。之后任何目录执行 kbs 即可。
set -e

MANIFEST_URL="https://gitee.com/quxh20000/kb-star-map/raw/master/dist/update.json"
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

say ''
say '正在从 Gitee 获取最新版本…'
INFO="$("$PY" - "$MANIFEST_URL" <<'PYEOF'
import json, sys, urllib.request
url = sys.argv[1]
req = urllib.request.Request(url, headers={"User-Agent": "kbs-installer"})
with urllib.request.urlopen(req, timeout=30) as r:
    print(json.dumps(json.loads(r.read().decode("utf-8")), ensure_ascii=False))
PYEOF
)" || die "读取更新清单失败，请检查网络。"
VERSION="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["version"])' "$INFO")"
say "版本: v$VERSION"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
say '正在下载…'
"$PY" - "$INFO" "$WORK/pkg.zip" <<'PYEOF'
import hashlib, json, sys, urllib.request
info = json.loads(sys.argv[1]); dest = sys.argv[2]
req = urllib.request.Request(info["zip_url"], headers={"User-Agent": "kbs-installer"})
with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
    while True:
        chunk = r.read(262144)
        if not chunk: break
        f.write(chunk)
digest = hashlib.sha256(open(dest, "rb").read()).hexdigest()
if info.get("sha256") and digest != info["sha256"].lower():
    sys.exit(f"校验不通过：期望 {info['sha256']} / 实际 {digest}")
print(f"校验通过（sha256 {digest[:16]}…）")
PYEOF

if [ -d "$HOME_DIR/tools" ]; then
  BACKUP="$HOME_DIR/升级备份/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BACKUP"
  cp -R "$HOME_DIR/tools" "$BACKUP/" 2>/dev/null || true
  say "已备份旧版本到 $BACKUP"
  rm -rf "$HOME_DIR/tools"
fi
mkdir -p "$HOME_DIR"
"$PY" -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$WORK/pkg.zip" "$HOME_DIR"
"$PY" -c 'import json,sys,datetime,pathlib; pathlib.Path(sys.argv[1]).write_text(json.dumps({"version": sys.argv[2], "installed_at": datetime.datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False), encoding="utf-8")' "$HOME_DIR/installed.json" "$VERSION"

# kbs 启动器
cat > "$HOME_DIR/kbs" <<'SHEOF'
#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
