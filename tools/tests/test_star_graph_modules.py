from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = TOOLS_DIR / "dashboard_assets"
BUILD_PATH = TOOLS_DIR / "build_kb_dashboard.py"


def load_build_module():
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    spec = importlib.util.spec_from_file_location("build_kb_dashboard", BUILD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载build_kb_dashboard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StarGraphModulesTest(unittest.TestCase):
    """star_graph 模块装配不变式。

    2026-09-11：原 star_graph.js（1134 行）先按接缝拆为 7 个片段，随后改造为
    各自独立的真模块——每个模块自带 IIFE 与 globalThis.KBStarGraph 命名空间，
    模块间只经命名空间通信。这些用例守住：清单完整、拼接确定、**每模块可独立
    语法校验**、旧单文件不回流。
    """

    @classmethod
    def setUpClass(cls):
        cls.build = load_build_module()
        cls.manifest = json.loads(
            (ASSETS_DIR / "star_graph.modules.json").read_text(encoding="utf-8")
        )

    def test_manifest_lists_existing_non_empty_modules(self):
        modules = self.manifest["modules"]
        self.assertGreaterEqual(len(modules), 2)
        for item in modules:
            path = ASSETS_DIR / item["file"]
            self.assertTrue(path.is_file(), f"缺少模块片段：{item['file']}")
            self.assertGreater(path.stat().st_size, 0, f"模块片段为空：{item['file']}")

    def test_manifest_has_no_duplicates_and_declares_roles(self):
        modules = self.manifest["modules"]
        names = [item["file"] for item in modules]
        self.assertEqual(len(names), len(set(names)), "清单中存在重复模块")
        for item in modules:
            self.assertTrue(item.get("role"), f"模块缺少职责说明：{item['file']}")

    def test_assembled_js_follows_manifest_order(self):
        expected = "".join(
            (ASSETS_DIR / item["file"]).read_text(encoding="utf-8")
            for item in self.manifest["modules"]
        )
        self.assertEqual(self.build.assemble_star_graph_js(ASSETS_DIR), expected)

    def test_every_module_is_independently_valid_javascript(self):
        """真模块的核心属性：每个文件都能被 JS 引擎单独解析。

        片段式拆分做不到这一点（片段单独看不是完整 JS）；本用例防止回退。
        """
        for item in self.manifest["modules"]:
            path = ASSETS_DIR / item["file"]
            result = subprocess.run(
                ["node", "--check", str(path)], capture_output=True, text=True
            )
            self.assertEqual(
                result.returncode,
                0,
                f"{item['file']} 不能独立通过语法校验：{result.stderr.strip()[:200]}",
            )

    def test_each_module_bootstraps_the_shared_namespace(self):
        for item in self.manifest["modules"]:
            text = (ASSETS_DIR / item["file"]).read_text(encoding="utf-8")
            self.assertIn(
                "globalThis.KBStarGraph = globalThis.KBStarGraph || {};",
                text,
                f"{item['file']} 未引导共享命名空间",
            )

    def test_assembled_js_is_valid(self):
        assembled = self.build.assemble_star_graph_js(ASSETS_DIR)
        self.assertTrue(assembled.rstrip().endswith("})();"), "拼接结果应以 IIFE 调用结尾")
        tmp = Path("/tmp") / "kb_star_graph_assembled_check.js"
        tmp.write_text(assembled, encoding="utf-8")
        result = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"拼接产物语法错误：{result.stderr.strip()[:200]}")

    def test_removed_monolith_does_not_come_back(self):
        self.assertFalse(
            (ASSETS_DIR / "star_graph.js").exists(),
            "旧单文件 star_graph.js 应已删除，避免与模块双份维护导致分叉",
        )



class TemplateContractTests(unittest.TestCase):
    """前端脚本引用的元素 id，模板里必须存在。

    这条测试是为了拦住一类真实事故：脚本升级了、模板没跟上，
    于是 getElementById 拿到 null，初始化抛错、整页停在静态降级视图。
    本地管理模式才会走到那段分支，所以 file:// 打开正常、走服务就白屏，
    极难从表面现象定位。
    """

    # 由脚本在运行时自己创建的 id，不需要模板提供
    # showBackgroundRecords 由 07 模块渲染进镜头面板，08 模块只是去同步它的状态
    RUNTIME_CREATED_IDS = {"relationItems", "subDimensionSuggestions", "showBackgroundRecords"}
    # 模板里确实不存在、且调用方已显式判空的 id（当前只剩早期运营模式的面板，
    # setMode 已无人调用）。放进这里等于承诺"用之前必须判空"。
    OPTIONAL_IDS = {"operationsPanel"}

    def test_js_element_ids_exist_in_template(self):
        template = (TOOLS_DIR / "templates" / "kb_dashboard_template.html").read_text(encoding="utf-8")
        template_ids = set(re.findall(r'id="([^"]+)"', template))

        referenced: dict[str, set[str]] = {}
        for path in sorted(ASSETS_DIR.glob("*.js")):
            text = path.read_text(encoding="utf-8")
            # 两种写法都要认：getElementById("x") 与设置模块里的 $("#x") 简写
            for pattern in (r'getElementById\(\s*"([^"]+)"', r'\$\(\s*"([^"]+)"\s*\)'):
                for match in re.finditer(pattern, text):
                    referenced.setdefault(match.group(1), set()).add(path.name)

        allowed = self.RUNTIME_CREATED_IDS | self.OPTIONAL_IDS
        missing = {
            name: files
            for name, files in referenced.items()
            if name not in template_ids and name not in allowed
        }
        self.assertEqual(
            missing, {},
            "模板里缺少脚本引用的元素 id（会导致初始化抛错、整页降级）："
            + "; ".join(f"{k} <- {', '.join(sorted(v))}" for k, v in sorted(missing.items())),
        )



if __name__ == "__main__":
    unittest.main()
