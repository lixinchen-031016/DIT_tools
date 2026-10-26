"""存储卡擦除保护状态测试。"""

import unittest

from DITWorkstation.App import config
from DITWorkstation.Services.card_identity import (
    CARD_FAILED,
    CARD_PENDING,
    CARD_VERIFIED,
    card_wipe_warning,
    compute_card_fingerprint,
    get_card_state,
    mark_card_state,
)


class TestCardVerificationState(unittest.TestCase):
    def setUp(self):
        self._orig_states = dict(getattr(config, "card_verification_states", {}))
        config.card_verification_states = {}

    def tearDown(self):
        config.card_verification_states = self._orig_states

    def test_unknown_state(self):
        self.assertEqual(get_card_state("no_such_fingerprint"), "unknown")

    def test_mark_and_get(self):
        mark_card_state("fp1", CARD_VERIFIED)
        mark_card_state("fp2", CARD_PENDING)
        mark_card_state("fp3", CARD_FAILED)
        self.assertEqual(get_card_state("fp1"), CARD_VERIFIED)
        self.assertEqual(get_card_state("fp2"), CARD_PENDING)
        self.assertEqual(get_card_state("fp3"), CARD_FAILED)

    def test_invalid_state_ignored(self):
        mark_card_state("fp1", "bogus")
        self.assertEqual(get_card_state("fp1"), "unknown")

    def test_card_wipe_warning_flow(self):
        """对临时目录模拟卡内容：状态流转对应不同警告"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            card = Path(tmp)
            (card / "DCIM").mkdir()
            (card / "DSC_0001.NEF").write_bytes(b"x" * 16)
            fp = compute_card_fingerprint(str(card))
            self.assertTrue(fp)

            # 未知状态 → 警告
            warning = card_wipe_warning(str(card))
            self.assertIn("请勿格式化", warning)

            mark_card_state(fp, CARD_PENDING)
            self.assertIn("未做校验备份", card_wipe_warning(str(card)))

            mark_card_state(fp, CARD_FAILED)
            self.assertIn("未完成", card_wipe_warning(str(card)))

            mark_card_state(fp, CARD_VERIFIED)
            self.assertIsNone(card_wipe_warning(str(card)))


if __name__ == "__main__":
    unittest.main()
