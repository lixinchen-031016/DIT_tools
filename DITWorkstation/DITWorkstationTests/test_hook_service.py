"""post-ingest 钩子服务测试。"""

import tempfile
import unittest
from pathlib import Path

from DITWorkstation.Services.hook_service import run_post_ingest_hook


class TestHookService(unittest.TestCase):
    def test_empty_command_is_noop_success(self):
        ok, _summary = run_post_ingest_hook("", {"source_path": "/x"})
        self.assertTrue(ok)

    def test_command_success_and_env_context(self):
        """命令成功执行且环境变量上下文可见"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "env.txt"
            # 通过环境变量把 DIT_SOURCE_PATH 落盘，验证上下文注入
            ok, _summary = run_post_ingest_hook(
                f'echo "$DIT_SOURCE_PATH" > "{out}"',
                {"source_path": "/Volumes/CARD_A"},
            )
            self.assertTrue(ok)
            self.assertEqual(out.read_text().strip(), "/Volumes/CARD_A")

    def test_command_failure_returncode(self):
        ok, summary = run_post_ingest_hook("exit 3")
        self.assertFalse(ok)
        self.assertIn("3", summary)

    def test_invalid_command_reports_error(self):
        ok, _ = run_post_ingest_hook("__definitely_not_a_command_xyz__")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
