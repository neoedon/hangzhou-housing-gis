import tempfile
import unittest
from pathlib import Path

import export_pages


class PagesExportTests(unittest.TestCase):
    def test_entity_shard_matches_javascript_fixtures(self):
        self.assertEqual(export_pages.entity_shard("osm:way:430812030"), "104")
        self.assertEqual(export_pages.entity_shard("official:school:2133001518001"), "043")
        self.assertNotEqual(export_pages.entity_shard("osm:way:430812030"), export_pages.entity_shard("osm:way:430812031"))

    def test_public_index_adds_static_marker_security_and_relative_assets(self):
        source = '<html><head></head><body><span class="status-dot"></span>本地资料库 <link href="./app.css">搜索仅在本地资料库进行。</body></html>'
        result = export_pages.public_index(source)
        self.assertIn('name="gis-static-data" content="true"', result)
        self.assertIn('http-equiv="Content-Security-Policy"', result)
        self.assertIn("公开静态资料库", result)
        self.assertIn("搜索仅在本地资料库进行。", result)
        self.assertIn('href="./app.css"', result)

    def test_public_index_refuses_ambiguous_template(self):
        with self.assertRaises(ValueError):
            export_pages.public_index("<head></head>本地资料库")

    def test_public_increment_fields_are_only_accepted_ui_aggregates(self):
        expected = {
            "projects", "deals", "listings", "market_snapshots", "deal_price_enrichments",
            "official_local_policies", "official_local_policy_entity_mentions",
            "social_new_posts", "social_new_bodies",
        }
        self.assertEqual(set(export_pages.PUBLIC_INCREMENT_KEYS), expected)
        self.assertNotIn("excluded_records", export_pages.PUBLIC_INCREMENT_KEYS)


if __name__ == "__main__":
    unittest.main()
