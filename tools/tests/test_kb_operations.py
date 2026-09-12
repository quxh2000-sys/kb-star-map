from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "kb_operations.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_operations", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 kb_operations.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OperationTaskTests(unittest.TestCase):
    def setUp(self):
        self.operations = load_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Path(self.temp_dir.name) / "运营任务草稿"
        self.now = datetime(2026, 9, 8, 10, 30, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_task_writes_versioned_draft_atomically(self):
        task = self.operations.create_task(self.store, "PMSmart资料运营", now=self.now)

        saved = json.loads((self.store / f"{task['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["title"], "PMSmart资料运营")
        self.assertEqual(saved["status"], "draft")
        self.assertEqual(saved["stage"], "资料读取")
        self.assertEqual(saved["sources"], [])
        self.assertEqual(saved["created_at"], "2026-09-08T10:30:00+00:00")
        self.assertEqual(list(self.store.glob("*.tmp")), [])

    def test_attach_source_is_idempotent_and_latest_task_is_restored(self):
        task = self.operations.create_task(self.store, "测试任务", now=self.now)
        source = {
            "path": "知识库/00_原始资料库/资料.md",
            "title": "资料",
            "node_id": "n1",
        }

        first = self.operations.attach_source(self.store, task["id"], source, now=self.now)
        second = self.operations.attach_source(self.store, task["id"], source, now=self.now)

        self.assertEqual(first["sources"], second["sources"])
        self.assertEqual(len(second["sources"]), 1)
        self.assertEqual(self.operations.latest_task(self.store)["id"], task["id"])

    def test_load_task_rejects_invalid_task_id(self):
        with self.assertRaisesRegex(ValueError, "任务ID无效"):
            self.operations.load_task(self.store, "../outside")

    def test_export_task_contains_only_relative_source_paths(self):
        task = self.operations.create_task(self.store, "导出测试", now=self.now)
        self.operations.attach_source(
            self.store,
            task["id"],
            {
                "path": "知识库/00_原始资料库/资料.md",
                "title": "资料",
                "node_id": "n1",
            },
            now=self.now,
        )

        exported = self.operations.export_task(self.store, task["id"])

        self.assertTrue(
            all(not Path(item["path"]).is_absolute() for item in exported["sources"])
        )

    def test_ingest_markdown_preserves_source_and_extracted_text(self):
        task = self.operations.create_task(self.store, "资料解析", now=self.now)

        updated, added = self.operations.ingest_file(
            self.store,
            task["id"],
            "测试资料.md",
            "# 测试资料\n\n这是用于分解的真实内容。".encode(),
            now=self.now,
        )

        self.assertTrue(added)
        source = updated["sources"][0]
        self.assertEqual(source["kind"], "uploaded")
        self.assertEqual(source["parse_status"], "parsed")
        self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(source["char_count"], 10)
        self.assertIn("测试资料", source["excerpt"])
        self.assertTrue((self.store / source["stored_path"]).is_file())
        self.assertTrue((self.store / source["extracted_path"]).is_file())

    def test_ingest_file_is_idempotent_by_sha256(self):
        task = self.operations.create_task(self.store, "资料去重", now=self.now)
        content = "# 同一资料\n\n重复上传只保留一份。".encode()

        first, first_added = self.operations.ingest_file(
            self.store, task["id"], "资料.md", content, now=self.now
        )
        second, second_added = self.operations.ingest_file(
            self.store, task["id"], "副本.md", content, now=self.now
        )

        self.assertTrue(first_added)
        self.assertFalse(second_added)
        self.assertEqual(len(first["sources"]), 1)
        self.assertEqual(len(second["sources"]), 1)

    def test_decompose_task_creates_source_backed_candidates_and_detects_exact_title(self):
        task = self.operations.create_task(self.store, "分解查重", now=self.now)
        content = (
            "# 安全管理要求\n\n项目必须建立安全责任清单，并按日检查隐患整改闭环，"
            "所有记录需要保留来源和责任人。\n\n"
            "# 进度协同场景\n\n项目团队需要统一计划、产值和现场进度口径，"
            "通过周计划与日任务形成持续纠偏，并保留偏差原因和责任记录。"
        ).encode()
        task, _ = self.operations.ingest_file(
            self.store, task["id"], "运营资料.md", content, now=self.now
        )

        updated = self.operations.decompose_task(
            self.store,
            task["id"],
            [{"id": "existing-1", "title": "安全管理要求", "path": "知识库/安全管理要求.md"}],
            now=self.now,
        )

        self.assertGreaterEqual(len(updated["candidates"]), 2)
        exact = next(item for item in updated["candidates"] if item["title"] == "安全管理要求")
        self.assertEqual(exact["disposition"], "merge")
        self.assertEqual(exact["matched_node_id"], "existing-1")
        self.assertTrue(exact["source_id"])
        self.assertTrue(exact["excerpt"])
        self.assertEqual(exact["knowledge_type"], "知识原子")
        self.assertEqual(exact["dimension"], "规则维度")

    def test_update_candidate_allows_only_review_fields_and_valid_dispositions(self):
        task = self.operations.create_task(self.store, "候选处置", now=self.now)
        task, _ = self.operations.ingest_file(
            self.store,
            task["id"],
            "资料.md",
            ("# 产品能力\n\n这是一个超过四十个字符的产品能力说明，用于验证候选处置字段和边界控制，"
             "并保留原始来源摘录。").encode(),
            now=self.now,
        )
        task = self.operations.decompose_task(self.store, task["id"], [], now=self.now)
        candidate_id = task["candidates"][0]["id"]

        updated = self.operations.update_candidate(
            self.store,
            task["id"],
            candidate_id,
            {"disposition": "update", "evidence_level": "2", "use_for": "售前方案"},
            now=self.now,
        )

        candidate = next(item for item in updated["candidates"] if item["id"] == candidate_id)
        self.assertEqual(candidate["disposition"], "update")
        self.assertEqual(candidate["use_for"], "售前方案")
        with self.assertRaisesRegex(ValueError, "候选更新字段无效"):
            self.operations.update_candidate(
                self.store, task["id"], candidate_id, {"source_id": "tampered"}
            )

    def test_add_relation_requires_allowed_type_and_is_idempotent(self):
        task = self.operations.create_task(self.store, "关系规则", now=self.now)

        first = self.operations.add_relation(
            self.store,
            task["id"],
            "source-a",
            "target-b",
            "来源支持",
            "来源段落直接支持目标知识",
            now=self.now,
        )
        second = self.operations.add_relation(
            self.store,
            task["id"],
            "source-a",
            "target-b",
            "来源支持",
            "来源段落直接支持目标知识",
            now=self.now,
        )

        self.assertEqual(len(first["relations"]), 1)
        self.assertEqual(first["relations"], second["relations"])
        with self.assertRaisesRegex(ValueError, "关系类型无效"):
            self.operations.add_relation(
                self.store, task["id"], "a", "b", "随意关联", "没有依据"
            )

    def test_analyze_requirement_layers_text_and_recalls_matching_knowledge(self):
        task = self.operations.create_task(self.store, "需求理解", now=self.now)
        knowledge = [
            {
                "id": "k1",
                "title": "安全生产责任闭环",
                "path": "知识库/安全生产责任闭环.md",
                "asset_type": "规则",
                "tags": ["安全管理"],
                "summary": "隐患整改闭环和责任清单",
                "evidence_level": "2",
            },
            {
                "id": "k2",
                "title": "物资库存分析",
                "path": "知识库/物资库存分析.md",
                "asset_type": "原子组件",
                "tags": ["物资"],
                "summary": "库存盘点",
                "evidence_level": "2",
            },
        ]

        updated = self.operations.analyze_requirement(
            self.store,
            task["id"],
            "客户现状是安全责任分散。目标是建立安全责任清单和隐患整改闭环。必须保留整改证据。现场责任人尚未明确。",
            knowledge,
            now=self.now,
        )

        kinds = {item["kind"] for item in updated["requirements"]}
        self.assertTrue({"fact", "goal", "constraint", "unknown"}.issubset(kinds))
        self.assertEqual(updated["retrievals"][0]["node_id"], "k1")
        self.assertEqual(updated["stage"], "需求理解与知识召回")

    def test_build_knowledge_package_keeps_sources_candidates_retrievals_and_gaps(self):
        task = self.operations.create_task(self.store, "知识包", now=self.now)
        task, _ = self.operations.ingest_file(
            self.store,
            task["id"],
            "资料.md",
            ("# 安全责任闭环\n\n客户需要建立安全责任清单和隐患整改闭环，必须保留来源证据，"
             "用于后续售前方案知识准备。" * 2).encode(),
            now=self.now,
        )
        task = self.operations.decompose_task(self.store, task["id"], [], now=self.now)
        task = self.operations.analyze_requirement(
            self.store,
            task["id"],
            "目标是建立安全责任清单和整改闭环。",
            [{"id": "k1", "title": "安全责任闭环", "path": "知识库/k1.md", "asset_type": "规则", "tags": [], "summary": "整改闭环", "evidence_level": "2"}],
            now=self.now,
        )

        updated = self.operations.build_knowledge_package(
            self.store, task["id"], now=self.now
        )

        package = updated["knowledge_package"]
        self.assertEqual(package["schema_version"], 1)
        self.assertEqual(package["task_id"], task["id"])
        self.assertEqual(len(package["sources"]), 1)
        self.assertTrue(package["candidates"])
        self.assertTrue(package["retrievals"])
        self.assertIn("gaps", package)
        self.assertTrue((self.store / updated["knowledge_package_path"]).is_file())
        self.assertEqual(updated["stage"], "输出准备与售前交接")


if __name__ == "__main__":
    unittest.main()
