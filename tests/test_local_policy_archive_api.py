import hashlib
import importlib.util
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_archive_server", APP / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class LocalPolicyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE sources(id TEXT PRIMARY KEY,label TEXT,kind TEXT,path TEXT,
                observed_at TEXT,source_as_of TEXT,url TEXT,sha256 TEXT,notes TEXT);
            CREATE TABLE policy_texts(id TEXT PRIMARY KEY,source_id TEXT,year TEXT,
                label TEXT,body TEXT,school_ids TEXT,kind TEXT);
        """)
        self.source = dict(id="local-policy:test", label="来源标题", kind="official_local_policy_archive",
                           path="../../must-not-be-opened.txt", observed_at="2026-08-28",
                           source_as_of="2026", url="https://example.gov.cn/policy/2026.html?original=1",
                           sha256="0" * 64, notes="原始文件哈希与展示文本哈希不同")
        self.db.execute("INSERT INTO sources VALUES(:id,:label,:kind,:path,:observed_at,:source_as_of,:url,:sha256,:notes)", self.source)
        self.body = "原始采集时间：2026-08-28（日期粒度）\nTLS 核验：无记录\n原始哈希：" + "0" * 64 + "\n\n政策原文展示。\n"
        self.db.execute("INSERT INTO policy_texts VALUES(?,?,?,?,?,?,?)", (
            "text:test", self.source["id"], "2026", "归档政策标题", self.body, "[]", "local_policy_archive"))

    def tearDown(self):
        self.db.close()

    def archive(self, source_id=None):
        # This new branch must never interpret the source path as a filesystem API.
        with patch.object(Path, "read_bytes", side_effect=AssertionError("Unexpected file access")), \
             patch.object(Path, "read_text", side_effect=AssertionError("Unexpected file access")):
            return server.source_archive(self.db, source_id or self.source["id"])

    def test_source_status_is_unverified_without_mutating_original(self):
        original = dict(self.source)
        with patch.object(Path, "read_text", side_effect=AssertionError("Not a live link check")):
            source = server.source_with_status(self.source)
        self.assertEqual(self.source, original)
        self.assertEqual(source["url"], self.source["url"])
        self.assertTrue(source["archive_available"])
        self.assertEqual(source["link_status"]["status"], "unverified")
        self.assertIsNone(source["link_status"]["checked_at"])
        self.assertIn("无核验记录", source["link_status"]["detail"])
        self.assertIn("当前", source["link_status"]["detail"])

    def test_exact_source_returns_full_text_url_date_grain_and_display_hash(self):
        archive = self.archive()
        self.assertEqual(archive["text"], self.body)
        self.assertEqual(archive["title"], "归档政策标题")
        self.assertEqual(archive["year"], "2026")
        self.assertEqual(archive["source_url"], self.source["url"])
        self.assertEqual(archive["archived_at"], "2026-08-28")
        self.assertEqual(archive["archive_sha256"], hashlib.sha256(self.body.encode("utf-8")).hexdigest())
        self.assertNotEqual(archive["archive_sha256"], self.source["sha256"])
        self.assertEqual(archive["archive_hash_scope"], "归档展示文本")
        self.assertFalse(archive["tls_verified"])
        self.assertIn("不一定发生过 TLS 握手失败", archive["limitations"])
        self.assertIn("不是原始文件哈希", archive["limitations"])

    def test_policy_from_another_source_is_not_returned(self):
        self.db.execute("UPDATE policy_texts SET source_id='local-policy:other'")
        self.assertIsNone(self.archive())

    def test_wrong_source_kind_is_rejected(self):
        self.db.execute("UPDATE sources SET kind='official_policy'")
        self.assertIsNone(self.archive())

    def test_wrong_policy_kind_is_rejected(self):
        self.db.execute("UPDATE policy_texts SET kind='service_area_text'")
        self.assertIsNone(self.archive())

    def test_unknown_ids_and_path_or_sql_injection_are_rejected(self):
        for source_id in ("missing", "../../server.py", "/etc/passwd", "' OR 1=1 --"):
            with self.subTest(source_id=source_id):
                self.assertIsNone(self.archive(source_id))

    def test_ambiguous_multiple_archives_do_not_pick_an_arbitrary_text(self):
        self.db.execute("INSERT INTO policy_texts VALUES(?,?,?,?,?,?,?)", (
            "text:other", self.source["id"], "2025", "另一篇政策", "其他正文", "[]", "local_policy_archive"))
        self.assertIsNone(self.archive())

    def test_empty_body_is_not_exposed_as_an_archive(self):
        for body in (None, "", " \n\t"):
            with self.subTest(body=body):
                self.db.execute("UPDATE policy_texts SET body=?", (body,))
                self.assertIsNone(self.archive())


if __name__ == "__main__":
    unittest.main()
