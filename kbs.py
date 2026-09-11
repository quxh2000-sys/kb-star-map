#!/usr/bin/env python3
"""kbs —— 知识库星图工作台命令行入口。

用法：
    kbs                    把当前目录当作笔记库，生成星图并打开本地管理模式
    kbs <笔记库目录>        指定笔记库
    kbs build [目录]       只生成 HTML，不启动服务
    kbs port <目录>        在指定端口起服务（不自动开浏览器）
    kbs stop               停止已启动的本地服务
    kbs check              检查是否有新版本
    kbs update             更新工具本身
    kbs version            显示版本与安装位置

安装位置：~/.kb-star-map（由 install.ps1 / install.sh 放到这里）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HOME_DIR = Path(__file__).resolve().parent
TOOLS_DIR = HOME_DIR / "tools"
DASHBOARD_SUBDIR = Path("系统") / "知识库可视化工作台"
RULES_FILENAME = "资产分类规则.yaml"
UPDATE_CONFIG_FILENAME = "更新配置.yaml"
VERSION_STAMP = "工具版本.txt"
DEFAULT_PORT = 8765


def _load(module_name: str):
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    return __import__(module_name)


def installed_version() -> str:
    manifest = HOME_DIR / "installed.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "未知")
    except (OSError, json.JSONDecodeError, AttributeError):
        return "未知"


def resolve_vault(raw: str | None) -> Path:
    vault = Path(raw).expanduser().resolve() if raw else Path.cwd()
    if not vault.is_dir():
        raise SystemExit(f"[错误] 不是文件夹：{vault}")
    markdown = [p for p in vault.rglob("*.md") if not any(part.startswith(".") for part in p.relative_to(vault).parts)]
    if not markdown:
        raise SystemExit(
            f"[错误] 在 {vault} 下没有找到 .md 笔记。\n"
            "  请在笔记库目录里运行 kbs，或指定路径： kbs \"D:\\我的笔记\""
        )
    return vault


def ensure_vault_ready(vault: Path) -> Path:
    """首次在某库使用时补齐该库需要的文件；已存在的绝不覆盖。"""
    dashboard = vault / DASHBOARD_SUBDIR
    dashboard.mkdir(parents=True, exist_ok=True)

    rules = dashboard / RULES_FILENAME
    if not rules.exists():
        template = HOME_DIR / "asset-rules.template.yaml"
        if template.exists():
            rules.write_bytes(template.read_bytes())
            print(f"[提示] 已写入分类规则模板：{rules}")
            print("       按你的目录结构改它，否则笔记会全部归入同一个类型。")

    config = dashboard / UPDATE_CONFIG_FILENAME
    if not config.exists():
        source = HOME_DIR / "update-source.txt"
        value = ""
        if source.exists():
            lines = [x.strip() for x in source.read_text(encoding="utf-8").splitlines()]
            value = next((x for x in lines if x and not x.startswith("#")), "")
        manifest = f'manifest_url: "{value}"' if value.startswith("http") else 'manifest_url: ""'
        repo = "" if value.startswith("http") else value
        config.write_text(
            "# 知识库星图工作台 · 更新源配置（只有主动检查更新时才会联网）\n"
            f'{manifest}\nrepo: "{repo}"\napi_base: "https://api.github.com"\n'
            "auto_check: false\ntimeout: 15\n",
            encoding="utf-8",
        )
    return dashboard


def run_build(vault: Path, dashboard: Path) -> int:
    return subprocess.call([
        sys.executable, str(TOOLS_DIR / "build_kb_dashboard.py"),
        "--vault", str(vault),
        "--output", str(dashboard / "知识库可视化工作台.html"),
    ])


def run_serve(vault: Path, dashboard: Path, port: int, launch: bool) -> int:
    args = [
        sys.executable, str(TOOLS_DIR / "serve_kb_dashboard.py"),
        "--launch" if launch else "--serve",
        "--vault", str(vault),
        "--dashboard-dir", str(dashboard),
        "--port", str(port),
    ]
    return subprocess.call(args)


def cmd_start(vault_arg: str | None, port: int, launch: bool, build_only: bool) -> int:
    vault = resolve_vault(vault_arg)
    dashboard = ensure_vault_ready(vault)
    print(f"笔记库：{vault}")
    if run_build(vault, dashboard) != 0:
        return 1
    if build_only:
        print(f"星图：{dashboard / '知识库可视化工作台.html'}")
        return 0
    return run_serve(vault, dashboard, port, launch)


def cmd_check(vault_arg: str | None) -> int:
    vault = resolve_vault(vault_arg)
    ensure_vault_ready(vault)
    kb_update = _load("kb_update")
    result = kb_update.check_for_update(vault)
    print(f"当前版本：v{installed_version()}")
    if result.get("error"):
        print(f"检查失败：{result['error']}")
        return 1
    if not result.get("configured"):
        print(result.get("message", "未配置更新源"))
        return 1
    if result.get("update_available"):
        print(f"有新版本：v{result['latest']}（运行 kbs update 更新）")
        if result.get("changelog"):
            print(result["changelog"][:400])
    else:
        print("已是最新版本。")
    return 0


def cmd_update(vault_arg: str | None) -> int:
    vault = resolve_vault(vault_arg)
    ensure_vault_ready(vault)
    kb_update = _load("kb_update")
    print("正在检查并更新…")
    result = kb_update.perform_update(vault)
    print(result.get("message", ""))
    if result.get("output"):
        print(result["output"][-1200:])
    if result.get("ok"):
        print(f"完成。当前版本：v{installed_version()}")
        return 0
    return 1


def cmd_stop(port: int) -> int:
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "serve_kb_dashboard.py"), "--stop", "--port", str(port)],
        capture_output=True, text=True,
    )
    print((result.stdout or result.stderr or "").strip())
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kbs",
        description="知识库星图工作台：把 Markdown 笔记库变成可缩放、筛选、回放的星图",
        add_help=True,
    )
    parser.add_argument("target", nargs="?", help="笔记库目录（省略则用当前目录）；或子命令 build/port/stop/check/update/version")
    parser.add_argument("path", nargs="?", help="子命令的路径参数")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"本地服务端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--no-open", action="store_true", help="不要自动打开浏览器")
    args = parser.parse_args(argv)

    sub = args.target
    if sub == "version":
        print(f"知识库星图工作台 v{installed_version()}")
        print(f"安装位置：{HOME_DIR}")
        return 0
    if sub == "check":
        return cmd_check(args.path)
    if sub == "update":
        return cmd_update(args.path)
    if sub == "stop":
        return cmd_stop(args.port)
    if sub == "build":
        return cmd_start(args.path, args.port, launch=False, build_only=True)
    if sub == "port":
        return cmd_start(args.path, args.port, launch=False, build_only=False)
    return cmd_start(sub, args.port, launch=not args.no_open, build_only=False)


if __name__ == "__main__":
    raise SystemExit(main())
