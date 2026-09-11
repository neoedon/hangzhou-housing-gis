import hashlib
import http.client
import importlib.util
import json
import sqlite3
import threading
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gis_server", APP / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
build_spec = importlib.util.spec_from_file_location("gis_build", APP / "build_data.py")
builder = importlib.util.module_from_spec(build_spec)
build_spec.loader.exec_module(builder)


class DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = server.connection()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def count(self, table):
        return self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_corpus_and_depth(self):
        metrics = json.loads(self.db.execute("SELECT value FROM meta WHERE key='metrics'").fetchone()[0])
        increments = metrics.get('incremental', {})
        self.assertEqual(self.count("posts"), 3021 + increments.get('social_new_posts', 0))
        self.assertEqual(self.db.execute("SELECT count(*) FROM posts WHERE depth='detail_description'").fetchone()[0], 514 + increments.get('social_new_bodies', 0))
        baseline = json.loads((APP/'data/incremental/2026-09-09/xiaohongshu/baseline.json').read_text())
        loaded = {r[0] for r in self.db.execute('SELECT id FROM posts')}
        self.assertEqual(len(baseline['valid_ids']), 3021)
        self.assertTrue(set(baseline['valid_ids']) <= loaded)

    def test_source_counts(self):
        self.assertEqual(self.count("candidates"), 179)
        self.assertEqual(self.count("school_records"), 95)
        self.assertEqual(self.count("admissions"), 3701)
        self.assertEqual(dict(self.db.execute("SELECT admission_type,count(*) FROM admissions GROUP BY admission_type")), {"户籍生":1426,"新杭州人":2275})
        self.assertEqual(self.db.execute("SELECT count(DISTINCT school_id) FROM admissions a JOIN entities e ON e.id=a.school_id WHERE e.district='拱墅区' AND a.year='2026'").fetchone()[0], 58)

    def test_price_grains_remain_separate(self):
        self.assertEqual(dict(self.db.execute("SELECT kind,count(*) FROM prices WHERE source_id IN ('deals','listing-base','listing-overlay') GROUP BY kind")), {"deal":1542,"listing":67,"reference":24})
        self.assertEqual(self.db.execute("SELECT max(event_date) FROM prices WHERE source_id='deals'").fetchone()[0], "2026-06-23")
        self.assertEqual(self.db.execute("SELECT count(*) FROM prices WHERE kind!='deal' AND event_date IS NOT NULL").fetchone()[0], 0)

    def test_bootstrap_exposes_ranking_price_fields_and_duplicate_flag(self):
        payload = server.bootstrap(self.db)
        price_rows = [row for entity in payload['entities'] for row in entity.get('price_filter', [])]
        self.assertTrue(price_rows)
        self.assertTrue(all('unit_yuan_sqm' in row and 'possible_duplicate' in row for row in price_rows))
        for row in price_rows:
            self.assertIsInstance(row['possible_duplicate'], bool)

    def test_reviewed_market_increment_contract(self):
        metrics = json.loads(self.db.execute("SELECT value FROM meta WHERE key='metrics'").fetchone()[0])
        inc = metrics.get('incremental', {})
        if not inc.get('source_manifest'):
            self.skipTest('No reviewed incremental manifest is active')
        self.assertEqual(self.count('projects'), inc['projects'])
        self.assertEqual(self.count('market_snapshots'), inc['market_snapshots'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM prices WHERE source_id LIKE 'incremental-price:%' AND kind='deal'").fetchone()[0], inc['deals'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM prices WHERE source_id LIKE 'incremental-price:%' AND kind='listing'").fetchone()[0], inc['listings'])
        for row in self.db.execute("SELECT kind,event_date,total_wan,unit_yuan_sqm,payload FROM prices WHERE source_id LIKE 'incremental-price:%'"):
            payload = json.loads(row[4])
            self.assertNotEqual(payload.get('community_source_id'), '2011155836')
            self.assertFalse(payload.get('possible_duplicate_group'))
            if payload.get('price_disclosure') == 'masked_or_missing' and not payload.get('price_enrichments'):
                self.assertTrue(row[2] is None or row[3] is None)
            if row[0] == 'deal':
                self.assertIsNotNone(row[1])
        for payload, event_date in self.db.execute("SELECT payload,event_date FROM prices WHERE source_id LIKE 'new-project:%'"):
            p = json.loads(payload)
            self.assertFalse(p.get('font_encoded'))
            self.assertFalse(p.get('is_current_sale_quote'))
            self.assertIsNone(event_date)
        self.assertEqual(self.db.execute("SELECT count(*) FROM policy_texts WHERE kind='construction_progress'").fetchone()[0], inc.get('school_observations', 0))

    def test_price_enrichments_preserve_record_identity_and_evidence(self):
        metrics = json.loads(self.db.execute("SELECT value FROM meta WHERE key='metrics'").fetchone()[0])
        enriched = 0
        for row in self.db.execute("SELECT id,source_id,observed_at,total_wan,unit_yuan_sqm,payload FROM prices WHERE kind='deal'"):
            raw = json.loads(row[5])
            evidence = raw.get('price_enrichments', [])
            if not evidence:
                continue
            enriched += len(evidence)
            self.assertEqual(row[0], 'incremental-deal:' + builder.digest(raw['id']))
            self.assertEqual(row[1], 'incremental-price:' + raw['id'])
            self.assertEqual(row[2], raw['observed_at'])
            self.assertEqual(raw['price_disclosure'], 'masked_or_missing')
            for item in evidence:
                self.assertIsNotNone(self.db.execute('SELECT id FROM sources WHERE id=?', (item['source_id'],)).fetchone())
                self.assertIn(raw['id'], item['evidence']['comparison_prior_record_ids'])
                self.assertEqual(row[3], item['evidence']['total_wan'])
                self.assertEqual(row[4], item['evidence']['unit_yuan_sqm'])
        self.assertEqual(enriched, metrics.get('incremental', {}).get('deal_price_enrichments', 0))

    def test_candidate_mapping_review_status(self):
        self.assertEqual(dict(self.db.execute("SELECT status,count(*) FROM mappings WHERE source_id='candidates' GROUP BY status")), {"name_match_review_required":94,"ambiguous":5,"unmatched":80})

    def test_no_invented_coordinates(self):
        self.assertEqual(self.db.execute("SELECT count(*) FROM entities WHERE id NOT LIKE 'osm:%' AND lat IS NOT NULL AND location_status!='official_portal_gcj02_to_wgs84'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM entities WHERE location_status='official_portal_gcj02_to_wgs84'").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM entities WHERE lat IS NOT NULL").fetchone()[0], 5362)
        self.assertEqual(json.loads(self.db.execute("SELECT value FROM meta WHERE key='metrics'").fetchone()[0])["official_located_school_records"], 93)

    def test_no_future_year_backfill(self):
        self.assertEqual(self.db.execute("SELECT count(*) FROM admissions WHERE year='2029'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT year FROM school_records WHERE official_id='3133000614001'").fetchone()[0], "2024")

    def test_no_dangling_edges(self):
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_named_school_detail_has_real_evidence(self):
        detail = server.entity_detail(self.db, "osm:way:406342921")
        self.assertEqual(len(detail["admissions"]), 26)
        original_mentions = json.loads((APP.parent/'杭州主城区小红书楼盘研究_2025-12-01至2026-09-07/map/post_place_mentions.json').read_text())
        baseline_ids = set(json.loads((APP/'data/incremental/2026-09-09/xiaohongshu/baseline.json').read_text())['valid_ids'])
        expected = {p['post_id'] for p in original_mentions if p['place_key']=='osm:way:406342921' and p['post_id'] in baseline_ids}
        self.assertEqual(len(expected),16)
        self.assertTrue(expected <= {p['id'] for p in detail['posts']})
        self.assertTrue(any(p["kind"] == "service_area_text" for p in detail["policies"]))
        self.assertTrue(all("scope_note" in a["payload"] for a in detail["admissions"]))

    def test_gongshu_school_detail_has_real_evidence(self):
        detail = server.entity_detail(self.db, "osm:way:546716678")
        self.assertGreater(len(detail["admissions"]), 0)
        self.assertTrue(any(p["kind"] == "service_area_text" for p in detail["policies"]))

    def test_verified_campus_links_reach_official_admissions(self):
        detail = server.entity_detail(self.db, "osm:way:430812030")
        self.assertEqual({r["official_id"] for r in detail["school_records"]}, {"2133001524001"})
        self.assertEqual(len(detail["admissions"]), 39)
        self.assertTrue(any(link["kind"] == "documented_campus_affiliation" for link in detail["school_links"]))
        self.assertGreaterEqual(self.count("school_campus_links"), 14)

    def test_historical_candidate_snapshots_are_daily(self):
        metrics = json.loads(self.db.execute("SELECT value FROM meta WHERE key='metrics'").fetchone()[0])
        self.assertEqual(
            self.db.execute("SELECT count(DISTINCT snapshot_date) FROM history").fetchone()[0],
            len(metrics["snapshot_dates"]),
        )
        self.assertEqual(self.db.execute("SELECT count(*) FROM (SELECT snapshot_date,entity_id,count(*) n FROM history GROUP BY 1,2 HAVING n>1)").fetchone()[0], 0)

    def test_read_only_connection(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.db.execute("DELETE FROM posts WHERE 0")

    def test_all_original_inputs_still_identical(self):
        fingerprints = json.loads(self.db.execute("SELECT value FROM meta WHERE key='source_fingerprints'").fetchone()[0])
        self.assertGreater(len(fingerprints),60)
        for relative, expected in fingerprints.items():
            self.assertEqual(hashlib.sha256((APP.parent / relative).read_bytes()).hexdigest(), expected, relative)

    def test_public_url_strips_secrets_and_rejects_javascript(self):
        self.assertEqual(builder.safe_url("https://www.xiaohongshu.com/explore/abc?xsec_token=secret#key"), "https://www.xiaohongshu.com/explore/abc")
        self.assertEqual(builder.safe_url("javascript:alert(1)"), "")

    def test_school_match_preserves_campus(self):
        self.assertNotEqual(builder.norm("杭州江南实验学校（月明校区）",True), builder.norm("杭州江南实验学校",True))
        self.assertEqual(builder.norm("杭州市闻涛小学",True), builder.norm("闻涛小学",True))


class CampusYearTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(builder.SCHEMA)
        for eid, kind in (("campus", "school"), ("official", "school"), ("other-campus", "school"), ("home", "residential")):
            self.db.execute("INSERT INTO entities(id,kind,name,district,aliases) VALUES(?,?,?,?,?)", (eid, kind, eid, "拱墅区", "[]"))
        for eid, years in (("campus", ("2023", "2025")), ("official", ("2024", "2026"))):
            for year in years:
                key = f"{eid}:{year}"
                self.db.execute("INSERT INTO school_records(id,entity_id,year,official_id,payload) VALUES(?,?,?,?,?)", (key, eid, year, eid + "-number", "{}"))
                self.db.execute("INSERT INTO admissions(id,school_id,home_id,year,admission_type,active,payload) VALUES(?,?,?,?,?,?,?)", (key, eid, "home", year, "户籍生", 1, "{}"))
                self.db.execute("INSERT INTO policy_texts(id,year,school_ids,kind) VALUES(?,?,?,?)", (key, year, json.dumps([eid]), "service_area_text"))
        for campus, year in (("campus", "2026"), ("other-campus", "2024"), ("other-campus", "2026")):
            self.db.execute("INSERT INTO school_campus_links(id,campus_id,official_school_id,official_id,year,payload) VALUES(?,?,?,?,?,?)", (f"{campus}:{year}", campus, "official", "official-number", year, "{}"))

    def tearDown(self):
        self.db.close()

    def test_campus_expansion_keeps_bridge_year_and_own_history(self):
        detail = server.entity_detail(self.db, "campus")
        expected = {"campus:2023", "campus:2025", "official:2026"}
        for field in ("school_records", "admissions", "policies"):
            self.assertEqual({r["id"] for r in detail[field]}, expected, field)
        self.assertEqual({r["year"] for r in detail["school_links"]}, {"2026"})

    def test_direct_school_and_home_retain_all_historical_records(self):
        school = server.entity_detail(self.db, "official")
        for field in ("school_records", "admissions", "policies", "school_links"):
            self.assertEqual({r["year"] for r in school[field]}, {"2024", "2026"}, field)
        home = server.entity_detail(self.db, "home")
        self.assertEqual({r["year"] for r in home["admissions"]}, {"2023", "2024", "2025", "2026"})

    def test_bridge_must_match_official_number_and_year(self):
        self.db.execute("UPDATE school_campus_links SET official_id='different-number' WHERE campus_id='campus'")
        detail = server.entity_detail(self.db, "campus")
        for field in ("school_records", "admissions", "policies"):
            self.assertEqual({r["id"] for r in detail[field]}, {"campus:2023", "campus:2025"}, field)


class SourceArchiveTests(unittest.TestCase):
    def test_article_parser_extracts_only_inert_first_article_text(self):
        parser = server.ArticleTextParser()
        parser.feed('''<meta name="ArticleTitle" content="测试正文"><nav>站点导航</nav>
          <div class="article-content other"><h2>招生范围</h2><p>正文<strong>重点</strong><br>第二行</p>
          <script>throw new Error("do-not-include")</script><style>.hidden{color:red}</style>
          <iframe>hidden-frame</iframe><img src="https://example.invalid/x" onerror="alert(1)">
          <table><tr><td>学校</td><td>地址</td></tr></table></div>
          <footer>页尾</footer><div class="article-content">重复移动版</div>''')
        parser.close()
        text = parser.text()
        self.assertEqual(parser.title, "测试正文")
        self.assertIn("正文重点\n第二行", text)
        self.assertIn("学校 地址", text)
        for value in ("站点导航", "页尾", "重复移动版", "do-not-include", "color:red", "hidden-frame", "onerror", "<img", "<script"):
            self.assertNotIn(value, text)

    def test_archive_path_rejects_unapproved_sources_and_paths(self):
        for source in ({"id":"policy:../../server.py", "path":"housing-gis/server.py"},
                       {"id":"policy:admission_plan", "path":"housing-gis/data/housing.sqlite"},
                       {"id":"policy:admission_plan", "path":"/etc/passwd"},
                       {"id":"posts", "path":"reports/automation-4-2026-09-08/source_snapshot_2026-09-08-0832.json"}):
            self.assertIsNone(server.policy_archive_path(source))


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        cls.port = cls.httpd.server_port
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self,path,headers=None,method="GET"):
        c = http.client.HTTPConnection("127.0.0.1",self.port)
        c.request(method,path,headers=headers or {})
        response=c.getresponse()
        status,headers,body=response.status,dict(response.headers),response.read()
        c.close()
        return status,headers,body

    def test_health(self):
        status,headers,body=self.request("/api/health")
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)["database"],"read-only")
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])

    def test_controls_module_is_served_as_javascript(self):
        status, headers, body = self.request("/controls.js")
        self.assertEqual(status, 200)
        self.assertIn(headers["Content-Type"].split(";")[0], ("text/javascript", "application/javascript"))
        self.assertEqual(body, (APP / "web/controls.js").read_bytes())
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_icons_module_and_license_are_served_with_correct_types(self):
        for route, relative, mime in (("/icons.js", "icons.js", "javascript"),
                                      ("/assets/hugeicons-LICENSE.txt", "assets/hugeicons-LICENSE.txt", "text/plain")):
            status, headers, body = self.request(route)
            self.assertEqual(status, 200, route)
            self.assertIn(mime, headers["Content-Type"])
            self.assertEqual(body, (APP / "web" / relative).read_bytes())
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_policy_sources_expose_current_link_check_and_archive(self):
        bootstrap = json.loads(self.request("/api/bootstrap")[2])
        by_id = {source["id"]: source for source in bootstrap["sources"]}
        for key in server.POLICY_ARCHIVE_KEYS:
            sid = "policy:" + key
            source = json.loads(self.request("/api/source?id=" + sid)[2])
            self.assertEqual(source["link_status"], by_id[sid]["link_status"])
            self.assertTrue(source["archive_available"])
            if key in ("admission_plan", "new_schools_boundary_result"):
                self.assertEqual(source["link_status"]["status"], "unreachable")
                self.assertIn("未确认永久删除", source["link_status"]["detail"])
            else:
                self.assertEqual(source["link_status"]["status"], "unverified")
                self.assertIsNone(source["link_status"]["checked_at"])

    def test_local_archive_returns_text_with_provenance_for_all_four_sources(self):
        for key in server.POLICY_ARCHIVE_KEYS:
            status, headers, body = self.request("/api/source-archive?id=policy:" + key)
            self.assertEqual(status, 200, key)
            self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
            archive = json.loads(body)
            self.assertGreater(len(archive["text"]), 500, key)
            self.assertTrue(archive["title"])
            self.assertFalse(archive["tls_verified"])
            self.assertTrue(archive["archived_at"].startswith("2026-09-08T08:33:"))
            self.assertRegex(archive["archive_sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("不包含图片", archive["limitations"])
            self.assertNotIn("<script", archive["text"])
            self.assertNotIn("$('.article-content')", archive["text"])

    def test_archive_route_rejects_unknown_ids_and_path_injection(self):
        for sid in ("missing", "posts", "policy:../../server.py", "%2Fetc%2Fpasswd", "policy:admission_plan%2F..%2F.."):
            self.assertEqual(self.request("/api/source-archive?id=" + sid)[0], 404)

    def test_no_path_traversal_or_workspace_serving(self):
        for path in ("/../GIS_项目整合方案_2026-09-08.md","/data/housing.sqlite","/assets/%2e%2e/%2e%2e/server.py","/server.py"):
            from urllib.parse import quote
            self.assertEqual(self.request(quote(path,safe='/%'))[0],404)

    def test_block_dns_rebinding_and_external_origins(self):
        self.assertEqual(self.request("/api/bootstrap",{"Host":"evil.example"})[0],403)
        self.assertEqual(self.request("/api/bootstrap",{"Origin":"https://evil.example"})[0],403)

    def test_mutation_rejected(self):
        self.assertEqual(self.request("/api/entity",method="POST")[0],405)

    def test_unknown_entity_and_sql_injection_are_safe(self):
        self.assertEqual(self.request("/api/entity?id=%27%20OR%201%3D1--")[0],404)

    def test_future_snapshot_returns_empty(self):
        self.assertEqual(json.loads(self.request("/api/snapshot?date=2029-01-01")[2])["records"],[])

    def test_stale_dataset_revision_rejected(self):
        self.assertEqual(self.request("/api/entity?id=anything&revision=old")[0],409)

    def test_wildcards_are_literal_in_post_search(self):
        result=json.loads(self.request("/api/posts?q=%25%25%25%25%25")[2])
        self.assertEqual(result["total"],0)

    def test_post_search_filters_after_entire_corpus_not_first_page(self):
        result=json.loads(self.request("/api/posts?q=")[2])
        with server.connection() as db:
            expected = {r[0] for r in db.execute('SELECT id FROM posts')}
        self.assertGreaterEqual(len(expected),3021)
        self.assertEqual(result["total"],len(expected))
        self.assertEqual({p['id'] for p in result['posts']},expected)


if __name__ == '__main__':
    unittest.main()
