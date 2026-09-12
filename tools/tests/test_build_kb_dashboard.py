from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "build_kb_dashboard.py"
SERVER_PATH = Path(__file__).resolve().parents[1] / "serve_kb_dashboard.py"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "kb_dashboard_template.html"


def load_module():
    spec = importlib.util.spec_from_file_location("build_kb_dashboard", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 build_kb_dashboard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_server_module():
    spec = importlib.util.spec_from_file_location("serve_kb_dashboard", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 serve_kb_dashboard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class VaultScanTests(unittest.TestCase):
    def test_pending_outputs_are_not_presented_as_completed_decomposition(self):
        module=load_module()
        with tempfile.TemporaryDirectory() as tmp:
            vault=Path(tmp)
            source=vault/'source.md'
            source.write_text('---\nknowledge_type: 拆解记录\n---\n## 原子落库清单\n- [x] [[已完成]]\n- [ ] [[未完成]]\n- [[候选]]（待人工复核）\n',encoding='utf-8')
            parsed=module.parse_note(source,vault)
            self.assertEqual(parsed.metadata['_declared_outputs'],['已完成'])

    def test_declared_outputs_complete_raw_to_atom_chain(self):
        module=load_module()
        with tempfile.TemporaryDirectory() as tmp:
            vault=Path(tmp)
            source=vault/'知识库/00_原始资料库/资料/README.md';source.parent.mkdir(parents=True)
            source.write_text('---\ntitle: 资料\n---\n## 关联拆解\n[[拆解]]\n## 普通推荐\n[[无关]]',encoding='utf-8')
            (vault/'拆解.md').write_text('---\nknowledge_type: 拆解记录\n---\n## 原子落库清单\n[[原子]]',encoding='utf-8')
            (vault/'原子.md').write_text('---\nknowledge_type: 知识原子\n---\n# 原子',encoding='utf-8')
            data=module.build_dashboard_data(vault,module.scan_vault(vault))
            self.assertEqual(data['graph']['sourceDiagnostics']['with_atoms'],1)
            self.assertEqual(sum(e['family']=='source' for e in data['graph']['edges']),2)

    def test_bad_legacy_source_file_does_not_erase_valid_source_ref(self):
        module = load_module()
        metadata, _, warning = module._split_frontmatter('---\ntitle: 样本\nsource_file: [附件](file:///legacy)\nsource_ref:\n- "[[来源]]"\nknowledge_type: 知识原子\n---\n正文')
        self.assertEqual(metadata.get('source_ref'), ['[[来源]]'])
        self.assertEqual(metadata.get('knowledge_type'), '知识原子')
        self.assertIsNotNone(warning)

    def setUp(self):
        self.dashboard = load_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scan_vault_excludes_runtime_dirs_and_parses_note(self):
        (self.vault / ".trash").mkdir()
        (self.vault / ".trash" / "ignored.md").write_text("# ignored", encoding="utf-8")
        runtime = self.vault / "系统" / "知识库可视化工作台" / "运营任务草稿" / "assets" / "runtime.md"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("# runtime", encoding="utf-8")
        note = self.vault / "知识库" / "01_原子知识组件库" / "桥梁监测.md"
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\ntitle: 桥梁监测\nstatus: Active\nevidence_level: 2\n"
            "source_ref:\n  - '[[来源材料]]'\n---\n# 桥梁监测\n关联[[监测产品]]",
            encoding="utf-8",
        )

        records = self.dashboard.scan_vault(self.vault)

        self.assertEqual([record.title for record in records], ["桥梁监测"])
        self.assertEqual(records[0].links, ["来源材料", "监测产品"])
        self.assertEqual(records[0].asset_type, "知识原子")
        self.assertEqual(records[0].status, "Active")

    def test_classify_note_uses_metadata_before_path(self):
        kind, inferred = self.dashboard.classify_note("收件箱/例子.md", {"type": "规则卡"})

        self.assertEqual(kind, "知识原子")
        self.assertFalse(inferred)

    def test_classify_note_marks_path_fallback_as_inferred(self):
        kind, inferred = self.dashboard.classify_note("知识库/97_成交证据/案例.md", {})

        self.assertEqual(kind, "知识原子")
        self.assertTrue(inferred)

    def test_classify_note_uses_nine_dimension_before_path(self):
        cases = {dimension: "知识原子" for dimension in (
            "行业维度", "工程维度", "客户维度", "产品维度", "管理维度",
            "资源维度", "业务维度", "案例维度", "规则维度",
        )}

        for dimension, expected in cases.items():
            with self.subTest(dimension=dimension):
                kind, inferred = self.dashboard.classify_note(
                    "收件箱/待分类.md",
                    {"asset_type": "原子组件", "dimension": dimension},
                )
                self.assertEqual(kind, expected)
                self.assertFalse(inferred)

    def test_classify_note_uses_uniform_knowledge_types(self):
        cases = (
            ("知识库/00_原始资料库/资料.md", {}, "来源资料"),
            ("知识库/03_存量方案拆解区/拆解.md", {}, "拆解记录"),
            ("知识库/01_原子知识组件库/组件.md", {}, "知识原子"),
            ("知识库/96_专题综述/专题.md", {}, "专题知识"),
            ("知识库/05_客户需求库/需求.md", {}, "需求记录"),
            ("知识库/解决方案/方案.md", {}, "方案成果"),
            ("知识库/04_需求与规则引擎/B流程反哺/反馈.md", {}, "验证反馈"),
            ("知识库/04_需求与规则引擎/规则.md", {}, "治理规则"),
            ("日记/2026-09-09.md", {}, "运行记录"),
        )

        for path, metadata, expected in cases:
            with self.subTest(path=path):
                kind, _ = self.dashboard.classify_note(path, metadata)
                self.assertEqual(kind, expected)

    def test_product_design_spec_is_not_classified_as_product_asset(self):
        kind, inferred = self.dashboard.classify_note(
            "收件箱/知识库可视化工作台-设计规格.md", {"type": "产品设计规格"}
        )

        self.assertEqual(kind, "运行记录")
        self.assertTrue(inferred)


class RulesFileBomTests(unittest.TestCase):
    """规则文件是交给同事用记事本改的，带 BOM 的 UTF-8 必须照样能读。"""

    def test_classification_rules_with_bom_are_loaded(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            rules_dir = vault / "系统" / "知识库可视化工作台"
            rules_dir.mkdir(parents=True)
            (rules_dir / "资产分类规则.yaml").write_bytes(
                b"\xef\xbb\xbf"
                + 'fallback: 兜底类\npath_rules:\n  - ["notes/", "来源类型"]\n'.encode("utf-8")
            )
            path, fallback, count = module.load_classification_rules(vault)

        self.assertEqual(count, 1, "带 BOM 时规则条数不应变成 0")
        self.assertEqual(fallback, "兜底类")

    def test_note_with_bom_still_parses_frontmatter(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "带BOM.md"
            note.write_bytes(b"\xef\xbb\xbf" + "---\nknowledge_type: 知识原子\n---\n正文\n".encode("utf-8"))
            record = module.parse_note(note, vault)

        self.assertEqual(record.asset_type, "知识原子", "BOM 不应让 frontmatter 失效")



class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.dashboard = load_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def record(self, path, title, asset_type, metadata=None, links=None):
        return self.dashboard.NoteRecord(
            path=path,
            title=title,
            asset_type=asset_type,
            inferred=True,
            status="Active",
            modified="2026-09-08",
            summary=f"{title}摘要",
            metadata=metadata or {},
            links=links or [],
        )

    def test_build_dashboard_data_counts_assets_and_metadata(self):
        records = [
            self.record(
                "知识库/01_原子知识组件库/a.md",
                "A",
                "原子组件",
                {"source_ref": ["x"], "evidence_level": 2, "use_for": "方案"},
            ),
            self.record("知识库/04_需求与规则引擎/r.md", "R", "规则"),
        ]

        data = self.dashboard.build_dashboard_data(self.vault, records)

        self.assertEqual(data["metrics"]["原子组件"], 1)
        self.assertEqual(data["metrics"]["规则"], 1)
        self.assertEqual(
            data["quality"]["原子组件"]["source_ref"],
            {"filled": 1, "total": 1},
        )

    def test_health_report_missing_is_not_backfilled_as_today(self):
        reports = self.dashboard.parse_health_reports(self.vault)

        self.assertEqual(reports, [])

    def test_health_report_extracts_values_and_issues(self):
        folder = self.vault / "知识库" / "99_更新日志"
        folder.mkdir(parents=True)
        (folder / "2026-09-08-知识库健康巡检.md").write_text(
            "**扫描文件**: 1929 个 Markdown\n"
            "| P1 | 0 |\n| P2 | 46 |\n| frontmatter 异常 | 0 |\n"
            "| Draft 笔记 | 1 |\n| 旁路目录提示 | 2 |\n| 候选断链 | 43 |\n"
            "| P2 | draft | `知识库/草稿.md` | 需要复核 |\n",
            encoding="utf-8",
        )

        reports = self.dashboard.parse_health_reports(self.vault)

        self.assertEqual(reports[-1]["p2"], 46)
        self.assertEqual(reports[-1]["broken_links"], 43)
        self.assertEqual(reports[-1]["issues"][0]["path"], "知识库/草稿.md")

    def test_graph_prefers_products_and_limits_detail_nodes(self):
        records = []
        for index in range(220):
            asset_type = "产品" if index < 20 else "其他笔记"
            records.append(
                self.record(
                    f"知识库/n{index}.md",
                    f"节点{index}",
                    asset_type,
                    {"tags": ["公路"] if index % 2 == 0 else ["铁路"]},
                    [f"节点{index + 1}"] if index < 219 else [],
                )
            )

        graph = self.dashboard.build_graph(records, max_detail_nodes=80)

        self.assertLessEqual(len(graph["detail_nodes"]), 80)
        self.assertTrue(any(node["type"] == "产品" for node in graph["detail_nodes"]))
        node_ids = set(graph["node_ids"])
        self.assertTrue(all(edge["source"] in node_ids for edge in graph["edges"]))
        self.assertTrue(all(edge["target"] in node_ids for edge in graph["edges"]))

    def test_graph_balances_core_asset_types_and_excludes_noise_clusters(self):
        asset_types = ["来源资料", "拆解记录", "知识原子", "专题知识", "需求记录", "方案成果", "验证反馈", "治理规则"]
        records = []
        for asset_type in asset_types:
            for index in range(12):
                records.append(
                    self.record(
                        f"知识库/{asset_type}/{index}.md",
                        f"{asset_type}{index}",
                        asset_type,
                    )
                )
        records.extend(
            self.record(f"日记/{index}.md", f"日记{index}", "运行记录") for index in range(30)
        )

        graph = self.dashboard.build_graph(records, max_detail_nodes=48)

        displayed_types = {node["type"] for node in graph["detail_nodes"]}
        aggregate_titles = {node["title"] for node in graph["aggregate_nodes"]}
        self.assertGreaterEqual(len(displayed_types.intersection(asset_types)), 6)
        self.assertNotIn("运行记录", aggregate_titles)

    def test_dashboard_data_uses_full_graph_without_detail_node_cap(self):
        records = [
            self.record(f"知识库/节点/{index}.md", f"节点{index}", "其他笔记")
            for index in range(230)
        ]

        data = self.dashboard.build_dashboard_data(self.vault, records)

        self.assertEqual(data["graph"]["stats"]["node_count"], 230)
        self.assertEqual(len(data["graph"]["nodes"]), 230)

    def test_graph_omits_generic_readme_nodes(self):
        records = [
            self.record("知识库/产品/README.md", "README", "产品"),
            self.record("知识库/产品/正式产品.md", "正式产品", "产品"),
        ]

        graph = self.dashboard.build_graph(records, max_detail_nodes=10)

        self.assertEqual([node["title"] for node in graph["detail_nodes"]], ["正式产品"])


class DashboardRenderTests(unittest.TestCase):
    def setUp(self):
        self.dashboard = load_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def minimal_data(self):
        return {
            "meta": {
                "title": "知识库可视化工作台",
                "generated_at": "2026-09-08T12:00:00+08:00",
                "vault_name": "2026笔记本地",
                "scanned_notes": 2,
                "parse_warnings": 0,
            },
            "metrics": {"Markdown": 2, "原子组件": 1, "规则": 1, "产品 canonical": 1},
            "quality": {},
            "health": {"latest": None, "trend": []},
            "recent": [],
            "rules": [],
            "graph": {
                "nodes": [],
                "edges": [],
                "clusters": [],
                "stats": {"node_count": 2, "edge_count": 0, "cluster_count": 0},
                "healthByNodeId": {},
            },
            "search": [],
            "warnings": [],
        }

    def test_render_dashboard_is_graph_first_single_file(self):
        template = TEMPLATE_PATH.read_text(encoding="utf-8")

        html_text = self.dashboard.render_dashboard(template, self.minimal_data())
        static_part = html_text.split('<script id="dashboard-data"', 1)[0]
        rail_part = static_part.split('<footer class="operations-rail">', 1)[1].split("</footer>", 1)[0]

        self.assertIn('id="starGraphCanvas"', html_text)
        self.assertNotIn('data-mode="operations"', html_text)
        self.assertNotIn('id="operationsPanel"', html_text)
        self.assertNotIn('<button id="maintenanceToggle"', html_text)
        self.assertNotIn('<button class="lens-toggle" id="lensToggle"', html_text)
        self.assertNotIn('<button class="node-close" id="closeLens"', html_text)
        self.assertNotIn('id="closeMaintenance"', html_text)
        self.assertIn('id="leftWorkspacePanel"', html_text)
        self.assertIn('id="leftTabGraph"', html_text)
        self.assertIn('id="leftTabMaintenance"', html_text)
        self.assertIn('id="graphLensContent"', html_text)
        self.assertIn('id="maintenancePanel"', html_text)
        self.assertIn('data-maintenance-mode="edit"', html_text)
        self.assertIn('data-maintenance-mode="create"', html_text)
        for element_id in (
            "maintenanceEditPath",
            "maintenanceEditContent",
            "maintenanceNewTitle",
            "maintenanceNewType",
            "maintenanceNewDimension",
            "maintenanceNewSubDimension",
            "maintenanceNewPath",
            "maintenanceNewContent",
            "previewMaintenance",
            "maintenanceDiff",
            "commitMaintenance",
            "maintenanceResult",
        ):
            self.assertIn(f'id="{element_id}"', html_text)
        for knowledge_type in ("来源资料", "拆解记录", "知识原子", "专题知识", "需求记录", "方案成果", "验证反馈", "治理规则"):
            self.assertIn(f"<option>{knowledge_type}</option>", html_text)
        self.assertIn('id="operationTitle"', html_text)
        self.assertIn('id="createOperationTask"', html_text)
        self.assertIn('id="exportOperationTask"', html_text)
        for element_id in (
            "operationFiles",
            "uploadOperationFiles",
            "decomposeOperation",
            "candidateList",
            "relationSource",
            "relationTarget",
            "relationType",
            "relationRationale",
            "addOperationRelation",
            "requirementText",
            "analyzeRequirement",
            "requirementResults",
            "buildKnowledgePackage",
            "packageStatus",
        ):
            self.assertIn(f'id="{element_id}"', html_text)
        self.assertNotIn("工作台总览", html_text)
        self.assertNotIn('data-view="monitor"', html_text)
        self.assertNotIn('data-view="rules"', html_text)
        self.assertNotIn("https://", html_text)
        self.assertNotIn("__DASHBOARD_DATA__", html_text)
        self.assertIn('aria-label="全局搜索"', html_text)
        self.assertIn('data-static-fallback="true"', static_part)
        self.assertIn("当前查看环境未启用交互功能", static_part)
        self.assertNotIn("__STATIC_", html_text)
        self.assertIn("data-open-obsidian", html_text)
        self.assertIn('id="editNodeInMaintenance"', html_text)
        for element_id in (
            "relationViewSummary",
        ):
            self.assertIn(f'id="{element_id}"', html_text)
        self.assertIn("/api/open-note", html_text)
        self.assertEqual(html_text.count('class="rail-swatch"'), 5)
        self.assertEqual(html_text.count('class="rail-item"'), 5)
        self.assertEqual(rail_part.count('aria-pressed="true"'), 1)
        self.assertEqual(rail_part.count('aria-pressed="false"'), 4)
        self.assertIn('id="typeLens"', html_text)
        self.assertIn('data-lens-options="type"', html_text)
        self.assertNotIn('<select id="typeLens"', html_text)
        self.assertIn('id="controlsToggle"', html_text)
        self.assertIn('<nav class="graph-controls" id="graphControls" aria-label="星图控制" hidden>', html_text)
        for label in ("当前节点", "待补来源", "低证据", "待治理节点", "最近更新"):
            self.assertIn(f">{label}<", static_part)
        self.assertNotIn(">产品 canonical<", static_part)
        self.assertNotIn(">内部规则<", static_part)
        for color in ("#2f6bff", "#27c2d1", "#8b5cf6", "#f2b84b", "#40536a"):
            self.assertIn(f"--rail-color:{color}", html_text)
        self.assertNotIn("__STAR_GRAPH_CSS__", html_text)
        self.assertNotIn("__STAR_GRAPH_CORE_JS__", html_text)
        self.assertNotIn("__STAR_GRAPH_JS__", html_text)

    def test_embedded_json_escapes_script_end(self):
        data = self.minimal_data()
        data["recent"] = [{"title": "</script><script>alert(1)</script>"}]

        html_text = self.dashboard.render_dashboard(
            '<script type="application/json">__DASHBOARD_DATA__</script>', data
        )

        self.assertNotIn("</script><script>alert(1)</script>", html_text)
        self.assertIn("\\u003c/script", html_text)

    def test_atomic_write_keeps_previous_output_on_replace_error(self):
        output = self.folder / "dashboard.html"
        output.write_text("previous", encoding="utf-8")

        with mock.patch.object(self.dashboard.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.dashboard.write_dashboard_atomic(output, "new")

        self.assertEqual(output.read_text(encoding="utf-8"), "previous")
        self.assertEqual(list(self.folder.glob("*.tmp")), [])

    def test_generated_html_has_no_external_script_or_stylesheet(self):
        template = TEMPLATE_PATH.read_text(encoding="utf-8")

        html_text = self.dashboard.render_dashboard(template, self.minimal_data())

        self.assertIsNone(
            re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://', html_text)
        )

    def test_generate_dashboard_writes_current_fixture_data(self):
        note = self.folder / "知识库" / "01_原子知识组件库" / "测试组件.md"
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\ntitle: 测试组件\nstatus: Active\nsource_ref: ['[[测试来源]]']\n---\n"
            "# 测试组件\n用于验证真实生成链路。",
            encoding="utf-8",
        )
        output = self.folder / "知识库工作台.html"

        summary = self.dashboard.generate_dashboard(self.folder, TEMPLATE_PATH, output)

        html_text = output.read_text(encoding="utf-8")
        self.assertIn("测试组件", html_text)
        self.assertIn('id="starGraphCanvas"', html_text)
        self.assertEqual(summary["scanned"], 1)
        self.assertGreater(summary["html_bytes"], 1000)


class DashboardPathTests(unittest.TestCase):
    def test_source_template_is_outside_share_folder(self):
        dashboard = load_module()

        _, template_path, output_path = dashboard._default_paths()

        self.assertNotEqual(template_path.parent, output_path.parent)
        self.assertEqual(template_path.parent.name, "templates")
        self.assertEqual(output_path.name, "知识库可视化工作台.html")


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.server = load_server_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name)
        self.dashboard_dir = self.vault / "系统" / "知识库可视化工作台"
        self.dashboard_dir.mkdir(parents=True)
        self.note = self.vault / "知识库" / "测试笔记.md"
        self.note.parent.mkdir(parents=True)
        self.note.write_text("# 测试笔记", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resolve_note_path_accepts_existing_markdown_inside_vault(self):
        resolved = self.server.resolve_note_path(self.vault, "知识库/测试笔记.md")

        self.assertEqual(resolved, self.note.resolve())

    def test_resolve_note_path_rejects_path_outside_vault(self):
        with self.assertRaises(ValueError):
            self.server.resolve_note_path(self.vault, "../外部.md")

    def test_build_obsidian_uri_uses_encoded_absolute_path(self):
        uri = self.server.build_obsidian_uri(self.note.resolve())

        self.assertTrue(uri.startswith("obsidian://open?path=%2F"))
        self.assertIn("%E6%B5%8B%E8%AF%95%E7%AC%94%E8%AE%B0.md", uri)

    def test_open_note_falls_back_to_system_default_without_obsidian(self):
        """没装 Obsidian 时必须用系统默认程序打开笔记，而不是静默失败。"""
        opened = []
        with mock.patch.dict(self.server.os.environ, {"KB_OPEN_WITH": "default"}), \
             mock.patch.object(self.server, "open_with_system_default", opened.append):
            result = self.server.open_note(self.vault, self.note)

        self.assertEqual(result, "default")
        self.assertEqual(opened, [str(self.note.resolve())])

    def test_open_note_uses_obsidian_uri_when_forced(self):
        opened = []
        with mock.patch.dict(self.server.os.environ, {"KB_OPEN_WITH": "obsidian"}), \
             mock.patch.object(self.server, "open_with_system_default", opened.append):
            result = self.server.open_note(self.vault, self.note)

        self.assertEqual(result, "obsidian")
        self.assertTrue(opened[0].startswith("obsidian://open?path="))

    def test_obsidian_available_respects_environment_override(self):
        with mock.patch.dict(self.server.os.environ, {"KB_OPEN_WITH": "default"}):
            self.assertFalse(self.server.obsidian_available())
        with mock.patch.dict(self.server.os.environ, {"KB_OPEN_WITH": "obsidian"}):
            self.assertTrue(self.server.obsidian_available())

    def test_open_with_system_default_is_cross_platform(self):
        """Windows 上原本硬编码 /usr/bin/open 会直接失败，这里锁定三平台分支。"""
        calls = []
        with mock.patch.object(self.server.sys, "platform", "darwin"), \
             mock.patch.object(self.server.subprocess, "run", lambda cmd, **kw: calls.append(cmd)):
            self.server.open_with_system_default("obsidian://open?path=%2Fx")
        self.assertEqual(calls[0][0], "/usr/bin/open")

        started = []
        with mock.patch.object(self.server.sys, "platform", "win32"), \
             mock.patch.object(self.server.os, "startfile", lambda t: started.append(t), create=True):
            self.server.open_with_system_default("C:/笔记/a.md")
        self.assertEqual(started, ["C:/笔记/a.md"])

        calls.clear()
        with mock.patch.object(self.server.sys, "platform", "linux"), \
             mock.patch.object(self.server.subprocess, "run", lambda cmd, **kw: calls.append(cmd)):
            self.server.open_with_system_default("/tmp/a.md")
        self.assertEqual(calls[0][0], "xdg-open")

    def test_dashboard_filename_is_safe_for_latin1_response_headers(self):
        """http.server 用 latin-1 编码响应头：中文文件名必须先用 quote 才能进 Location。

        未编码时 GET / 会抛 UnicodeEncodeError 直接 500，首页打不开。
        """
        encoded = "/" + self.server.quote(self.server.DASHBOARD_HTML_NAME)

        encoded.encode("latin-1")  # 不抛异常即说明可安全放入响应头
        self.assertTrue(encoded.startswith("/%"))
        self.assertNotIn(self.server.DASHBOARD_HTML_NAME, encoded)

    def test_pid_path_uses_system_temp_directory(self):
        """原本写死 /private/tmp，Windows 上不存在，会让本地模式启动器直接失败。"""
        pid_path = self.server._pid_path(8765)

        self.assertTrue(str(pid_path).startswith(tempfile.gettempdir()))
        self.assertNotIn("/private/tmp", str(pid_path))

    def test_operations_store_lives_under_dashboard_directory(self):
        store = self.server.operations_store(self.dashboard_dir)

        self.assertEqual(store, self.dashboard_dir / "运营任务草稿")

    def test_static_dashboard_responses_disable_browser_cache(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.parse import quote
        from urllib.request import urlopen

        (self.dashboard_dir / "知识库可视化工作台.html").write_text("ok", encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.server.make_handler(self.vault, self.dashboard_dir))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/{quote('知识库可视化工作台.html')}") as response:
                self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_parse_operation_route_extracts_task_id_and_action(self):
        task_id = "a" * 32

        route = self.server.parse_operation_route(
            f"/api/operations/tasks/{task_id}/sources"
        )

        self.assertEqual(route, (task_id, "sources"))
        self.assertIsNone(self.server.parse_operation_route("/api/operations/tasks/bad/sources"))

    def test_build_source_payload_uses_validated_vault_note(self):
        payload = self.server.build_source_payload(self.vault, "知识库/测试笔记.md")

        self.assertEqual(payload["path"], "知识库/测试笔记.md")
        self.assertEqual(payload["title"], "测试笔记")
        self.assertRegex(payload["node_id"], r"^[0-9a-f]{14}$")

    def test_parse_maintenance_route_and_default_new_note_path(self):
        self.assertEqual(
            self.server.parse_maintenance_route("/api/maintenance/preview"),
            "preview",
        )
        self.assertEqual(
            self.server.parse_maintenance_route("/api/maintenance/commit"),
            "commit",
        )
        self.assertIsNone(
            self.server.parse_maintenance_route("/api/maintenance/unknown")
        )
        self.assertEqual(
            self.server.maintenance_default_path("治理规则", "安全/责任闭环"),
            "知识库/04_需求与规则引擎/维护新增/安全-责任闭环.md",
        )
        self.assertEqual(
            self.server.maintenance_default_path("知识原子", "施工工序"),
            "知识库/01_原子知识组件库/99_待归类/施工工序.md",
        )


class MarkdownLinkExtractionTests(unittest.TestCase):
    """工具面向通用 Markdown 库，标准链接语法必须与 wikilink 同等地产生关系边。"""

    def test_relative_markdown_link_resolves_against_source_directory(self):
        module = load_module()
        self.assertEqual(module.extract_links("[A](概念A.md)", "notes/b.md"), ["notes/概念A"])
        self.assertEqual(module.extract_links("[A](sub/概念A.md)", "notes/b.md"), ["notes/sub/概念A"])
        self.assertEqual(module.extract_links("[A](../术语表.md)", "notes/b.md"), ["术语表"])

    def test_leading_slash_markdown_link_is_treated_as_vault_root(self):
        module = load_module()
        self.assertEqual(module.extract_links("[A](/知识库/概念A.md)", "notes/b.md"), ["知识库/概念A"])

    def test_markdown_link_target_normalization_variants(self):
        module = load_module()
        # 锚点、URL 编码、尖括号空格写法、title 都应归一到同一目标
        for text in (
            "[A](概念A.md#小节)",
            "[A](%E6%A6%82%E5%BF%B5%20A.md)",
            "[A](<概念 A.md>)",
            '[A](概念A.md "标题")',
        ):
            with self.subTest(text=text):
                expected = ["notes/概念 A"] if "A" in text and ("%20" in text or "<" in text) else ["notes/概念A"]
                self.assertEqual(module.extract_links(text, "notes/b.md"), expected)

    def test_non_note_and_external_markdown_links_are_ignored(self):
        module = load_module()
        for text in (
            "[A](https://example.com/y.md)",
            "[A](mailto:a@b.com)",
            "![图](img.md)",
            "[A](附件.pdf)",
            "[A](#小节)",
            "[A](../../外部.md)",
            "[A](规则.md.txt)",
        ):
            with self.subTest(text=text):
                self.assertEqual(module.extract_links(text, "notes/b.md"), [])

    def test_wikilink_and_markdown_link_to_same_target_deduplicate(self):
        module = load_module()
        self.assertEqual(
            module.extract_links("[[notes/概念A]]\n[A](概念A.md)", "notes/b.md"),
            ["notes/概念A"],
        )

    def test_extract_links_without_source_path_treats_link_as_vault_root(self):
        module = load_module()
        self.assertEqual(module.extract_links("[A](知识库/概念A.md)"), ["知识库/概念A"])

    def test_markdown_links_become_edges_in_full_graph(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "notes").mkdir()
            (vault / "notes" / "a.md").write_text("见 [B](b.md) 与 [术语](../术语表.md)\n", encoding="utf-8")
            (vault / "notes" / "b.md").write_text("见 [A](a.md)\n", encoding="utf-8")
            (vault / "术语表.md").write_text("见 [B](notes/b.md)\n", encoding="utf-8")
            records = module.scan_vault(vault)
            graph = module.build_full_graph(records)
            self.assertEqual(len(graph.nodes), 3)
            # a→b、a→术语表、b→a、术语表→b
            self.assertEqual(len(graph.edges), 4)

    def test_markdown_directory_note_is_collected_into_graph(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "索引.md").write_text("- [甲](子目录/甲.md)\n", encoding="utf-8")
            (vault / "子目录").mkdir()
            (vault / "子目录" / "甲.md").write_text("内容\n", encoding="utf-8")
            graph = module.build_full_graph(module.scan_vault(vault))
            labels = {node.title for node in graph.nodes}
            self.assertIn("甲", labels)
            self.assertEqual(len(graph.edges), 1)


if __name__ == "__main__":
    unittest.main()
