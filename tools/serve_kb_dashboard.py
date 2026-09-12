#!/usr/bin/env python3
"""Serve the knowledge dashboard locally and open notes in the user's own editor."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import yaml
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import urlopen

from build_kb_dashboard import scan_vault, stable_node_id
from kb_handoff import (
    build_handoff_package,
    default_handoff_dir,
    ingest_receipts,
    list_handoffs,
    load_receipt,
)
from kb_update import check_for_update, perform_update
from kb_maintenance import (
    MaintenanceConflict,
    commit_preview,
    preview_create,
    preview_edit,
    read_note,
)
from kb_operations import (
    add_relation,
    analyze_requirement,
    attach_source,
    build_knowledge_package,
    create_task,
    decompose_task,
    export_task,
    ingest_file,
    latest_task,
    load_task,
    update_candidate,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 45 * 1024 * 1024
# 生成的星图文件名。http.server 用 latin-1 编码响应头，中文名放进 Location
# 会直接抛 UnicodeEncodeError 使根路径 500，因此凡进入 URL/响应头处都必须 quote。
DASHBOARD_HTML_NAME = "知识库可视化工作台.html"
MAINTENANCE_DIRS = {
    "来源资料": "知识库/00_原始资料库/维护新增",
    "拆解记录": "知识库/03_存量方案拆解区/维护新增",
    "知识原子": "知识库/01_原子知识组件库/99_待归类",
    "专题知识": "知识库/96_专题综述",
    "需求记录": "知识库/05_客户需求库",
    "方案成果": "知识库/解决方案",
    "验证反馈": "知识库/04_需求与规则引擎/B流程反哺",
    "治理规则": "知识库/04_需求与规则引擎/维护新增",
}


def parse_maintenance_route(path: str) -> str | None:
    match = re.fullmatch(r"/api/maintenance/(note|preview|commit)", path)
    return match.group(1) if match else None


SETTINGS_FILENAME = "设置.yaml"
NOTE_OPEN_MODES = ("auto", "obsidian", "system")
DEFAULT_SETTINGS: dict[str, object] = {
    "note_open_mode": "auto",      # auto | obsidian | system
    "agent_write": True,           # 关闭后 /api 只读
    "agent_token": "",
    "show_background": False,      # 默认是否展示后台记录
}


def settings_path() -> Path:
    """设置放在工具自己的安装目录里（与更新配置同级）。

    KB_SETTINGS_PATH 可覆盖：开发目录里直接跑服务时用它，
    免得把设置写进笔记库。正常安装（~/.kb-star-map）不需要设。
    """
    override = os.environ.get("KB_SETTINGS_PATH")
    if override:
        return Path(override).expanduser()
    try:
        from kb_update import install_root
        return install_root() / SETTINGS_FILENAME
    except Exception:                  # noqa: BLE001
        return Path(__file__).resolve().parent.parent / SETTINGS_FILENAME


def _write_settings(data: dict[str, object]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# 知识库星图工作台 · 设置（界面里改即可，一般不用手改）\n"
        f"note_open_mode: {data['note_open_mode']}\n"
        f"agent_write: {str(bool(data['agent_write'])).lower()}\n"
        f"show_background: {str(bool(data['show_background'])).lower()}\n"
        f'agent_token: "{data["agent_token"]}"\n',
        encoding="utf-8",
    )


def load_settings() -> dict[str, object]:
    data = dict(DEFAULT_SETTINGS)
    path = settings_path()
    if path.exists():
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
            if isinstance(parsed, dict):
                data.update({k: v for k, v in parsed.items() if k in DEFAULT_SETTINGS})
        except Exception:              # noqa: BLE001 - 配置坏了不该让服务起不来
            pass
    if not data.get("agent_token"):
        # 注意用 _write_settings 而不是 save_settings：
        # save_settings 会回调 load_settings，那样会无限递归。
        data["agent_token"] = secrets.token_hex(16)
        _write_settings(data)
    return data


def save_settings(updates: dict[str, object]) -> dict[str, object]:
    data = load_settings()
    for key, value in updates.items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key == "note_open_mode" and value not in NOTE_OPEN_MODES:
            continue
        if key in ("agent_write", "show_background"):
            value = bool(value)
        if key == "agent_token":
            value = str(value).strip()
            if not value:
                continue
        data[key] = value
    _write_settings(data)
    return data


def reload_token(dashboard_dir: Path) -> str:
    """页面自动刷新用的指纹：工具版本 + 页面文件 mtime。

    任一变化都意味着「页面该刷新了」：
      · 工具更新（kbs update / 界面点更新）→ 版本号变
      · 星图重建（kbs build / kbs / 维护写入）→ HTML mtime 变
    前端轮询这个值，变了就自动重载，不需要用户手动刷新。
    """
    page = dashboard_dir / DASHBOARD_HTML_NAME
    try:
        stamp = page.stat().st_mtime_ns
    except OSError:
        stamp = 0
    try:
        from kb_update import read_installed_version
        version = read_installed_version("未知")
    except Exception:                      # noqa: BLE001 - 版本读不到不该影响服务
        version = "未知"
    return f"{version}:{stamp}"


def _schedule_self_restart(delay: float = 1.5) -> None:
    """更新已把磁盘上的代码换掉，但本进程还跑着旧模块——原地重启以载入新版本。

    否则页面刷新了、行为还是旧的（v1.0.12 那个坏掉的「检查更新」就是这么藏了 7 个版本）。
    """
    import threading

    def _restart() -> None:
        time.sleep(delay)
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except OSError:
            os._exit(1)                    # 重启失败就退出，至少不留在旧代码上

    threading.Thread(target=_restart, daemon=True).start()


def maintenance_default_path(asset_type: str, title: str) -> str:
    folder = MAINTENANCE_DIRS.get(str(asset_type), "收件箱/知识维护新增")
    clean_title = re.sub(r"[\\/:*?\"<>|]+", "-", " ".join(str(title).split())).strip(" .-")
    if not clean_title:
        raise ValueError("新增笔记标题不能为空")
    return f"{folder}/{clean_title}.md"


def operations_store(dashboard_dir: Path) -> Path:
    return dashboard_dir / "运营任务草稿"


def parse_operation_route(path: str) -> tuple[str, str] | None:
    match = re.fullmatch(
        r"/api/operations/tasks/([0-9a-f]{32})/(sources|files|decompose|candidates|relations|requirements|package|export|handoff|receipts)",
        path,
    )
    return match.groups() if match else None


def parse_handoff_route(path: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"/api/handoffs/([0-9a-f]{16})/(receipt)", path)
    return match.groups() if match else None


def resolve_note_path(vault: Path, requested_path: str) -> Path:
    vault = vault.resolve()
    relative = Path(unquote(requested_path))
    if relative.is_absolute():
        candidate = relative.resolve()
    else:
        candidate = (vault / relative).resolve()
    if candidate != vault and vault not in candidate.parents:
        raise ValueError("笔记路径超出活动Vault")
    if candidate.suffix.lower() != ".md":
        raise ValueError("仅允许打开Markdown笔记")
    if not candidate.is_file():
        raise ValueError("目标笔记不存在")
    return candidate


def build_obsidian_uri(note_path: Path) -> str:
    return "obsidian://open?path=" + quote(str(note_path.resolve()), safe="")


def obsidian_available() -> bool:
    """是否用 Obsidian 深链打开笔记；装了 Obsidian 才用，否则退回系统默认程序。

    环境变量 KB_OPEN_WITH 可强制覆盖：obsidian 强制深链，default 强制系统默认程序
    （想让笔记开在自己的 Markdown 编辑器里就设 default）。
    """
    configured = os.environ.get("KB_OPEN_WITH", "auto").strip().lower()
    if configured == "default":
        return False
    if configured == "obsidian":
        return True
    if sys.platform == "darwin":
        return any(
            (base / "Obsidian.app").exists()
            for base in (Path("/Applications"), Path.home() / "Applications")
        )
    if sys.platform.startswith("win"):
        bases = [os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")]
        return any(
            base and (Path(base) / "Obsidian" / "Obsidian.exe").exists() for base in bases
        ) or shutil.which("Obsidian.exe") is not None
    return shutil.which("obsidian") is not None


def open_with_system_default(target: str) -> None:
    """用系统默认程序打开路径或 URL（Windows 由 os.startfile 处理已注册协议）。"""
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", target], check=True, timeout=10)
    elif sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", target], check=True, timeout=10)


def open_note(vault: Path, note_path: Path, mode: str = "auto") -> str:
    """打开笔记，返回实际使用的打开方式（obsidian / default）。

    mode 来自设置：auto 有 Obsidian 就用；obsidian 强制走 Obsidian；
    system 一律用系统默认程序。
    """
    wants_obsidian = mode == "obsidian" or (mode == "auto" and obsidian_available())
    if wants_obsidian and obsidian_available():
        open_with_system_default(build_obsidian_uri(note_path))
        return "obsidian"
    if mode == "obsidian":
        raise RuntimeError("设置了「总是用 Obsidian」，但这台机器上没检测到 Obsidian。")
    open_with_system_default(str(note_path.resolve()))
    return "default"


def build_source_payload(vault: Path, requested_path: str) -> dict[str, str]:
    note = resolve_note_path(vault, requested_path)
    relative = note.relative_to(vault.resolve()).as_posix()
    return {
        "path": relative,
        "title": note.stem,
        "node_id": stable_node_id(relative),
    }


def build_knowledge_index(vault: Path) -> list[dict]:
    return [
        {
            "id": stable_node_id(note.path),
            "title": note.title,
            "path": note.path,
            "asset_type": note.asset_type,
            "tags": note.metadata.get("tags") or [],
            "summary": note.summary,
            "evidence_level": note.metadata.get("evidence_level", ""),
        }
        for note in scan_vault(vault)
    ]


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def make_handler(vault: Path, dashboard_dir: Path, handoff_dir: Path | None = None, port: int = DEFAULT_PORT):
    store = operations_store(dashboard_dir)
    handoff_root = (handoff_dir or default_handoff_dir()).resolve()

    class DashboardHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dashboard_dir), **kwargs)

        def log_message(self, format_string, *args):
            return

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _send_json(
            self,
            status: int,
            payload: dict,
            download_name: str | None = None,
        ) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if download_name:
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{download_name}"',
                )
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_BODY_BYTES:
                raise ValueError("请求内容长度无效")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须是JSON对象")
            return payload

        def _serve_dashboard(self) -> None:
            """送出页面时把运行时配置（含访问令牌）注入进去。

            令牌只出现在同源页面里：跨站脚本无法读取响应体（CORS），
            所以拿不到令牌就写不了。这也是「智能体写入」开关能做判断的前提。
            """
            page = dashboard_dir / DASHBOARD_HTML_NAME
            try:
                html = page.read_text(encoding="utf-8")
            except OSError:
                self.send_error(404, "dashboard not found")
                return
            current = load_settings()
            runtime = json.dumps({
                "mode": "local",
                "service": f"http://127.0.0.1:{port}",
                "vault": str(vault),
                "token": current["agent_token"],
                "settings": {
                    "note_open_mode": current["note_open_mode"],
                    "agent_write": bool(current["agent_write"]),
                    "show_background": bool(current["show_background"]),
                },
            }, ensure_ascii=False)
            html = re.sub(
                r'<script id="kb-runtime">.*?</script>',
                '<script id="kb-runtime">window.__KB_RUNTIME__ = ' + runtime + ';</script>',
                html, count=1, flags=re.S,
            )
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                self._send_json(200, {"ok": True, "mode": "local-management"})
                return
            if parsed.path == "/api/version":
                self._send_json(200, {"ok": True, "token": reload_token(dashboard_dir)})
                return
            if parsed.path == "/api/settings":
                current = load_settings()
                self._send_json(200, {
                    "ok": True,
                    "settings": {
                        "note_open_mode": current["note_open_mode"],
                        "agent_write": bool(current["agent_write"]),
                        "show_background": bool(current["show_background"]),
                    },
                    "agent_token": current["agent_token"],
                    "service": f"http://127.0.0.1:{port}",
                    "vault": str(vault),
                })
                return
            if parsed.path == "/api/operations/current":
                self._send_json(200, {"ok": True, "task": latest_task(store)})
                return
            if parsed.path == "/api/handoffs":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "handoff_dir": str(handoff_root),
                        "items": list_handoffs(handoff_root),
                    },
                )
                return
            if parse_maintenance_route(parsed.path) == "note":
                try:
                    requested = parse_qs(parsed.query).get("path", [""])[0]
                    self._send_json(200, {"ok": True, "note": read_note(vault, requested)})
                except FileNotFoundError as exc:
                    self._send_json(404, {"ok": False, "message": str(exc)})
                except ValueError as exc:
                    self._send_json(400, {"ok": False, "message": str(exc)})
                return
            operation_route = parse_operation_route(parsed.path)
            if operation_route and operation_route[1] == "export":
                try:
                    task = export_task(store, operation_route[0])
                except FileNotFoundError as exc:
                    self._send_json(404, {"ok": False, "message": str(exc)})
                    return
                except (ValueError, json.JSONDecodeError) as exc:
                    self._send_json(400, {"ok": False, "message": str(exc)})
                    return
                self._send_json(
                    200,
                    task,
                    download_name=f"operation-{task['id'][:8]}.json",
                )
                return
            if unquote(parsed.path) == "/" + DASHBOARD_HTML_NAME:
                self._serve_dashboard()
                return
            if parsed.path == "/":
                self.send_response(302)
                self.send_header("Location", "/" + quote(DASHBOARD_HTML_NAME))
                self.end_headers()
                return
            super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            # 写操作一律要令牌：跨站脚本发得出请求，但读不到响应体，
            # 也就拿不到注入在页面里的令牌。
            current = load_settings()
            if self.headers.get("X-KB-Token", "") != current["agent_token"]:
                self._send_json(403, {"ok": False, "message": "缺少或错误的访问令牌，已拒绝写入"})
                return
            # 关掉「允许智能体写入」后，只放行浏览器界面自己的同源请求
            origin = self.headers.get("Origin", "")
            same_origin = origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
            if not same_origin and not current["agent_write"]:
                self._send_json(403, {"ok": False, "message": "已关闭智能体写入，只能在界面里操作"})
                return
            try:
                payload = self._read_json()
                maintenance_route = parse_maintenance_route(path)
                if maintenance_route == "preview":
                    mode = str(payload.get("mode", ""))
                    if mode == "edit":
                        preview = preview_edit(
                            vault,
                            str(payload.get("path", "")),
                            str(payload.get("content", "")),
                            str(payload.get("baseline_sha256", "")),
                        )
                    elif mode == "create":
                        requested_path = str(payload.get("path", "")) or maintenance_default_path(
                            str(payload.get("asset_type", "")),
                            str(payload.get("title", "")),
                        )
                        preview = preview_create(
                            vault,
                            requested_path,
                            str(payload.get("content", "")),
                        )
                    else:
                        raise ValueError("维护模式无效")
                    self._send_json(200, {"ok": True, "preview": preview})
                    return
                if maintenance_route == "commit":
                    preview_payload = payload.get("preview")
                    if not isinstance(preview_payload, dict):
                        raise ValueError("缺少有效差异预览")
                    result = commit_preview(
                        vault,
                        preview_payload,
                        str(payload.get("token", "")),
                        bool(payload.get("confirm")),
                    )
                    builder = Path(__file__).resolve().parent / "build_kb_dashboard.py"
                    subprocess.run(
                        [
                            sys.executable,
                            str(builder),
                            "--vault",
                            str(vault),
                            "--output",
                            str(dashboard_dir / DASHBOARD_HTML_NAME),
                        ],
                        check=True,
                        timeout=60,
                    )
                    self._send_json(200, {"ok": True, "result": result})
                    return
                if path == "/api/open-note":
                    note_path = resolve_note_path(vault, str(payload.get("path", "")))
                    opened_with = open_note(vault, note_path, str(load_settings()["note_open_mode"]))
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "path": note_path.relative_to(vault).as_posix(),
                            "openedWith": opened_with,
                        },
                    )
                    return
                if path == "/api/settings":
                    updated = save_settings(payload if isinstance(payload, dict) else {})
                    self._send_json(200, {
                        "ok": True,
                        "settings": {
                            "note_open_mode": updated["note_open_mode"],
                            "agent_write": bool(updated["agent_write"]),
                            "show_background": bool(updated["show_background"]),
                        },
                    })
                    return
                if path == "/api/update":
                    action = str(payload.get("action") or "check")
                    if action == "check":
                        self._send_json(200, {"ok": True, **check_for_update()})
                    elif action == "apply":
                        result = perform_update()
                        # 更新后数据已变，让前端提示刷新；HTML 由新版安装器重建。
                        self._send_json(200 if result.get("ok") else 502, result)
                        if result.get("ok") and not result.get("skipped"):
                            # 代码已换新，让进程也换成新代码——只刷页面不重启等于没更新
                            _schedule_self_restart()
                    else:
                        self._send_json(400, {"ok": False, "message": f"未知的更新动作: {action}"})
                    return
                if path == "/api/operations/tasks":
                    task = create_task(store, str(payload.get("title", "")))
                    self._send_json(201, {"ok": True, "task": task})
                    return
                handoff_route = parse_handoff_route(path)
                if handoff_route:
                    receipt = load_receipt(handoff_root, handoff_route[0])
                    if receipt is None:
                        self._send_json(404, {"ok": False, "message": "回执尚未返回"})
                        return
                    self._send_json(200, {"ok": True, "receipt": receipt})
                    return
                operation_route = parse_operation_route(path)
                if operation_route and operation_route[1] == "handoff":
                    context = payload.get("context")
                    if not isinstance(context, dict):
                        raise ValueError("缺少交接上下文")
                    package = build_handoff_package(
                        store,
                        operation_route[0],
                        context,
                        handoff_root,
                    )
                    self._send_json(201, {"ok": True, "handoff": package})
                    return
                if operation_route and operation_route[1] == "receipts":
                    updated = ingest_receipts(store, operation_route[0], handoff_root)
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "updated": updated,
                            "handoff_dir": str(handoff_root),
                        },
                    )
                    return
                if operation_route and operation_route[1] == "sources":
                    source = build_source_payload(vault, str(payload.get("path", "")))
                    task_before = load_task(store, operation_route[0])
                    existing = {item["path"] for item in task_before["sources"]}
                    task = attach_source(store, operation_route[0], source)
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "added": source["path"] not in existing,
                            "task": task,
                        },
                    )
                    return
                if operation_route and operation_route[1] == "files":
                    try:
                        content = base64.b64decode(
                            str(payload.get("content_base64", "")),
                            validate=True,
                        )
                    except (ValueError, binascii.Error) as exc:
                        raise ValueError("资料内容不是有效Base64") from exc
                    task, added = ingest_file(
                        store,
                        operation_route[0],
                        str(payload.get("filename", "")),
                        content,
                    )
                    self._send_json(
                        200,
                        {"ok": True, "added": added, "task": task},
                    )
                    return
                if operation_route and operation_route[1] == "decompose":
                    index = build_knowledge_index(vault)
                    task = decompose_task(store, operation_route[0], index)
                    self._send_json(200, {"ok": True, "task": task})
                    return
                if operation_route and operation_route[1] == "candidates":
                    task = update_candidate(
                        store,
                        operation_route[0],
                        str(payload.get("candidate_id", "")),
                        payload.get("patch") if isinstance(payload.get("patch"), dict) else {},
                    )
                    self._send_json(200, {"ok": True, "task": task})
                    return
                if operation_route and operation_route[1] == "relations":
                    task = add_relation(
                        store,
                        operation_route[0],
                        str(payload.get("source_id", "")),
                        str(payload.get("target_id", "")),
                        str(payload.get("relation_type", "")),
                        str(payload.get("rationale", "")),
                    )
                    self._send_json(200, {"ok": True, "task": task})
                    return
                if operation_route and operation_route[1] == "requirements":
                    task = analyze_requirement(
                        store,
                        operation_route[0],
                        str(payload.get("text", "")),
                        build_knowledge_index(vault),
                    )
                    self._send_json(200, {"ok": True, "task": task})
                    return
                if operation_route and operation_route[1] == "package":
                    task = build_knowledge_package(store, operation_route[0])
                    self._send_json(200, {"ok": True, "task": task})
                    return
                self._send_json(404, {"ok": False, "message": "接口不存在"})
                return
            except FileNotFoundError as exc:
                self._send_json(404, {"ok": False, "message": str(exc)})
                return
            except MaintenanceConflict as exc:
                self._send_json(409, {"ok": False, "message": str(exc)})
                return
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "message": str(exc)})
                return
            except (OSError, subprocess.SubprocessError) as exc:
                self._send_json(500, {"ok": False, "message": f"打开笔记失败: {exc}"})
                return

    return DashboardHandler


def _pid_path(port: int) -> Path:
    return Path(tempfile.gettempdir()) / f"kb-dashboard-{port}.pid"


def _server_ready(host: str, port: int) -> bool:
    try:
        with urlopen(f"http://{host}:{port}/api/status", timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and payload.get("mode") == "local-management"
    except (OSError, URLError, json.JSONDecodeError):
        return False


def run_server(
    vault: Path,
    dashboard_dir: Path,
    host: str,
    port: int,
    handoff_dir: Path | None = None,
) -> None:
    pid_file = _pid_path(port)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    handler = make_handler(
        vault.resolve(),
        dashboard_dir.resolve(),
        handoff_dir.resolve() if handoff_dir else None,
        port,
    )
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        pid_file.unlink(missing_ok=True)


def stop_server(port: int) -> bool:
    pid_file = _pid_path(port)
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError):
        pid_file.unlink(missing_ok=True)
        return False
    return True


def launch_dashboard(
    vault: Path,
    dashboard_dir: Path,
    host: str,
    port: int,
    handoff_dir: Path | None = None,
) -> None:
    builder = Path(__file__).resolve().parent / "build_kb_dashboard.py"
    output = dashboard_dir / DASHBOARD_HTML_NAME
    subprocess.run(
        [sys.executable, str(builder), "--vault", str(vault), "--output", str(output)],
        check=True,
    )
    if not _server_ready(host, port):
        log_path = Path(tempfile.gettempdir()) / f"kb-dashboard-{port}.log"
        log_handle = log_path.open("a", encoding="utf-8")
        serve_args = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
            "--vault",
            str(vault),
            "--dashboard-dir",
            str(dashboard_dir),
            "--host",
            host,
            "--port",
            str(port),
        ]
        if handoff_dir:
            serve_args += ["--handoff-dir", str(handoff_dir)]
        subprocess.Popen(
            serve_args,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )
        log_handle.close()
        for _ in range(30):
            if _server_ready(host, port):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(f"本地管理服务未能启动，日志: {log_path}")
    url = f"http://{host}:{port}/" + quote(DASHBOARD_HTML_NAME)
    try:
        open_with_system_default(url)
    except (OSError, subprocess.SubprocessError) as exc:
        # 浏览器打不开不该让本地模式失败：服务已经起来了，把地址给用户手动打开。
        print(f"未能自动打开浏览器（{exc}）")
        print(f"请手动访问：{url}")


def default_paths() -> tuple[Path, Path]:
    vault = Path(__file__).resolve().parents[3]
    return vault, vault / "系统" / "知识库可视化工作台"


def main(argv: list[str] | None = None) -> int:
    default_vault, default_dashboard_dir = default_paths()
    parser = argparse.ArgumentParser(description="知识库工作台本地管理服务")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--launch", action="store_true", help="刷新数据、启动服务并用系统默认浏览器打开")
    mode.add_argument("--serve", action="store_true", help="以前台模式运行本地服务")
    mode.add_argument("--stop", action="store_true", help="停止已启动的本地服务")
    parser.add_argument("--vault", type=Path, default=default_vault)
    parser.add_argument("--dashboard-dir", type=Path, default=default_dashboard_dir)
    parser.add_argument(
        "--handoff-dir",
        type=Path,
        default=default_handoff_dir(),
        help="与售前方案工作台共用的交接箱目录",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    if args.stop:
        stopped = stop_server(args.port)
        print("知识库工作台本地服务已停止" if stopped else "知识库工作台本地服务未运行")
        return 0
    if args.launch:
        launch_dashboard(
            args.vault.resolve(),
            args.dashboard_dir.resolve(),
            args.host,
            args.port,
            args.handoff_dir,
        )
        print("知识库工作台已用系统默认浏览器打开")
        return 0
    run_server(
        args.vault.resolve(),
        args.dashboard_dir.resolve(),
        args.host,
        args.port,
        args.handoff_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
