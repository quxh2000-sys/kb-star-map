"""按清单从远端逐文件安装/更新本工具。

不用 zip：zip 只是一个"把多文件装进一个容器"的办法，而它带来的代价是
每次发布要额外产出一个二进制、要防 zip-slip、要处理"删掉的文件还留在旧包里"。
逐文件下载配 sha256 校验可以达到同样的原子性与完整性，且发布物就是仓库本身。

清单（dist/files.json）长这样：
    {"version": "1.1.0", "files": {"kbs.py": "<sha256>", "tools/xxx.py": "<sha256>"}}

命令行：
    python3 bootstrap.py --manifest <files.json 的 URL> --target ~/.kb-star-map
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "kbs-bootstrap"
RETRY_ATTEMPTS = 3
RETRY_DELAY = 1.2
TIMEOUT = 30
# 安装目录里这些不属于"程序文件"，更新时不能动
KEEP_NAMES = {"升级备份", "更新配置.yaml", "installed.json"}
MANIFEST_NAME = "installed.json"


def _fetch(url: str, timeout: int = TIMEOUT) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _fetch_with_retry(url: str, attempts: int = RETRY_ATTEMPTS) -> bytes:
    """实测 Windows 上偶发瞬时 TLS 失败，重试一次就好——不该让用户看到莫名报错。"""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _fetch(url)
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                raise                      # 4xx 重试没意义
            last = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
        if attempt < attempts:
            time.sleep(RETRY_DELAY * attempt)
    raise last if last else RuntimeError("下载失败")


def load_manifest(manifest_url: str) -> dict:
    data = json.loads(_fetch_with_retry(manifest_url).decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError(f"清单格式不对：{manifest_url}")
    if not data.get("version"):
        raise ValueError(f"清单里没有 version：{manifest_url}")
    return data


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _raw_url(raw_base: str, relative: str) -> str:
    return f"{raw_base.rstrip('/')}/{relative}"


def download_file(raw_base: str, relative: str, expected: str) -> bytes:
    """下载单个文件并校验。

    加 cache-buster 重取一次：Gitee raw 有 CDN 缓存，刚发布的版本头几十秒
    可能还是旧内容——不重取会一直校验失败。
    """
    data = _fetch_with_retry(_raw_url(raw_base, relative))
    if expected and sha256_of(data) != expected:
        stamped = f"{_raw_url(raw_base, relative)}?t={int(time.time())}"
        data = _fetch_with_retry(stamped)
        if sha256_of(data) != expected:
            raise ValueError(f"{relative} 校验不通过（可能 CDN 还没刷新，稍后重试）")
    return data



def _previous_files(target: Path) -> set[str]:
    """上一次安装装了哪些文件。没有记录时返回空集——宁可少删也不误删。"""
    try:
        data = json.loads((target / MANIFEST_NAME).read_text(encoding="utf-8-sig"))
        recorded = data.get("files")
        if isinstance(recorded, list):
            return {str(item) for item in recorded}
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return set()


def install(manifest_url: str, target: Path, raw_base: str | None = None,
            log=print) -> dict:
    """按清单把文件铺到 target。返回 {ok, version, files, removed, message}。

    先全部下到临时目录并校验，再整体替换——中途失败不会留下半新半旧的安装。
    """
    staging = Path(tempfile.mkdtemp(prefix="kbs-stage-"))
    version = "?"
    try:
        manifest = load_manifest(manifest_url)
        version = str(manifest["version"])
        files: dict[str, str] = manifest["files"]
        base = raw_base or str(manifest.get("raw_base") or "") or manifest_url.rsplit("/dist/", 1)[0]
        log(f"从 {base} 拉取 v{version}（{len(files)} 个文件）")
        for index, (relative, digest) in enumerate(sorted(files.items()), 1):
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"清单里有越界路径：{relative}")
            data = download_file(base, relative, digest)
            destination = staging / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            log(f"  [{index}/{len(files)}] {relative}")

        # 整体替换。清理只针对「上一次安装清单里记过、这一版已经没有」的文件——
        # 绝不能按白名单反选：kbs 启动器、设置.yaml、更新配置.yaml 都不在清单里，
        # 用白名单漏一个就会把用户的配置和命令删掉（实测踩过）。
        target.mkdir(parents=True, exist_ok=True)
        previous = _previous_files(target)
        removed: list[str] = []
        for relative in sorted(previous - set(files)):
            stale = target / relative
            if stale.is_file():
                stale.unlink()
                removed.append(relative)
        for stale in sorted(target.rglob("*"), reverse=True):
            if stale.is_dir() and not any(stale.iterdir()):
                if stale.relative_to(target).parts[0] in KEEP_NAMES:
                    continue
                stale.rmdir()
        shutil.copytree(staging, target, dirs_exist_ok=True)

        (target / MANIFEST_NAME).write_text(
            json.dumps({"version": version,
                        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "files": sorted(files)},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        log(f"已安装 v{version} 到 {target}" + (f"，清理 {len(removed)} 个旧文件" if removed else ""))
        return {"ok": True, "version": version, "files": len(files), "removed": removed, "target": str(target)}
    except Exception as error:                       # noqa: BLE001 - 统一回报给调用方
        return {"ok": False, "version": version, "message": f"{error.__class__.__name__}: {error}"}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap", description="按清单从远端安装本工具（不用 zip）")
    parser.add_argument("--manifest", required=True, help="dist/files.json 的 URL")
    parser.add_argument("--target", required=True, type=Path, help="安装目录")
    parser.add_argument("--raw-base", default=None, help="raw 根地址；默认从清单 URL 推导")
    args = parser.parse_args(argv)

    result = install(args.manifest, args.target.expanduser(), args.raw_base)
    if not result["ok"]:
        print(f"[错误] {result['message']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
