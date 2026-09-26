import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from build_data import Builder, SCHEMA, dump
from residential_identity_bridge_import import integrate


class ResidentialIdentityBridgeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(SCHEMA)
        self.builder = Builder(self.db)
        self.temp = tempfile.TemporaryDirectory(dir=APP / "data")
        self.path = Path(self.temp.name) / "reviewed.json"

        self.builder.entity("local:source", "平台全名", "residential", "滨江区")
        self.builder.entity(
            "osm:way:target", "地图简称", "residential", "滨江区", 30.2, 120.2
        )
        self.db.execute(
            "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)",
            ("project-source", "项目证据", "new_project", "fixture", "2026-09-27", None, "", "", ""),
        )
        self.db.execute(
            "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)",
            ("price-source", "价格证据", "transaction", "fixture", "2026-09-27", None, "", "", ""),
        )
        self.db.execute(
            "INSERT INTO projects VALUES(?,?,?,?,?)",
            (
                "project:one",
                "osm:way:target",
                "project-source",
                "2026-09-27",
                dump({"name": "平台全名", "aliases": ["地图简称"]}),
            ),
        )
        self.db.execute(
            "INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("price:one", "local:source", "price-source", "deal", "2026-09-27", "2026-09-01", 300, 30000, 100, "{}"),
        )

    def tearDown(self):
        self.temp.cleanup()
        self.db.close()

    def catalogue(self, **changes):
        bridge = {
            "source_entity_id": "local:source",
            "source_name": "平台全名",
            "target_entity_id": "osm:way:target",
            "target_name": "地图简称",
            "district": "滨江区",
            "evidence_project_id": "project:one",
            "price_ids": ["price:one"],
            "market_snapshot_ids": [],
        }
        bridge.update(changes.pop("bridge", {}))
        value = {
            "schema_version": 1,
            "reviewed": True,
            "reviewed_at": "2026-09-27",
            "source_as_of": "2026-09-27",
            "scope_policy": "fixture",
            "bridges": [bridge],
        }
        value.update(changes)
        self.path.write_text(json.dumps(value, ensure_ascii=False))
        return self.path

    def test_exact_reviewed_price_moves_and_other_edges_remain(self):
        self.db.execute(
            "INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?)",
            ("admission:one", "osm:way:target", "local:source", "price-source", "2026", "户籍生", 1, "{}"),
        )
        result = integrate(self.builder, self.catalogue())
        self.assertEqual(result["bridges"], 1)
        self.assertEqual(result["prices"], 1)
        self.assertEqual(
            self.db.execute("SELECT entity_id FROM prices WHERE id='price:one'").fetchone()[0],
            "osm:way:target",
        )
        self.assertEqual(
            self.db.execute("SELECT home_id FROM admissions WHERE id='admission:one'").fetchone()[0],
            "local:source",
        )
        mapping = self.db.execute(
            "SELECT entity_id,status,options FROM mappings WHERE source_id='residential-identity-bridges'"
        ).fetchone()
        self.assertEqual(mapping[:2], ("osm:way:target", "reviewed_residential_identity_bridge"))
        self.assertEqual(json.loads(mapping[2])["scope"], "prices_and_market_snapshots_only")

    def test_unlisted_source_price_rejects_stale_review(self):
        self.db.execute(
            "INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("price:two", "local:source", "price-source", "deal", "2026-09-27", "2026-09-02", 310, 31000, 100, "{}"),
        )
        with self.assertRaisesRegex(ValueError, "reviewed price_ids changed"):
            integrate(self.builder, self.catalogue())

    def test_cross_district_and_non_osm_targets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cross districts"):
            integrate(self.builder, self.catalogue(bridge={"district": "拱墅区"}))
        self.builder.entity("local:target", "另一个简称", "residential", "滨江区")
        with self.assertRaisesRegex(ValueError, "reviewed OSM"):
            integrate(self.builder, self.catalogue(bridge={
                "target_entity_id": "local:target", "target_name": "另一个简称"
            }))

    def test_project_must_live_on_target_and_name_the_source(self):
        self.db.execute(
            "UPDATE projects SET payload=? WHERE id='project:one'",
            (dump({"name": "其他项目", "aliases": ["地图简称"]}),),
        )
        with self.assertRaisesRegex(ValueError, "absent from target project evidence"):
            integrate(self.builder, self.catalogue())

    def test_unreviewed_catalogue_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not reviewed"):
            integrate(self.builder, self.catalogue(reviewed=False))


if __name__ == "__main__":
    unittest.main()
