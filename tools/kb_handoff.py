#!/usr/bin/env python3
"""Hand knowledge packages off to the presales workbench, and read receipts back.

The contract lives in 00-工具交接箱/契约/. This module only produces and reads
files there; it never writes into the presales project directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from kb_operations import load_task, save_task_atomic


HANDOFF_SCHEMA_VERSION = 1
HANDOFF_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
DELIVERABLE_TYPES = {"word", "ppt", "checklist", "sales-talk", "other"}
REQUIRED_CONTEXT_FIELDS = ("customer", "project_name", "sales_stage", "deliverable_type")
HANDOFF_STATUSES = {"pending", "sent", "received", "imported", "failed", "completed"}


def default_handoff_dir() -> Path:
    """Resolve the handoff box without hard-coding any user home path."""
    return Path.home() / "Documents" / "60-工具与开发" / "00-工具交接箱"


def _now(now: datetime | None = None) -> str:
    return (now or datetime.now().astimezone()).isoformat(timespec="seconds")


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _digest(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def handoff_id_for(task_id: str, context: dict[str, Any]) -> str:
    """Stable across content edits, distinct per project and deliverable type."""
    seed = "\n".join(
        [
            str(task_id),
            str(context.get("customer", "")).strip(),
            str(context.get("project_name", "")).strip(),
            str(context.get("deliverable_type", "")).strip(),
        ]
    )
    return _digest(seed)


def _validated_context(context: dict[str, Any]) -> dict[str, str]:
    if not isinstance(context, dict):
        raise ValueError("交接上下文必须是对象")
    clean: dict[str, str] = {}
    for field in REQUIRED_CONTEXT_FIELDS:
        value = " ".join(str(context.get(field, "")).split())
        if not value:
            raise ValueError(f"交接上下文缺少{field}")
        clean[field] = value
    if clean["deliverable_type"] not in DELIVERABLE_TYPES:
        raise ValueError("交付物类型无效")
    for optional in ("owner", "notes"):
        value = " ".join(str(context.get(optional, "")).split())
        if value:
            clean[optional] = value
    return clean


def _relative_path(value: Any) -> str:
    if not value:
        return ""
    path = Path(str(value))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("交接包中出现非法路径")
    return path.as_posix()


def _selected_atoms(package: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the package candidates as-is. build_knowledge_package already dropped
    ignored ones and those without a live source, so this must not re-derive selection."""
    atoms: list[dict[str, Any]] = []
    for item in package.get("candidates", []):
        atoms.append(
            {
                "id": str(item.get("id", "")),
                "title": str(item.get("title", "")),
                "dimension": str(item.get("dimension", "")),
                "sub_dimension": str(item.get("sub_dimension", "")),
                "evidence_level": str(item.get("evidence_level", "")),
                "use_for": str(item.get("use_for", "")),
                "boundary": str(item.get("boundary", "")),
            }
        )
    return atoms


def _graph_snapshot(sources: list[dict[str, Any]], retrievals: list[dict[str, Any]], relations: list[dict[str, Any]]) -> dict[str, Any]:
    """Only emit nodes and edges that already exist in the draft. Nothing is invented."""
    nodes: dict[str, dict[str, Any]] = {}
    for source in sources:
        node_id = str(source.get("node_id") or f"source:{source.get('source_id')}")
        nodes[node_id] = {
            "node_id": node_id,
            "title": str(source.get("title", "")),
            "role": "source",
        }
    for item in retrievals:
        node_id = str(item.get("node_id", ""))
        if not node_id or node_id in nodes:
            continue
        nodes[node_id] = {
            "node_id": node_id,
            "title": str(item.get("title", "")),
            "role": "knowledge",
        }
    edges = [
        {
            "source_id": str(relation.get("source_id", "")),
            "target_id": str(relation.get("target_id", "")),
            "relation_type": str(relation.get("relation_type", "")),
            "rationale": str(relation.get("rationale", "")),
        }
        for relation in relations
        if str(relation.get("source_id", "")) in nodes and str(relation.get("target_id", "")) in nodes
    ]
    return {"nodes": list(nodes.values()), "edges": edges}


