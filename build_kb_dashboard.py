#!/usr/bin/env python3
"""Build a self-contained visual dashboard from Xiaohua's Obsidian vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from kb_star_graph import build_full_graph, layout_star_graph, stable_node_id, source_diagnostics, relationship_diagnostics


EXCLUDED_DIRS = {
    ".git",
    ".obsidian",
    ".trash",
    ".codex-memory",
    ".opencode",
    "__pycache__",
    "node_modules",
    "运营任务草稿",
}

WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]\n]+)\]\]")
# 标准 Markdown 链接：先捕获括号内全部内容，标题/空格/尖括号交给归一化处理。
MDLINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*([^)\n]*?)\s*\)")
# 带协议的链接（http:、mailto:、obsidian: 等）不是库内笔记目标。
MDLINK_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class NoteRecord:
    path: str
    title: str
    asset_type: str
    inferred: bool
    status: str
    modified: str
    summary: str
    metadata: dict[str, Any]
    links: list[str]


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, str | None]:
    if not text.startswith("---\n"):
        return {}, text, None
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text, "frontmatter未闭合"
    raw = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        recovered = {}
        # Recover independently valid top-level fields; keep the original warning.
        blocks = re.split(r"(?m)(?=^[A-Za-z_\u4e00-\u9fff][^:\n]*:)", raw)
        for block in blocks:
            try:
                value = yaml.safe_load(block)
            except yaml.YAMLError:
                continue
            if isinstance(value, dict):
                for key, item in value.items():
                    if key not in recovered:
                        recovered[key] = item
        return _json_safe(recovered), body, f"frontmatter解析失败: {exc.__class__.__name__}（有效字段已保留）"
    if not isinstance(parsed, dict):
        return {}, body, "frontmatter不是对象"
    return _json_safe(parsed), body, None


def _normalize_link(raw: str) -> str:
    target = raw.split("|", 1)[0].split("#", 1)[0].strip()
    if target.endswith(".md"):
        target = target[:-3]
    return target


def _normalize_markdown_target(raw: str, source_path: str | None) -> str:
    """把标准 Markdown 链接目标归一成与 wikilink 同域的库内路径键。

    `[文本](a.md)` 中的 a 按 Markdown 通用语义是相对**源笔记所在目录**解析的，
    因此这里预先拼成库根相对路径，再交给既有 `_resolve_target` / `_target_index`
    （已登记全部路径后缀）命中。`source_path` 缺失时（如正文声明的产出清单）
    按库根相对处理。返回空串表示这不是库内笔记目标。
    """
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("<"):
        end = value.find(">")
        value = value[1:end] if end != -1 else value[1:]
    else:
        # 去掉 `"标题"` / `'标题'` 之类的 title 部分；尖括号写法才允许目标含空格。
        parts = value.split()
        value = parts[0] if parts else ""
    value = unquote(value.split("#", 1)[0].split("?", 1)[0]).strip()
    if not value or MDLINK_SCHEME_RE.match(value) or not value.lower().endswith(".md"):
        return ""
    value = value[:-3]
    if value.startswith("/"):
        value = value.lstrip("/")  # 前导斜杠按库根绝对路径处理
    elif source_path:
        base = posixpath.dirname(source_path.replace("\\", "/"))
        if base:
            value = posixpath.normpath(posixpath.join(base, value))
    if not value or value == "." or value.startswith("../"):
        return ""  # 越出库根，不是库内目标
    return value


def extract_links(text: str, source_path: str | None = None) -> list[str]:
    """抽取笔记内的链接目标，同时支持 `[[wikilink]]` 与 `[文本](路径.md)`。

    wikilink 走「标题 / 别名 / 路径后缀」名称解析；标准 Markdown 链接走相对路径解析。
    两种写法指向同一目标时，建图阶段按 (source, target) 去重，不会重复计边。
    """
    seen: set[str] = set()
    links: list[str] = []
    for match in WIKILINK_RE.finditer(text):
        target = _normalize_link(match.group(1))
        if not target or target in seen:
            continue
        seen.add(target)
        links.append(target)
        if len(links) >= 160:
            return links
    for match in MDLINK_RE.finditer(text):
        target = _normalize_markdown_target(match.group(1), source_path)
        if not target or target in seen:
            continue
        seen.add(target)
        links.append(target)
        if len(links) >= 160:
            break
    return links


# ---------- 资产分类规则（可外置）----------
# 第四层「按路径判定」绑定的是本机目录结构，同事的库不适用；兜底类型若默认隐藏，
# 首次运行会近乎空图。故规则与兜底均可由配置文件覆盖，缺失时用下列内置默认
# （与历史行为逐字一致，本机产物不变）。
DEFAULT_PATH_RULES: tuple[tuple[str, str], ...] = (
    ("知识库/00_原始资料库/", "来源资料"),
    ("知识库/03_存量方案拆解区/", "拆解记录"),
    ("知识库/97_成交证据/", "知识原子"),
    ("知识库/01_原子知识组件库/", "知识原子"),
    ("知识库/96_专题综述/", "专题知识"),
    ("知识库/05_客户需求库/", "需求记录"),
    ("知识库/解决方案/", "方案成果"),
    ("知识库/销售话术/", "方案成果"),
    ("输出/", "方案成果"),
    ("知识库/04_需求与规则引擎/B流程反哺/", "验证反馈"),
    ("知识库/04_需求与规则引擎/", "治理规则"),
    ("知识库/99_更新日志/", "运行记录"),
    ("项目/", "运行记录"),
    ("收件箱/", "运行记录"),
    ("日记/", "运行记录"),
    ("系统/", "运行记录"),
)
DEFAULT_FALLBACK_TYPE = "运行记录"
RULES_FILENAME = "资产分类规则.yaml"

_ACTIVE_PATH_RULES: tuple[tuple[str, str], ...] = DEFAULT_PATH_RULES
_ACTIVE_FALLBACK: str = DEFAULT_FALLBACK_TYPE


def apply_classification_rules(rules, fallback: str) -> None:
    """设置本次构建生效的分类规则。规则为空时保留内置默认。"""
    global _ACTIVE_PATH_RULES, _ACTIVE_FALLBACK
    _ACTIVE_PATH_RULES = tuple((str(a), str(b)) for a, b in rules) or DEFAULT_PATH_RULES
    _ACTIVE_FALLBACK = str(fallback or DEFAULT_FALLBACK_TYPE)


def load_classification_rules(vault: Path, explicit: Path | None = None) -> tuple[Path | None, str, int]:
    """读取分类规则配置。返回 (配置文件路径或None, 兜底类型, 规则条数)。

    查找顺序：显式指定 → <vault>/系统/知识库可视化工作台/资产分类规则.yaml → 内置默认。
    配置文件损坏时不静默吞掉，直接报错，避免"改了没生效"。
    """
    candidates = [p for p in (explicit, vault / "系统" / "知识库可视化工作台" / RULES_FILENAME) if p]
    for path in candidates:
        if not path.exists():
            continue
        # utf-8-sig：无 BOM 时解码结果与 utf-8 完全一致；带 BOM 时自动剥离。
        # 同事用记事本另存为「UTF-8 带 BOM」是很常见的，BOM 会让 YAML 解析失败。
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"分类规则文件应为键值映射: {path}")
        rules = data.get("path_rules") or []
        fallback = data.get("fallback")
        apply_classification_rules(rules, fallback)
        return path, _ACTIVE_FALLBACK, len(_ACTIVE_PATH_RULES)
    apply_classification_rules(DEFAULT_PATH_RULES, DEFAULT_FALLBACK_TYPE)
    return None, _ACTIVE_FALLBACK, len(_ACTIVE_PATH_RULES)


def classify_note(relative_path: str, metadata: dict[str, Any]) -> tuple[str, bool]:
    canonical_types = {
        "来源资料",
        "拆解记录",
        "知识原子",
        "专题知识",
        "需求记录",
        "方案成果",
        "验证反馈",
        "治理规则",
        "运行记录",
    }
    knowledge_type = str(metadata.get("knowledge_type", "")).strip()
    if knowledge_type in canonical_types:
        return knowledge_type, False

    dimension = str(metadata.get("dimension", "")).strip()
    if dimension in {"行业维度", "工程维度", "客户维度", "产品维度", "管理维度", "资源维度", "业务维度", "案例维度", "规则维度"}:
        return "知识原子", False

    explicit = " ".join(
        str(metadata.get(key, "")) for key in ("type", "category")
    ).lower()
    explicit_rules = (
        (("原始资料", "来源资料"), "来源资料"),
        (("拆解", "逐页索引", "逐节索引"), "拆解记录"),
        (("规则卡", "原子", "组件", "成交证据", "案例"), "知识原子"),
        (("专题综述", "专题知识"), "专题知识"),
        (("客户需求", "需求记录"), "需求记录"),
        (("反哺", "验证反馈"), "验证反馈"),
        (("sop", "执行卡", "治理规则"), "治理规则"),
        (("方案", "话术", "输出"), "方案成果"),
    )
    if explicit.strip() and not ("产品设计规格" in explicit):
        for keywords, result in explicit_rules:
            if any(keyword in explicit for keyword in keywords):
                return result, False
        if explicit.strip() in {"产品", "产品卡", "产品知识", "产品与工具", "product"}:
            return "知识原子", False

    path = relative_path.replace("\\", "/")
    for prefix, result in _ACTIVE_PATH_RULES:
        if path.startswith(prefix):
            return result, True
    return _ACTIVE_FALLBACK, True


def _make_summary(body: str, limit: int = 180) -> str:
    lines = []
    in_code = False
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line or line.startswith(("#", "|", "> [!")):
            continue
        line = re.sub(r"!?\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), line)
        line = re.sub(r"[*_`~]", "", line)
        lines.append(line)
        if sum(len(item) for item in lines) >= limit:
            break
    summary = " ".join(lines)
    return summary[:limit] + ("…" if len(summary) > limit else "")


# 敏感笔记：正文不得进入星图聚合快照。
# 星图 HTML 内嵌全库 summary，按文件路径排除对聚合产物无效，
# 因此必须在这里遮蔽内容。新增凭据类笔记时请同步此清单。
SENSITIVE_NOTE_PATHS = {
    "系统/大模型APIkey.md",
    "系统/Claude桌面端DeepSeek代理方案.md",
}

SENSITIVE_SUMMARY = "（敏感笔记：正文不进入星图快照，请直接在 Obsidian 中查看）"


def parse_note(path: Path, vault: Path) -> NoteRecord:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    metadata, body, warning = _split_frontmatter(text)
    relative_path = path.relative_to(vault).as_posix()
    asset_type, inferred = classify_note(relative_path, metadata)
    heading = HEADING_RE.search(body)
    title = str(metadata.get("title") or (heading.group(1).strip() if heading else path.stem))
    modified = str(
        metadata.get("date_modified")
        or metadata.get("date modified")
        or metadata.get("updated")
        or datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    )
    normalized_metadata = dict(metadata)
    if asset_type in {"来源资料", "拆解记录"}:
        active_level = 0
        output_lines = []
        in_code = False
        for line in body.splitlines():
            if line.strip().startswith("```"):
                in_code = not in_code
            if in_code:
                continue
            match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if match:
                level, heading_text = len(match.group(1)), match.group(2)
                if re.search(r"关联拆解|关联原子|关联产物|关联入库成果|原子落库清单|原子知识库分解落库清单|原子组件与规则卡|原子组件全景索引", heading_text):
                    active_level = level
                elif level <= active_level:
                    active_level = 0
            elif active_level:
                if not re.search(r"\[ \]|待人工复核|待下一轮|待补|候选|如证据充分", line):
                    output_lines.append(line)
        normalized_metadata["_declared_outputs"] = extract_links("\n".join(output_lines))
    if warning:
        normalized_metadata["_parse_warning"] = warning
    return NoteRecord(
        path=relative_path,
        title=title,
        asset_type=asset_type,
        inferred=inferred,
        status=str(metadata.get("status") or "未标注"),
        modified=modified,
        summary=(
            SENSITIVE_SUMMARY
            if relative_path in SENSITIVE_NOTE_PATHS
            else _make_summary(body)
        ),
        metadata=normalized_metadata,
        links=extract_links(text, relative_path),
    )


def scan_vault(vault: Path) -> list[NoteRecord]:
    records: list[NoteRecord] = []
    for path in sorted(vault.rglob("*.md")):
        relative_parts = path.relative_to(vault).parts
        if any(part in EXCLUDED_DIRS or part.startswith(".") for part in relative_parts):
            continue
        records.append(parse_note(path, vault))
    return records


def _metric_from_text(text: str, label: str) -> int:
    pattern = re.compile(rf"^\|\s*{re.escape(label)}\s*\|\s*(\d+)\s*\|", re.MULTILINE)
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def parse_health_reports(vault: Path) -> list[dict[str, Any]]:
    folder = vault / "知识库" / "99_更新日志"
    if not folder.exists():
        return []
    reports: list[dict[str, Any]] = []
    for path in sorted(folder.glob("????-??-??-知识库健康巡检.md")):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        date_match = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
        scan_match = re.search(r"\*\*扫描文件\*\*:\s*(\d+)\s*个", text)
        issues = []
        issue_pattern = re.compile(
            r"^\|\s*(P[12])\s*\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|\s*$",
            re.MULTILINE,
        )
        for match in issue_pattern.finditer(text):
            issues.append(
                {
                    "priority": match.group(1),
                    "kind": match.group(2).strip(),
                    "path": match.group(3).strip(),
                    "message": match.group(4).strip(),
                }
            )
        reports.append(
            {
                "date": date_match.group(1) if date_match else path.stem,
                "path": path.relative_to(vault).as_posix(),
                "scanned": int(scan_match.group(1)) if scan_match else 0,
                "p1": _metric_from_text(text, "P1"),
                "p2": _metric_from_text(text, "P2"),
                "frontmatter": _metric_from_text(text, "frontmatter 异常"),
                "drafts": _metric_from_text(text, "Draft 笔记"),
                "deprecated_paths": _metric_from_text(text, "废弃路径引用文件"),
                "bypass_dirs": _metric_from_text(text, "旁路目录提示"),
                "entry_freshness": _metric_from_text(text, "入口新鲜度异常"),
                "broken_links": _metric_from_text(text, "候选断链"),
                "pending_rules": _metric_from_text(text, "待接入规则候选"),
                "issues": issues,
            }
        )
    return reports


def count_asset_types(notes: list[NoteRecord]) -> dict[str, int]:
    counts = Counter(note.asset_type for note in notes)
    result = {"Markdown": len(notes)}
    for key in sorted(counts):
        result[key] = counts[key]
    return result


def build_quality_coverage(notes: list[NoteRecord]) -> dict[str, dict[str, dict[str, int]]]:
    fields = ("source_ref", "evidence_level", "use_for")
    result: dict[str, dict[str, dict[str, int]]] = {}
    grouped: dict[str, list[NoteRecord]] = defaultdict(list)
    for note in notes:
        grouped[note.asset_type].append(note)
    for asset_type, records in sorted(grouped.items()):
        result[asset_type] = {}
        for field in fields:
            result[asset_type][field] = {
                "filled": sum(1 for note in records if note.metadata.get(field) not in (None, "", [])),
                "total": len(records),
            }
    return result


def _stable_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:14]


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [part.strip() for part in re.split(r"[/;,，；]", str(value)) if part.strip()]


INDUSTRY_KEYWORDS = {
    "公路": ("公路", "高速", "路桥", "路基", "路面"),
    "铁路": ("铁路", "高铁", "铁建"),
    "水利水电": ("水利", "水电", "大坝", "水工"),
    "房建": ("房建", "建筑企业", "住宅", "建造模型"),
    "市政": ("市政", "城市更新", "管廊"),
}


def infer_industries(note: NoteRecord) -> list[str]:
    haystack = " ".join(
        [
            note.title,
            note.path,
            " ".join(_as_list(note.metadata.get("tags"))),
            str(note.metadata.get("industry", "")),
            str(note.metadata.get("domain", "")),
        ]
    )
    return [name for name, keywords in INDUSTRY_KEYWORDS.items() if any(key in haystack for key in keywords)]


def _node_payload(note: NoteRecord) -> dict[str, Any]:
    return {
        "id": _stable_id(note.path),
        "title": note.title,
        "type": note.asset_type,
        "status": note.status,
        "path": note.path,
        "modified": note.modified,
        "summary": note.summary,
        "inferred": note.inferred,
        "tags": _as_list(note.metadata.get("tags")),
        "aliases": _as_list(note.metadata.get("aliases")),
        "industries": infer_industries(note),
        "evidence_level": note.metadata.get("evidence_level", ""),
        "use_for": _as_list(note.metadata.get("use_for")),
        "source_ref": _as_list(note.metadata.get("source_ref")),
        "dimension": str(note.metadata.get("dimension", "")),
        "sub_dimension": str(note.metadata.get("sub_dimension", "")),
    }


def build_graph(notes: list[NoteRecord], max_detail_nodes: int = 180) -> dict[str, Any]:
    priority = {
        "来源资料": 0,
        "拆解记录": 1,
        "知识原子": 2,
        "专题知识": 3,
        "需求记录": 4,
        "方案成果": 5,
        "验证反馈": 6,
        "治理规则": 7,
        "运行记录": 20,
    }
    core_types = ["来源资料", "拆解记录", "知识原子", "专题知识", "需求记录", "方案成果", "验证反馈", "治理规则"]
    graph_notes = [
        note
        for note in notes
        if note.title.strip().lower() not in {"readme", "index", "索引"}
    ]
    ordered = sorted(
        graph_notes,
        key=lambda note: (priority.get(note.asset_type, 15), -len(note.links), note.modified, note.title),
    )
    quota = max(1, max_detail_nodes // len(core_types))
    selected: list[NoteRecord] = []
    selected_paths: set[str] = set()
    for asset_type in core_types:
        candidates = [note for note in ordered if note.asset_type == asset_type]
        for note in candidates[:quota]:
            selected.append(note)
            selected_paths.add(note.path)
    for note in ordered:
        if len(selected) >= max_detail_nodes:
            break
        if note.path in selected_paths:
            continue
        selected.append(note)
        selected_paths.add(note.path)
    detail_nodes = [_node_payload(note) for note in selected]
    selected_by_path = {note.path: note for note in selected}
    selected_ids = {_stable_id(path) for path in selected_by_path}

    target_index: dict[str, NoteRecord] = {}
    for note in selected:
        candidates = {
            note.title,
            Path(note.path).stem,
            note.path,
            note.path[:-3] if note.path.endswith(".md") else note.path,
            *_as_list(note.metadata.get("aliases")),
        }
        for candidate in candidates:
            target_index.setdefault(candidate.strip(), note)

    edges = []
    edge_keys: set[tuple[str, str]] = set()
    for source_note in selected:
        source_id = _stable_id(source_note.path)
        for raw_target in source_note.links:
            target_note = target_index.get(raw_target) or target_index.get(Path(raw_target).name)
            if target_note is None:
                continue
            target_id = _stable_id(target_note.path)
            if target_id not in selected_ids or target_id == source_id:
                continue
            key = (source_id, target_id)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            edges.append({"source": source_id, "target": target_id, "type": "wikilink"})
            if len(edges) >= max_detail_nodes * 4:
                break

    aggregate_types = set(core_types)
    type_counts = Counter(note.asset_type for note in notes if note.asset_type in aggregate_types)
    industry_counts = Counter(industry for note in notes for industry in infer_industries(note))
    aggregate_nodes = [
        {"id": f"type:{name}", "title": name, "type": "资产类型", "count": count}
        for name, count in type_counts.most_common()
    ] + [
        {"id": f"industry:{name}", "title": name, "type": "行业", "count": count}
        for name, count in industry_counts.most_common()
    ]
    return {
        "aggregate_nodes": aggregate_nodes,
        "detail_nodes": detail_nodes,
        "edges": edges,
        "node_ids": sorted(selected_ids),
        "total_notes": len(notes),
        "displayed_notes": len(detail_nodes),
    }


def _rule_payload(note: NoteRecord) -> dict[str, Any]:
    payload = _node_payload(note)
    payload["triggers"] = _as_list(note.metadata.get("triggers") or note.metadata.get("trigger"))
    return payload


def build_rule_index(notes: list[NoteRecord]) -> list[dict[str, Any]]:
    rules = [_rule_payload(note) for note in notes if note.asset_type == "治理规则"]
    return sorted(rules, key=lambda item: (item["status"] != "Active", item["title"]))


def build_recent_assets(notes: list[NoteRecord], limit: int = 20) -> list[dict[str, Any]]:
    excluded = {"运行记录"}
    selected = [note for note in notes if note.asset_type not in excluded]
    selected.sort(key=lambda note: (note.modified, note.title), reverse=True)
    return [_node_payload(note) for note in selected[:limit]]


def build_search_index(notes: list[NoteRecord]) -> list[dict[str, Any]]:
    return [_node_payload(note) for note in notes]


def count_canonical_products(vault: Path) -> int:
    path = vault / "知识库" / "01_原子知识组件库" / "98_产品与工具" / "产品canonical索引.md"
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    section = text.split("## 2. canonical 清单", 1)[-1].split("## 3.", 1)[0]
    return sum(1 for line in section.splitlines() if line.startswith("|") and "[[" in line)


def build_dashboard_data(vault: Path, notes: list[NoteRecord]) -> dict[str, Any]:
    reports = parse_health_reports(vault)
    metrics = count_asset_types(notes)
    metrics["产品 canonical"] = count_canonical_products(vault)
    latest_report = reports[-1] if reports else None
    graph_model = build_full_graph(notes)
    full_graph = layout_star_graph(graph_model).to_dict()
    full_graph["sourceDiagnostics"] = source_diagnostics(notes, graph_model)
    full_graph["relationshipDiagnostics"] = relationship_diagnostics(notes, graph_model)
    health_by_node_id: dict[str, str] = {}
    for issue in (latest_report or {}).get("issues", []):
        path = issue.get("path", "")
        if not path:
            continue
        health_by_node_id[stable_node_id(path)] = "blocked" if issue.get("priority") == "P1" else "warning"
    full_graph["healthByNodeId"] = health_by_node_id
    full_graph["detail_nodes"] = full_graph["nodes"][:180]
    full_graph["aggregate_nodes"] = [
        {"id": f"type:{cluster['id']}", "title": cluster["title"], "type": "资产类型", "count": cluster["count"]}
        for cluster in full_graph["clusters"]
    ]
    full_graph["node_ids"] = [node["id"] for node in full_graph["detail_nodes"]]
    full_graph["total_notes"] = len(full_graph["nodes"])
    full_graph["displayed_notes"] = len(full_graph["detail_nodes"])
    warnings = [
        {"path": note.path, "message": note.metadata["_parse_warning"]}
        for note in notes
        if note.metadata.get("_parse_warning")
    ]
    return {
        "meta": {
            "title": "知识库可视化工作台",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "vault_name": vault.name,
            "vault_path": str(vault.resolve()),
            "scanned_notes": len(notes),
            "parse_warnings": len(warnings),
        },
        "taxonomy": _json_safe(yaml.safe_load((vault / "知识库/04_需求与规则引擎/知识库九维分类与关系词典.yaml").read_text(encoding="utf-8-sig"))) if (vault / "知识库/04_需求与规则引擎/知识库九维分类与关系词典.yaml").exists() else {},
        "metrics": metrics,
        "quality": build_quality_coverage(notes),
        "health": {"latest": latest_report, "trend": reports[-20:]},
        "recent": build_recent_assets(notes),
        "rules": build_rule_index(notes),
        "graph": full_graph,
        "search": build_search_index(notes),
        "warnings": warnings[:100],
    }


def _static_overview_fragments(data: dict[str, Any]) -> dict[str, str]:
    metrics = data.get("metrics", {})
    latest = data.get("health", {}).get("latest") or {}
    specs = (
        ("Markdown", "知识文件", "全库可检索文本", "#2563eb", "#eaf2ff"),
        ("知识原子", "知识原子", "可复用最小知识单元", "#0891b2", "#e8f7fa"),
        ("产品 canonical", "产品 canonical", "统一产品命名", "#0f9f86", "#e6f7f3"),
        ("治理规则", "治理规则", "流程与交付约束", "#7c3aed", "#f1eafe"),
        ("验证反馈", "验证反馈", "实际使用与反哺记录", "#db6b16", "#fff3e8"),
        ("方案成果", "方案成果", "知识调用后的业务输出", "#c2410c", "#fff0e8"),
    )
    kpis = "".join(
        f'<article class="card kpi-card" style="--kpi-soft:{soft}">' 
        f'<div class="kpi-label">{escape(label)}</div>'
        f'<div class="kpi-value" style="color:{color}">{int(metrics.get(key, 0)):,}</div>'
        f'<div class="kpi-note">{escape(note)}</div></article>'
        for key, label, note, color, soft in specs
    )

    health_specs = (
        (latest.get("p1", 0), "P1阻塞", "danger" if latest.get("p1", 0) else ""),
        (latest.get("p2", 0), "P2治理项", "warning" if latest.get("p2", 0) else ""),
        (latest.get("broken_links", 0), "候选断链", "warning" if latest.get("broken_links", 0) else ""),
        (latest.get("drafts", 0), "Draft", "warning" if latest.get("drafts", 0) else ""),
        (latest.get("bypass_dirs", 0), "旁路目录", "warning" if latest.get("bypass_dirs", 0) else ""),
        (latest.get("entry_freshness", 0), "入口异常", "warning" if latest.get("entry_freshness", 0) else ""),
        (data.get("meta", {}).get("parse_warnings", 0), "解析警告", "warning" if data.get("meta", {}).get("parse_warnings", 0) else ""),
    )
    health = "".join(
        f'<article class="card health-item"><i class="status-dot {tone}" aria-hidden="true"></i>'
        f'<div><strong>{int(value):,}</strong><span>{escape(label)}</span></div></article>'
        for value, label, tone in health_specs
    )

    issues = latest.get("issues", [])
    priority_rows = "".join(
        f'<tr class="clickable"><td><span class="pill {"danger" if item.get("priority") == "P1" else "warning"}">{escape(str(item.get("priority", "")))}</span></td>'
        f'<td>{escape(str(item.get("kind", "")))}</td><td><button class="table-button" data-health-issue="{index}">{escape(str(item.get("message", "")))}</button></td>'
        f'<td><div class="path-text" title="{escape(str(item.get("path", "")))}">{escape(str(item.get("path", "")))}</div></td></tr>'
        for index, item in enumerate(issues[:8])
    ) or '<tr><td colspan="4" class="empty">当前没有可展示的治理问题</td></tr>'

    excluded = {"Markdown", "运行记录", "产品 canonical"}
    entries = sorted(
        ((key, value) for key, value in metrics.items() if key not in excluded),
        key=lambda pair: pair[1],
        reverse=True,
    )[:9]
    maximum = max((value for _, value in entries), default=1)
    bars = "".join(
        f'<div class="bar-row"><span class="bar-label">{escape(key)}</span><div class="bar-track">'
        f'<div class="bar-fill" style="width:{max(3, value / maximum * 100):.1f}%"></div></div>'
        f'<span class="bar-value">{int(value):,}</span></div>'
        for key, value in entries
    )

    recent_rows = "".join(
        f'<tr class="clickable"><td><button class="table-button" data-item-id="{escape(str(item.get("id", "")))}">{escape(str(item.get("title", "")))}</button>'
        f'<div class="path-text" title="{escape(str(item.get("path", "")))}">{escape(str(item.get("path", "")))}</div></td>'
        f'<td><span class="pill">{escape(str(item.get("type", "")))}</span></td>'
        f'<td>{escape("、".join(item.get("industries", [])) or "—")}</td><td>{escape(str(item.get("status", "")))}</td>'
        f'<td>{escape(str(item.get("modified", "")))}</td></tr>'
        for item in data.get("recent", [])[:20]
    ) or '<tr><td colspan="5" class="empty">当前没有近期资产</td></tr>'

    issue_total = int(latest.get("p1", 0)) + int(latest.get("p2", 0))
    issue_summary = f'{issue_total:,}项 · {escape(str(latest.get("date", "无今日巡检")))}'
    cluster_items = "".join(
        f"<li><strong>{escape(str(cluster.get('title', '')))}</strong> · {int(cluster.get('count', 0)):,}</li>"
        for cluster in data.get("graph", {}).get("clusters", [])
    )
    star_summary = (
        f"<p><strong>{int(data.get('meta', {}).get('scanned_notes', 0)):,}</strong> 个知识节点，"
        f"<strong>{int(data.get('graph', {}).get('stats', {}).get('edge_count', 0)):,}</strong> 条可解析关系，"
        f"<strong>{issue_total:,}</strong> 项治理状态。</p><ul>{cluster_items}</ul>"
    )
    return {
        "__STATIC_KPI_CARDS__": kpis,
        "__STATIC_HEALTH_CARDS__": health,
        "__STATIC_PRIORITY_ROWS__": priority_rows,
        "__STATIC_ASSET_BARS__": bars,
        "__STATIC_RECENT_ROWS__": recent_rows,
        "__STATIC_ISSUE_SUMMARY__": issue_summary,
        "__STATIC_STAR_SUMMARY__": star_summary,
    }


def assemble_star_graph_js(assets: Path) -> str:
    """按清单拼接 star_graph 模块片段。

    2026-09-11：原 star_graph.js（1134 行、61KB）按既有接缝拆为 7 个片段以便
    分模块维护。片段按 star_graph.modules.json 的顺序拼接，结果与拆分前
    逐字节相同，因此单文件 HTML 产物完全不变。
    """
    manifest = json.loads((assets / "star_graph.modules.json").read_text(encoding="utf-8"))
    return "".join(
        (assets / item["file"]).read_text(encoding="utf-8") for item in manifest["modules"]
    )


def render_dashboard(template: str, data: dict[str, Any]) -> str:
    rendered = template
    assets = SCRIPT_DIR / "dashboard_assets"
    rendered = rendered.replace(
        "__STAR_GRAPH_CSS__", (assets / "star_graph.css").read_text(encoding="utf-8")
    )
    rendered = rendered.replace(
        "__STAR_GRAPH_CORE_JS__", (assets / "star_graph_core.js").read_text(encoding="utf-8")
    )
    rendered = rendered.replace("__STAR_GRAPH_JS__", assemble_star_graph_js(assets))
    for marker, fragment in _static_overview_fragments(data).items():
        rendered = rendered.replace(marker, fragment)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return rendered.replace("__DASHBOARD_DATA__", payload)


def write_dashboard_atomic(output: Path, html_text: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",  # 禁止换行翻译，Windows 产物才能与 macOS 逐字节一致
            dir=output.parent,
            prefix=f".{output.stem}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(html_text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, output)
    except Exception:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        raise


def generate_dashboard(vault: Path, template_path: Path, output: Path,
                       rules_path: Path | None = None) -> dict[str, Any]:
    vault = vault.resolve()
    rules_file, fallback_type, rule_count = load_classification_rules(vault, rules_path)
    notes = scan_vault(vault)
    data = build_dashboard_data(vault, notes)
    template = template_path.read_text(encoding="utf-8")
    if "__DASHBOARD_DATA__" not in template:
        raise ValueError(f"模板缺少数据占位符: {template_path}")
    html_text = render_dashboard(template, data)
    write_dashboard_atomic(output, html_text)
    return {
        "scanned": len(notes),
        "html_bytes": len(html_text.encode("utf-8")),
        "graph_nodes": data["graph"]["stats"]["node_count"],
        "graph_edges": data["graph"]["stats"]["edge_count"],
        "warnings": data["meta"]["parse_warnings"],
        "output": str(output.resolve()),
    }


def _extract_embedded_data(html_text: str) -> dict[str, Any]:
    match = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>',
        html_text,
        re.DOTALL,
    )
    if not match:
        raise ValueError("正式HTML缺少dashboard-data快照")
    return json.loads(match.group(1))


def _comparable_data(data: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(data, ensure_ascii=False))
    copied.get("meta", {}).pop("generated_at", None)
    return copied


def check_dashboard_current(vault: Path, output: Path) -> tuple[bool, dict[str, Any]]:
    if not output.exists():
        return False, {"reason": "正式HTML不存在", "output": str(output)}
    notes = scan_vault(vault.resolve())
    current = build_dashboard_data(vault.resolve(), notes)
    embedded = _extract_embedded_data(output.read_text(encoding="utf-8"))
    is_current = _comparable_data(current) == _comparable_data(embedded)
    return is_current, {
        "reason": "数据一致" if is_current else "Vault数据已变化，请重新生成",
        "scanned": len(notes),
        "output": str(output.resolve()),
    }


def _default_paths() -> tuple[Path, Path, Path]:
    vault = Path(__file__).resolve().parents[3]
    folder = vault / "系统" / "知识库可视化工作台"
    template = Path(__file__).resolve().parent / "templates" / "kb_dashboard_template.html"
    return vault, template, folder / "知识库可视化工作台.html"


def main(argv: list[str] | None = None) -> int:
    default_vault, default_template, default_output = _default_paths()
    parser = argparse.ArgumentParser(description="从Obsidian Vault生成知识库可视化工作台")
    parser.add_argument("--vault", type=Path, default=default_vault, help="活动Vault根目录")
    parser.add_argument("--template", type=Path, default=default_template, help="HTML模板")
    parser.add_argument("--output", type=Path, default=default_output, help="正式HTML输出路径")
    parser.add_argument("--rules", type=Path, default=None,
                        help=f"资产分类规则YAML（默认读 <vault>/系统/知识库可视化工作台/{RULES_FILENAME}）")
    parser.add_argument("--inspect-json", type=Path, help="仅输出数据快照用于检查，不生成正式HTML")
    parser.add_argument("--check", action="store_true", help="检查正式HTML是否与当前Vault一致")
    args = parser.parse_args(argv)

    vault = args.vault.resolve()
    # 先加载分类规则：--check / --inspect-json 与正式生成必须同一口径，
    # 否则同事改完规则会发现"--check 说一致、页面却没变"。
    rules_file, fallback_type, rule_count = load_classification_rules(vault, args.rules)
    if args.check:
        current, summary = check_dashboard_current(vault, args.output)
        print(json.dumps({"current": current, **summary}, ensure_ascii=False))
        return 0 if current else 1

    if args.inspect_json:
        notes = scan_vault(vault)
        data = build_dashboard_data(vault, notes)
        args.inspect_json.parent.mkdir(parents=True, exist_ok=True)
        with args.inspect_json.open("w", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "scanned": len(notes),
                    "graph_nodes": data["graph"]["stats"]["node_count"],
                    "graph_edges": data["graph"]["stats"]["edge_count"],
                    "warnings": data["meta"]["parse_warnings"],
                    "inspect_json": str(args.inspect_json.resolve()),
                    "classification": {"rules_file": str(rules_file) if rules_file else None,
                                       "fallback_type": fallback_type, "path_rule_count": rule_count},
                },
                ensure_ascii=False,
            )
        )
        return 0

    summary = generate_dashboard(vault, args.template, args.output, rules_path=args.rules)
    summary["classification"] = {
        "rules_file": str(rules_file) if rules_file else None,
        "fallback_type": fallback_type,
        "path_rule_count": rule_count,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
