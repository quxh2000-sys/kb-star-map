#!/usr/bin/env python3
"""Versioned draft storage for knowledge operations tasks."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import uuid
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
STAGES = (
    "资料读取",
    "结构解析",
    "分解与查重",
    "知识原子与原子规则",
    "业务规则与关系链接",
    "需求理解与知识召回",
    "输出准备与售前交接",
    "交付反馈与知识反哺",
)
TASK_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
ALLOWED_SOURCE_SUFFIXES = {".pdf", ".pptx", ".docx", ".xlsx", ".md", ".txt"}
MAX_SOURCE_BYTES = 30 * 1024 * 1024
def _default_converter() -> Path:
    """markitdown 位置：KB_MARKITDOWN > 本机惯用位置 > PATH。

    先认 ~/.local/bin 是为了不改动既有机器上的行为（那里可能有专门装的版本），
    同事机器上没有这个目录时再回落到 PATH 查找。
    """
    override = os.environ.get("KB_MARKITDOWN")
    if override:
        return Path(override).expanduser()
    preferred = Path.home() / ".local" / "bin" / "markitdown"
    if preferred.exists():
        return preferred
    found = shutil.which("markitdown")
    return Path(found) if found else preferred


DEFAULT_CONVERTER = _default_converter()
DISPOSITIONS = {"new", "merge", "update", "conflict", "ignore"}
CANDIDATE_FIELDS = {"disposition", "asset_type", "knowledge_type", "dimension", "sub_dimension", "evidence_level", "use_for", "boundary"}
RELATION_TYPES = {
    "来源支持",
    "组成包含",
    "调用适用",
    "前置后续",
    "验证证明",
    "冲突替代",
    "输出引用",
}


def save_task_atomic(store: Path, task: dict[str, Any]) -> None:
    store.mkdir(parents=True, exist_ok=True)
    output = store / f"{task['id']}.json"
    temp = store / f".{task['id']}.tmp"
    try:
        with temp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(task, ensure_ascii=False, indent=2) + "\n")
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)


def create_task(
    store: Path,
    title: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean_title = " ".join(str(title).split())
    if not clean_title or len(clean_title) > 80:
        raise ValueError("任务名称长度应为1至80个字符")
    created_at = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    task: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": uuid.uuid4().hex,
        "title": clean_title,
        "status": "draft",
        "stage": STAGES[0],
        "created_at": created_at,
        "updated_at": created_at,
        "sources": [],
        "candidates": [],
        "relations": [],
        "requirements": [],
        "retrievals": [],
        "gaps": [],
        "knowledge_package": None,
    }
    save_task_atomic(store, task)
    return task


def _task_path(store: Path, task_id: str) -> Path:
    if not TASK_ID_PATTERN.fullmatch(str(task_id)):
        raise ValueError("任务ID无效")
    return store / f"{task_id}.json"


def load_task(store: Path, task_id: str) -> dict[str, Any]:
    path = _task_path(store, task_id)
    if not path.is_file():
        raise FileNotFoundError("运营任务不存在")
    task = json.loads(path.read_text(encoding="utf-8"))
    if task.get("schema_version") != SCHEMA_VERSION or task.get("id") != task_id:
        raise ValueError("运营任务草稿格式无效")
    return task


def latest_task(store: Path) -> dict[str, Any] | None:
    if not store.is_dir():
        return None
    tasks: list[dict[str, Any]] = []
    for path in store.glob("*.json"):
        if not TASK_ID_PATTERN.fullmatch(path.stem):
            continue
        tasks.append(load_task(store, path.stem))
    if not tasks:
        return None
    return max(tasks, key=lambda task: (str(task.get("updated_at", "")), task["id"]))


def _validated_source(source: dict[str, Any]) -> dict[str, str]:
    path = Path(str(source.get("path", "")))
    title = " ".join(str(source.get("title", "")).split())
    node_id = str(source.get("node_id", "")).strip()
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
        raise ValueError("来源路径必须是Vault内Markdown相对路径")
    if not title or not node_id:
        raise ValueError("来源标题和节点ID不能为空")
    return {"kind": "vault", "path": path.as_posix(), "title": title, "node_id": node_id}


def attach_source(
    store: Path,
    task_id: str,
    source: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    task = load_task(store, task_id)
    clean_source = _validated_source(source)
    if any(item.get("path") == clean_source["path"] for item in task["sources"]):
        return task
    task["sources"].append(clean_source)
    task["updated_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    save_task_atomic(store, task)
    return task


def export_task(store: Path, task_id: str) -> dict[str, Any]:
    task = load_task(store, task_id)
    for source in task.get("sources", []):
        if source.get("kind") == "uploaded":
            for key in ("stored_path", "extracted_path"):
                path = Path(str(source.get(key, "")))
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("导出来源包含非法路径")
        else:
            _validated_source(source)
    return json.loads(json.dumps(task, ensure_ascii=False))


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_bytes(content)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def ingest_file(
    store: Path,
    task_id: str,
    filename: str,
    content: bytes,
    converter: Path = DEFAULT_CONVERTER,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    task = load_task(store, task_id)
    safe_name = Path(str(filename)).name.strip()
    suffix = Path(safe_name).suffix.lower()
    if not safe_name or safe_name in {".", ".."} or suffix not in ALLOWED_SOURCE_SUFFIXES:
        raise ValueError("仅支持PDF、PPTX、DOCX、XLSX、Markdown和TXT资料")
    if not content or len(content) > MAX_SOURCE_BYTES:
        raise ValueError("资料大小必须在1字节至30MB之间")
    digest = hashlib.sha256(content).hexdigest()
    if any(source.get("sha256") == digest for source in task["sources"]):
        return task, False

    assets_dir = store / "assets" / task_id
    source_id = digest[:16]
    stored = assets_dir / f"{source_id}-{safe_name}"
    extracted = assets_dir / f"{source_id}.md"
    _write_bytes_atomic(stored, content)
    if suffix in {".md", ".txt"}:
        text = content.decode("utf-8", errors="replace")
        _write_bytes_atomic(extracted, text.encode("utf-8"))
    else:
        try:
            subprocess.run(
                [str(converter), str(stored), "-o", str(extracted)],
                check=True,
                timeout=120,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"资料解析失败: {exc}") from exc
        text = extracted.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise ValueError("资料解析结果为空")
    relative_stored = stored.relative_to(store).as_posix()
    relative_extracted = extracted.relative_to(store).as_posix()
    source = {
        "kind": "uploaded",
        "source_id": source_id,
        "node_id": f"source:{source_id}",
        "title": Path(safe_name).stem,
        "original_name": safe_name,
        "stored_path": relative_stored,
        "extracted_path": relative_extracted,
        "sha256": digest,
        "parse_status": "parsed",
        "char_count": len(text),
        "unit_count": max(1, len(re.findall(r"(?m)^#{1,6}\s+", text))),
        "excerpt": " ".join(text.split())[:240],
    }
    task["sources"].append(source)
    task["updated_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    save_task_atomic(store, task)
    return task, True


def _normalize_title(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", text))
    sections: list[tuple[str, str]] = []
    if matches:
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if len(body) >= 40:
                for offset in range(0, len(body), 1200):
                    chunk = body[offset : offset + 1200].strip()
                    if len(chunk) >= 40:
                        title = match.group(1).strip()
                        if offset:
                            title = f"{title}-{offset // 1200 + 1}"
                        sections.append((title, chunk))
    else:
        for paragraph in re.split(r"\n\s*\n", text):
            clean = paragraph.strip()
            if len(clean) >= 40:
                sections.append((" ".join(clean.split())[:36], clean[:1200]))
    return sections


def _suggest_classification(title: str, text: str) -> tuple[str, str]:
    combined = f"{title} {text}"
    if any(word in combined for word in ("必须", "应当", "禁止", "规则", "要求")):
        return "知识原子", "规则维度"
    if any(word in combined for word in ("成交", "案例", "验证证明", "应用成效")):
        return "知识原子", "案例维度"
    if any(word in combined for word in ("产品", "平台", "系统", "工具")):
        return "知识原子", "产品维度"
    if any(word in combined for word in ("需求", "痛点", "目标", "约束")):
        return "需求记录", ""
    return "知识原子", "业务维度"


def decompose_task(
    store: Path,
    task_id: str,
    existing_titles: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    task = load_task(store, task_id)
    candidates: list[dict[str, Any]] = []
    for source in task.get("sources", []):
        extracted_path = source.get("extracted_path")
        if not extracted_path:
            continue
        extracted = (store / str(extracted_path)).resolve()
        if store.resolve() not in extracted.parents or not extracted.is_file():
            raise ValueError("来源提取文件不存在或超出草稿目录")
        text = extracted.read_text(encoding="utf-8", errors="replace")
        for title, chunk in _markdown_sections(text):
            normalized = _normalize_title(title)
            best: dict[str, Any] | None = None
            best_score = 0.0
            for item in existing_titles:
                score = SequenceMatcher(None, normalized, _normalize_title(str(item.get("title", "")))).ratio()
                if score > best_score:
                    best, best_score = item, score
            disposition = "merge" if best and best_score == 1 else "update" if best and best_score >= 0.72 else "new"
            candidate_id = hashlib.sha1(
                f"{source.get('source_id') or source.get('node_id')}\n{title}\n{chunk}".encode("utf-8")
            ).hexdigest()[:16]
            knowledge_type, dimension = _suggest_classification(title, chunk)
            candidates.append(
                {
                    "id": candidate_id,
                    "source_id": source.get("source_id") or source.get("node_id"),
                    "title": title,
                    "text": chunk,
                    "excerpt": " ".join(chunk.split())[:240],
                    "suggested_type": knowledge_type,
                    "asset_type": knowledge_type,
                    "knowledge_type": knowledge_type,
                    "dimension": dimension,
                    "sub_dimension": "",
                    "disposition": disposition,
                    "matched_node_id": best.get("id") if best else None,
                    "matched_path": best.get("path") if best else None,
                    "similarity": round(best_score, 3),
                    "evidence_level": "",
                    "use_for": "",
                    "boundary": "",
                }
            )
    if not candidates:
        raise ValueError("当前任务没有可分解的已解析资料")
    task["candidates"] = candidates
    task["stage"] = "分解与查重"
    task["updated_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    save_task_atomic(store, task)
    return task


def update_candidate(
    store: Path,
    task_id: str,
    candidate_id: str,
    patch: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    if not patch or not set(patch).issubset(CANDIDATE_FIELDS):
        raise ValueError("候选更新字段无效")
    if "disposition" in patch and patch["disposition"] not in DISPOSITIONS:
        raise ValueError("候选处置无效")
    task = load_task(store, task_id)
    candidate = next((item for item in task.get("candidates", []) if item.get("id") == candidate_id), None)
    if not candidate:
        raise ValueError("候选节点不存在")
    candidate.update({key: str(value).strip() for key, value in patch.items()})
    task["updated_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    save_task_atomic(store, task)
    return task


def add_relation(
    store: Path,
    task_id: str,
    source_id: str,
    target_id: str,
    relation_type: str,
    rationale: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    clean_type = str(relation_type).strip()
    clean_source = str(source_id).strip()
    clean_target = str(target_id).strip()
    clean_rationale = " ".join(str(rationale).split())
    if clean_type not in RELATION_TYPES:
        raise ValueError("关系类型无效")
    if not clean_source or not clean_target or clean_source == clean_target:
        raise ValueError("关系两端节点无效")
    if len(clean_rationale) < 4:
        raise ValueError("关系依据至少4个字符")
    task = load_task(store, task_id)
    relation_id = hashlib.sha1(
        f"{clean_source}\n{clean_target}\n{clean_type}".encode("utf-8")
    ).hexdigest()[:16]
    if not any(item.get("id") == relation_id for item in task.get("relations", [])):
        task.setdefault("relations", []).append(
            {
                "id": relation_id,
                "source_id": clean_source,
                "target_id": clean_target,
                "relation_type": clean_type,
                "rationale": clean_rationale,
                "status": "candidate",
            }
        )
        task["stage"] = "业务规则与关系链接"
        task["updated_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
        save_task_atomic(store, task)
    return task


def _requirement_kind(text: str) -> str:
    if any(word in text for word in ("尚未", "未明确", "待确认", "不清楚", "未知")):
        return "unknown"
    if any(word in text for word in ("必须", "不得", "约束", "限制", "符合", "保留")):
        return "constraint"
    if any(word in text for word in ("目标", "希望", "实现", "建立", "提升", "降低")):
        return "goal"
    return "fact"


def _search_tokens(text: str) -> set[str]:
    compact = _normalize_title(text)
    tokens = {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}
    tokens.update(re.findall(r"[a-z0-9]{2,}", text.lower()))
    return {token for token in tokens if token}


def analyze_requirement(
    store: Path,
    task_id: str,
    text: str,
    knowledge_index: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    clean_text = "\n".join(line.strip() for line in str(text).splitlines() if line.strip())
    if len(clean_text) < 8:
        raise ValueError("需求文本至少8个字符")
    statements = [item.strip() for item in re.split(r"[。！？!?；;\n]+", clean_text) if item.strip()]
    requirements = [
        {
            "id": hashlib.sha1(statement.encode("utf-8")).hexdigest()[:14],
            "kind": _requirement_kind(statement),
            "text": statement,
            "source": "user_input",
        }
        for statement in statements
    ]
    query_tokens = _search_tokens(clean_text)
    retrievals: list[dict[str, Any]] = []
    for node in knowledge_index:
        searchable = " ".join(
            [
                str(node.get("title", "")),
                " ".join(str(value) for value in node.get("tags", [])),
                str(node.get("summary", "")),
            ]
        )
        node_tokens = _search_tokens(searchable)
        if not query_tokens or not node_tokens:
            continue
        overlap = len(query_tokens & node_tokens)
        score = overlap / max(1, min(len(query_tokens), len(node_tokens)))
        if score <= 0:
            continue
        retrievals.append(
            {
                "node_id": node.get("id"),
                "title": node.get("title"),
                "path": node.get("path"),
                "asset_type": node.get("asset_type"),
                "evidence_level": node.get("evidence_level", ""),
                "score": round(score, 3),
                "coverage": "covered" if score >= 0.18 else "partial",
            }
        )
    retrievals.sort(key=lambda item: (-item["score"], str(item["title"])))
    retrievals = retrievals[:12]
    gaps = [] if retrievals else [f"[推演] 当前知识库未召回与需求直接相关的节点：{item['text']}" for item in requirements]
    task = load_task(store, task_id)
    task["requirements"] = requirements
    task["retrievals"] = retrievals
    task["gaps"] = gaps
    task["stage"] = "需求理解与知识召回"
    task["updated_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    save_task_atomic(store, task)
    return task


def build_knowledge_package(
    store: Path,
    task_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    task = load_task(store, task_id)
    sources = task.get("sources", [])
    source_ids = {source.get("source_id") or source.get("node_id") for source in sources}
    candidates = [
        item
        for item in task.get("candidates", [])
        if item.get("disposition") != "ignore" and item.get("source_id") in source_ids
    ]
    warnings = []
    if not sources:
        warnings.append("缺少来源")
    if not task.get("requirements"):
        warnings.append("缺少需求理解")
    if not candidates and task.get("candidates"):
        warnings.append("没有可进入知识包的候选")
    if not task.get("relations"):
        warnings.append("尚未建立候选业务关系")
    package = {
        "schema_version": 1,
        "task_id": task["id"],
        "title": task["title"],
        "status": "draft",
        "generated_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "sources": sources,
        "requirements": task.get("requirements", []),
        "candidates": candidates,
        "relations": task.get("relations", []),
        "retrievals": task.get("retrievals", []),
        "gaps": task.get("gaps", []),
        "warnings": warnings,
    }
    package_path = Path("packages") / f"{task['id']}-knowledge-package.json"
    _write_bytes_atomic(
        store / package_path,
        (json.dumps(package, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    task["knowledge_package"] = package
    task["knowledge_package_path"] = package_path.as_posix()
    task["stage"] = "输出准备与售前交接"
    task["updated_at"] = package["generated_at"]
    save_task_atomic(store, task)
    return task