def _content_sha256(payload: dict[str, Any]) -> str:
    """Hash only the knowledge payload, so status and timestamps do not churn it."""
    material = {
        "sources": payload.get("sources", []),
        "requirements": payload.get("requirements", []),
        "knowledge": payload.get("knowledge", {}),
        "coverage": payload.get("coverage", {}),
        "outline": payload.get("outline", {}),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_handoff_package(
    store: Path,
    task_id: str,
    context: dict[str, Any],
    handoff_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compose the contract package from the operational draft and write it to outbox."""
    task = load_task(store, task_id)
    clean_context = _validated_context(context)
    root = Path(handoff_dir) if handoff_dir else default_handoff_dir()
    outbox = root / "outbox"

    package = task.get("knowledge_package")
    if not isinstance(package, dict):
        raise ValueError("请先生成知识包，再执行交接")

    warnings = [str(item) for item in package.get("warnings", [])]
    if not package.get("requirements"):
        warnings.append("缺少需求理解")
    if not package.get("retrievals"):
        warnings.append("知识召回为空")

    sources = [
        {
            "kind": str(source.get("kind", "")),
            "source_id": str(source.get("source_id") or source.get("node_id") or ""),
            "node_id": str(source.get("node_id", "")),
            "title": str(source.get("title", "")),
            "original_name": str(source.get("original_name", "")),
            "stored_path": _relative_path(source.get("stored_path")),
            "extracted_path": _relative_path(source.get("extracted_path")),
            "sha256": str(source.get("sha256", "")),
            "parse_status": str(source.get("parse_status", "")),
            "char_count": int(source.get("char_count") or 0),
            "unit_count": int(source.get("unit_count") or 0),
            "scope": str(source.get("scope", "")),
            "excerpt": str(source.get("excerpt", "")),
        }
        for source in package.get("sources", [])
    ]
    sources = [source for source in sources if source["source_id"]]

    relations = [
        {
            "id": str(item.get("id", "")),
            "source_id": str(item.get("source_id", "")),
            "target_id": str(item.get("target_id", "")),
            "relation_type": str(item.get("relation_type", "")),
            "rationale": str(item.get("rationale", "")),
            "status": str(item.get("status", "candidate")),
        }
        for item in package.get("relations", [])
    ]
    retrievals = [
        {
            "node_id": str(item.get("node_id", "")),
            "title": str(item.get("title", "")),
            "path": _relative_path(item.get("path")),
            "asset_type": str(item.get("asset_type", "")),
            "evidence_level": str(item.get("evidence_level", "")),
            "score": float(item.get("score") or 0),
            "coverage": str(item.get("coverage", "partial")),
        }
        for item in package.get("retrievals", [])
    ]

    timestamp = _now(now)
    handoff_id = handoff_id_for(task["id"], clean_context)
    existing_path = outbox / f"{handoff_id}.json"
    created_at = timestamp
    if existing_path.is_file():
        try:
            created_at = str(json.loads(existing_path.read_text(encoding="utf-8")).get("created_at") or timestamp)
        except (OSError, json.JSONDecodeError):
            created_at = timestamp

    payload: dict[str, Any] = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "handoff_id": handoff_id,
        "knowledge_task_id": task["id"],
        "title": str(task.get("title", "")),
        "status": "pending",
        "created_at": created_at,
        "updated_at": timestamp,
        "context": clean_context,
        "sources": sources,
        "requirements": [
            {
                "id": str(item.get("id", "")),
                "kind": str(item.get("kind", "unknown")),
                "text": str(item.get("text", "")),
                "source": str(item.get("source", "")),
            }
            for item in package.get("requirements", [])
        ],
        "knowledge": {
            "atoms": _selected_atoms(package),
            "rules": [],
            "relations": relations,
            "canonical": [],
            "cases": [],
            "evidences": [],
        },
        "coverage": {
            "retrievals": retrievals,
            "gaps": [str(item) for item in package.get("gaps", [])],
            "conflicts": [],
            "boundaries": [],
            "risks": [],
        },
        "outline": {
            "mainlines": [],
            "sections": [],
            "graph_snapshot": _graph_snapshot(sources, retrievals, relations),
        },
        "warnings": warnings,
    }
    payload["content_sha256"] = _content_sha256(payload)
    _write_atomic(existing_path, payload)

    task["handoffs"] = [
        item
        for item in task.get("handoffs", [])
        if item.get("handoff_id") != handoff_id
    ] + [
        {
            "handoff_id": handoff_id,
            "status": "pending",
            "handoff_dir": root.as_posix(),
            "content_sha256": payload["content_sha256"],
            "updated_at": timestamp,
        }
    ]
    task["stage"] = "输出准备与售前交接"
    task["updated_at"] = timestamp
    save_task_atomic(store, task)
    return payload


def load_handoff(handoff_dir: Path, handoff_id: str) -> dict[str, Any]:
    if not HANDOFF_ID_PATTERN.fullmatch(str(handoff_id)):
        raise ValueError("交接包ID无效")
    path = Path(handoff_dir) / "outbox" / f"{handoff_id}.json"
    if not path.is_file():
        raise FileNotFoundError("交接包不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def list_handoffs(handoff_dir: Path) -> list[dict[str, Any]]:
    outbox = Path(handoff_dir) / "outbox"
    if not outbox.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(outbox.glob("*.json")):
        if not HANDOFF_ID_PATTERN.fullmatch(path.stem):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items.append(
            {
                "handoff_id": str(payload.get("handoff_id", path.stem)),
                "title": str(payload.get("title", "")),
                "customer": str(payload.get("context", {}).get("customer", "")),
                "project_name": str(payload.get("context", {}).get("project_name", "")),
                "deliverable_type": str(payload.get("context", {}).get("deliverable_type", "")),
                "status": str(payload.get("status", "pending")),
                "updated_at": str(payload.get("updated_at", "")),
            }
        )
    return items


def load_receipt(handoff_dir: Path, handoff_id: str) -> dict[str, Any] | None:
    if not HANDOFF_ID_PATTERN.fullmatch(str(handoff_id)):
        raise ValueError("交接包ID无效")
    path = Path(handoff_dir) / "receipts" / f"{handoff_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_receipts(
    store: Path,
    task_id: str,
    handoff_dir: Path,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Pull receipts back into the operational draft so the loop closes on the knowledge side."""
    root = Path(handoff_dir)
    task = load_task(store, task_id)
    timestamp = _now(now)
    updated: list[dict[str, Any]] = []
    for entry in task.get("handoffs", []):
        handoff_id = str(entry.get("handoff_id", ""))
        receipt = load_receipt(root, handoff_id) if HANDOFF_ID_PATTERN.fullmatch(handoff_id) else None
        if not receipt:
            continue
        status = str(receipt.get("status", "received"))
        entry.update(
            {
                "status": status,
                "receipt_status": status,
                "presales_project_id": str(receipt.get("presales", {}).get("project_id", "")),
                "customer_feedback": str(receipt.get("customer_feedback", "")),
                "updated_at": str(receipt.get("updated_at") or timestamp),
            }
        )
        updated.append(entry)
    if updated:
        task["stage"] = "交付反馈与知识反哺"
        task["updated_at"] = timestamp
        save_task_atomic(store, task)
    return updated
