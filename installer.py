#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库星图工作台 · 安装器（macOS / Windows 通用）

做四件事：
  1. 校验目标笔记库（任意含 Markdown 的文件夹，装了 Obsidian 更好，不装也行）
  2. 检查 Python 与 PyYAML（缺 PyYAML 时尝试自动安装）
  3. 把工具脚本装到 <库>/系统/工作流/工具脚本/
  4. 生成星图 HTML，并打印打开方式

设计约束：工具脚本靠**自身位置**推导库根（parents[3]），因此必须装到固定的
相对路径 `系统/工作流/工具脚本/`，不能改动这个层级。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Windows 控制台默认代码页可能表示不了中文（如英文版为 cp437），
# 直接 print 中文会抛 UnicodeEncodeError 使安装中断。这里统一改为 UTF-8 并容错替换，
# 保证"最坏情况是显示成问号，而不是中断安装"。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

MIN_PYTHON = (3, 9)
# 只用来判断"这是不是一个笔记库"，数到上限就够，不必扫完超大目录。
MARKDOWN_LIMIT = 20000
SCRIPTS_SUBDIR = Path("系统") / "工作流" / "工具脚本"
DASHBOARD_SUBDIR = Path("系统") / "知识库可视化工作台"
RULES_FILENAME = "资产分类规则.yaml"
UPDATE_CONFIG_FILENAME = "更新配置.yaml"
UPDATE_SOURCE_FILE = "update-source.txt"
HTML_NAME = "知识库可视化工作台.html"
# 安装清单与版本戳：升级时据此只清理"上一版由本工具装进去、这一版不再分发"的文件，
# 绝不触碰同事自己放的文件。
MANIFEST_NAME = "installed.json"
VERSION_STAMP = "工具版本.txt"
BACKUP_SUBDIR = "升级备份"
KEEP_BACKUPS = 3
DEFAULT_VERSION = "1.0.0"
# 分类规则的 schema 版本。旧规则文件在升级后不会被覆盖，靠这个值判断它是否还兼容。
RULES_SCHEMA_VERSION = 1

# 数据层：这些路径由同事产生，升级只读不动。安装结束会显式回报"已保留"。
PROTECTED_NAMES = ("资产分类规则.yaml", "运营任务草稿", "更新配置.yaml")

TOOL_FILES = (
    "build_kb_dashboard.py",
    "kb_star_graph.py",
    "serve_kb_dashboard.py",
    "kb_maintenance.py",
    "kb_operations.py",
    "kb_handoff.py",
    "kb_update.py",
)
RESOURCE_DIRS = ("dashboard_assets", "templates")


def package_version(package_root: Path) -> str:
    """版本以包内 VERSION 文件为准，安装器里只是兜底默认值。"""
    version_file = package_root / "VERSION"
    try:
        text = version_file.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return DEFAULT_VERSION
    return text or DEFAULT_VERSION


def say(msg: str = "") -> None:
    print(msg, flush=True)


def fail(msg: str, hint: str = "") -> "NoReturn":  # type: ignore[valid-type]
    say()
    say(f"❌ {msg}")
    if hint:
        say(f"   {hint}")
    say()
    sys.exit(1)


# ---------- ① 定位笔记库 ----------
def ask_vault() -> Path:
    say("请把笔记库的根目录拖到本窗口，然后按回车。")
    say("（也就是直接装着 .md 笔记的那层文件夹；用不用 Obsidian 都可以）")
    say("（也可以直接输入路径，例如 D:\\我的笔记 或 ~/Documents/我的笔记）")
    say()
    try:
        raw = input("库根目录：").strip()
    except (EOFError, KeyboardInterrupt):
        fail("没有收到路径，已取消。")
    raw = raw.strip().strip('"').strip("'").rstrip("\\/")
    if not raw:
        fail("路径为空，已取消。", "重新双击启动器即可再试一次。")
    return Path(raw).expanduser()


