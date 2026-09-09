from pathlib import Path
import sqlite3
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from build_data import Builder, SCHEMA
from school_observation_import import add_school_observation


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript(SCHEMA)
        self.builder = Builder(self.db)
        self.builder.entity('test-school', '杭州市新浦河小学', 'school', '滨江区')
        self.db.execute("INSERT INTO school_records(id,entity_id,year,official_id) VALUES('record','test-school','2026','L000000210001')")
        self.bundle = APP/'data/incremental/2026-09-09/official/supplemental_school_observations.json'
        self.row = self.builder.read(self.bundle)['records'][0]

    def tearDown(self):
        self.db.close()

    def test_progress_is_dated_evidence_not_current_opening_or_admission(self):
        self.assertTrue(add_school_observation(self.builder,self.bundle,self.row))
        self.assertEqual(self.db.execute('SELECT count(*) FROM admissions').fetchone()[0],0)
        body,kind = self.db.execute('SELECT body,kind FROM policy_texts').fetchone()
        self.assertEqual(kind,'construction_progress')
        self.assertIn('不是 9 月实际投用确认',body)
        self.assertIn('2026-07-16',body)
        self.assertFalse(add_school_observation(self.builder,self.bundle,self.row))

    def test_identity_year_and_non_admission_gates(self):
        for changes in ({'baseline_official_id':'wrong'},{'baseline_school_record_year':'2025'},
                        {'canonical_school_name':'其他小学'},{'creates_official_admission_relation':True},
                        {'admission_year':'2026'},{'residential_names':['测试小区']}):
            self.assertFalse(add_school_observation(self.builder,self.bundle,dict(self.row,**changes)))


if __name__=='__main__':
    unittest.main()
