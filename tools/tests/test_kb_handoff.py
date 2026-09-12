from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SAMPLE_MARKDOWN = """# 工程项目安全生产责任闭环

客户现状是项目安全责任分散，隐患检查、整改派发和复核记录分别保存在不同表格中，管理人员难以持续追踪责任落实情况。

## 隐患整改闭环要求

整改必须留下派发时间、整改人和复核结论，任何一条隐患在未复核前不得关闭，形成可追溯的责任链条。
"""


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.operations = load_module("kb_operations")
        self.handoff = load_module("kb_handoff")
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = root / "运营任务草稿"
        self.handoff_dir = root / "交接箱"
        self.now = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        self.task = self._prepared_task()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _prepared_task(self):
        task = self.operations.create_task(self.store, "PMSmart资料运营", now=self.now)
        self.operations.ingest_file(
            self.store,
            task["id"],
            "安全责任样本.md",
            SAMPLE_MARKDOWN.encode("utf-8"),
            now=self.now,
        )
        self.operations.decompose_task(self.store, task["id"], [], now=self.now)
        self.operations.analyze_requirement(
            self.store,
            task["id"],
            "客户希望隐患整改闭环，必须留下复核结论。",
            [],
            now=self.now,
        )
        return self.operations.build_knowledge_package(self.store, task["id"], now=self.now)

    def _context(self, **overrides):
        context = {
            "customer": "新疆区域重点高速公路项目",
            "project_name": "独库高速3标",
            "sales_stage": "方案",
            "deliverable_type": "word",
        }
        context.update(overrides)
        return context

    def test_build_writes_contract_package_with_pending_status(self):
        package = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )

        path = self.handoff_dir / "outbox" / f"{package['handoff_id']}.json"
        self.assertTrue(path.is_file())
        saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["status"], "pending")
        self.assertEqual(saved["knowledge_task_id"], self.task["id"])
        self.assertEqual(saved["context"]["project_name"], "独库高速3标")
        self.assertEqual(saved["context"]["deliverable_type"], "word")
        self.assertEqual(len(saved["sources"]), 1)
        self.assertTrue(saved["requirements"])
        self.assertTrue(saved["knowledge"]["atoms"])
        self.assertIn("content_sha256", saved)
        self.assertEqual(list((self.handoff_dir / "outbox").glob("*.tmp")), [])

    def test_handoff_id_is_stable_across_rebuilds(self):
        first = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )
        second = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )

        self.assertEqual(first["handoff_id"], second["handoff_id"])
        self.assertEqual(len(list((self.handoff_dir / "outbox").glob("*.json"))), 1)
        self.assertEqual(first["created_at"], second["created_at"])

    def test_handoff_id_differs_per_project_and_deliverable(self):
        base = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )
        other_project = self.handoff.build_handoff_package(
            self.store,
            self.task["id"],
            self._context(project_name="独库高速4标"),
            self.handoff_dir,
            now=self.now,
        )
        other_deliverable = self.handoff.build_handoff_package(
            self.store,
            self.task["id"],
            self._context(deliverable_type="ppt"),
            self.handoff_dir,
            now=self.now,
        )

        self.assertNotEqual(base["handoff_id"], other_project["handoff_id"])
        self.assertNotEqual(base["handoff_id"], other_deliverable["handoff_id"])
        self.assertEqual(len(list((self.handoff_dir / "outbox").glob("*.json"))), 3)

    def test_content_sha256_tracks_knowledge_changes(self):
        first = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )

        self.operations.analyze_requirement(
            self.store,
            self.task["id"],
            "客户新提出必须保留整改复核结论，并且要求月度汇总。",
            [],
            now=self.now,
        )
        self.operations.build_knowledge_package(self.store, self.task["id"], now=self.now)
        second = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )

        self.assertEqual(first["handoff_id"], second["handoff_id"])
        self.assertNotEqual(first["content_sha256"], second["content_sha256"])

    def test_context_requires_all_fields(self):
        for missing in ("customer", "project_name", "sales_stage", "deliverable_type"):
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(ValueError, f"缺少{missing}"):
                    context = self._context()
                    context.pop(missing)
                    self.handoff.build_handoff_package(
                        self.store, self.task["id"], context, self.handoff_dir, now=self.now
                    )

    def test_rejects_invalid_deliverable_type(self):
        with self.assertRaisesRegex(ValueError, "交付物类型无效"):
            self.handoff.build_handoff_package(
                self.store,
                self.task["id"],
                self._context(deliverable_type="excel-report"),
                self.handoff_dir,
                now=self.now,
            )

    def test_requires_knowledge_package_before_handoff(self):
        bare = self.operations.create_task(self.store, "未生成知识包", now=self.now)

        with self.assertRaisesRegex(ValueError, "请先生成知识包"):
            self.handoff.build_handoff_package(
                self.store, bare["id"], self._context(), self.handoff_dir, now=self.now
            )

    def test_ignored_candidates_are_excluded(self):
        package = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )
        self.assertTrue(package["knowledge"]["atoms"])

        target = package["knowledge"]["atoms"][0]["id"]
        self.operations.update_candidate(
            self.store, self.task["id"], target, {"disposition": "ignore"}, now=self.now
        )
        self.operations.build_knowledge_package(self.store, self.task["id"], now=self.now)
        rebuilt = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )

        self.assertNotIn(target, [atom["id"] for atom in rebuilt["knowledge"]["atoms"]])

    def test_rejects_absolute_path_in_package_sources(self):
        task = self.operations.load_task(self.store, self.task["id"])
        task["knowledge_package"]["sources"][0]["stored_path"] = "/Users/someone/secret.md"
        self.operations.save_task_atomic(self.store, task)

        with self.assertRaisesRegex(ValueError, "非法路径"):
            self.handoff.build_handoff_package(
                self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
            )

    def test_graph_snapshot_only_contains_existing_nodes(self):
        package = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )

        snapshot = package["outline"]["graph_snapshot"]
        source_ids = {source["node_id"] for source in package["sources"] if source["node_id"]}
        self.assertTrue(source_ids)
        self.assertTrue(source_ids.issubset({node["node_id"] for node in snapshot["nodes"]}))
        for edge in snapshot["edges"]:
            self.assertIn(edge["source_id"], {node["node_id"] for node in snapshot["nodes"]})
            self.assertIn(edge["target_id"], {node["node_id"] for node in snapshot["nodes"]})

    def test_list_handoffs_reports_only_real_files(self):
        self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )

        items = self.handoff.list_handoffs(self.handoff_dir)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "pending")
        self.assertEqual(items[0]["project_name"], "独库高速3标")

    def test_receipt_ingestion_moves_task_to_feedback_stage(self):
        package = self.handoff.build_handoff_package(
            self.store, self.task["id"], self._context(), self.handoff_dir, now=self.now
        )
        self.assertIsNone(self.handoff.load_receipt(self.handoff_dir, package["handoff_id"]))

        receipts = self.handoff_dir / "receipts"
        receipts.mkdir(parents=True, exist_ok=True)
        (receipts / f"{package['handoff_id']}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "handoff_id": package["handoff_id"],
                    "knowledge_task_id": self.task["id"],
                    "status": "imported",
                    "created_at": "2026-09-10T12:00:00+08:00",
                    "presales": {"project_id": "PRJ-1", "task_ids": ["DT-1"]},
                    "customer_feedback": "客户认可隐患闭环口径",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        updated = self.handoff.ingest_receipts(
            self.store, self.task["id"], self.handoff_dir, now=self.now
        )

        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["status"], "imported")
        self.assertEqual(updated[0]["presales_project_id"], "PRJ-1")
        reloaded = self.operations.load_task(self.store, self.task["id"])
        self.assertEqual(reloaded["stage"], "交付反馈与知识反哺")

    def test_rejects_malformed_handoff_id(self):
        with self.assertRaisesRegex(ValueError, "交接包ID无效"):
            self.handoff.load_handoff(self.handoff_dir, "../escape")


if __name__ == "__main__":
    unittest.main()