def _count_markdown(vault: Path) -> int:
    """统计库内 Markdown 笔记数；隐藏目录（.git/.obsidian/.trash 等）不计。"""
    count = 0
    try:
        for path in vault.rglob("*.md"):
            if any(part.startswith(".") for part in path.relative_to(vault).parts):
                continue
            count += 1
            if count >= MARKDOWN_LIMIT:
                break
    except OSError:
        return count
    return count


def _likely_real_vault(vault: Path) -> Path | None:
    """选了笔记库的上一层时，找出唯一装着笔记的子目录，用于提醒（不阻断）。

    判定只看"本层有没有 .md"：本层没有、且恰好只有一个子目录有笔记，才认为选高了一层。
    分类规则按目录前缀匹配，根目录选错会让规则全部失配，所以值得提醒一句。
    """
    try:
        if any(child.suffix.lower() == ".md" for child in vault.iterdir() if child.is_file()):
            return None
        children = sorted(p for p in vault.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return None
    candidates = [p for p in children if _count_markdown(p)]
    return candidates[0] if len(candidates) == 1 else None


def validate_vault(vault: Path) -> None:
    """只要求"这是一个含 Markdown 笔记的文件夹"，不要求装了 Obsidian。"""
    if not vault.exists() or not vault.is_dir():
        fail(f"这个路径不是一个文件夹：{vault}")
    markdown_count = _count_markdown(vault)
    if not markdown_count:
        fail(
            f"在 {vault} 下没有找到任何 Markdown（.md）笔记。",
            "请确认路径指向的是装有笔记的那一层文件夹。",
        )
    tag = "（Obsidian 库）" if (vault / ".obsidian").exists() else "（通用 Markdown 目录）"
    say(f"✅ 找到 {markdown_count} 篇 Markdown 笔记{tag}")
    deeper = _likely_real_vault(vault)
    if deeper:
        say(f"⚠️  注意：{vault} 这一层本身没有笔记，笔记都在 {deeper} 下。")
        say(f"    如果你的库根目录其实是 {deeper}，建议改用它重跑一次——")
        say("    分类规则按目录前缀匹配，根目录选高一层会让规则失配。")


# ---------- ② 运行环境 ----------
def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        fail(
            f"Python 版本过低：{sys.version.split()[0]}，需要 "
            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]} 及以上。",
            "请到 https://www.python.org/downloads/ 安装后重试（Windows 安装时务必勾选 Add Python to PATH）。",
        )
    say(f"✅ Python {sys.version.split()[0]}")


def ensure_yaml() -> None:
    try:
        import yaml  # noqa: F401
        say("✅ PyYAML 已就绪")
        return
    except ImportError:
        pass
    say("… 缺少 PyYAML，正在尝试自动安装")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "pyyaml"],
        capture_output=True, text=True,
    )
    try:
        import yaml  # noqa: F401
        say("✅ PyYAML 安装成功")
        return
    except ImportError:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        fail(
            "PyYAML 安装失败，无法继续。",
            "请手动执行： " + f'"{sys.executable}" -m pip install pyyaml'
            + (("\n   最后一行错误：" + detail[-1]) if detail else ""),
        )


# ---------- ③ 安装 ----------
def _shipped_files(package_root: Path) -> list[str]:
    """本次要装进脚本层的文件（相对脚本目录），用于写清单与清理旧版遗留。"""
    source = package_root / "tools"
    files: list[str] = []
    for name in TOOL_FILES:
        if (source / name).exists():
            files.append(name)
        else:
            say(f"⚠️  分发包里缺少 {name}（该功能将不可用）")
    for name in RESOURCE_DIRS:
        src = source / name
        if src.is_dir():
            files.extend(
                (Path(name) / path.relative_to(src)).as_posix()
                for path in sorted(src.rglob("*"))
                if path.is_file()
            )
    return files


