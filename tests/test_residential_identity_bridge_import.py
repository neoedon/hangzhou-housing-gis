import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from build_data import Builder, SCHEMA, dump, digest
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

    def test_hashed_record_set_moves_only_the_reviewed_complete_set(self):
        path = self.catalogue(bridge={
            "price_ids": None,
            "price_id_set": {"count": 1, "sha256": digest(dump(["price:one"]))},
        })
        document = json.loads(path.read_text())
        document["bridges"][0].pop("price_ids")
        path.write_text(json.dumps(document, ensure_ascii=False))
        result = integrate(self.builder, path)
        self.assertEqual(result["prices"], 1)
        mapping = json.loads(self.db.execute(
            "SELECT options FROM mappings WHERE source_id='residential-identity-bridges'"
        ).fetchone()[0])
        self.assertEqual(mapping["price_record_count"], 1)
        self.assertEqual(mapping["price_record_sha256"], digest(dump(["price:one"])))

    def test_hashed_record_set_rejects_changed_count_or_hash(self):
        for lock in ({"count": 2, "sha256": digest(dump(["price:one"]))},
                     {"count": 1, "sha256": "0" * 64}):
            path = self.catalogue(bridge={"price_ids": None, "price_id_set": lock})
            document = json.loads(path.read_text())
            document["bridges"][0].pop("price_ids")
            path.write_text(json.dumps(document, ensure_ascii=False))
            with self.assertRaisesRegex(ValueError, "reviewed price_ids changed"):
                integrate(self.builder, path)

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

    def test_brand_prefix_basis_requires_exact_composition_and_project_corroboration(self):
        self.db.execute("UPDATE entities SET name='品牌项目名' WHERE id='local:source'")
        self.db.execute("UPDATE entities SET name='项目名' WHERE id='osm:way:target'")
        self.db.execute(
            "UPDATE projects SET payload=? WHERE id='project:one'",
            (dump({"name": "项目名", "aliases": [], "developer": "品牌集团"}),),
        )
        bridge = {
            "source_name": "品牌项目名",
            "target_name": "项目名",
            "identity_basis": "brand_prefix_plus_project_name",
            "brand_prefix": "品牌",
            "base_name": "项目名",
        }
        self.assertEqual(integrate(self.builder, self.catalogue(bridge=bridge))["prices"], 1)

    def test_project_text_basis_requires_reviewed_phrase_containing_source_name(self):
        self.db.execute(
            "UPDATE projects SET payload=? WHERE id='project:one'",
            (dump({
                "name": "地图简称",
                "aliases": [],
                "field_values": {"项目介绍": "平台全名花园已交付"},
            }),),
        )
        bridge = {
            "identity_basis": "project_text_contains_source_name",
            "project_evidence_field": "field_values.项目介绍",
            "evidence_contains": "平台全名花园",
        }
        self.assertEqual(integrate(self.builder, self.catalogue(bridge=bridge))["prices"], 1)

    def test_project_phase_suffix_requires_exact_name_and_permit_evidence(self):
        self.db.execute("UPDATE entities SET name='项目名一区' WHERE id='local:source'")
        self.db.execute("UPDATE entities SET name='项目名' WHERE id='osm:way:target'")
        self.db.execute(
            "UPDATE projects SET payload=? WHERE id='project:one'",
            (dump({
                "name": "项目名",
                "aliases": [],
                "presale_permits": [{"buildings_raw": "一区1#、一区8#"}],
            }),),
        )
        bridge = {
            "source_name": "项目名一区",
            "target_name": "项目名",
            "identity_basis": "project_phase_suffix",
            "base_name": "项目名",
            "phase_suffix": "一区",
        }
        self.assertEqual(integrate(self.builder, self.catalogue(bridge=bridge))["prices"], 1)

    def test_project_phase_suffix_rejects_missing_permit_phase(self):
        self.db.execute("UPDATE entities SET name='项目名一区' WHERE id='local:source'")
        self.db.execute("UPDATE entities SET name='项目名' WHERE id='osm:way:target'")
        self.db.execute(
            "UPDATE projects SET payload=? WHERE id='project:one'",
            (dump({
                "name": "项目名",
                "aliases": [],
                "presale_permits": [{"buildings_raw": "二区1#"}],
            }),),
        )
        bridge = {
            "source_name": "项目名一区",
            "target_name": "项目名",
            "identity_basis": "project_phase_suffix",
            "base_name": "项目名",
            "phase_suffix": "一区",
        }
        with self.assertRaisesRegex(ValueError, "phase marker is absent"):
            integrate(self.builder, self.catalogue(bridge=bridge))

    def test_unreviewed_catalogue_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not reviewed"):
            integrate(self.builder, self.catalogue(reviewed=False))


if __name__ == "__main__":
    unittest.main()
