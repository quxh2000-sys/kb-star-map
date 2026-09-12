#!/usr/bin/env python3
"""知识库星图工作台 · 更新器。

更新源是一份**文件清单**（dist/files.json），逐文件从 Gitee raw 拉取并校验 sha256。
不用 zip、不用 GitHub Releases：发布物就是仓库本身，不需要额外产出二进制资产。

清单格式：
    {"version": "1.1.0", "files": {"kbs.py": "<sha256>", "tools/xxx.py": "<sha256>"}}

**联网边界**：只有主动检查/更新时才联网；请求里只有文件路径，不含任何笔记内容。
把 更新配置.yaml 的 manifest_url 留空，就完全不联网。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
CONFIG_FILENAME = "更新配置.yaml"
MANIFEST_NAME = "installed.json"
DEFAULT_MANIFEST_URL = "https://gitee.com/quxh20000/kb-star-map/raw/master/dist/files.json"
RETRY_ATTEMPTS = 3
RETRY_DELAY = 1.2
DEFAULT_TIMEOUT = 15
USER_AGENT = "kbs-updater"


def _load_bootstrap():
    """复用安装器那份下载实现——安装与更新只有一份逻辑。"""
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    import bootstrap
    return bootstrap


# ---------------------------------------------------------------- 安装位置

def install_root() -> Path:
    """工具的安装根目录：~/.kb-star-map（kb_update.py 位于其 tools/ 下）。"""
    return Path(__file__).resolve().parent.parent


def read_installed_version(fallback: str = "未知") -> str:
    """当前版本：读工具自己的安装清单。

    utf-8-sig：Windows 上 Set-Content -Encoding UTF8 会写 BOM，
    用 utf-8 读会解析失败导致版本显示"未知"。
    """
    try:
        data = json.loads((install_root() / MANIFEST_NAME).read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and data.get("version"):
            return str(data["version"])
    except (OSError, json.JSONDecodeError):
        pass
    return fallback


# ---------------------------------------------------------------- 版本比较

def parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(value).lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    target = parse_version(candidate)
    if not target:
        return False
    have = parse_version(current)
    if not have:
        return True                       # 当前版本未知时按"可升级"处理
    width = max(len(target), len(have))
    return target + (0,) * (width - len(target)) > have + (0,) * (width - len(have))


# ---------------------------------------------------------------- 配置

@dataclass(frozen=True)
class UpdateConfig:
    manifest_url: str = ""
    auto_check: bool = False
    timeout: int = DEFAULT_TIMEOUT
    parse_error: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.manifest_url) and not self.parse_error


def config_path() -> Path:
    """更新源配置放在工具自己的安装目录里——更新源是工具的属性，不属于某个笔记库。

    KB_UPDATE_CONFIG 可覆盖：开发目录里直接跑时用它，免得把配置写进笔记库。
    """
    override = os.environ.get("KB_UPDATE_CONFIG")
    if override:
        return Path(override).expanduser()
    return install_root() / CONFIG_FILENAME


def load_config() -> UpdateConfig:
    path = config_path()
    if not path.exists():
        return UpdateConfig()
    try:
        import yaml
        parsed = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except Exception as exc:              # noqa: BLE001 - 配置坏了不该让服务起不来
        return UpdateConfig(parse_error=f"{CONFIG_FILENAME} 解析失败：{exc}")
    if not isinstance(parsed, dict):
        return UpdateConfig(parse_error=f"{CONFIG_FILENAME} 不是键值结构")
    url = str(parsed.get("manifest_url") or "").strip()
    # 老配置指向 update.json（zip 时代的清单），自动换到 files.json，免得更新直接失效
    if url.endswith("/update.json"):
        url = url[: -len("/update.json")] + "/files.json"
    try:
        timeout = int(parsed.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return UpdateConfig(manifest_url=url, auto_check=bool(parsed.get("auto_check")), timeout=timeout)


# ---------------------------------------------------------------- 网络

def _retry(operation, attempts: int = RETRY_ATTEMPTS, delay: float = RETRY_DELAY):
    """实测瞬时 TLS 失败重试一次即成功——不该让用户看到莫名报错。4xx 不重试。"""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                raise
            last = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
        if attempt < attempts:
            time.sleep(delay * attempt)
    raise last if last else RuntimeError("请求失败")


def fetch_manifest_remote(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    def operation():
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    data = _retry(operation)
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError(f"清单格式不对（缺少 files）：{url}")
    return data


# ---------------------------------------------------------------- 对外接口

def check_for_update() -> dict:
    """看有没有新版。任何失败都转成结构化结果，不让界面崩。"""
    current = read_installed_version()
    config = load_config()
    if config.parse_error:
        return {"ok": False, "configured": False, "current": current,
                "error": f"{config.parse_error}，请修正 {config_path()}"}
    if not config.enabled:
        return {"ok": True, "configured": False, "current": current,
                "message": f"未配置更新源。在 {config_path()} 里填写 manifest_url"}
    try:
        manifest = fetch_manifest_remote(config.manifest_url, config.timeout)
    except Exception as error:            # noqa: BLE001
        return {"ok": False, "configured": True, "current": current,
                "error": f"{error.__class__.__name__}: {error}"}
    latest = str(manifest.get("version") or "")
    return {
        "ok": True,
        "configured": True,
        "current": current,
        "latest": latest,
        "update_available": is_newer(latest, current),
        "file_count": len(manifest.get("files") or {}),
        "changelog": str(manifest.get("changelog") or ""),
    }


def perform_update() -> dict:
    """按清单更新。先整体下载校验，再一次性替换，中途失败不留半新半旧。"""
    config = load_config()
    if config.parse_error:
        return {"ok": False, "message": f"{config.parse_error}，请修正 {config_path()}"}
    if not config.enabled:
        return {"ok": False, "message": f"未配置更新源：{config_path()}"}

    current = read_installed_version()
    try:
        manifest = fetch_manifest_remote(config.manifest_url, config.timeout)
    except Exception as error:            # noqa: BLE001
        return {"ok": False, "message": f"读取更新清单失败：{error}"}

    latest = str(manifest.get("version") or "")
    if not is_newer(latest, current):
        return {"ok": True, "skipped": True, "from": current, "to": current,
                "message": f"已是最新版本 v{current}"}

    bootstrap = _load_bootstrap()
    root = install_root()
    backup = root / "升级备份" / time.strftime("%Y%m%d-%H%M%S")
    try:
        if (root / "tools").is_dir():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root / "tools", backup / "tools", dirs_exist_ok=True)
    except OSError:
        pass

    result = bootstrap.install(config.manifest_url, root, log=lambda *_: None)
    if not result.get("ok"):
        return {"ok": False, "from": current, "to": latest,
                "message": f"更新失败：{result.get('message')}"}
    return {
        "ok": True,
        "from": current,
        "to": str(result.get("version") or latest),
        "files": result.get("files"),
        "removed": result.get("removed") or [],
        "backup": str(backup),
        "message": f"已更新到 v{result.get('version') or latest}",
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="kb_update", description="检查并更新知识库星图工作台")
    parser.add_argument("--check", action="store_true", help="只检查有没有新版本")
    args = parser.parse_args(argv)
    result = check_for_update() if args.check else perform_update()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
