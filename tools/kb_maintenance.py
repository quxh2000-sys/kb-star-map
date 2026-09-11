#!/usr/bin/env python3
"""Safe preview-and-confirm writes for the active Obsidian vault."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote


MAX_CONTENT_BYTES = 2 * 1024 * 1024


class MaintenanceConflict(RuntimeError):
    """Raised when the preview baseline no longer matches the target."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def resolve_markdown_path(vault: Path, requested_path: str, must_exist: bool) -> Path:
    vault = vault.resolve()
    relative = Path(unquote(str(requested_path)))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("维护路径必须是活动Vault内相对路径")
    candidate = (vault / relative).resolve()
    if candidate == vault or vault not in candidate.parents:
        raise ValueError("维护路径超出活动Vault")
    if candidate.suffix.lower() != ".md":
        raise ValueError("只允许维护Markdown笔记")
    if must_exist and not candidate.is_file():
        raise FileNotFoundError("目标笔记不存在")
    return candidate


def read_note(vault: Path, path: str) -> dict[str, Any]:
    note = resolve_markdown_path(vault, path, must_exist=True)
    content = note.read_bytes()
    return {
        "path": note.relative_to(vault.resolve()).as_posix(),
        "content": content.decode("utf-8", errors="replace"),
        "sha256": _sha256(content),
        "bytes": len(content),
    }


def _validate_content(content: str) -> str:
    text = str(content)
    encoded = text.encode("utf-8")
    if not text.strip() or len(encoded) > MAX_CONTENT_BYTES:
        raise ValueError("笔记内容必须非空且不超过2MB")
    return text


def preview_token(preview: dict[str, Any]) -> str:
    payload = {
        "mode": preview.get("mode"),
        "path": preview.get("path"),
        "baseline_sha256": preview.get("baseline_sha256"),
        "content": preview.get("content"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256(canonical.encode("utf-8"))


def _diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def preview_edit(
    vault: Path,
    path: str,
    content: str,
    baseline_sha256: str,
) -> dict[str, Any]:
    current = read_note(vault, path)
    if current["sha256"] != str(baseline_sha256):
        raise MaintenanceConflict("目标笔记已变化，请重新加载后生成差异")
    after = _validate_content(content)
    preview = {
        "mode": "edit",
        "path": current["path"],
        "baseline_sha256": current["sha256"],
        "content": after,
        "diff": _diff(current["path"], current["content"], after),
    }
    preview["token"] = preview_token(preview)
    return preview


def preview_create(vault: Path, path: str, content: str) -> dict[str, Any]:
    target = resolve_markdown_path(vault, path, must_exist=False)
    if target.exists():
        raise MaintenanceConflict("目标笔记已存在，不允许静默覆盖")
    after = _validate_content(content)
    relative = target.relative_to(vault.resolve()).as_posix()
    preview = {
        "mode": "create",
        "path": relative,
        "baseline_sha256": None,
        "content": after,
        "diff": _diff(relative, "", after),
    }
    preview["token"] = preview_token(preview)
    return preview


def commit_preview(
    vault: Path,
    preview: dict[str, Any],
    token: str,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise ValueError("必须显式确认后才能写入")
    if str(token) != preview_token(preview):
        raise ValueError("差异预览令牌无效")
    mode = preview.get("mode")
    path = str(preview.get("path", ""))
    content = _validate_content(str(preview.get("content", "")))
    target = resolve_markdown_path(vault, path, must_exist=mode == "edit")
    if mode == "edit":
        current = read_note(vault, path)
        if current["sha256"] != preview.get("baseline_sha256"):
            raise MaintenanceConflict("目标笔记已变化，已阻止覆盖")
    elif mode == "create":
        if target.exists():
            raise MaintenanceConflict("目标笔记已存在，已阻止覆盖")
    else:
        raise ValueError("维护模式无效")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.maintenance.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    result = read_note(vault, path)
    result["mode"] = mode
    result["verified"] = result["content"] == content
    return result
