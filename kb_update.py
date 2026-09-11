#!/usr/bin/env python3
"""知识库星图工作台 · 更新器：从 GitHub Releases 检查并应用更新。

**联网边界（重要）**
  · 只有你主动点「检查更新」或「立即更新」时才访问 GitHub；
  · 请求里只包含仓库名，**不含任何笔记内容**；
  · 把 更新配置.yaml 的 repo 留空，本工具就完全不联网。

**为什么更新器很短**
  它不自己实现升级，而是下载新版分发包、解压，然后调用**新版自带的 installer.py**。
  于是"升级"这套动作（备份 → 覆盖程序层 → 清理旧版遗留 → 保护数据层）只有一份实现，
  自动更新与手动重跑安装器的安全保证完全一致，也不会随版本漂移。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DASHBOARD_SUBDIR = Path("系统") / "知识库可视化工作台"
MANIFEST_NAME = "installed.json"
CONFIG_FILENAME = "更新配置.yaml"

DEFAULT_API_BASE = "https://api.github.com"
RELEASES_PATH = "/repos/{repo}/releases/latest"
USER_AGENT = "kb-star-map-updater"
DEFAULT_TIMEOUT = 15
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
DIGEST_RE = re.compile(r"sha256[:\s=]+([0-9a-fA-F]{64})")
# 实测 Windows 上偶发瞬时 TLS 失败（CERTIFICATE_VERIFY_FAILED 立即重试即成功），
# 网络抖动不该让同事看到一句莫名的"检查失败"。
RETRY_ATTEMPTS = 3
RETRY_DELAY = 1.5

# Windows 控制台默认代码页可能表示不了中文，直接 print 会抛 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


@dataclass(frozen=True)
class UpdateConfig:
    repo: str
    auto_check: bool
    timeout: int
    # 接口基址。默认 GitHub 公有云；换成企业版或内网镜像时只改这一项。
    # 本地测试也用它把请求指向 mock 服务。
    api_base: str = DEFAULT_API_BASE
    # 静态清单地址。设了就优先用它，完全不依赖任何平台的 releases 接口——
    # 国内把 update.json 与 zip 放在 Gitee raw / 对象存储 / 内网文件服务器都行。
    manifest_url: str = ""
    # 配置文件存在但读不出来时的原因（例如被存成了带 BOM 的 UTF-8）。
    # 有值就说明"不是没配，是没读成功"，界面必须把这句话说出来，不能显示"未配置"。
    parse_error: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.manifest_url) or bool(self.repo and "/" in self.repo)

    @property
    def mode(self) -> str:
        if self.manifest_url:
            return "manifest"
        return "releases" if self.repo and "/" in self.repo else "none"


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    changelog: str
    zip_url: str
    zip_name: str
    published_at: str


def config_path() -> Path:
    """更新源配置放在工具自己的安装目录里——更新源是工具的属性，不属于某个笔记库。"""
    return install_root() / CONFIG_FILENAME


def install_root() -> Path:
    """工具的安装根目录。

    只有一种形态：kb_update.py 位于 <安装根>/tools/ 下，即 ~/.kb-star-map。
    （测试可通过替身覆盖此函数。）
    """
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


def load_config() -> UpdateConfig:
    """读更新配置；文件缺失或损坏都按"未配置"处理，绝不因为配置文件阻断升级。"""
    import yaml

    path = config_path()
    data: dict = {}
    parse_error = ""
    if path.exists():
        try:
            # utf-8-sig：无 BOM 时与 utf-8 等价；带 BOM 时自动剥离。
            # 同事用记事本另存为「UTF-8 带 BOM」会让 YAML 直接解析失败。
            loaded = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
            else:
                parse_error = f"{CONFIG_FILENAME} 不是键值结构"
        except Exception as exc:
            parse_error = f"{CONFIG_FILENAME} 解析失败：{exc}"
    return UpdateConfig(
        repo=str(data.get("repo") or "").strip(),
        auto_check=bool(data.get("auto_check", False)),
        timeout=int(data.get("timeout") or DEFAULT_TIMEOUT),
        api_base=str(data.get("api_base") or DEFAULT_API_BASE).rstrip("/"),
        manifest_url=str(data.get("manifest_url") or "").strip(),
        parse_error=parse_error,
    )



def _retry(operation, attempts: int = RETRY_ATTEMPTS, delay: float = RETRY_DELAY):
    """重试瞬时网络/TLS 故障；4xx 是确定答复，不重试。"""
    last: Exception | None = None
    for index in range(attempts):
        try:
            return operation()
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                raise
            last = exc
        except (URLError, OSError) as exc:
            last = exc
        if index + 1 < attempts:
            time.sleep(delay * (index + 1))
    raise last  # type: ignore[misc]

def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2' → (1, 2, 3) / (1, 2)。取不到数字时返回空元组。"""
    numbers = re.findall(r"\d+", str(text))
    return tuple(int(n) for n in numbers) if numbers else ()


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if not new:
        return False
    if not old:
        return True
    # 补齐长度再比，避免 1.2 与 1.2.0 被判成不同
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def fetch_latest_release(repo: str, timeout: int = DEFAULT_TIMEOUT, api_base: str = DEFAULT_API_BASE) -> ReleaseInfo:
    """取最新 release。只发一个 GET，不带任何本地信息。"""
    def _fetch():
        request = Request(
            api_base.rstrip("/") + RELEASES_PATH.format(repo=repo),
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    payload = _retry(_fetch)
    if not isinstance(payload, dict):
        raise ValueError("GitHub 返回的不是一个 release 对象")
    tag = str(payload.get("tag_name") or "").strip()
    assets = [a for a in payload.get("assets") or [] if str(a.get("name", "")).lower().endswith(".zip")]
    if not assets:
        raise ValueError(f"release {tag or '(无 tag)'} 里没有 .zip 资产")
    asset = assets[0]
    return ReleaseInfo(
        version=tag.lstrip("vV") or tag,
        tag=tag,
        changelog=str(payload.get("body") or "").strip(),
        zip_url=str(asset.get("browser_download_url") or ""),
        zip_name=str(asset.get("name") or "package.zip"),
        published_at=str(payload.get("published_at") or ""),
    )



def fetch_manifest(manifest_url: str, timeout: int = DEFAULT_TIMEOUT) -> ReleaseInfo:
    """读静态清单 update.json。任何静态托管都能用，不依赖平台接口。

    清单格式：
        {"version": "1.0.2", "zip_url": "...", "sha256": "...", "changelog": "..."}
    """
    def _fetch():
        request = Request(manifest_url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    payload = _retry(_fetch)
    if not isinstance(payload, dict):
        raise ValueError("更新清单不是 JSON 对象")
    version = str(payload.get("version") or "").strip()
    zip_url = str(payload.get("zip_url") or "").strip()
    if not version or not zip_url:
        raise ValueError("更新清单缺少 version 或 zip_url")
    digest = str(payload.get("sha256") or "").strip().lower()
    changelog = str(payload.get("changelog") or "").strip()
    if digest:
        changelog = f"{changelog}\n\nsha256: {digest}".strip()
    return ReleaseInfo(
        version=version.lstrip("vV"),
        tag=version,
        changelog=changelog,
        zip_url=zip_url,
        zip_name=zip_url.rstrip("/").split("/")[-1] or "package.zip",
        published_at=str(payload.get("published_at") or ""),
    )


def latest_release(config: UpdateConfig) -> ReleaseInfo:
    """按配置选源：静态清单优先，其次 releases 接口。"""
    if config.manifest_url:
        return fetch_manifest(config.manifest_url, config.timeout)
    return fetch_latest_release(config.repo, config.timeout, config.api_base)

def download_release(url: str, dest: Path, timeout: int = 60, max_bytes: int = MAX_DOWNLOAD_BYTES) -> Path:
    def _download():
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = Request(url, headers={"User-Agent": USER_AGENT})
        written = 0
        with urlopen(request, timeout=timeout) as response, dest.open("wb") as handle:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError(f"下载超过上限 {max_bytes // 1024 // 1024} MB，已中止")
                handle.write(chunk)
        return dest

    try:
        return _retry(_download)
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_digest(changelog: str) -> str:
    """release 说明里写了 `sha256: <64位十六进制>` 就拿来校验，没写就跳过。"""
    match = DIGEST_RE.search(changelog or "")
    return match.group(1).lower() if match else ""


def extract_package(zip_path: Path, dest: Path) -> Path:
    """解压并做 zip-slip 防护；返回解压后的包根目录。

    校验标志是 kbs.py（home 模式的入口）；installer.py 已随旧安装方式移除。
    """
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (dest / member.filename).resolve()
            if not str(target).startswith(str(root)):
                raise ValueError(f"压缩包内含越界路径，已拒绝：{member.filename}")
        archive.extractall(dest)
    if not (dest / "kbs.py").exists() and not list(dest.rglob("kbs.py")):
        raise ValueError("压缩包里没有 kbs.py，可能不是本工具的分发包")
    return dest


def apply_home_update(zip_path: Path) -> dict[str, object]:
    """home 模式升级：备份旧 tools，把新包覆盖到安装目录。

    只覆盖工具自己的安装目录，绝不碰任何笔记库。
    """
    root = install_root()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = root / "升级备份" / stamp
    try:
        if (root / "tools").is_dir():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root / "tools", backup / "tools", dirs_exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="kb-home-"))
        try:
            extract_package(zip_path, workdir)
            shutil.copytree(workdir, root, dirs_exist_ok=True)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return {"ok": True, "output": f"已更新工具目录 {root}；旧版本备份在 {backup}"}
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return {"ok": False, "output": f"覆盖安装失败：{exc.__class__.__name__}: {exc}"}


