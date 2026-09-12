"""分发包的打包契约测试。

分发包在库之外（默认 /Users/quxh/Documents/60-工具与开发/kb-star-map/），
不在时整体跳过；可用 KB_PACKAGE_DIR 环境变量覆盖位置。

这里钉死的都是**实测踩过**的坑：
  · 包里的工具脚本必须与库内权威副本逐字节一致，否则会发出旧版；
  · Windows 的 .ps1 必须 UTF-8 带 BOM + CRLF（PS 5.1 按 ANSI 读脚本，无 BOM 的中文会解析失败）；
  · shell 里变量后跟非 ASCII 字符必须写 ${VAR}（否则中文 locale 下会吞字节）；
  · 包里不能混入仓库元数据与生成物。
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

# 仓库根：tools/tests/ 往上两级
PACKAGE_DIR = Path(os.environ.get("KB_PACKAGE_DIR", str(Path(__file__).resolve().parents[2])))
VAULT_TOOLS = Path(__file__).resolve().parents[1]
SHIPPED_PY = (
    "build_kb_dashboard.py",
    "kb_star_graph.py",
    "serve_kb_dashboard.py",
    "kb_maintenance.py",
    "kb_operations.py",
    "kb_handoff.py",
    "kb_update.py",
)


@unittest.skipUnless(PACKAGE_DIR.exists(), f"分发包不在 {PACKAGE_DIR}")
class ShippedToolsTests(unittest.TestCase):
    def test_shipped_tools_match_vault_scripts(self):
        """包里的工具脚本必须与库内权威副本逐字节一致。"""
        shipped = PACKAGE_DIR / "tools"
        mismatched = []
        for name in SHIPPED_PY:
            source = VAULT_TOOLS / name
            target = shipped / name
            if source.exists() and target.exists() and source.read_bytes() != target.read_bytes():
                mismatched.append(name)
        for sub in ("dashboard_assets", "templates"):
            for source in sorted((VAULT_TOOLS / sub).iterdir()):
                target = shipped / sub / source.name
                if target.exists() and source.read_bytes() != target.read_bytes():
                    mismatched.append(f"{sub}/{source.name}")
        self.assertEqual(mismatched, [], f"分发包中的这些文件与库内不一致：{mismatched}")

    def test_kbs_entrypoint_is_in_sync(self):
        source = VAULT_TOOLS / "kbs.py"
        target = PACKAGE_DIR / "kbs.py"
        if source.exists() and target.exists():
            self.assertEqual(source.read_bytes(), target.read_bytes(), "kbs.py 与库内不一致")


@unittest.skipUnless(PACKAGE_DIR.exists(), f"分发包不在 {PACKAGE_DIR}")
class PackageHygieneTests(unittest.TestCase):
    def test_no_zip_artifacts_anywhere(self):
        """已改为「按清单逐文件拉取」：仓库里不应再有 zip 产物。"""
        strays = [str(p.relative_to(PACKAGE_DIR)) for p in PACKAGE_DIR.rglob("*.zip")]
        self.assertEqual(strays, [], f"不该再有 zip 产物：{strays}")
        self.assertFalse((PACKAGE_DIR / "dist" / "update.json").exists(),
                         "update.json 是 zip 时代的清单，应已被 files.json 取代")

    def test_manifest_lists_every_shipped_file_with_matching_digest(self):
        """清单是发布物本身：它必须覆盖全部工具文件，且哈希与工作区一致。"""
        import hashlib
        import json

        manifest_path = PACKAGE_DIR / "dist" / "files.json"
        self.assertTrue(manifest_path.exists(), "缺少 dist/files.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("version", manifest)
        files = manifest["files"]

        for required in ("kbs.py", "install.sh", "install.ps1",
                         "tools/bootstrap.py", "tools/serve_kb_dashboard.py",
                         "tools/dashboard_assets/star_graph.css"):
            self.assertIn(required, files, f"清单里缺少 {required}")

        mismatched = []
        for relative, digest in files.items():
            path = PACKAGE_DIR / relative
            if not path.is_file():
                mismatched.append(f"{relative}（清单里有、磁盘上没有）")
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != digest:
                mismatched.append(f"{relative}（哈希不符，清单可能没重算）")
        self.assertEqual(mismatched, [], f"清单与实际文件不一致：{mismatched}")

    def test_manifest_excludes_user_data_and_repo_metadata(self):
        import json

        files = json.loads((PACKAGE_DIR / "dist" / "files.json").read_text(encoding="utf-8"))["files"]
        for forbidden in ("installed.json", "更新配置.yaml", "设置.yaml", ".gitignore", "dist/files.json"):
            self.assertNotIn(forbidden, files, f"清单里不该有 {forbidden}")
        for relative in files:
            self.assertFalse(relative.startswith(".git/"), f"清单里不该有仓库元数据：{relative}")
            self.assertNotIn("__pycache__", relative)


    def test_package_root_has_only_expected_entries(self):
        """根层只该有入口与配置；工具脚本必须在 tools/ 下。

        实测踩过：同步脚本写重了一次，把 build_kb_dashboard.py / kb_update.py
        同时拷到了根层，打出来的包结构就乱了。

        看的是 git 跟踪的文件而不是文件系统：开发目录跑一次就会生成
        设置.yaml / installed.json 这类运行时产物，它们已被 .gitignore 覆盖，
        不会进发布物，不该算"包结构漂移"。
        """
        import subprocess

        tracked = subprocess.run(
            ["git", "-C", str(PACKAGE_DIR), "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout.split("\n")
        root_entries = {line.split("/")[0] for line in tracked if line.strip()}
        allowed = {
            "README.md", "VERSION", "asset-rules.template.yaml",
            "install.sh", "install.ps1", "kbs.py", "update-source.txt",
            ".gitignore", "tools", "dist",
        }
        stray = sorted(root_entries - allowed)
        self.assertEqual(stray, [], f"包根层出现不该跟踪的条目：{stray}")
        for required in ("kbs.py", "install.sh", "install.ps1", "VERSION", "tools", "dist"):
            self.assertIn(required, root_entries, f"根层缺少 {required}")


    def test_installers_do_not_auto_search_for_a_vault(self):
        """首次安装必须让用户明确指定笔记库，不能自己去扫。

        实测事故：在 ~ 下执行安装，脚本把家目录当成笔记库并开始全盘扫描，
        终端卡住两分钟、浏览器一直不出现，还在家目录里建了工作台目录。
        """
        sh = (PACKAGE_DIR / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn('find "$PWD"', sh, "install.sh 不应自动搜索当前目录当笔记库")
        self.assertNotIn("-maxdepth 2 -name '*.md'", sh)

        ps = (PACKAGE_DIR / "install.ps1").read_bytes().decode("utf-8-sig")
        self.assertNotIn("-Recurse -Depth", ps, "install.ps1 不应递归搜索笔记库")

    def test_kbs_refuses_the_home_directory(self):
        """家目录几乎必然含 .md，一旦被当成库就会去扫整个 home。"""
        text = (PACKAGE_DIR / "kbs.py").read_text(encoding="utf-8")
        self.assertIn("Path.home()", text, "kbs 必须拒绝把家目录当笔记库")

    def test_setup_scripts_exist(self):
        for name in ("install.sh", "install.ps1", "kbs.py", "VERSION", "README.md",
                     "tools/bootstrap.py"):
            self.assertTrue((PACKAGE_DIR / name).exists(), f"缺少 {name}")

    def test_legacy_vault_installer_is_gone(self):
        """旧的「装进笔记库」方式已移除，不要再被带回来。"""
        for name in ("installer.py", "1-SETUP-Windows.bat", "1-SETUP-macOS.command"):
            self.assertFalse((PACKAGE_DIR / name).exists(), f"{name} 属于已废弃的安装方式")


@unittest.skipUnless(PACKAGE_DIR.exists(), f"分发包不在 {PACKAGE_DIR}")
class ShellScriptEncodingTests(unittest.TestCase):
    def test_shell_script_has_no_bare_variable_before_non_ascii(self):
        """`$VAR）` 在中文 locale 下会把全角字符的首字节吃进变量名，导致运行时吞字节。

        实测：安装完成提示里的版本号会变成乱码（v + 半个全角括号）。
        """
        text = (PACKAGE_DIR / "install.sh").read_text(encoding="utf-8")
        offenders = re.findall(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7F]", text)
        self.assertEqual(
            offenders, [],
            "这些位置要写成 ${VAR}：" + ", ".join(repr(x) for x in offenders),
        )

    def test_shell_script_is_valid_utf8_without_bom(self):
        raw = (PACKAGE_DIR / "install.sh").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "shell 脚本不要带 BOM")
        raw.decode("utf-8")   # 解不开就会抛

    def test_powershell_script_is_utf8_with_bom_and_crlf(self):
        """PS 5.1 按 ANSI 代码页读 .ps1，UTF-8 无 BOM 的中文会让整个脚本解析失败。"""
        raw = (PACKAGE_DIR / "install.ps1").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "install.ps1 必须带 UTF-8 BOM")
        self.assertEqual(
            raw.count(b"\n") - raw.count(b"\r\n"), 0, "install.ps1 必须全部用 CRLF"
        )
        raw.decode("utf-8-sig")


@unittest.skipUnless(PACKAGE_DIR.exists(), f"分发包不在 {PACKAGE_DIR}")
class VersionTests(unittest.TestCase):
    def test_version_is_semver_like(self):
        version = (PACKAGE_DIR / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+(\.\d+)?$")

    def test_update_source_is_a_reachable_looking_url(self):
        lines = [
            line.strip()
            for line in (PACKAGE_DIR / "update-source.txt").read_text(encoding="utf-8").splitlines()
        ]
        value = next((x for x in lines if x and not x.startswith("#")), "")
        self.assertTrue(value.startswith("https://"), f"更新源应为 https 地址，实际是 {value!r}")


if __name__ == "__main__":
    unittest.main()
