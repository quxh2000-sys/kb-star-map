#!/bin/zsh
# 知识库星图工作台 · 安装并生成（macOS）
# 双击本文件即可。它会找到可用的 Python，然后运行安装器。
cd "$(dirname "$0")" || exit 1

PY=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 python3; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "未找到 Python 3.9 或更高版本。"
  echo
  echo "两种解决办法（任选其一）："
  echo "  1) 在“终端”里执行：xcode-select --install   （装完就有系统自带的 Python 3）"
  echo "  2) 到 https://www.python.org/downloads/ 下载安装"
  echo
  echo "装好后重新双击本文件即可。"
  read -k1 -s "?按任意键关闭窗口..."
  exit 1
fi

echo "使用 Python：$("$PY" --version 2>&1)"
echo
"$PY" installer.py
status=$?

echo
if [ $status -ne 0 ]; then
  echo "安装未完成，请看上面的提示。"
fi
read -k1 -s "?按任意键关闭窗口..."
