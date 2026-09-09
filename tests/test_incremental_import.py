import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from build_data import Builder, SCHEMA, dump, digest, safe_url
from incremental_import import add_project, add_price, add_market_snapshot, integrate, prepare_market_identities


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript(SCHEMA)
        self.builder = Builder(self.db)
        self.bundle = APP / 'data/incremental/2026-09-09/test-fixture.json'

    def tearDown(self):
        self.db.close()

    def project(self, **changes):
        row = dict(project_id='leju:hangzhou:123',name='测试住宅一期',district='滨江区',address='测试路',
                   aliases=[],detail_fetch_status='ok',district_consistent=True,is_residential=True,
                   detail_observed_at='2026-09-09T00:30:00+08:00',source_url='https://hangzhou.leju.com/house/123/xinxi/',
                   prices=[dict(price_type='platform_reference',amount=30000,unit='元/㎡',price_as_of=None,
                                observed_at='2026-09-09',source_url='https://hangzhou.leju.com/house/123/xinxi/')])
        row.update(changes)
        return row

    def deal(self, **changes):
        row = dict(id='fang:deal:1',record_kind='deal',community='测试住宅一期',district='滨江区',
                   event_date='2026-08-12',area_sqm=89,total_wan=0,unit_yuan_sqm=None,
                   observed_at='2026-09-09',source_url='https://hz.esf.fang.com/chengjiao/1',
                   business_fingerprint='stable-key')
        row.update(changes)
        return row

    def market_rows(self, community='钱江彩虹城', directory='彩虹城'):
        common = dict(community_source_id='123',district='滨江区',observed_at='2026-09-09',
                      source_url='https://m.fang.com/chengjiao/hz/?projcode=123')
        deal = self.deal(**common, community=community, community_directory_name=directory)
        listing = dict(common,id='fang:listing:1',record_kind='listing',community=directory,total_wan=300,
                       area_sqm=89,unit_yuan_sqm=None,event_date=None)
        reference = dict(common,id='fang:reference:1',record_kind='community_reference',community=directory,
                         reference_unit_yuan_sqm=35000,source_as_of=None,address='未经核验地址',built_year_source='1900')
        return deal, listing, reference

    def make_manifest(self, directory, entries):
        bundles = []
        for kind, records in entries:
            path = Path(directory) / (kind + '.json')
            path.write_text(dump(records))
            rows = records['records'] if isinstance(records, dict) else records
            bundles.append(dict(kind=kind,path=str(path.relative_to(APP.parent)),
                                sha256=digest(path.read_bytes()),records=len(rows)))
        path = Path(directory) / 'manifest.json'
        path.write_text(dump(dict(schema_version=1,reviewed=True,bundles=bundles)))
        return path

    def test_project_keeps_reference_grain_and_unknown_statistical_date(self):
        self.assertTrue(add_project(self.builder,self.bundle,self.project()))
        row = self.db.execute('SELECT kind,event_date,total_wan,unit_yuan_sqm,payload FROM prices').fetchone()
        self.assertEqual(row[:4],('reference',None,None,30000))
        self.assertIsNone(json.loads(row[4])['price_as_of'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM entities WHERE lat IS NOT NULL').fetchone()[0],0)

    def test_failed_wrong_district_commercial_and_missing_provenance_do_not_enter(self):
        for changes in ({'detail_fetch_status':'403'},{'district_consistent':False},{'is_residential':False},{'source_url':'javascript:bad()'}):
            self.assertFalse(add_project(self.builder,self.bundle,self.project(**changes)))
        self.assertEqual(self.db.execute('SELECT count(*) FROM projects').fetchone()[0],0)

    def test_encoded_directory_numbers_and_ranges_are_not_unit_quotes(self):
        bad = [dict(price_type='directory_model_reference',amount=94962,unit='元/㎡'),
               dict(price_type='platform_reference',amount=94962,unit='元/㎡',font_encoded=True),
               dict(price_type='platform_reference',amount=100,unit='万元/套'),
               dict(price_type='platform_reference',amount=10000,amount_high=30000,unit='元/㎡')]
        self.assertTrue(add_project(self.builder,self.bundle,self.project(prices=bad)))
        self.assertEqual(self.db.execute('SELECT count(*) FROM prices').fetchone()[0],0)

    def test_same_project_is_idempotent(self):
        self.assertTrue(add_project(self.builder,self.bundle,self.project()))
        self.assertFalse(add_project(self.builder,self.bundle,self.project()))
        self.assertEqual(self.db.execute('SELECT count(*) FROM projects').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM prices').fetchone()[0],1)

    def test_masked_deal_prices_remain_null_and_date_is_actual_event(self):
        self.assertTrue(add_price(self.builder,self.bundle,self.deal()))
        self.assertEqual(self.db.execute('SELECT kind,total_wan,unit_yuan_sqm,event_date FROM prices').fetchone(),('deal',None,None,'2026-08-12'))
        self.assertFalse(add_price(self.builder,self.bundle,self.deal()))

    def test_record_identity_does_not_change_with_observed_business_enrichment(self):
        self.assertTrue(add_price(self.builder,self.bundle,self.deal()))
        self.assertFalse(add_price(self.builder,self.bundle,self.deal(business_fingerprint='enriched-fields',total_wan=350)))
        self.assertEqual(self.db.execute('SELECT count(*) FROM prices').fetchone()[0],1)

    def test_safe_url_only_retains_numeric_fang_project_identifier(self):
        self.assertEqual(safe_url('https://m.fang.com/chengjiao/hz/?projcode=123&token=secret#fragment'),
                         'https://m.fang.com/chengjiao/hz/?projcode=123')
        for suffix in ('?projcode=one','?projcode=1&projcode=2','?projcode=123%2F4','?token=secret'):
            self.assertEqual(safe_url('https://m.fang.com/chengjiao/hz/' + suffix),'https://m.fang.com/chengjiao/hz/')
        self.assertEqual(safe_url('https://example.com/chengjiao/hz/?projcode=123'),'https://example.com/chengjiao/hz/')
        self.assertEqual(safe_url('https://m.fang.com/esf/hz/?projcode=123'),'https://m.fang.com/esf/hz/')
        self.assertEqual(safe_url('https://user:secret@m.fang.com/chengjiao/hz/?projcode=123'),'')

    def test_project_query_survives_price_and_source_links(self):
        row = self.market_rows()[0]
        self.assertTrue(add_price(self.builder,self.bundle,row))
        self.assertEqual(self.db.execute('SELECT url FROM sources').fetchone()[0],row['source_url'])
        self.assertEqual(json.loads(self.db.execute('SELECT payload FROM prices').fetchone()[0])['url'],row['source_url'])

    def test_district_conflict_and_possible_duplicates_are_quarantined(self):
        for row in self.market_rows():
            row['community_source_id'] = '2011155836'
            fn = add_market_snapshot if row['record_kind'] == 'community_reference' else add_price
            self.assertFalse(fn(self.builder,self.bundle,row))
        self.assertFalse(add_price(self.builder,self.bundle,self.deal(id='duplicate',possible_duplicate_group='group')))
        reasons = [r['reason'] for r in self.builder._incremental_exclusions.values()]
        self.assertEqual(reasons.count('district_conflict_2011155836'),3)
        self.assertEqual(reasons.count('possible_duplicate_unresolved'),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM entities').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)

    def test_source_project_marketing_names_couple_all_three_grains(self):
        self.builder.entity('osm:way:test','彩虹城','residential','滨江区',30.19,120.18,address='已存地址')
        rows = self.market_rows()
        prepare_market_identities(self.builder,rows)
        self.assertTrue(add_price(self.builder,self.bundle,rows[0]))
        self.assertTrue(add_price(self.builder,self.bundle,rows[1]))
        self.assertTrue(add_market_snapshot(self.builder,self.bundle,rows[2]))
        entities = self.db.execute('SELECT entity_id FROM prices UNION SELECT entity_id FROM market_snapshots').fetchall()
        self.assertEqual(entities,[('osm:way:test',)])
        self.assertEqual(self.db.execute('SELECT address FROM entities').fetchone()[0],'已存地址')
        payload = json.loads(self.db.execute("SELECT payload FROM prices WHERE kind='deal'").fetchone()[0])
        self.assertEqual(payload['community'],'钱江彩虹城')
        self.assertEqual(payload['market_identity_status'],'source_project_marketing_alias_bridge')

    def test_real_phases_are_not_stripped_or_merged_into_parent(self):
        self.builder.entity('osm:way:parent','测试家园','residential','滨江区',30.19,120.18)
        rows = self.market_rows('测试家园一期','测试家园')
        rows = [*rows,self.deal(id='phase-two',community='测试家园二期',community_directory_name='测试家园',
                              community_source_id='123',source_url=rows[0]['source_url'])]
        prepare_market_identities(self.builder,rows)
        for row in rows:
            (add_market_snapshot if row['record_kind']=='community_reference' else add_price)(self.builder,self.bundle,row)
        entities = self.db.execute('SELECT DISTINCT entity_id FROM prices').fetchall()
        self.assertEqual(len(entities),3)
        self.assertEqual(next(iter(self.builder._market_identities.values()))['status'],'phase_names_preserved')

    def test_two_spatial_alias_targets_are_not_arbitrarily_selected(self):
        self.builder.entity('osm:way:a','钱江彩虹城','residential','滨江区',30.19,120.18)
        self.builder.entity('osm:way:b','彩虹城','residential','滨江区',30.20,120.19)
        rows=self.market_rows();prepare_market_identities(self.builder,rows)
        self.assertEqual(next(iter(self.builder._market_identities.values()))['status'],'ambiguous_catalogue_matches')
        add_price(self.builder,self.bundle,rows[0]);add_price(self.builder,self.bundle,rows[1])
        self.assertEqual(self.db.execute('SELECT count(DISTINCT entity_id) FROM prices').fetchone()[0],2)

    def test_unproven_directory_or_foreign_source_has_no_alias_bridge(self):
        for foreign in (False,True):
            rows=self.market_rows()
            if foreign:
                for row in rows:row['source_url']='https://example.com/houses/123'
            else:
                del rows[0]['community_directory_name']
            prepare_market_identities(self.builder,rows)
            self.assertTrue(all(e['status']!='source_project_marketing_alias_bridge' for e in self.builder._market_identities.values()))

    def test_existing_business_record_and_undated_deal_are_not_added(self):
        self.assertFalse(add_price(self.builder,self.bundle,self.deal(baseline_status='matched_existing_business_key')))
        self.assertFalse(add_price(self.builder,self.bundle,self.deal(event_date=None)))

    def test_community_statistic_is_not_a_transaction_or_listing(self):
        row = dict(id='fang:reference:123',record_kind='community_reference',community='测试住宅一期',district='滨江区',
                   reference_unit_yuan_sqm=35000,observed_at='2026-09-09',source_as_of=None,source_url='https://hz.esf.fang.com/123')
        self.assertTrue(add_market_snapshot(self.builder,self.bundle,row))
        self.assertEqual(self.db.execute('SELECT count(*) FROM prices').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM market_snapshots').fetchone()[0],1)
        self.assertFalse(add_market_snapshot(self.builder,self.bundle,row))
        self.assertFalse(add_market_snapshot(self.builder,self.bundle,dict(row,id='unsafe',source_url='javascript:bad()')))

    def test_integrate_reports_exclusions_without_changing_original_bundle(self):
        with tempfile.TemporaryDirectory(prefix='import-test-',dir=APP/'data/incremental') as directory:
            normal,listing,reference=self.market_rows()
            excluded=self.deal(id='unresolved',possible_duplicate_group='same')
            manifest=self.make_manifest(directory,[('deals',[normal,excluded]),('listings',[listing]),('market_snapshots',[reference])])
            before={p.name:digest(p.read_bytes()) for p in Path(directory).glob('*.json')}
            result=integrate(self.builder,manifest)
            self.assertEqual((result['deals'],result['listings'],result['market_snapshots']),(1,1,1))
            self.assertEqual(result['exclusion_counts'],{'possible_duplicate_unresolved':1})
            self.assertEqual(len(result['bridged_market_projects']),1)
            self.assertEqual({p.name:digest(p.read_bytes()) for p in Path(directory).glob('*.json')},before)

    def test_school_observation_wrapper_dispatches_without_becoming_admission(self):
        with tempfile.TemporaryDirectory(prefix='import-test-',dir=APP/'data/incremental') as directory:
            record={'id':'school-observation-test'}
            manifest=self.make_manifest(directory,[('school_observations',{'records':[record]})])
            handler=Mock(return_value=True)
            with patch.dict(sys.modules,school_observation_import=SimpleNamespace(add_school_observation=handler)):
                result=integrate(self.builder,manifest)
            self.assertEqual(result['school_observations'],1)
            self.assertEqual(handler.call_args.args[2],record)
            self.assertEqual(self.db.execute('SELECT count(*) FROM admissions').fetchone()[0],0)

    def test_unreviewed_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='incremental-test-',dir=APP/'data') as directory:
            manifest=Path(directory)/'manifest.json';manifest.write_text(dump({'schema_version':1,'reviewed':False}))
            with self.assertRaisesRegex(ValueError,'not passed review'):
                integrate(self.builder,manifest)

    def test_missing_manifest_leaves_baseline_unchanged(self):
        self.assertEqual(integrate(self.builder,APP/'data/no-such-approved-manifest.json')['projects'],0)


if __name__ == '__main__':
    unittest.main()