def check_for_update() -> dict[str, object]:
    """给界面用的检查结果。任何异常都转成结构化结果，不抛给调用方。"""
    config = load_config()
    current = read_installed_version()
    if not config.enabled:
        if config.parse_error:
            return {
                "configured": False,
                "current": current,
                "error": config.parse_error,
                "message": f"{config.parse_error}，请修正 {config_path()}",
            }
        return {
            "configured": False,
            "current": current,
            "message": f"未配置更新源。请在 {config_path()} 里填写 manifest_url 或 repo",
        }
    try:
        release = latest_release(config)
    except (HTTPError, URLError, ValueError, OSError, json.JSONDecodeError) as exc:
        return {"configured": True, "current": current, "error": f"{exc.__class__.__name__}: {exc}"}
    return {
        "configured": True,
        "current": current,
        "latest": release.version,
        "tag": release.tag,
        "changelog": release.changelog[:2000],
        "published_at": release.published_at,
        "zip_name": release.zip_name,
        "update_available": is_newer(release.version, current),
    }


def perform_update() -> dict[str, object]:
    """完整执行一次更新：检查 → 下载 → 校验 → 交给新版安装器。"""
    config = load_config()
    if not config.enabled:
        detail = f"（{config.parse_error}）" if config.parse_error else ""
        return {"ok": False, "message": f"未配置更新源{detail}：{config_path()}"}
    try:
        release = latest_release(config)
    except (HTTPError, URLError, ValueError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "message": f"获取版本信息失败：{exc}"}

    current = read_installed_version()
    if not is_newer(release.version, current):
        return {"ok": True, "skipped": True, "message": f"已是最新版本 v{current}"}

    workdir = Path(tempfile.mkdtemp(prefix="kb-download-"))
    try:
        archive = download_release(release.zip_url, workdir / release.zip_name, timeout=120)
        digest = sha256_of(archive)
        wanted = expected_digest(release.changelog)
        if wanted and wanted != digest:
            return {
                "ok": False,
                "message": f"校验失败，已中止：期望 sha256 {wanted[:12]}…，实际 {digest[:12]}…",
            }
        applied = apply_home_update(archive)
        if applied["ok"]:
            try:
                (install_root() / MANIFEST_NAME).write_text(
                    json.dumps({"version": release.version, "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                               ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
        return {
            "ok": bool(applied["ok"]),
            "from": current,
            "to": release.version,
            "digest": digest,
            "digest_verified": bool(wanted),
            "output": applied["output"],
            "message": f"已更新到 v{release.version}" if applied["ok"] else "更新过程中安装器报错",
        }
    except (HTTPError, URLError, ValueError, OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "message": f"更新失败：{exc.__class__.__name__}: {exc}"}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识库星图工作台 · 检查并应用更新")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="只检查是否有新版本（不下载）")
    mode.add_argument("--apply", action="store_true", help="下载并应用更新")
    args = parser.parse_args(argv)

    if args.check:
        print(json.dumps(check_for_update(), ensure_ascii=False, indent=2))
        return 0
    result = perform_update()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
