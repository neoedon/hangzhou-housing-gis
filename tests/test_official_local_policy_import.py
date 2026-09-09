import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from build_data import Builder, SCHEMA, digest, dump
from incremental_import import integrate
from official_local_policy_import import integrate_local_policies


class LocalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript(SCHEMA)
        self.builder = Builder(self.db)
        self.temp = tempfile.TemporaryDirectory(prefix='local-policy-test-', dir=APP / 'data/incremental')
        self.folder = Path(self.temp.name)
        self.base = self.folder / 'sealed'
        (self.base / 'originals').mkdir(parents=True)
        self.bundle = self.folder / 'projection.json'
        self.builder.entity('school:a', '测试小学', 'school', '滨江区', 30.2, 120.2)
        self.builder.entity('home:a', '测试花园', 'residential', '滨江区', 30.21, 120.21)
        self.builder.entity('school:b', '另外小学', 'school', '拱墅区')
        self.db.execute("INSERT INTO school_records(id,entity_id,year,official_id,payload) VALUES('school-a-2026','school:a','2026','official-current','{}')")
        self.sources = [self.source_html(), self.source_json()]
        self.rows = [self.row(self.sources[0], [('school:a', '测试小学', 'school', '杭州市测试小学'),
                                              ('home:a', '测试花园', 'residential', '测试花园')]),
                     self.row(self.sources[1], [('school:b', '另外小学', 'school', '杭州市另外小学')])]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def descriptor(self, path):
        raw = path.read_bytes()
        return dict(path=str(path.relative_to(APP.parent)), absolute_path=str(path), bytes=len(raw), sha256=digest(raw))

    def source_html(self):
        article = ('根据有关义务教育法律法规，现公布2025学年公办小学招生服务区。\n'
                   '杭州市测试小学\n测试花园（自愿原则）。\n'
                   '本公告保留原始道路边界与报名条件，仅适用于2025学年，不得跨年推断。'
                   '测试街道道路边界须另行核对，家庭报名资格和实际录取结果须依当年教育部门规定确定。\n2025年5月27日')
        title = '2025年小学服务区公告'
        html = '<meta name="ArticleTitle" content="' + title + '"><meta name="PubDate" content="2025-07-07 16:15">'
        html += '<div class="article-content">' + ''.join('<p>' + part + '</p>' for part in article.splitlines()) + '</div>'
        original = self.folder / 'original.body'
        original.write_text(html)
        (self.base / 'originals/preserved.body').write_text(html)
        headers = self.folder / 'capture.headers'
        headers.write_text('Date: Tue, 08 Sep 2026 00:33:28 GMT\n')
        url = 'https://www.hhtz.gov.cn/art/2025/7/7/test.html'
        metadata = self.folder / 'metadata.json'
        metadata.write_text(dump(dict(run_at_cst='2026-09-08 08:32:49 CST', official_dynamic_detail_sources={
            'test': dict(url=url, raw_sha256=digest(original.read_bytes()), tls_verified=False,
                         date_header='Tue, 08 Sep 2026 00:33:28 GMT')})))
        return dict(id='local-official:binjiang-test:2025', district='滨江区', source_year='2025', title=title,
                    url=url, publication_date='2025-07-07', document_signed_date='2025-05-27',
                    original_observation=dict(exact_client_observed_at=None,
                        original_snapshot_run_at='2026-09-08 08:32:49 CST',
                        server_response_date_header='Tue, 08 Sep 2026 00:33:28 GMT'),
                    tls_verified=False, tls_note='Original capture did not verify its TLS certificate.',
                    this_run_network_fetch=False, original_file=self.descriptor(original),
                    metadata_source=self.descriptor(metadata), original_headers_file=self.descriptor(headers),
                    preserved_copy='originals/preserved.body', full_original_article_text=article,
                    full_original_visible_page_text=article, text_sha256=digest(article))

    def source_json(self):
        article = ('根据公开决策程序，现公布杭州市另外小学的道路边界调整结果。\n'
                   '道路以东、河流以西、街道以南的区域为政策文字描述。本公告未列明住宅名称，'
                   '不得使用地图内地点标签推导具体小区名单。学区名称与校区身份需另行核对，不生成住宅招生关系。\n'
                   '本方案自2026学年起施行。\n拱墅区教育局\n2026年5月29日')
        title = '另外小学学区调整结果公告'
        visible = title + '\n发布日期：2026-05-29 18:31\n' + article + '\n页脚导航'
        original = dict(title=title, url='https://www.gongshu.gov.cn/art/2026/test.html',
                        text=visible, observed_at='2026-09-07T01:56:13.822Z', links=[])
        path = self.folder / 'original.json'
        path.write_text(dump(original))
        (self.base / 'originals/preserved.json').write_text(dump(original))
        return dict(id='local-official:gongshu-test:2026', district='拱墅区', source_year='2026',
                    title=title, url=original['url'], publication_date='2026-05-29',
                    original_observation=dict(exact_client_observed_at=original['observed_at']),
                    tls_verified=None, tls_note='Original visible JSON has no TLS handshake evidence.',
                    this_run_network_fetch=False, original_file=self.descriptor(path),
                    preserved_copy='originals/preserved.json', full_original_article_text=article,
                    full_original_visible_page_text=visible, text_sha256=digest(visible))

    def row(self, source, attachments):
        return dict(id=source['id'], source=source, creates_official_admission_relation=False, approved=False,
                    source_bundle_path=str(self.base.relative_to(APP.parent)),
                    related_entities=[dict(entity_id=eid, entity_name=name, kind=kind, district=source['district'],
                                           source_name=source_name) for eid, name, kind, source_name in attachments])

    def registered(self, rows=None):
        rows = self.rows if rows is None else rows
        source_path = self.base / 'sources.json'
        unique = {row['source']['id']: row['source'] for row in rows}
        source_path.write_text(dump(dict(schema_version=1, sources=list(unique.values()))))
        sha = digest(source_path.read_bytes())
        for row in rows:
            row['source_bundle_sources_sha256'] = sha
        self.bundle.write_text(dump(rows))
        records = self.builder.read(self.bundle)
        return records, digest(self.bundle.read_bytes())

    def run_import(self, rows=None):
        records, sha = self.registered(rows)
        return integrate_local_policies(self.builder, self.bundle, records, reviewed_sha256=sha)

    def untouched_tables(self):
        return {table: self.db.execute('SELECT * FROM ' + table).fetchall()
                for table in ('entities', 'school_records', 'admissions', 'school_campus_links', 'mappings')}

    def test_imports_full_documents_with_school_and_residential_access_without_relations(self):
        before = self.untouched_tables()
        result = self.run_import()
        self.assertEqual(result, dict(official_local_policies=2, official_local_policy_sources=2,
                                     official_local_policy_entity_mentions=3))
        self.assertEqual(before, self.untouched_tables())
        self.assertEqual(self.db.execute("SELECT count(*) FROM school_records WHERE year='2025'").fetchone()[0], 0)
        rows = self.db.execute('SELECT year,body,school_ids,kind FROM policy_texts ORDER BY year').fetchall()
        self.assertEqual(json.loads(rows[0][2]), ['school:a', 'home:a'])
        self.assertIn(self.sources[0]['full_original_article_text'], rows[0][1])
        self.assertIn(self.sources[1]['full_original_visible_page_text'], rows[1][1])
        self.assertTrue(all(row[3] == 'local_policy_archive' for row in rows))
        self.assertTrue(self.db.in_transaction)

    def test_observation_precision_and_tls_uncertainty_are_preserved(self):
        self.run_import()
        rows = self.db.execute('SELECT observed_at,source_as_of,notes FROM sources ORDER BY source_as_of').fetchall()
        self.assertIsNone(rows[0][0])
        self.assertIn('2026-09-08 08:32:49 CST', rows[0][2])
        self.assertIn('server_response_date_header', rows[0][2])
        self.assertIn('TLS 证书未核验', rows[0][2])
        self.assertEqual(rows[1][0], '2026-09-07T01:56:13.822Z')
        self.assertIn('缺少 TLS 握手', rows[1][2])

    def test_source_files_and_preserved_copies_are_hash_registered(self):
        self.run_import()
        for source in self.sources:
            original = source['original_file']
            self.assertEqual(self.builder.hashes[original['path']], original['sha256'])
            copy_path = str((self.base / source['preserved_copy']).relative_to(APP.parent))
            self.assertEqual(self.builder.hashes[copy_path], original['sha256'])
        self.assertIn(self.sources[0]['metadata_source']['path'], self.builder.hashes)

    def test_identical_reimport_is_idempotent_and_does_not_commit(self):
        self.run_import()
        self.assertEqual(self.run_import()['official_local_policies'], 0)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM policy_texts').fetchone()[0], 0)

    def test_missing_or_unregistered_review_hash_and_argument_tampering_reject(self):
        records, sha = self.registered()
        for expected in (None, '0' * 64):
            with self.assertRaises(ValueError):
                integrate_local_policies(self.builder, self.bundle, records, reviewed_sha256=expected)
        altered = copy.deepcopy(records)
        altered[0]['source']['title'] = '伪造标题'
        with self.assertRaises(ValueError):
            integrate_local_policies(self.builder, self.bundle, altered, reviewed_sha256=sha)
        del self.builder.hashes[str(self.bundle.relative_to(APP.parent))]
        with self.assertRaises(ValueError):
            integrate_local_policies(self.builder, self.bundle, records, reviewed_sha256=sha)

    def test_raw_or_preserved_source_change_rejects_before_any_writes(self):
        for path in (Path(self.sources[0]['original_file']['absolute_path']), self.base / self.sources[0]['preserved_copy']):
            old = path.read_bytes()
            path.write_bytes(old + b'changed')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                self.run_import()
            path.write_bytes(old)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0], 0)

    def test_sealed_source_diff_and_cross_root_paths_reject(self):
        rows, sha = self.registered()
        (self.base / 'sources.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            integrate_local_policies(self.builder, self.bundle, rows, reviewed_sha256=sha)
        changed = copy.deepcopy(self.rows)
        changed[0]['source']['original_file']['path'] = '/etc/passwd'
        changed[0]['source']['original_file'].pop('absolute_path')
        with self.assertRaisesRegex(ValueError, 'outside allowed roots'):
            self.run_import(changed)

    def test_duplicate_missing_ambiguous_or_wrong_district_entities_reject(self):
        changes = ({'entity_id': 'absent'}, {'entity_name': '改名'}, {'district': '拱墅区'},
                   {'source_name': '文中未提及小区'}, {'kind': 'road'})
        for change in changes:
            rows = copy.deepcopy(self.rows)
            rows[0]['related_entities'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_import(rows)
        rows = copy.deepcopy(self.rows)
        rows[0]['related_entities'].append(copy.deepcopy(rows[0]['related_entities'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate policy entity'):
            self.run_import(rows)
        self.builder.entity('school:ambiguous', '测试小学', 'school', '滨江区')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.run_import()

    def test_changed_article_year_url_tls_or_capture_time_are_rejected(self):
        changes = ({'full_original_article_text': self.sources[0]['full_original_article_text'][:-1]},
                   {'source_year': '2026'}, {'url': 'https://evil.example/policy'}, {'tls_verified': True},
                   {'this_run_network_fetch': True}, {'publication_date': '2025-07-08'},
                   {'original_observation': dict(exact_client_observed_at='2026-09-08T08:33:28+08:00')})
        for change in changes:
            rows = copy.deepcopy(self.rows)
            rows[0]['source'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_import(rows)
        self.assertEqual(self.db.execute('SELECT count(*) FROM policy_texts').fetchone()[0], 0)

    def test_admission_permission_and_duplicate_policy_ids_reject_whole_bundle(self):
        rows = copy.deepcopy(self.rows)
        rows[1]['creates_official_admission_relation'] = True
        with self.assertRaisesRegex(ValueError, 'no admissions'):
            self.run_import(rows)
        with self.assertRaisesRegex(ValueError, 'Duplicate policy identity'):
            self.run_import([self.rows[0], self.rows[0]])
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0], 0)

    def test_manifest_entrypoint_enforces_review_and_returns_independent_counts(self):
        records, sha = self.registered()
        manifest = self.folder / 'reviewed.json'
        value = dict(schema_version=1, reviewed=True, bundles=[dict(kind='official_local_policies',
            path=str(self.bundle.relative_to(APP.parent)), sha256=sha, records=len(records))])
        manifest.write_text(dump(value))
        result = integrate(self.builder, manifest)
        self.assertEqual(result['official_local_policies'], 2)
        self.assertEqual(result['official_local_policy_entity_mentions'], 3)
        self.assertEqual(result['deals'], 0)
        value['reviewed'] = False
        manifest.write_text(dump(value))
        with self.assertRaisesRegex(ValueError, 'not passed review'):
            integrate(self.builder, manifest)


if __name__ == '__main__':
    unittest.main()
