import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from build_data import Builder, SCHEMA, digest, dump
from transaction_enrichment_import import integrate_enrichments


class TransactionEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript(SCHEMA)
        self.builder = Builder(self.db)
        self.temp = tempfile.TemporaryDirectory(prefix='enrichment-import-test-', dir=APP / 'data/incremental')
        self.folder = Path(self.temp.name)
        self.bundle = self.folder / 'reviewed-enrichments.json'
        self.old_bundle = self.folder / 'old-deals.json'
        self.raw = self.folder / 'public-detail.body'
        self.raw.write_bytes(b'<html>Public complete-price detail: 300 / 30000 / 100.</html>')
        self.target = 'fang:deal:' + 'a' * 64
        self.pid = 'incremental-deal:' + digest(self.target)
        self.original = dict(id=self.target, record_kind='deal', source_record_id=None,
            community='测试花园一期', district='滨江区', community_source_id='2010000001',
            source_url='https://m.fang.com/chengjiao/hz/?projcode=2010000001',
            evidence_url='https://m.fang.com/chengjiao/hz/?projcode=2010000001',
            evidence_sha256='c' * 64, evidence_raw_path='original-capture-remains-unchanged.body',
            observed_at='2026-09-08T08:00:00+08:00', event_date='2026-07-01', area_sqm=100.0,
            layout='3室2厅', orientation='南', floor='高楼层层/28层',
            total_wan=None, unit_yuan_sqm=None, price_disclosure='masked_or_missing',
            total_price_source_text='3**', note='原始记录不得被补价改写',
            business_fingerprint='d' * 64, identity_basis='visible_business_fields_no_unit_identifier')
        self.old_bundle.write_text(dump([self.original]))
        self.builder.read(self.old_bundle)
        self.builder.entity('test:home', '测试花园一期', 'residential', '滨江区')
        self.insert_old(self.original)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def insert_old(self, row, pid=None):
        sid = 'incremental-price:' + row['id']
        self.builder.source(sid, self.old_bundle, row['community'], 'transaction', row['observed_at'],
                            row['event_date'], 'original', row['source_url'])
        self.db.execute('INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?)',
            (pid or 'incremental-deal:' + digest(row['id']), 'test:home', sid, 'deal',
             row['observed_at'], row['event_date'], row['total_wan'], row['unit_yuan_sqm'], row['area_sqm'], dump(row)))

    def row(self, **changes):
        value = dict(id='fang:deal:EX_123', source_record_id='EX_123',
            record_kind='deal_price_enrichment_candidate', baseline_status='existing_841_price_enrichment_candidate',
            comparison_prior_record_ids=[self.target], source_url='https://m.fang.com/chengjiao/hz/EX_123.html',
            evidence_url='https://m.fang.com/chengjiao/hz/EX_123.html',
            evidence_sha256=digest(self.raw.read_bytes()), evidence_raw_path=str(self.raw.relative_to(APP.parent)),
            observed_at='2026-09-09T01:31:00+08:00', event_date='2026-07-01', district='滨江区',
            community='测试花园一期', community_directory_name='测试花园', community_source_id='2010000001',
            layout='3室2厅', orientation='南', floor='', area_sqm=100.0, total_wan=300.0, unit_yuan_sqm=30000.0,
            tls_verification_enabled=True, price_disclosure='visible', source_claim='市场信息，未经登记机构逐套核验')
        value.update(changes)
        return value

    def registered(self, rows):
        self.bundle.write_text(dump(rows))
        records = self.builder.read(self.bundle)
        return records, digest(self.bundle.read_bytes())

    def run_import(self, rows):
        records, sha = self.registered(rows)
        return integrate_enrichments(self.builder, self.bundle, records, reviewed_sha256=sha)

    def price(self):
        names = [r[1] for r in self.db.execute('PRAGMA table_info(prices)')]
        return dict(zip(names, self.db.execute('SELECT * FROM prices WHERE id=?', (self.pid,)).fetchone()))

    def rejection(self, row, reason):
        before = self.price()
        sources = self.db.execute('SELECT count(*) FROM sources').fetchone()[0]
        result = self.run_import([row])
        self.assertEqual(result['deal_price_enrichments'], 0)
        self.assertEqual(result['deal_price_enrichment_exclusion_counts'], {reason: 1})
        self.assertEqual(self.price(), before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0], sources)

    def test_only_prices_are_filled_and_original_identity_and_evidence_preserved(self):
        before, row = self.price(), self.row()
        statements = []
        self.db.set_trace_callback(statements.append)
        result = self.run_import([row])
        self.db.set_trace_callback(None)
        after = self.price()
        self.assertEqual(result['deal_price_enrichments'], 1)
        self.assertEqual(result['deal_price_enrichment_sources'], 1)
        self.assertNotIn('deals', result)
        self.assertEqual(self.db.execute('SELECT count(*) FROM prices').fetchone()[0], 1)
        self.assertFalse(any('INSERT' in q.upper() and 'INTO PRICES' in q.upper() for q in statements))
        for key in set(before) - {'payload', 'total_wan', 'unit_yuan_sqm'}:
            self.assertEqual(after[key], before[key], key)
        self.assertEqual((after['total_wan'], after['unit_yuan_sqm']), (300, 30000))
        payload = json.loads(after['payload'])
        for key, value in self.original.items():
            self.assertEqual(payload[key], value, key)
        self.assertEqual(payload['price_enrichments'][0]['evidence'], row)
        self.assertEqual(payload['price_enrichments'][0]['applied_fields'], ['total_wan', 'unit_yuan_sqm'])
        self.assertEqual(payload['price_enrichment_original_values'], {'total_wan': None, 'unit_yuan_sqm': None})
        sid = payload['price_enrichments'][0]['source_id']
        source = self.db.execute('SELECT kind,url,sha256,notes FROM sources WHERE id=?', (sid,)).fetchone()
        self.assertEqual(source[:3], ('transaction_enrichment', row['source_url'], digest(self.bundle.read_bytes())))
        self.assertIn(row['evidence_sha256'], source[3])
        self.assertEqual(self.builder.hashes[row['evidence_raw_path']], row['evidence_sha256'])

    def test_one_missing_field_is_filled_only_when_existing_value_matches(self):
        original = dict(self.original, total_wan=300)
        self.db.execute('UPDATE prices SET total_wan=300,payload=? WHERE id=?', (dump(original), self.pid))
        result = self.run_import([self.row()])
        self.assertEqual(result['deal_price_enrichments'], 1)
        payload = json.loads(self.price()['payload'])
        self.assertEqual(payload['price_enrichments'][0]['applied_fields'], ['unit_yuan_sqm'])
        self.assertEqual(payload['total_wan'], 300)
        self.assertIsNone(payload['unit_yuan_sqm'])

    def test_existing_price_conflict_and_complete_prices_are_not_overwritten(self):
        original = dict(self.original, total_wan=301)
        self.db.execute('UPDATE prices SET total_wan=301,payload=? WHERE id=?', (dump(original), self.pid))
        self.rejection(self.row(), 'existing_price_conflict')
        original = dict(self.original, total_wan=300, unit_yuan_sqm=30000)
        self.db.execute('UPDATE prices SET total_wan=300,unit_yuan_sqm=30000,payload=? WHERE id=?', (dump(original), self.pid))
        self.rejection(self.row(), 'price_already_complete')

    def test_all_immutable_fields_and_disclosed_floor_must_match(self):
        for key, value in {'community': '测试花园二期', 'community_source_id': '2010000002',
                           'event_date': '2026-07-02', 'area_sqm': 100.01,
                           'layout': '2室1厅', 'orientation': '北'}.items():
            with self.subTest(key=key):
                self.rejection(self.row(**{key: value}), 'immutable_fields_mismatch')
        self.rejection(self.row(district='拱墅区'), 'target_entity_district_conflict')
        self.rejection(self.row(floor='低楼层层/28层'), 'disclosed_floor_conflict')

    def test_unknown_or_multiple_anonymous_targets_and_invalid_source_identity_reject(self):
        self.rejection(self.row(comparison_prior_record_ids=['fang:deal:' + 'b' * 64]), 'target_not_loaded')
        self.rejection(self.row(comparison_prior_record_ids=[self.target, self.target]), 'not_one_exact_anonymous_target')
        self.rejection(self.row(comparison_prior_record_ids=['fang:deal:EX_1']), 'not_one_exact_anonymous_target')
        self.rejection(self.row(source_url='https://m.fang.com/chengjiao/hz/EX_999.html'), 'detail_url_identity_mismatch')
        self.rejection(self.row(source_url='https://m.fang.com/chengjiao/hz/EX_123.html?login=true'), 'detail_url_identity_mismatch')

    def test_illegal_empty_and_masked_prices_and_non_2026_are_rejected(self):
        for key in ('total_wan', 'unit_yuan_sqm', 'area_sqm'):
            for value in (None, 0, -1, True, '300', '3**', float('inf')):
                with self.subTest(key=key, value=value):
                    self.rejection(self.row(**{key: value}), 'invalid_or_missing_price_or_area')
        for value in ('2025-07-01', '2026-13-01', '20260701'):
            self.rejection(self.row(event_date=value), 'invalid_2026_event_or_observation_time')
        self.rejection(self.row(observed_at='2026-06-30T01:00:00+08:00'), 'invalid_2026_event_or_observation_time')
        self.rejection(self.row(observed_at='2026-09-09'), 'invalid_2026_event_or_observation_time')

    def test_district_conflict_and_marked_duplicates_never_import(self):
        self.rejection(self.row(community_source_id='2011155836'), 'district_conflict_2011155836')
        self.rejection(self.row(possible_duplicate_group='group'), 'possible_duplicate_unresolved')
        self.rejection(self.row(price_disclosure='masked_or_missing'), 'not_visible_tls_verified_evidence')
        self.rejection(self.row(tls_verification_enabled=False), 'not_visible_tls_verified_evidence')

    def test_changed_missing_and_path_injected_captures_reject(self):
        self.rejection(self.row(evidence_sha256='0' * 64), 'missing_changed_or_outside_evidence_capture')
        self.rejection(self.row(evidence_raw_path='../outside-increment-tree.body'), 'missing_changed_or_outside_evidence_capture')
        self.rejection(self.row(evidence_raw_path=str(self.folder / 'missing.body')), 'missing_changed_or_outside_evidence_capture')

    def test_review_hash_registration_and_argument_gates_fail_before_writes(self):
        records, sha = self.registered([self.row()])
        with self.assertRaisesRegex(ValueError, 'reviewed_sha256'):
            integrate_enrichments(self.builder, self.bundle, records)
        self.builder.hashes.pop(str(self.bundle.relative_to(APP.parent)))
        with self.assertRaisesRegex(ValueError, 'registered hash gate'):
            integrate_enrichments(self.builder, self.bundle, records, reviewed_sha256=sha)
        records, sha = self.registered([self.row()])
        with self.assertRaisesRegex(ValueError, 'differ'):
            integrate_enrichments(self.builder, self.bundle, [], reviewed_sha256=sha)
        self.bundle.write_text(dump([]))
        with self.assertRaisesRegex(ValueError, 'registered hash gate'):
            integrate_enrichments(self.builder, self.bundle, records, reviewed_sha256=sha)
        self.assertIsNone(self.price()['total_wan'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0], 1)

    def test_repeated_execution_is_idempotent(self):
        row = self.row()
        self.assertEqual(self.run_import([row])['deal_price_enrichments'], 1)
        self.rejection(row, 'already_applied')
        self.assertEqual(self.db.execute('SELECT count(*) FROM prices').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0], 2)

    def test_duplicate_target_in_bundle_rejects_both_without_first_wins(self):
        row = self.row()
        result = self.run_import([row, copy.deepcopy(row)])
        self.assertEqual(result['deal_price_enrichments'], 0)
        self.assertEqual(result['deal_price_enrichment_exclusion_counts'], {'duplicate_target_in_bundle': 2})
        self.assertIsNone(self.price()['total_wan'])

    def test_multiple_loaded_visible_business_keys_are_not_resolved_by_target_id(self):
        self.insert_old(dict(self.original, id='fang:deal:' + 'b' * 64, layout='2室1厅'))
        self.rejection(self.row(), 'ambiguous_loaded_business_key')

    def test_detail_id_already_attached_to_another_transaction_rejects(self):
        self.insert_old(dict(self.original, id='fang:deal:EX_123', source_record_id='EX_123', area_sqm=99))
        self.rejection(self.row(), 'detail_identity_already_used_by_another_deal')

    def test_source_project_and_columns_must_retain_original_agreement(self):
        self.db.execute('UPDATE prices SET area_sqm=99 WHERE id=?', (self.pid,))
        self.rejection(self.row(), 'target_columns_payload_conflict')
        self.db.execute('UPDATE prices SET area_sqm=100 WHERE id=?', (self.pid,))
        self.db.execute("UPDATE sources SET url='https://m.fang.com/chengjiao/hz/?projcode=9'")
        self.rejection(self.row(), 'target_original_source_project_mismatch')

    def test_adapter_does_not_commit_callers_transaction(self):
        self.db.commit()
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.run_import([self.row()])['deal_price_enrichments'], 1)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertIsNone(self.price()['total_wan'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0], 1)

    def test_source_and_price_update_are_rolled_back_together_on_stale_row(self):
        before = self.price()
        source = self.builder.source
        def race(*args, **kwargs):
            result = source(*args, **kwargs)
            self.db.execute("UPDATE prices SET payload='changed after preflight' WHERE id=?", (self.pid,))
            return result
        with patch.object(self.builder, 'source', side_effect=race):
            with self.assertRaisesRegex(RuntimeError, 'changed before'):
                self.run_import([self.row()])
        self.assertEqual(self.price(), before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
