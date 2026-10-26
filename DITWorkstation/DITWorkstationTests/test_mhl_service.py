"""ASC MHL 服务测试：生成、解析、目录约定写入与按清单校验。"""

import tempfile
import unittest
from pathlib import Path

from DITWorkstation.Models import ChecksumAlgorithm
from DITWorkstation.Services.checksum_service import ChecksumService
from DITWorkstation.Services.mhl_service import (
    MHLVerifyResult,
    generate_asc_mhl,
    next_mhl_generation,
    parse_mhl,
    verify_against_mhl,
    write_mhl_to_folder,
)


class TestMHLService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # 构造两份源文件（含子目录）
        self.sub = self.root / "DCIM" / "100NIKON"
        self.sub.mkdir(parents=True)
        (self.sub / "a.NEF").write_bytes(b"nikon raw content A" * 10)
        (self.root / "top.txt").write_bytes(b"top level file" * 5)

        self.checksum_service = ChecksumService()

    def tearDown(self):
        self._tmp.cleanup()

    def _build_files(self):
        files = []
        for p in (self.sub / "a.NEF", self.root / "top.txt"):
            stat = p.stat()
            checksum = self.checksum_service.compute_file_checksum(
                str(p), ChecksumAlgorithm.XXHASH64
            )
            files.append(
                {
                    "path": str(p),
                    "relative": p.relative_to(self.root).as_posix(),
                    "size": stat.st_size,
                    "checksum": checksum.hash_value,
                }
            )
        return files

    def test_generate_and_parse_roundtrip(self):
        """生成 ASC MHL v2 并可解析回读全部条目"""
        xml = generate_asc_mhl(
            self._build_files(), ChecksumAlgorithm.XXHASH64, source_root=str(self.root)
        )
        self.assertIn('<ascmhl version="2.0">', xml)
        self.assertIn("<hashformat>xxh64</hashformat>", xml)

        mhl_path = self.root / "test-0001-ascmhl.xml"
        mhl_path.write_text(xml, encoding="utf-8")

        _source, entries = parse_mhl(mhl_path)
        self.assertEqual(len(entries), 2)
        paths = {e.path for e in entries}
        self.assertEqual(paths, {"DCIM/100NIKON/a.NEF", "top.txt"})
        for e in entries:
            self.assertEqual(e.hash_format, "xxh64")
            self.assertTrue(e.hash_value)
            self.assertIsNotNone(e.size)

    def test_parse_legacy_hashlist(self):
        """兼容原始 MHL（<hashlist>）格式解析"""
        legacy = """<?xml version="1.0" encoding="UTF-8"?>
<hashlist version="2.0">
  <hash>
    <path>top.txt</path>
    <size>70</size>
    <hashformat>md5</hashformat>
    <hashvalue>abc123</hashvalue>
  </hash>
</hashlist>
"""
        mhl_path = self.root / "legacy.mhl"
        mhl_path.write_text(legacy, encoding="utf-8")
        _, entries = parse_mhl(mhl_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].hash_format, "md5")

    def test_write_folder_convention(self):
        """写入 .ascmhl 目录并按代数递增命名"""
        xml = generate_asc_mhl(self._build_files(), ChecksumAlgorithm.XXHASH64)
        target = self.root / "backup_copy"
        target.mkdir()
        p1 = write_mhl_to_folder(xml, target, "cardA")
        p2 = write_mhl_to_folder(xml, target, "cardA")
        self.assertEqual(p1.parent.name, ".ascmhl")
        self.assertEqual(p1.name, "cardA-0001-ascmhl.xml")
        self.assertEqual(p2.name, "cardA-0002-ascmhl.xml")
        self.assertEqual(next_mhl_generation(target, "cardB"), 1)

    def test_verify_against_mhl(self):
        """按清单校验：全部通过 / 内容被篡改 / 文件缺失"""
        files = self._build_files()
        xml = generate_asc_mhl(files, ChecksumAlgorithm.XXHASH64)
        mhl_path = self.root / "verify-0001-ascmhl.xml"
        mhl_path.write_text(xml, encoding="utf-8")

        result = verify_against_mhl(mhl_path, self.root, self.checksum_service)
        self.assertIsInstance(result, MHLVerifyResult)
        self.assertEqual(result.verified, 2)
        self.assertEqual(result.mismatched + result.missing + result.skipped, 0)

        # 篡改其中一个文件内容
        (self.root / "top.txt").write_bytes(b"tampered content" * 5)
        result2 = verify_against_mhl(mhl_path, self.root, self.checksum_service)
        self.assertEqual(result2.mismatched, 1)
        self.assertEqual(result2.verified, 1)

        # 删除另一个文件
        (self.sub / "a.NEF").unlink()
        result3 = verify_against_mhl(mhl_path, self.root, self.checksum_service)
        self.assertEqual(result3.missing, 1)
        self.assertIn("文件缺失", result3.summary())

    def test_verify_unsupported_algorithm_skipped(self):
        """清单内未知算法的条目应跳过而不报错"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<ascmhl version="2.0">
  <hash>
    <path>top.txt</path>
    <size>70</size>
    <hashformat>sha512</hashformat>
    <hashvalue>deadbeef</hashvalue>
  </hash>
</ascmhl>
"""
        mhl_path = self.root / "unknown-ascmhl.xml"
        mhl_path.write_text(xml, encoding="utf-8")
        result = verify_against_mhl(mhl_path, self.root, self.checksum_service)
        self.assertEqual(result.skipped, 1)

    def test_parse_invalid_file_raises(self):
        """非法 XML 根元素应抛出 ValueError"""
        mhl_path = self.root / "bad.xml"
        mhl_path.write_text('<?xml version="1.0"?><other/>', encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_mhl(mhl_path)


if __name__ == "__main__":
    unittest.main()
