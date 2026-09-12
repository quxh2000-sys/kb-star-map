from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).resolve().parents[1] / "kb_star_graph.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_star_graph", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载kb_star_graph.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def note(path, title, asset_type="原子组件", links=None, metadata=None, **kwargs):
    values = {
        "path": path,
        "title": title,
        "asset_type": asset_type,
        "status": "Active",
        "modified": "2026-09-08",
        "summary": f"{title}摘要",
        "links": links or [],
        "metadata": metadata or {},
        "inferred": False,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


class FullGraphTests(unittest.TestCase):
    def test_relationship_audit_exposes_unresolved_links_and_reverse_source_direction(self):
        module=load_module()
        raw=note('raw.md','原文','来源资料',metadata={'source_ref':['[[拆解]]'],'dimension_review':{'行业维度':'not_in_source'}})
        split=note('split.md','拆解','拆解记录',links=['不存在'])
        graph=module.build_full_graph([raw,split])
        audit=module.relationship_diagnostics([raw,split],graph)
        self.assertEqual({i['kind'] for i in audit['issues']},{'unresolved','source_direction'})
        self.assertEqual(graph.to_dict()['nodes'][0]['dimension_review'],{'行业维度':'not_in_source'})

    def test_source_path_normalizes_brackets_without_falling_back_to_another_readme(self):
        module = load_module()
        wrong = note('知识库/00_原始资料库/无关/README.md','README','来源资料')
        raw = note('知识库/00_原始资料库/PMSmart (基建)-V2/README.md','原始资料索引','来源资料')
        split = note('知识库/03_存量方案拆解区/V2.md','拆解记录','拆解记录', metadata={'source_ref':['[[00_原始资料库/PMSmart（基建）-V2/README]]']})
        graph=module.build_full_graph([wrong,raw,split])
        self.assertEqual([(e.source,e.target) for e in graph.edges],[(module.stable_node_id(raw.path),module.stable_node_id(split.path))])

    def test_unknown_path_is_not_connected_to_a_generic_readme(self):
        module=load_module()
        raw=note('无关/README.md','README','来源资料')
        split=note('拆解.md','拆解','拆解记录',metadata={'source_ref':['[[不存在/README]]']})
        graph=module.build_full_graph([raw,split])
        self.assertEqual(len(graph.edges),0)

    def test_two_semantics_between_same_notes_are_not_lost(self):
        graph_module = load_module()
        a = note('a.md', 'A', metadata={'source_ref':['[[B]]'], 'relations':{'supports':['[[B]]'], 'random':['[[B]]']}})
        b = note('b.md', 'B')
        graph = graph_module.build_full_graph([a,b])
        self.assertEqual(sorted((e.relation, e.family) for e in graph.edges), [('decomposes_to','source'),('supports','call')])

    def setUp(self):
        self.graph = load_module()

    def test_build_full_graph_keeps_every_note(self):
        notes = [note(f"知识库/{index}.md", f"节点{index}") for index in range(240)]

        graph = self.graph.build_full_graph(notes)

        self.assertEqual(len(graph.nodes), 240)
        self.assertEqual({node.path for node in graph.nodes}, {item.path for item in notes})

    def test_build_full_graph_resolves_aliases_and_deduplicates_edges(self):
        source = note("a.md", "来源", links=["目标别名", "目标别名"])
        target = note("b.md", "目标", metadata={"aliases": ["目标别名"]})

        graph = self.graph.build_full_graph([source, target])

        self.assertEqual(
            [(edge.source, edge.target) for edge in graph.edges],
            [(self.graph.stable_node_id("a.md"), self.graph.stable_node_id("b.md"))],
        )

    def test_node_payload_keeps_nine_dimension_fields(self):
        product = note(
            "产品.md",
            "产品",
            asset_type="产品",
            metadata={"dimension": "产品维度", "sub_dimension": "平台产品"},
        )

        payload = self.graph.build_full_graph([product]).to_dict()["nodes"][0]

        self.assertEqual(payload["dimension"], "产品维度")
        self.assertEqual(payload["sub_dimension"], "平台产品")

    def test_source_ref_creates_semantic_edge_from_source_to_product(self):
        source = note("来源.md", "来源", asset_type="原始资料")
        product = note(
            "产品.md",
            "产品",
            asset_type="产品",
            links=["来源"],
            metadata={"source_ref": ["[[来源]]"], "dimension": "产品维度"},
        )

        graph = self.graph.build_full_graph([source, product])

        self.assertEqual(len(graph.edges), 1)
        edge = graph.edges[0]
        self.assertEqual(edge.source, self.graph.stable_node_id("来源.md"))
        self.assertEqual(edge.target, self.graph.stable_node_id("产品.md"))
        self.assertEqual(edge.relation, "decomposes_to")
        self.assertEqual(edge.family, "source")

    def test_explicit_relations_create_call_family_edges(self):
        product = note(
            "产品.md",
            "产品",
            asset_type="知识原子",
            links=["业务闭环"],
            metadata={"relations": {"supports": ["[[业务闭环]]"]}},
        )
        business = note("业务.md", "业务闭环", asset_type="知识原子")

        graph = self.graph.build_full_graph([product, business])

        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.edges[0].relation, "supports")
        self.assertEqual(graph.edges[0].family, "call")

    def test_layout_is_deterministic_and_inside_canvas(self):
        notes = [
            note(f"{kind}/{index}.md", f"{kind}{index}", asset_type=kind)
            for kind in ("原始资料", "原子组件", "规则", "产品", "项目", "成交证据", "输出")
            for index in range(15)
        ]

        first = self.graph.layout_star_graph(self.graph.build_full_graph(notes)).to_dict()
        second = self.graph.layout_star_graph(self.graph.build_full_graph(notes)).to_dict()

        self.assertEqual(first["nodes"], second["nodes"])
        self.assertTrue(all(0 <= item["x"] <= 2400 for item in first["nodes"]))
        self.assertTrue(all(0 <= item["y"] <= 1600 for item in first["nodes"]))
        self.assertTrue(all(item["cluster_id"] for item in first["nodes"]))

    def test_layout_separates_cluster_centers(self):
        notes = [
            note(f"{kind}/{index}.md", f"{kind}{index}", asset_type=kind)
            for kind in ("来源资料", "拆解记录", "知识原子", "专题知识", "需求记录", "方案成果", "验证反馈", "治理规则")
            for index in range(4)
        ]

        graph = self.graph.layout_star_graph(self.graph.build_full_graph(notes))
        centers = [(cluster.x, cluster.y) for cluster in graph.clusters]
        distances = [
            ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            for index, a in enumerate(centers)
            for b in centers[index + 1 :]
        ]

        self.assertGreaterEqual(min(distances), 260)

    def test_layout_uses_uniform_knowledge_type_order(self):
        order = ("来源资料", "拆解记录", "知识原子", "专题知识", "需求记录", "方案成果", "验证反馈", "治理规则", "运行记录")
        notes = [note(f"{kind}.md", kind, asset_type=kind) for kind in order]

        graph = self.graph.layout_star_graph(self.graph.build_full_graph(notes))

        self.assertEqual(tuple(cluster.id for cluster in graph.clusters), order)

    def test_node_infers_industries_from_title_path_and_tags(self):
        source = note(
            "知识库/铁路/隧道标准.md",
            "铁路隧道标准",
            metadata={"tags": ["公路", "施工标准"]},
        )

        graph = self.graph.build_full_graph([source])

        self.assertEqual(set(graph.nodes[0].industries), {"公路", "铁路"})



