"""Full-vault graph model and deterministic clustered star layout."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
import posixpath
from urllib.parse import unquote
from dataclasses import asdict, dataclass, replace, field
from typing import Any, Iterable


def stable_node_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:14]


def _as_strings(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    return (str(value),)


INDUSTRY_KEYWORDS = {
    "公路": ("公路", "高速", "路桥", "路基", "路面"),
    "铁路": ("铁路", "高铁", "铁建"),
    "水利水电": ("水利", "水电", "大坝", "水工"),
    "房建": ("房建", "住宅", "建造模型", "建筑企业"),
    "市政": ("市政", "城市更新", "管廊"),
}


def infer_industries(note: Any) -> tuple[str, ...]:
    metadata = getattr(note, "metadata", {}) or {}
    explicit = _as_strings(metadata.get("industry"))
    text = " ".join(
        [
            str(getattr(note, "title", "")),
            str(getattr(note, "path", "")),
            " ".join(_as_strings(metadata.get("tags"))),
            " ".join(explicit),
        ]
    )
    values = set(explicit)
    for name, keywords in INDUSTRY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            values.add(name)
    return tuple(sorted(values))


@dataclass(frozen=True)
class StarNode:
    id: str
    path: str
    title: str
    asset_type: str
    status: str
    modified: str
    industries: tuple[str, ...]
    evidence_level: str
    inferred: bool
    summary: str
    tags: tuple[str, ...]
    aliases: tuple[str, ...]
    use_for: tuple[str, ...]
    source_ref: tuple[str, ...]
    dimension: str
    sub_dimension: str
    dimension_review: dict[str, str] = field(default_factory=dict)
    degree: int = 0
    x: float = 0.0
    y: float = 0.0
    radius: float = 2.4
    cluster_id: str = ""
    label_priority: int = 1


@dataclass(frozen=True)
class StarEdge:
    id: str
    source: str
    target: str
    relation: str = "wikilink"
    family: str = "unclassified"
    inferred: bool = False


@dataclass(frozen=True)
class StarCluster:
    id: str
    title: str
    count: int
    x: float = 0.0
    y: float = 0.0
    color_key: str = ""


@dataclass(frozen=True)
class StarGraph:
    nodes: tuple[StarNode, ...]
    edges: tuple[StarEdge, ...]
    clusters: tuple[StarCluster, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        nodes = []
        for node in self.nodes:
            payload = asdict(node)
            payload["type"] = payload["asset_type"]
            nodes.append(payload)
        return {
            "nodes": nodes,
            "edges": [asdict(edge) for edge in self.edges],
            "clusters": [asdict(cluster) for cluster in self.clusters],
            "stats": {
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                "cluster_count": len(self.clusters),
            },
        }


def _node_from_note(note: Any) -> StarNode:
    metadata = getattr(note, "metadata", {}) or {}
    industries = tuple(getattr(note, "industries", ()) or ()) or infer_industries(note)
    return StarNode(
        id=stable_node_id(note.path),
        path=note.path,
        title=note.title,
        asset_type=note.asset_type,
        status=note.status,
        modified=note.modified,
        industries=industries,
        evidence_level=str(metadata.get("evidence_level", "")),
        inferred=bool(note.inferred),
        summary=getattr(note, "summary", ""),
        tags=_as_strings(metadata.get("tags")),
        aliases=_as_strings(metadata.get("aliases")),
        use_for=_as_strings(metadata.get("use_for")),
        source_ref=_as_strings(metadata.get("source_ref")),
        dimension=str(metadata.get("dimension", "")),
        sub_dimension=str(metadata.get("sub_dimension", "")),
        dimension_review=metadata.get("dimension_review", {}) if isinstance(metadata.get("dimension_review", {}), dict) else {},
    )


def _link_key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", unquote(value))).removesuffix(".md")


def _target_index(notes: Iterable[Any]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for note in notes:
        node_id = stable_node_id(note.path)
        parts = note.path.split("/")
        names = {note.title, *_as_strings((getattr(note, "metadata", {}) or {}).get("aliases"))}
        names.update("/".join(parts[i:]).removesuffix(".md") for i in range(len(parts)))
        for name in names:
            index.setdefault("exact:" + str(name).strip(), set()).add(node_id)
            index.setdefault("normalized:" + _link_key(str(name)), set()).add(node_id)
    return index


def _resolve_target(index: dict[str, set[str]], value: str, source_path: str) -> str | None:
    raw = unquote(value).removesuffix(".md")
    if "/" not in raw:
        local = posixpath.join(posixpath.dirname(source_path), raw)
        for key in ("exact:" + local, "normalized:" + _link_key(local)):
            matches = index.get(key, set())
            if len(matches) == 1:
                return next(iter(matches))
    if raw.startswith(("./", "../")):
        raw = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), raw))
    for key in ("exact:" + raw, "normalized:" + _link_key(raw)):
        matches = index.get(key, set())
        if matches:
            return next(iter(matches)) if len(matches) == 1 else None
    return None


def _relation_target(raw: Any) -> str:
    value = str(raw).strip()
    match = re.search(r"\[\[([^\]]+)\]\]", value)
    if match:
        value = match.group(1)
    return value.split("|", 1)[0].split("#", 1)[0].strip().removesuffix(".md")


def _explicit_relations(metadata: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    relations = metadata.get("relations") or {}
    if not isinstance(relations, dict):
        return ()
    result: list[tuple[str, str]] = []
    for relation, raw_targets in relations.items():
        for raw_target in _as_strings(raw_targets):
            target = _relation_target(raw_target)
            if target:
                result.append((str(relation), target))
    return tuple(result)


def build_full_graph(notes: list[Any]) -> StarGraph:
    nodes = tuple(_node_from_note(note) for note in notes)
    targets = _target_index(notes)
    edge_keys: set[tuple[str, str]] = set()
    semantic_pairs: set[frozenset[str]] = set()
    edges: list[StarEdge] = []
    degrees: dict[str, int] = {node.id: 0 for node in nodes}
    node_types = {node.id: node.asset_type for node in nodes}
    for note in notes:
        atom = stable_node_id(note.path)
        metadata = getattr(note, "metadata", {}) or {}
        for raw_target in _as_strings(metadata.get("source_ref")):
            normalized = _relation_target(raw_target)
            source = _resolve_target(targets, normalized, note.path)
            if not source or source == atom:
                continue
            key = (source, atom)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            semantic_pairs.add(frozenset((source, atom)))
            edge_id = hashlib.sha1(f"{source}>{atom}:decomposes_to".encode("utf-8")).hexdigest()[:14]
            edges.append(StarEdge(id=edge_id, source=source, target=atom, relation="decomposes_to", family="source"))
            degrees[source] += 1
            degrees[atom] += 1
    for note in notes:
        source = stable_node_id(note.path)
        metadata = getattr(note, "metadata", {}) or {}
        declared = tuple(("decomposes_to", _relation_target(t)) for t in metadata.get("_declared_outputs", ()))
        declared = tuple((r,t) for r,t in declared if node_types.get(_resolve_target(targets,t,note.path)) in {"拆解记录","知识原子"})
        for relation, normalized in _explicit_relations(metadata) + declared:
            target = _resolve_target(targets, normalized, note.path)
            if not target or target == source:
                continue
            pair = frozenset((source, target))
            allowed = {"derived_from", "decomposes_to", "supports", "validates", "governs", "applies_to", "composed_of", "precedes", "data_input", "data_output", "conflicts_with", "replaces"}
            if relation not in allowed:
                continue
            family = "source" if relation in {"derived_from", "decomposes_to"} else "call"
            if relation == "derived_from":
                edge_source, edge_target, relation = target, source, "decomposes_to"
            else:
                edge_source, edge_target = source, target
            if any(e.source == edge_source and e.target == edge_target and e.relation == relation for e in edges):
                continue
            key = (source, target, relation)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            semantic_pairs.add(pair)
            edge_id = hashlib.sha1(f"{source}>{target}:{relation}".encode("utf-8")).hexdigest()[:14]
            edges.append(StarEdge(id=edge_id, source=edge_source, target=edge_target, relation=relation, family=family))
            degrees[source] += 1
            degrees[target] += 1
    for note in notes:
        source = stable_node_id(note.path)
        for raw_target in getattr(note, "links", ()):
            normalized = str(raw_target).split("|", 1)[0].split("#", 1)[0].strip().removesuffix(".md")
            target = _resolve_target(targets, normalized, note.path)
            if not target or target == source:
                continue
            if frozenset((source, target)) in semantic_pairs:
                continue
            key = (source, target)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            edge_id = hashlib.sha1(f"{source}>{target}".encode("utf-8")).hexdigest()[:14]
            edges.append(StarEdge(id=edge_id, source=source, target=target, family="unclassified"))
            degrees[source] += 1
            degrees[target] += 1
    nodes = tuple(replace(node, degree=degrees[node.id]) for node in nodes)
    return StarGraph(nodes=nodes, edges=tuple(edges))


CLUSTER_ORDER = (
    "来源资料",
    "拆解记录",
    "知识原子",
    "专题知识",
    "需求记录",
    "方案成果",
    "验证反馈",
    "治理规则",
    "运行记录",
)


def relationship_diagnostics(notes: list[Any], graph: StarGraph) -> dict[str, Any]:
    """Expose unresolved references and suspicious provenance; never invent repairs."""
    index = _target_index(notes)
    issues, seen = [], set()
    for note in notes:
        metadata = getattr(note, "metadata", {}) or {}
        refs = [*_as_strings(metadata.get("source_ref")), *getattr(note, "links", ()), *(t for _, t in _explicit_relations(metadata))]
        for raw in refs:
            target = _relation_target(raw)
            key = (note.path, target)
            if not target or key in seen:
                continue
            seen.add(key)
            if not _resolve_target(index, target, note.path):
                issues.append({"path": note.path, "reference": str(raw), "kind": "unresolved", "reason": "未能唯一定位到图中笔记；附件、占位或重名需核对"})
    by_id = {n.id:n for n in graph.nodes}
    source_pairs = {(e.source,e.target) for e in graph.edges if e.family == "source"}
    for source, target in sorted(source_pairs):
        a,b = by_id[source],by_id[target]
        if b.asset_type == "来源资料" and a.asset_type in {"拆解记录","知识原子"}:
            issues.append({"path":b.path,"reference":a.path,"kind":"source_direction","reason":"拆解/原子被登记为原始资料来源，需核对方向"})
        if (target,source) in source_pairs and source < target:
            issues.append({"path":a.path,"reference":b.path,"kind":"source_cycle","reason":"相互登记为来源，需核对引用与拆解的区别"})
    return {"issues":issues,"issue_count":len(issues),"unclassified_atoms":sum(n.asset_type=="知识原子" and not n.dimension for n in graph.nodes)}


def source_diagnostics(notes: list[Any], graph: StarGraph) -> dict[str, Any]:
    targets = _target_index(notes)
    unresolved = []
    for note in notes:
        for raw in _as_strings((getattr(note,"metadata",{}) or {}).get("source_ref")):
            target = _relation_target(raw)
            if target and not _resolve_target(targets,target,note.path):
                unresolved.append({"path":note.path,"reference":str(raw),"reason":"无法唯一定位到图中笔记"})
    outgoing: dict[str,set[str]] = {}
    by_id = {n.id:n for n in graph.nodes}
    for edge in graph.edges:
        if edge.family == "source":
            outgoing.setdefault(edge.source,set()).add(edge.target)
    chains=[]
    for node in graph.nodes:
        if node.asset_type != "来源资料": continue
        seen={node.id};queue=[node.id];atoms=set()
        while queue:
            current=queue.pop()
            for target in outgoing.get(current,set()):
                if target in seen: continue
                seen.add(target)
                if by_id[target].asset_type == "知识原子": atoms.add(target)
                elif by_id[target].asset_type in {"来源资料","拆解记录"}: queue.append(target)
        chains.append({"id":node.id,"path":node.path,"atom_count":len(atoms),"linked_atoms":sorted(atoms)})
    return {"unresolved":unresolved,"chains":chains,"source_count":len(chains),"with_atoms":sum(c['atom_count']>0 for c in chains)}


def layout_star_graph(graph: StarGraph, width: int = 2400, height: int = 1600) -> StarGraph:
    grouped: dict[str, list[StarNode]] = {}
    for node in graph.nodes:
        grouped.setdefault(node.asset_type or "运行记录", []).append(node)
    ordered_types = [kind for kind in CLUSTER_ORDER if kind in grouped]
    ordered_types.extend(sorted(kind for kind in grouped if kind not in ordered_types))
    center_x, center_y = width / 2, height / 2
    orbit_x, orbit_y = min(width * 0.44, 1060), min(height * 0.39, 625)
    preferred_centers = {
        "来源资料": (560, 300),
        "拆解记录": (920, 400),
        "知识原子": (1450, 420),
        "专题知识": (1940, 250),
        "需求记录": (500, 980),
        "方案成果": (1740, 1080),
        "验证反馈": (1100, 1320),
        "治理规则": (260, 610),
        "运行记录": (2180, 760),
    }
    clusters: list[StarCluster] = []
    laid_out: list[StarNode] = []
    golden_angle = math.pi * (3 - math.sqrt(5))
    total_clusters = max(1, len(ordered_types))
    for cluster_index, kind in enumerate(ordered_types):
        angle = -math.pi / 2 + 2 * math.pi * cluster_index / total_clusters
        cx, cy = preferred_centers.get(
            kind,
            (center_x + orbit_x * math.cos(angle), center_y + orbit_y * math.sin(angle)),
        )
        members = sorted(grouped[kind], key=lambda item: (-item.degree, item.id))
        clusters.append(StarCluster(id=kind, title=kind, count=len(members), x=round(cx, 2), y=round(cy, 2), color_key=kind))
        for member_index, node in enumerate(members):
            digest = int(node.id[:8], 16)
            jitter = ((digest % 1000) / 1000 - 0.5) * 0.36
            radius_from_center = 22 + 15.5 * math.sqrt(member_index)
            member_angle = member_index * golden_angle + jitter
            x = min(width, max(0, cx + radius_from_center * math.cos(member_angle)))
            y = min(height, max(0, cy + radius_from_center * math.sin(member_angle)))
            node_radius = min(10.0, 2.4 + math.log2(node.degree + 1) * 1.2)
            priority = 4 if node.degree >= 15 or "canonical" in node.title.lower() else 3 if node.degree >= 6 else 2 if node.degree else 1
            laid_out.append(
                replace(
                    node,
                    x=round(x, 2),
                    y=round(y, 2),
                    radius=round(node_radius, 2),
                    cluster_id=kind,
                    label_priority=priority,
                )
            )
    laid_out.sort(key=lambda item: item.id)
    return StarGraph(nodes=tuple(laid_out), edges=graph.edges, clusters=tuple(clusters))