def read_manifest(scripts_dir: Path) -> dict[str, Any]:
    """读上一版安装清单；没有或损坏都按"首次安装"处理，不阻断升级。"""
    path = scripts_dir / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def prune_previous_version(scripts_dir: Path, previous: dict[str, Any], keep: set[str]) -> list[str]:
    """只删"上一版本工具装进来、这一版不再分发"的文件。

    同事自己放进脚本目录的文件不在清单里，所以永远不会被删——这是升级安全的关键。
    """
    removed: list[str] = []
    for rel in previous.get("files", []):
        rel = str(rel)
        if rel in keep or rel == MANIFEST_NAME:
            continue
        candidate = (scripts_dir / rel).resolve()
        # 双保险：只允许删脚本目录内的普通文件，防止清单被改坏后越界删除
        try:
            candidate.relative_to(scripts_dir.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            candidate.unlink()
            removed.append(rel)
    return removed


def backup_before_update(vault: Path, scripts_dir: Path) -> Path | None:
    """升级前把脚本层与分类规则整份备份，出问题可回滚。

    判据是"脚本目录里已经有东西"，而不是"有上一版清单"：
    v1.0 是不带清单的，从它升级上来这一次同样必须先备份。
    """
    if not scripts_dir.is_dir() or not any(scripts_dir.iterdir()):
        return None
    previous = read_manifest(scripts_dir)
    dashboard_dir = vault / DASHBOARD_SUBDIR
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = dashboard_dir / BACKUP_SUBDIR / f"v{previous.get('version', '未记录版本')}-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(scripts_dir, target / "工具脚本", dirs_exist_ok=True)
    rules = dashboard_dir / RULES_FILENAME
    if rules.exists():
        shutil.copy2(rules, target / RULES_FILENAME)
    # 只留最近 KEEP_BACKUPS 份，避免长期堆积
    backups = sorted(p for p in (dashboard_dir / BACKUP_SUBDIR).iterdir() if p.is_dir())
    for stale in backups[:-KEEP_BACKUPS]:
        shutil.rmtree(stale, ignore_errors=True)
    return target


def install_tools(package_root: Path, vault: Path) -> Path:
    target = vault / SCRIPTS_SUBDIR
    source = package_root / "tools"  # 包内用 ASCII 名，规避 ZIP 中文文件名乱码
    if not source.is_dir():
        fail(f"分发包不完整：找不到 {source}", "请重新解压完整的压缩包再运行。")

    target.mkdir(parents=True, exist_ok=True)
    previous = read_manifest(target)
    shipped = _shipped_files(package_root)

    for rel in shipped:
        src = source / rel
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    removed = prune_previous_version(target, previous, set(shipped))
    if removed:
        say(f"✅ 已清理上一版遗留的 {len(removed)} 个文件：{', '.join(removed[:6])}"
            + ("…" if len(removed) > 6 else ""))
    say(f"✅ 工具脚本已安装到 {target}")
    return target


def write_manifest(package_root: Path, scripts_dir: Path) -> dict[str, Any]:
    version = package_version(package_root)
    manifest = {
        "version": version,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "files": _shipped_files(package_root),
    }
    (scripts_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def write_version_stamp(vault: Path, manifest: dict[str, Any]) -> Path:
    dashboard_dir = vault / DASHBOARD_SUBDIR
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    path = dashboard_dir / VERSION_STAMP
    path.write_text(
        f"知识库星图工作台\n版本：{manifest['version']}\n安装时间：{manifest['installed_at']}\n"
        f"文件数：{len(manifest['files'])}\n\n"
        "升级方式：解压新版分发包，对它重新运行一次安装器即可。\n"
        "本目录下的「资产分类规则.yaml」与「运营任务草稿/」不会被覆盖或删除。\n",
        encoding="utf-8",
    )
    return path


def warn_if_rules_look_unconfigured(rules_path: Path) -> None:
    """升级时旧规则文件不会被动，所以要主动提醒它是否还能用。

    这是升级最常见的"静默失效"：版本换了、规则文件还是老的——
    若它没有 path_rules、或 schema_version 超出本版支持范围，星图会退化成一个星系。
    """
    try:
        text = rules_path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    try:
        import yaml

        data = yaml.safe_load(text) or {}
    except Exception:
        say(f"⚠️  {RULES_FILENAME} 无法解析，本次将回落到内置默认规则。")
        return
    if not isinstance(data, dict):
        say(f"⚠️  {RULES_FILENAME} 不是键值结构，本次将回落到内置默认规则。")
        return
    schema = data.get("schema_version")
    if isinstance(schema, int) and schema > RULES_SCHEMA_VERSION:
        say(f"⚠️  {RULES_FILENAME} 的 schema_version={schema} 高于本版支持的 "
            f"{RULES_SCHEMA_VERSION}，请更新到对应的分发包。")
    if not data.get("path_rules"):
        say(f"⚠️  {RULES_FILENAME} 里没有 path_rules，笔记会全部落进同一个类型。")
        say("    → 按你的目录结构补上，例如： path_rules: [[\"我的目录/\", \"来源资料\"]]")


def report_preserved(vault: Path) -> None:
    """显式回报哪些是同事的数据——升级时看得见才有信任。"""
    dashboard_dir = vault / DASHBOARD_SUBDIR
    say()
    say("已保留的你的数据（升级不会覆盖或删除）：")
    rules = dashboard_dir / RULES_FILENAME
    if rules.exists():
        say(f"  · {rules.name}（{rules.stat().st_size} 字节）")
    store = dashboard_dir / "运营任务草稿"
    if store.is_dir():
        drafts = sum(1 for p in store.rglob("*") if p.is_file())
        say(f"  · 运营任务草稿/（{drafts} 个文件）")
    if (dashboard_dir / UPDATE_CONFIG_FILENAME).exists():
        say(f"  · {UPDATE_CONFIG_FILENAME}（更新源设置）")
    backups = dashboard_dir / BACKUP_SUBDIR
    if backups.is_dir():
        say(f"  · 升级备份/（{sum(1 for p in backups.iterdir() if p.is_dir())} 份）")



def install_update_config(package_root: Path, vault: Path) -> Path:
    """写更新源配置（已存在则不覆盖——同事改过的 repo/api_base 必须保住）。

    仓库地址来自包内的 update-source.txt，这样同事装完就能直接点「检查更新」，
    不需要自己找仓库名。
    """
    dashboard_dir = vault / DASHBOARD_SUBDIR
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    target = dashboard_dir / UPDATE_CONFIG_FILENAME
    if target.exists():
        say(f"✅ 已存在更新配置，未覆盖：{target}")
        return target
    # update-source.txt 一行即可：以 http 开头视为静态清单地址，否则视为 账号/仓库名。
    manifest_url = ""
    repo = ""
    source = package_root / UPDATE_SOURCE_FILE
    if source.exists():
        lines = [line.strip() for line in source.read_text(encoding="utf-8-sig").splitlines()]
        value = next((line for line in lines if line and not line.startswith("#")), "")
        if value.startswith(("http://", "https://")):
            manifest_url = value
        else:
            repo = value
    target.write_text(
        "# 知识库星图工作台 · 更新源配置\n"
        "#\n"
        "# 只有你主动点「检查更新 / 立即更新」时才会访问这里配置的地址；\n"
        "# 请求里只带版本信息，不含任何笔记内容。两个地址都留空即完全关闭联网。\n"
        "#\n"
        "# 【方式一】静态清单（国内推荐）：把 update.json 与 zip 放到任意能访问的地方，\n"
        "#           Gitee raw、对象存储、内网文件服务器都可以。\n"
        f'manifest_url: "{manifest_url}"\n'
        "#\n"
        "# 【方式二】releases 接口：账号/仓库名（GitHub 或 Gitee）\n"
        f'repo: "{repo}"\n'
        "# 接口基址：GitHub 保持默认；Gitee 改成 https://gitee.com/api/v5\n"
        'api_base: "https://api.github.com"\n'
        "\n"
        "# 启动本地模式时自动检查一次（只取版本号，不下载）。默认关闭。\n"
        "auto_check: false\n"
        "\n"
        "timeout: 15\n",
        encoding="utf-8",
    )
    if manifest_url:
        say(f"✅ 已写入更新配置：{target}（静态清单 {manifest_url}）")
    elif repo:
        say(f"✅ 已写入更新配置：{target}（更新源 {repo}）")
    else:
        say(f"✅ 已写入更新配置：{target}")
        say("    → 包内未指定更新源，如需「检查更新」请填写 manifest_url 或 repo")
    return target


def install_rules(package_root: Path, vault: Path) -> Path:
    dashboard_dir = vault / DASHBOARD_SUBDIR
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    target = dashboard_dir / RULES_FILENAME
    if target.exists():
        say(f"✅ 已存在分类规则，未覆盖：{target}")
        warn_if_rules_look_unconfigured(target)
        return target
    template = package_root / "asset-rules.template.yaml"
    if template.exists():
        shutil.copy2(template, target)
        say(f"✅ 已写入分类规则模板：{target}")
        say("    → 请按你自己的目录结构修改它，否则笔记会全部归入「未分类」")
    return target


# ---------- ④ 生成 ----------
LAUNCH_HELPER_NAME = "kb-dashboard-launch.py"

# 与启动器同目录的 ASCII 命名助手：.bat 只负责用 %~dp0 找到它，
# 中文路径、工具脚本定位都由此文件以 UTF-8 处理。
LAUNCH_HELPER_SOURCE = '''"""本地模式启动助手（由安装器生成，请勿手改）。

放在星图 HTML 同一目录。文件名与调用它的 .bat 都保持纯 ASCII，
以免 cmd.exe 按活动代码页解码批处理时出错；中文路径在这里由 Python
按 UTF-8 源码处理，不依赖控制台代码页。
"""
import subprocess
import sys
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
TOOL_SCRIPTS = DASHBOARD_DIR.parent.parent / "系统" / "工作流" / "工具脚本"
SERVE = TOOL_SCRIPTS / "serve_kb_dashboard.py"
if not SERVE.exists():
    SERVE = DASHBOARD_DIR / "serve_kb_dashboard.py"

if not SERVE.exists():
    print("找不到本地模式脚本 serve_kb_dashboard.py：")
    print(f"  已找过 {TOOL_SCRIPTS}")
    print(f"  以及   {DASHBOARD_DIR}")
    print("请重新运行安装器修复。")
    raise SystemExit(1)

raise SystemExit(subprocess.call([sys.executable, str(SERVE), "--launch"]))
'''


def write_text_exact(path: Path, text: str) -> None:
    """按字面写入，禁止换行翻译。

    Windows 的文本模式会把 `\\n` 再翻译成 `\\r\\n`，于是含字面 `\\r\\n` 的
    `.bat` 会被写成 `\\r\\r\\n`，cmd 会把多余的 CR 带进命令行参数。
    固定 newline="" 才能逐字写出（也用 Path.open 以兼容 Python 3.9——
    Path.write_text 的 newline 参数是 3.10 才有的）。
    """
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def write_launcher(vault: Path, scripts_dir: Path) -> Path:
    """在库里写一个当前平台的本地模式启动器。

    只双击 HTML 是"分享快照"，无法打开笔记；本地管理模式才能打开。
    同事不必自己记路径，这里直接生成对应平台的双击文件。
    """
    dashboard_dir = vault / DASHBOARD_SUBDIR
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    serve = scripts_dir / "serve_kb_dashboard.py"

    if sys.platform == "darwin":
        path = dashboard_dir / "启动本地工作台.command"
        write_text_exact(
            path,
            "#!/bin/zsh\n"
            "# 由安装器生成：启动知识库星图的本地管理模式（可打开笔记）\n"
            f'exec "{sys.executable}" "{serve}" --launch\n',
        )
        path.chmod(0o755)
    elif sys.platform.startswith("win"):
        # 批处理必须**纯 ASCII**。实测（Windows 11 + Python 3.12）：
        # 中文路径写在命令行上没问题，但批处理里只要出现一个非 ASCII 字节
        # （哪怕只是 `rem` 注释），cmd.exe 按当前代码页解码时会吞掉行终止符，
        # 导致 `The filename, directory name, or volume label syntax is incorrect.`，
        # 双击启动器静默失败。故中文路径全部交给下面这个 ASCII 命名的 Python 助手，
        # 由 Python 以 UTF-8 源码方式处理，彻底不依赖控制台代码页。
        write_text_exact(dashboard_dir / LAUNCH_HELPER_NAME, LAUNCH_HELPER_SOURCE)
        path = dashboard_dir / "启动本地工作台.bat"
        python_exe = sys.executable if sys.executable.isascii() else "python"
        write_text_exact(
            path,
            "@echo off\r\n"
            "rem KB star-map local mode launcher.\r\n"
            "rem ASCII-only on purpose: cmd.exe misparses batch bytes outside the active code page.\r\n"
            "chcp 65001 >nul\r\n"
            f'"{python_exe}" "%~dp0{LAUNCH_HELPER_NAME}"\r\n'
            "pause\r\n",
        )
    else:
        path = dashboard_dir / "启动本地工作台.sh"
        write_text_exact(
            path,
            "#!/usr/bin/env bash\n"
            f'exec "{sys.executable}" "{serve}" --launch\n',
        )
        path.chmod(0o755)

    say(f"✅ 已生成本地模式启动器：{path}")
    return path


def build(vault: Path, scripts_dir: Path) -> Path:
    output = vault / DASHBOARD_SUBDIR / HTML_NAME
    result = subprocess.run(
        [sys.executable, str(scripts_dir / "build_kb_dashboard.py"),
         "--vault", str(vault), "--output", str(output)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        say()
        say("生成失败，原始输出如下：")
        say((result.stdout or "").strip()[-1500:])
        say((result.stderr or "").strip()[-1500:])
        fail("生成星图失败。", "把上面的输出发给技术支持可以定位问题。")
    say((result.stdout or "").strip())
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="安装并生成知识库星图工作台")
    parser.add_argument("--vault", type=Path, default=None, help="笔记库根目录；省略则交互询问")
    parser.add_argument("--package-root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)

    version = package_version(args.package_root)
    say("=" * 52)
    say(f"知识库星图工作台 · 安装 v{version}")
    say("=" * 52)

    check_python()
    ensure_yaml()

    vault = args.vault.expanduser() if args.vault else ask_vault()
    validate_vault(vault)

    # 先备份上一版（仅升级时），再覆盖；数据文件不在覆盖范围内。
    scripts_dir = vault / SCRIPTS_SUBDIR
    previous = read_manifest(scripts_dir)
    backup = backup_before_update(vault, scripts_dir)
    if backup:
        old = f"v{previous['version']}" if previous.get("version") else "上一版（无清单）"
        say(f"✅ 升级：{old} → v{version}，旧版已备份到 {backup}")

    install_tools(args.package_root, vault)
    manifest = write_manifest(args.package_root, scripts_dir)
    rules = install_rules(args.package_root, vault)
    update_config = install_update_config(args.package_root, vault)
    launcher = write_launcher(vault, scripts_dir)
    output = build(vault, scripts_dir)
    stamp = write_version_stamp(vault, manifest)

    say()
    say("=" * 52)
    say("完成。" if not backup else f"完成（升级到 v{version}，旧版可回滚）。")
    say("=" * 52)
    say(f"星图文件：{output}")
    say()
    say("打开方式：直接双击这个 HTML（用 Chrome 或 Edge 效果最好）。")
    say("本地管理模式下才能从星图直接打开笔记（装了 Obsidian 就用 Obsidian，否则用系统默认程序）。")
    say("只双击 HTML 时该按钮会改为复制路径。")
    say(f"分类规则：{rules}")
    say(f"本地模式启动器：{launcher}")
    say(f"版本记录：{stamp}")
    say(f"更新配置：{update_config}")
    report_preserved(vault)
    say()
    say("下次升级：解压新版分发包，对它重新运行一次安装器即可；你的数据保持不变。")
    say()
    return 0


if __name__ == "__main__":
    sys.exit(main())
