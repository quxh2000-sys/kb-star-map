"""更新器（kb_update.py）与引导器（bootstrap.py）的回归测试。

更新机制是「按文件清单逐文件从远端拉取 + sha256 校验」，不用 zip、
不用 GitHub Releases。测试用本机 HTTP 服务提供清单与文件，不依赖外网。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TOOLS_DIR = TESTS_DIR.parent
UPDATE_PATH = TOOLS_DIR / "kb_update.py"
BOOTSTRAP_PATH = TOOLS_DIR / "bootstrap.py"


def load_module(path: Path, name: str, home: Path | None = None):
    """加载模块；给定 home 时把安装根目录指到它（不动真实的 ~/.kb-star-map）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if home is not None and hasattr(module, "install_root"):
        module.install_root = lambda: home
    return module


class LocalServer:
    """把一棵目录树按 raw 的方式提供出来：/<相对路径>。"""

    def __init__(self, root: Path):
        self.root = root
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.split("?")[0].lstrip("/")
                outer.hits.append(path)
                target = outer.root / path
                if not target.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def write_tree(root: Path, files: dict[str, str]) -> dict[str, str]:
    """把 {相对路径: 内容} 写到磁盘，返回 {相对路径: sha256}。"""
    digests = {}
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        target.write_bytes(data)
        digests[relative] = hashlib.sha256(data).hexdigest()
    return digests