class LayoutNoClampTests(unittest.TestCase):
    """布局不得把越界节点压到画布边界上。

    实测事故：运行记录 861 个节点按黄金角螺旋铺开后超出 x=2400，
    原实现逐个 min/max 夹取，把 190 个节点全压到 x=2400，
    在图上表现为一条与主团分离的竖直细线（用户圈出来了）。
    """

    WIDTH, HEIGHT = 2400, 1600

    def _graph(self, counts: dict[str, int]):
        module = load_module()
        # note(...) 是本文件顶部的构造辅助（asset_type 即聚类类型）
        notes = []
        for kind, n in counts.items():
            for i in range(n):
                notes.append(note(f"{kind}/{kind}-{i:04d}.md", f"{kind}-{i:04d}", asset_type=kind))
        return module.build_full_graph(notes)

    def test_no_node_lands_on_the_canvas_border(self):
        module = load_module()
        graph = module.layout_star_graph(
            self._graph({"运行记录": 900, "知识原子": 120, "来源资料": 60}),
            width=self.WIDTH, height=self.HEIGHT,
        )
        on_border = [
            n.id for n in graph.nodes
            if n.x <= 0.01 or n.y <= 0.01 or n.x >= self.WIDTH - 0.01 or n.y >= self.HEIGHT - 0.01
        ]
        self.assertEqual(on_border, [], f"{len(on_border)} 个节点被压在画布边界上（会形成竖线/横线）")

    def test_every_node_stays_within_the_canvas(self):
        module = load_module()
        graph = module.layout_star_graph(self._graph({"运行记录": 900}), width=self.WIDTH, height=self.HEIGHT)
        for n in graph.nodes:
            self.assertGreater(n.x, 0)
            self.assertLess(n.x, self.WIDTH)
            self.assertGreater(n.y, 0)
            self.assertLess(n.y, self.HEIGHT)

    def test_relative_shape_is_preserved(self):
        """只做等比缩放平移——聚类之间与节点之间的相对位置不能变形。"""
        module = load_module()
        raw = module.layout_star_graph(self._graph({"运行记录": 900, "知识原子": 120}),
                                      width=self.WIDTH, height=self.HEIGHT)
        groups = {}
        for n in raw.nodes:
            groups.setdefault(n.cluster_id, []).append(n)
        for kind, members in groups.items():
            xs = [n.x for n in members]; ys = [n.y for n in members]
            w = max(xs) - min(xs); h = max(ys) - min(ys)
            if w > 1 and h > 1:
                ratio = w / h
                self.assertGreater(ratio, 0.7, f"{kind} 被压扁了（纵横比 {ratio:.2f}）")
                self.assertLess(ratio, 1.4, f"{kind} 被拉长了（纵横比 {ratio:.2f}）")

    def test_layout_is_deterministic(self):
        module = load_module()
        first = module.layout_star_graph(self._graph({"运行记录": 300, "知识原子": 80}),
                                        width=self.WIDTH, height=self.HEIGHT)
        second = module.layout_star_graph(self._graph({"运行记录": 300, "知识原子": 80}),
                                         width=self.WIDTH, height=self.HEIGHT)
        self.assertEqual([(n.id, n.x, n.y) for n in first.nodes],
                         [(n.id, n.x, n.y) for n in second.nodes])



if __name__ == "__main__":
    unittest.main()
