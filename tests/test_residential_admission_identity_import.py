import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from build_data import Builder, SCHEMA, dump, digest
from residential_admission_identity_import import integrate


class ResidentialAdmissionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(SCHEMA)
        self.builder = Builder(self.db)
        self.temp = tempfile.TemporaryDirectory(dir=APP / "data")
        self.path = Path(self.temp.name) / "reviewed.json"

        self.builder.entity("local:official-home", "备案小区名", "residential", "滨江区")
        self.builder.entity(
            "osm:way:target", "品牌楼盘名", "residential", "滨江区", 30.2, 120.2
        )
        self.builder.entity("osm:way:school", "测试小学", "school", "滨江区", 30.21, 120.21)
        self.db.execute(
            "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "official-source", "官方名单", "official_admissions", "fixture",
                "2026-09-27", "2026", "", "", "",
            ),
        )
        self.db.execute(
            "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "project-source", "项目证据", "new_project", "fixture",
                "2026-09-27", None, "", "", "",
            ),
        )
        self.db.execute(
            "INSERT INTO projects VALUES(?,?,?,?,?)",
            (
                "project:one", "osm:way:target", "project-source", "2026-09-27",
                dump({"name": "品牌楼盘名", "aliases": ["备案小区名"]}),
            ),
        )
        self.db.execute(
            "INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?)",
            (
                "admission:one", "osm:way:school", "local:official-home", "official-source",
                "2026", "户籍生", 1,
                dump({"residential_name": "备案小区名", "display_name": "备案小区名"}),
            ),
        )

    def tearDown(self):
        self.temp.cleanup()
        self.db.close()

    def catalogue(self, **changes):
        bridge = {
            "source_entity_id": "local:official-home",
            "source_name": "备案小区名",
            "target_entity_id": "osm:way:target",
            "target_name": "品牌楼盘名",
            "district": "滨江区",
            "year": "2026",
            "evidence_project_id": "project:one",
            "identity_basis": "exact_official_name_in_project_name_or_alias",
            "admission_id_set": {
                "count": 1,
                "sha256": digest(dump(["admission:one"])),
            },
        }
        bridge.update(changes.pop("bridge", {}))
        value = {
            "schema_version": 1,
            "reviewed": True,
            "reviewed_at": "2026-09-27",
            "source_as_of": "2026",
            "scope_policy": "fixture",
            "bridges": [bridge],
        }
        value.update(changes)
        self.path.write_text(json.dumps(value, ensure_ascii=False))
        return self.path

    def test_moves_only_hash_locked_official_admissions(self):
        self.db.execute(
            "INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "price:one", "local:official-home", "official-source", "reference",
                "2026-09-27", None, None, 30000, None, "{}",
            ),
        )
        result = integrate(self.builder, self.catalogue())
        self.assertEqual((result["bridges"], result["admissions"]), (1, 1))
        self.assertEqual(
            self.db.execute(
                "SELECT home_id FROM admissions WHERE id='admission:one'"
            ).fetchone()[0],
            "osm:way:target",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT entity_id FROM prices WHERE id='price:one'"
            ).fetchone()[0],
            "local:official-home",
        )
        mapping = self.db.execute(
            "SELECT entity_id,status,options FROM mappings "
            "WHERE source_id='residential-admission-identity-bridges'"
        ).fetchone()
        self.assertEqual(
            mapping[:2],
            ("osm:way:target", "reviewed_residential_admission_identity_bridge"),
        )
        self.assertEqual(json.loads(mapping[2])["scope"], "admissions_only")

    def test_changed_relation_set_is_rejected(self):
        self.db.execute(
            "INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?)",
            (
                "admission:two", "osm:way:school", "local:official-home", "official-source",
                "2026", "新杭州人", 1,
                dump({"residential_name": "备案小区名"}),
            ),
        )
        with self.assertRaisesRegex(ValueError, "reviewed IDs changed"):
            integrate(self.builder, self.catalogue())

    def test_project_alias_and_relation_payload_are_both_required(self):
        self.db.execute(
            "UPDATE projects SET payload=? WHERE id='project:one'",
            (dump({"name": "品牌楼盘名", "aliases": []}),),
        )
        with self.assertRaisesRegex(ValueError, "absent from target project"):
            integrate(self.builder, self.catalogue())
        self.db.execute(
            "UPDATE projects SET payload=? WHERE id='project:one'",
            (dump({"name": "品牌楼盘名", "aliases": ["备案小区名"]}),),
        )
        self.db.execute(
            "UPDATE admissions SET payload=? WHERE id='admission:one'",
            (dump({"residential_name": "其他小区"}),),
        )
        with self.assertRaisesRegex(ValueError, "name changed inside admission"):
            integrate(self.builder, self.catalogue())

    def test_existing_target_relation_is_rejected(self):
        self.db.execute(
            "INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?)",
            (
                "admission:target", "osm:way:school", "osm:way:target", "official-source",
                "2026", "户籍生", 1,
                dump({"residential_name": "品牌楼盘名"}),
            ),
        )
        with self.assertRaisesRegex(ValueError, "target already has"):
            integrate(self.builder, self.catalogue())

    def test_nonofficial_cross_district_and_non_osm_relations_are_rejected(self):
        self.db.execute("UPDATE sources SET kind='fixture' WHERE id='official-source'")
        with self.assertRaisesRegex(ValueError, "only accepts official"):
            integrate(self.builder, self.catalogue())
        self.db.execute("UPDATE sources SET kind='official_admissions' WHERE id='official-source'")
        with self.assertRaisesRegex(ValueError, "cross districts"):
            integrate(
                self.builder,
                self.catalogue(bridge={"district": "拱墅区"}),
            )
        self.builder.entity("local:target", "品牌楼盘名", "residential", "滨江区")
        with self.assertRaisesRegex(ValueError, "reviewed OSM"):
            integrate(
                self.builder,
                self.catalogue(
                    bridge={
                        "target_entity_id": "local:target",
                        "target_name": "品牌楼盘名",
                    }
                ),
            )

    def test_unreviewed_catalogue_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not reviewed"):
            integrate(self.builder, self.catalogue(reviewed=False))


if __name__ == "__main__":
    unittest.main()
