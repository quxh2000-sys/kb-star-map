from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "kb_maintenance.py"


def load_module():
    spec = importlib.util.spec_from_file_location("kb_maintenance", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 kb_maintenance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.maintenance = load_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name)
        self.note = self.vault / "知识库" / "现有笔记.md"
        self.note.parent.mkdir(parents=True)
        self.note.write_text("---\ntitle: 现有笔记\n---\n\n# 现有笔记\n\n原始正文。\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_note_returns_relative_path_content_and_sha256(self):
        result = self.maintenance.read_note(self.vault, "知识库/现有笔记.md")

        self.assertEqual(result["path"], "知识库/现有笔记.md")
        self.assertIn("原始正文", result["content"])
        self.assertRegex(result["sha256"], r"^[0-9a-f]{64}$")

    def test_edit_and_create_preview_return_diff_and_matching_token(self):
        note = self.maintenance.read_note(self.vault, "知识库/现有笔记.md")
        edited = note["content"].replace("原始正文", "调改正文")

        edit_preview = self.maintenance.preview_edit(
            self.vault, note["path"], edited, note["sha256"]
        )
        create_preview = self.maintenance.preview_create(
            self.vault,
            "收件箱/知识维护新增/新笔记.md",
            "---\ntitle: 新笔记\n---\n\n# 新笔记\n\n新增正文。\n",
        )

        self.assertIn("-原始正文", edit_preview["diff"])
        self.assertIn("+调改正文", edit_preview["diff"])
        self.assertEqual(edit_preview["token"], self.maintenance.preview_token(edit_preview))
        self.assertIn("+# 新笔记", create_preview["diff"])

    def test_commit_preview_writes_and_rejects_stale_or_unconfirmed_changes(self):
        note = self.maintenance.read_note(self.vault, "知识库/现有笔记.md")
        preview = self.maintenance.preview_edit(
            self.vault,
            note["path"],
            note["content"].replace("原始正文", "确认正文"),
            note["sha256"],
        )

        result = self.maintenance.commit_preview(
            self.vault, preview, preview["token"], confirm=True
        )

        self.assertIn("确认正文", self.note.read_text(encoding="utf-8"))
        self.assertEqual(result["sha256"], self.maintenance.read_note(self.vault, note["path"])["sha256"])
        with self.assertRaisesRegex(ValueError, "必须显式确认"):
            self.maintenance.commit_preview(
                self.vault, preview, preview["token"], confirm=False
            )

    def test_commit_rejects_baseline_change_and_create_target_collision(self):
        note = self.maintenance.read_note(self.vault, "知识库/现有笔记.md")
        preview = self.maintenance.preview_edit(
            self.vault, note["path"], note["content"] + "追加", note["sha256"]
        )
        self.note.write_text(note["content"] + "用户同时修改", encoding="utf-8")

        with self.assertRaises(self.maintenance.MaintenanceConflict):
            self.maintenance.commit_preview(
                self.vault, preview, preview["token"], confirm=True
            )

        create = self.maintenance.preview_create(
            self.vault, "收件箱/新笔记.md", "# 新笔记\n\n正文"
        )
        target = self.vault / "收件箱" / "新笔记.md"
        target.parent.mkdir(parents=True)
        target.write_text("占用", encoding="utf-8")
        with self.assertRaises(self.maintenance.MaintenanceConflict):
            self.maintenance.commit_preview(
                self.vault, create, create["token"], confirm=True
            )


if __name__ == "__main__":
    unittest.main()