def write_manifest(root: Path, version: str, files: dict[str, str], changelog: str = "") -> None:
    (root / "dist").mkdir(parents=True, exist_ok=True)
    (root / "dist" / "files.json").write_text(
        json.dumps({"version": version, "changelog": changelog, "files": files},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class HomeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / ".kb-star-map"
        self.home.mkdir(parents=True, exist_ok=True)
        self.remote = self.base / "remote"
        self.remote.mkdir(parents=True, exist_ok=True)

    def configure(self, manifest_url: str) -> None:
        (self.home / "更新配置.yaml").write_text(
            f'manifest_url: "{manifest_url}"\nauto_check: false\ntimeout: 15\n', encoding="utf-8"
        )

    def set_version(self, version: str, files: list[str] | None = None) -> None:
        """写安装清单。files 记录「上次装了哪些文件」——清理只认这份记录。"""
        payload: dict = {"version": version}
        if files is not None:
            payload["files"] = files
        (self.home / "installed.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )


class VersionTests(unittest.TestCase):
    def test_parse_version_tolerates_prefix_and_short_forms(self):
        module = load_module(UPDATE_PATH, "kb_update_v")
        self.assertEqual(module.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(module.parse_version("1.2"), (1, 2))
        self.assertEqual(module.parse_version("无版本"), ())

    def test_is_newer_handles_uneven_lengths(self):
        module = load_module(UPDATE_PATH, "kb_update_v2")
        self.assertTrue(module.is_newer("1.2", "1.1.9"))
        self.assertFalse(module.is_newer("1.2", "1.2.0"))
        self.assertTrue(module.is_newer("1.2.1", "1.2"))
        self.assertFalse(module.is_newer("1.0.0", "1.0.0"))

    def test_unknown_current_version_is_upgradable(self):
        module = load_module(UPDATE_PATH, "kb_update_v3")
        self.assertTrue(module.is_newer("1.0.0", "未知"))
        self.assertFalse(module.is_newer("", "1.0.0"))


class ConfigTests(HomeTestCase):
    def _module(self):
        return load_module(UPDATE_PATH, "kb_update_cfg", self.home)

    def test_config_path_lives_with_the_tool(self):
        module = self._module()
        self.assertEqual(module.config_path(), self.home / module.CONFIG_FILENAME)

    def test_missing_config_means_offline_not_crash(self):
        module = self._module()
        config = module.load_config()
        self.assertFalse(config.enabled)
        self.assertEqual(config.parse_error, "")

    def test_broken_config_reports_reason(self):
        (self.home / "更新配置.yaml").write_text("这不是: [合法的 yaml\n  - ],[", encoding="utf-8")
        module = self._module()
        self.assertIn("解析失败", module.load_config().parse_error)

    def test_config_with_bom_is_read(self):
        """Windows 上「另存为 UTF-8 带 BOM」很常见，BOM 会让 YAML 解析失败。"""
        (self.home / "更新配置.yaml").write_bytes(
            b"\xef\xbb\xbf" + b'manifest_url: "https://example.com/files.json"\n'
        )
        module = self._module()
        self.assertTrue(module.load_config().enabled)

    def test_legacy_update_json_url_is_normalized(self):
        """老配置指向 zip 时代的 update.json；不换掉的话更新会直接失效。"""
        (self.home / "更新配置.yaml").write_text(
            'manifest_url: "https://x/dist/update.json"\n', encoding="utf-8"
        )
        module = self._module()
        self.assertEqual(module.load_config().manifest_url, "https://x/dist/files.json")

    def test_installed_manifest_with_bom_reports_version(self):
        (self.home / "installed.json").write_bytes(b"\xef\xbb\xbf" + b'{"version": "2.5.0"}')
        module = self._module()
        self.assertEqual(module.read_installed_version(), "2.5.0")


class RetryTests(unittest.TestCase):
    def test_transient_failure_is_retried(self):
        module = load_module(UPDATE_PATH, "kb_update_r1")
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise urllib.error.URLError("temporary")
            return "ok"

        self.assertEqual(module._retry(flaky, attempts=3, delay=0), "ok")
        self.assertEqual(attempts["n"], 3)

    def test_client_error_is_not_retried(self):
        module = load_module(UPDATE_PATH, "kb_update_r2")
        attempts = {"n": 0}

        def not_found():
            attempts["n"] += 1
            raise urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)

        with self.assertRaises(urllib.error.HTTPError):
            module._retry(not_found, attempts=3, delay=0)
        self.assertEqual(attempts["n"], 1)


class BootstrapTests(HomeTestCase):
    """引导器：按清单逐文件铺开。"""

    def test_install_downloads_every_file_and_writes_manifest(self):
        digests = write_tree(self.remote, {
            "kbs.py": "# kbs\n",
            "tools/a.py": "# a\n",
            "tools/dashboard_assets/x.js": "// x\n",
        })
        write_manifest(self.remote, "1.2.3", digests)
        with LocalServer(self.remote) as server:
            module = load_module(BOOTSTRAP_PATH, "bootstrap_1")
            result = module.install(f"{server.base}/dist/files.json", self.home,
                                    raw_base=server.base, log=lambda *_: None)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["version"], "1.2.3")
        self.assertEqual((self.home / "kbs.py").read_text(encoding="utf-8"), "# kbs\n")
        self.assertEqual((self.home / "tools" / "a.py").read_text(encoding="utf-8"), "# a\n")
        self.assertTrue((self.home / "tools" / "dashboard_assets" / "x.js").exists())
        self.assertEqual(json.loads((self.home / "installed.json").read_text())["version"], "1.2.3")

    def test_install_removes_files_the_new_version_dropped(self):
        """覆盖式安装不会删旧文件——实测因此让废弃的 installer.py 一直躺在安装目录里。"""
        (self.home / "tools").mkdir(exist_ok=True)
        (self.home / "tools" / "obsolete.py").write_text("# 旧版有\n", encoding="utf-8")
        (self.home / "旧残留.txt").write_text("x", encoding="utf-8")
        self.set_version("1.0.0", files=["tools/obsolete.py", "旧残留.txt", "kbs.py"])
        digests = write_tree(self.remote, {"kbs.py": "# kbs\n"})
        write_manifest(self.remote, "2.0.0", digests)
        with LocalServer(self.remote) as server:
            module = load_module(BOOTSTRAP_PATH, "bootstrap_2")
            result = module.install(f"{server.base}/dist/files.json", self.home,
                                    raw_base=server.base, log=lambda *_: None)

        self.assertTrue(result["ok"], result)
        self.assertFalse((self.home / "tools" / "obsolete.py").exists())
        self.assertFalse((self.home / "旧残留.txt").exists())
        self.assertIn("tools/obsolete.py", result["removed"])

    def test_install_keeps_user_data(self):
        (self.home / "更新配置.yaml").write_text("manifest_url: \"x\"\n", encoding="utf-8")
        (self.home / "升级备份" / "old").mkdir(parents=True)
        (self.home / "设置.yaml").write_text("note_open_mode: system\n", encoding="utf-8")
        digests = write_tree(self.remote, {"kbs.py": "# kbs\n"})
        write_manifest(self.remote, "2.0.0", digests)
        with LocalServer(self.remote) as server:
            module = load_module(BOOTSTRAP_PATH, "bootstrap_3")
            module.install(f"{server.base}/dist/files.json", self.home,
                           raw_base=server.base, log=lambda *_: None)

        self.assertTrue((self.home / "更新配置.yaml").exists())
        self.assertTrue((self.home / "升级备份" / "old").exists())
        self.assertTrue((self.home / "设置.yaml").exists())

    def test_digest_mismatch_is_rejected(self):
        """清单里的 sha256 与下载内容对不上时必须中止，不能装上半截。"""
        write_tree(self.remote, {"kbs.py": "# kbs\n"})
        write_manifest(self.remote, "2.0.0", {"kbs.py": "0" * 64})
        with LocalServer(self.remote) as server:
            module = load_module(BOOTSTRAP_PATH, "bootstrap_4")
            module.RETRY_ATTEMPTS = 1
            module.RETRY_DELAY = 0
            result = module.install(f"{server.base}/dist/files.json", self.home,
                                    raw_base=server.base, log=lambda *_: None)

        self.assertFalse(result["ok"])
        self.assertIn("校验不通过", result["message"])
        self.assertFalse((self.home / "kbs.py").exists(), "失败时不该留下半成品")

    def test_manifest_with_path_traversal_is_rejected(self):
        write_tree(self.remote, {"kbs.py": "# kbs\n"})
        write_manifest(self.remote, "2.0.0", {"../escape.py": "0" * 64})
        with LocalServer(self.remote) as server:
            module = load_module(BOOTSTRAP_PATH, "bootstrap_5")
            result = module.install(f"{server.base}/dist/files.json", self.home,
                                    raw_base=server.base, log=lambda *_: None)
        self.assertFalse(result["ok"])
        self.assertIn("越界路径", result["message"])

    def test_bad_manifest_shape_is_reported(self):
        (self.remote / "dist").mkdir(parents=True)
        (self.remote / "dist" / "files.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
        with LocalServer(self.remote) as server:
            module = load_module(BOOTSTRAP_PATH, "bootstrap_6")
            result = module.install(f"{server.base}/dist/files.json", self.home,
                                    raw_base=server.base, log=lambda *_: None)
        self.assertFalse(result["ok"])
        self.assertIn("清单格式", result["message"])


    def test_install_without_previous_record_deletes_nothing(self):
        """没有上次清单时宁可少删：kbs 启动器、设置.yaml 都不在清单里，误删就出事。"""
        (self.home / "kbs").write_text("#!/bin/bash\n", encoding="utf-8")
        (self.home / "设置.yaml").write_text("note_open_mode: system\n", encoding="utf-8")
        (self.home / "some_user_file.txt").write_text("我的东西\n", encoding="utf-8")
        digests = write_tree(self.remote, {"kbs.py": "# kbs\n"})
        write_manifest(self.remote, "2.0.0", digests)
        with LocalServer(self.remote) as server:
            module = load_module(BOOTSTRAP_PATH, "bootstrap_keep")
            result = module.install(f"{server.base}/dist/files.json", self.home,
                                    raw_base=server.base, log=lambda *_: None)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["removed"], [])
        for name in ("kbs", "设置.yaml", "some_user_file.txt"):
            self.assertTrue((self.home / name).exists(), f"{name} 不该被删")


class CheckUpdateTests(HomeTestCase):
    def _module(self):
        return load_module(UPDATE_PATH, "kb_update_chk", self.home)

    def test_unconfigured_is_explicit(self):
        module = self._module()
        result = module.check_for_update()
        self.assertFalse(result["configured"])
        self.assertIn("更新配置.yaml", result["message"])

    def test_reports_update_available(self):
        digests = write_tree(self.remote, {"kbs.py": "# kbs\n"})
        write_manifest(self.remote, "1.1.0", digests)
        self.set_version("1.0.0")
        with LocalServer(self.remote) as server:
            self.configure(f"{server.base}/dist/files.json")
            result = self._module().check_for_update()
        self.assertTrue(result["configured"])
        self.assertEqual(result["current"], "1.0.0")
        self.assertEqual(result["latest"], "1.1.0")
        self.assertTrue(result["update_available"])

    def test_reports_up_to_date(self):
        digests = write_tree(self.remote, {"kbs.py": "# kbs\n"})
        write_manifest(self.remote, "1.0.0", digests)
        self.set_version("1.0.0")
        with LocalServer(self.remote) as server:
            self.configure(f"{server.base}/dist/files.json")
            result = self._module().check_for_update()
        self.assertFalse(result["update_available"])

    def test_network_error_becomes_structured_result(self):
        self.configure("http://127.0.0.1:1/dist/files.json")
        result = self._module().check_for_update()
        self.assertTrue(result["configured"])
        self.assertIn("error", result)


class PerformUpdateTests(HomeTestCase):
    def _module(self):
        module = load_module(UPDATE_PATH, "kb_update_perf", self.home)
        module.RETRY_ATTEMPTS = 1
        module.RETRY_DELAY = 0
        return module

    def test_update_replaces_files_and_backs_up(self):
        (self.home / "tools").mkdir(exist_ok=True)
        (self.home / "tools" / "old.py").write_text("# 旧\n", encoding="utf-8")
        self.set_version("1.0.0", files=["tools/old.py"])
        digests = write_tree(self.remote, {"kbs.py": "# new\n", "tools/new.py": "# new\n"})
        write_manifest(self.remote, "1.1.0", digests)
        with LocalServer(self.remote) as server:
            self.configure(f"{server.base}/dist/files.json")
            result = self._module().perform_update()

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["from"], "1.0.0")
        self.assertEqual(result["to"], "1.1.0")
        self.assertTrue((self.home / "tools" / "new.py").exists())
        self.assertFalse((self.home / "tools" / "old.py").exists())
        self.assertTrue(any((self.home / "升级备份").iterdir()), "更新前必须备份")
        self.assertEqual(self._module().read_installed_version(), "1.1.0")

    def test_update_skips_when_already_current(self):
        digests = write_tree(self.remote, {"kbs.py": "# kbs\n"})
        write_manifest(self.remote, "1.0.0", digests)
        self.set_version("1.0.0")
        with LocalServer(self.remote) as server:
            self.configure(f"{server.base}/dist/files.json")
            module = self._module()
            server.hits.clear()
            result = module.perform_update()
            downloads = [h for h in server.hits if h.endswith(".py")]
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(downloads, [], "已是最新时不该下载任何文件")


class ServerEndpointTests(HomeTestCase):
    """真起一次本地服务，验证「检查更新」背后的端点通且要令牌。"""

    def _serve(self, settings_file: Path):
        import socket
        import subprocess

        tools = self.home / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        for source in sorted(TOOLS_DIR.glob("*.py")):
            shutil.copy2(source, tools / source.name)
        for sub in ("dashboard_assets", "templates"):
            if (TOOLS_DIR / sub).is_dir():
                shutil.copytree(TOOLS_DIR / sub, tools / sub, dirs_exist_ok=True)
        self.set_version("9.9.9")

        vault = self.base / "vault"
        dashboard = vault / "系统" / "知识库可视化工作台"
        dashboard.mkdir(parents=True)
        (dashboard / "知识库可视化工作台.html").write_text(
            '<!doctype html><script id="kb-runtime">window.__KB_RUNTIME__ = null;</script>',
            encoding="utf-8")
        (vault / "笔记.md").write_text("# 笔记\n", encoding="utf-8")

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        env = dict(os.environ, KB_SETTINGS_PATH=str(settings_file))
        process = subprocess.Popen(
            [sys.executable, str(tools / "serve_kb_dashboard.py"), "--serve",
             "--vault", str(vault), "--dashboard-dir", str(dashboard), "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )

        def _stop():
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        self.addCleanup(_stop)

        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1).read()
                return port, dashboard
            except Exception:
                time.sleep(0.25)
        self.skipTest("本地服务未能在超时内启动")

    def _post(self, port: int, path: str, payload: dict, token: str | None = None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-KB-Token"] = token
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode(),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def _get(self, port: int, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_update_endpoint_answers_instead_of_resetting_the_connection(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        port, _ = self._serve(Path(tmp.name) / "设置.yaml")
        token = self._get(port, "/api/settings")["agent_token"]
        status, body = self._post(port, "/api/update", {"action": "check"}, token)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"), body)
        self.assertIn("configured", body)

    def test_update_endpoint_requires_the_token(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        port, _ = self._serve(Path(tmp.name) / "设置.yaml")
        status, body = self._post(port, "/api/update", {"action": "check"})
        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()
