"""Append reviewed public price evidence to an existing anonymous deal only.

Call after ordinary deals have loaded and the outer manifest's ``reviewed``
gate has passed. Pass that entry's sha256 as ``reviewed_sha256``. This adapter
does not discover bundles, insert prices, change source identities, or commit.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import json
import math
from pathlib import Path
import re

from build_data import APP, ROOT, digest, dump, safe_url

HASH = re.compile(r'[0-9a-f]{64}')
ANONYMOUS_ID = re.compile(r'fang:deal:[0-9a-f]{64}')
DETAIL_ID = re.compile(r'EX_[0-9]+')
FOCUS = {'滨江区', '拱墅区'}


def _positive(value):
    # JSON numeric values only: do not silently parse masked strings or bools.
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _verified_bundle(builder, bundle, records, reviewed_sha256):
    if not isinstance(records, list):
        raise ValueError('Enrichment bundle must contain a records list')
    if not isinstance(reviewed_sha256, str) or not HASH.fullmatch(reviewed_sha256):
        raise ValueError('A reviewed manifest entry must explicitly supply reviewed_sha256')
    path = Path(bundle).resolve()
    path.relative_to(APP / 'data/incremental')
    raw = path.read_bytes()
    relative = str(path.relative_to(ROOT))
    if digest(raw) != reviewed_sha256 or builder.hashes.get(relative) != reviewed_sha256:
        raise ValueError('Enrichment bundle is changed or has not passed the registered hash gate')
    if json.loads(raw) != records:
        raise ValueError('Enrichment arguments differ from the reviewed immutable bundle')
    return path, relative


def _shape_reason(row):
    if not isinstance(row, dict):
        return 'invalid_record_shape'
    if (row.get('record_kind') != 'deal_price_enrichment_candidate'
            or row.get('baseline_status') != 'existing_841_price_enrichment_candidate'):
        return 'not_reviewable_enrichment_candidate'
    sid = row.get('source_record_id')
    if not isinstance(sid, str) or not DETAIL_ID.fullmatch(sid) or row.get('id') != 'fang:deal:' + sid:
        return 'invalid_detail_identity'
    url = 'https://m.fang.com/chengjiao/hz/' + sid + '.html'
    if row.get('source_url') != url or row.get('evidence_url') != url:
        return 'detail_url_identity_mismatch'
    ids = row.get('comparison_prior_record_ids')
    if (not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str)
            or not ANONYMOUS_ID.fullmatch(ids[0])):
        return 'not_one_exact_anonymous_target'
    cid = row.get('community_source_id')
    if not isinstance(cid, str) or not re.fullmatch(r'[0-9]{1,20}', cid):
        return 'invalid_source_community_id'
    if cid == '2011155836':
        return 'district_conflict_2011155836'
    if row.get('district') not in FOCUS:
        return 'district_out_of_scope'
    if any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('community', 'layout', 'orientation')):
        return 'missing_immutable_fields'
    if any(not _positive(row.get(k)) for k in ('area_sqm', 'total_wan', 'unit_yuan_sqm')):
        return 'invalid_or_missing_price_or_area'
    try:
        event = date.fromisoformat(row.get('event_date', ''))
        observed = datetime.fromisoformat(row.get('observed_at', '').replace('Z', '+00:00'))
        if (event.year != 2026 or event.isoformat() != row['event_date']
                or observed.utcoffset() is None or observed.date() < event):
            return 'invalid_2026_event_or_observation_time'
    except (ValueError, TypeError, AttributeError):
        return 'invalid_2026_event_or_observation_time'
    if row.get('possible_duplicate_group'):
        return 'possible_duplicate_unresolved'
    if row.get('price_disclosure') != 'visible' or row.get('tls_verification_enabled') is not True:
        return 'not_visible_tls_verified_evidence'
    if not isinstance(row.get('evidence_sha256'), str) or not HASH.fullmatch(row['evidence_sha256']):
        return 'invalid_evidence_hash'
    return None


def _evidence_path(row):
    """Only a bundle-attested local capture inside the increment tree is read."""
    if not isinstance(row.get('evidence_raw_path'), str):
        return None
    try:
        path = (ROOT / row['evidence_raw_path']).resolve()
        path.relative_to(APP / 'data/incremental')
        if path.suffix != '.body' or not path.is_file() or digest(path.read_bytes()) != row['evidence_sha256']:
            return None
        return path
    except (ValueError, OSError):
        return None


def _loaded_deals(builder):
    fields = ('id', 'entity_id', 'source_id', 'observed_at', 'event_date', 'total_wan',
              'unit_yuan_sqm', 'area_sqm', 'payload_raw', 'entity_district', 'entity_kind', 'source_url')
    result = []
    for values in builder.db.execute('''SELECT p.id,p.entity_id,p.source_id,p.observed_at,p.event_date,
            p.total_wan,p.unit_yuan_sqm,p.area_sqm,p.payload,e.district,e.kind,s.url
            FROM prices p LEFT JOIN entities e ON e.id=p.entity_id
            LEFT JOIN sources s ON s.id=p.source_id WHERE p.kind='deal' '''):
        item = dict(zip(fields, values))
        try:
            item['payload'] = json.loads(item['payload_raw'])
        except (ValueError, TypeError):
            item['payload'] = None
        result.append(item)
    return result


def _match(row, loaded):
    target = row['comparison_prior_record_ids'][0]
    pid = 'incremental-deal:' + digest(target)
    targets = [old for old in loaded if old['id'] == pid]
    if len(targets) != 1:
        return None, 'target_not_loaded'
    old = targets[0]
    original = old['payload']
    if not isinstance(original, dict) or original.get('id') != target:
        return None, 'target_identity_payload_mismatch'
    if ('price_enrichments' in original and (not isinstance(original['price_enrichments'], list) or not original['price_enrichments'])):
        return None, 'invalid_existing_enrichment_payload'
    if 'price_enrichment_original_values' in original and 'price_enrichments' not in original:
        return None, 'invalid_existing_enrichment_payload'
    if (original.get('source_record_id') not in (None, '') or original.get('record_kind') != 'deal'
            or old['source_id'] != 'incremental-price:' + target):
        return None, 'target_is_not_original_anonymous_deal'
    if original.get('possible_duplicate_group'):
        return None, 'target_possible_duplicate_unresolved'
    if old['entity_kind'] != 'residential' or old['entity_district'] != row['district']:
        return None, 'target_entity_district_conflict'
    immutable = ('district', 'community_source_id', 'community', 'event_date', 'area_sqm', 'layout', 'orientation')
    if any(original.get(k) != row.get(k) for k in immutable):
        return None, 'immutable_fields_mismatch'
    if row.get('floor') and row['floor'] != original.get('floor'):
        return None, 'disclosed_floor_conflict'
    if old['event_date'] != row['event_date'] or old['area_sqm'] != row['area_sqm']:
        return None, 'target_columns_payload_conflict'
    old_url = 'https://m.fang.com/chengjiao/hz/?projcode=' + row['community_source_id']
    if original.get('source_url') != old_url or old['source_url'] != old_url:
        return None, 'target_original_source_project_mismatch'
    # ID alone is insufficient when the same visible business key occurs twice.
    peers = [p for p in loaded if isinstance(p['payload'], dict)
             and all(p['payload'].get(k) == row[k] for k in ('district', 'community_source_id', 'event_date', 'area_sqm'))]
    if len(peers) != 1 or peers[0]['id'] != pid:
        return None, 'ambiguous_loaded_business_key'
    for item in loaded:
        payload = item['payload'] if isinstance(item['payload'], dict) else {}
        attached = payload.get('price_enrichments') or []
        if not isinstance(attached, list):
            if item['id'] == pid:
                return None, 'invalid_existing_enrichment_payload'
            continue
        used = payload.get('source_record_id') == row['source_record_id'] or any(
            isinstance(a, dict) and isinstance(a.get('evidence'), dict)
            and a['evidence'].get('source_record_id') == row['source_record_id'] for a in attached)
        if used and item['id'] != pid:
            return None, 'detail_identity_already_used_by_another_deal'
    enrichments = original.get('price_enrichments') or []
    if any(isinstance(a, dict) and a.get('evidence') == row for a in enrichments):
        if old['total_wan'] == row['total_wan'] and old['unit_yuan_sqm'] == row['unit_yuan_sqm']:
            return None, 'already_applied'
        return None, 'applied_evidence_columns_conflict'
    if enrichments:
        return None, 'existing_enrichment_not_overwritten'
    missing = []
    for key in ('total_wan', 'unit_yuan_sqm'):
        if original.get(key) != old[key]:
            return None, 'target_columns_payload_conflict'
        if old[key] is None:
            missing.append(key)
        elif not _positive(old[key]) or old[key] != row[key]:
            return None, 'existing_price_conflict'
    if not missing:
        return None, 'price_already_complete'
    return (old, missing), None


def integrate_enrichments(builder, bundle, records, *, reviewed_sha256=None):
    """Return independent enrichment deltas; all rejected rows have reasons.

    ``builder.read(bundle)`` must already have registered the reviewed bundle.
    All record checks run before writes; duplicate targets/sources in one bundle
    are rejected together. Only NULL price columns can be filled. Source values
    are used as published, never reverse-calculated from masked prices.
    """
    bundle, relative = _verified_bundle(builder, bundle, records, reviewed_sha256)
    loaded = _loaded_deals(builder)
    result = dict(deal_price_enrichments=0, deal_price_enrichment_sources=0,
                  deal_price_enrichment_applied=[], deal_price_enrichment_excluded_records=[])
    valid = [(row, _shape_reason(row)) for row in records]
    target_counts = Counter(row['comparison_prior_record_ids'][0] for row, reason in valid if reason is None)
    detail_counts = Counter(row['source_record_id'] for row, reason in valid if reason is None)
    prepared = []
    for row, reason in valid:
        if reason is None and target_counts[row['comparison_prior_record_ids'][0]] != 1:
            reason = 'duplicate_target_in_bundle'
        if reason is None and detail_counts[row['source_record_id']] != 1:
            reason = 'duplicate_detail_identity_in_bundle'
        capture = None
        if reason is None:
            capture = _evidence_path(row)
            if capture is None:
                reason = 'missing_changed_or_outside_evidence_capture'
        match = None
        if reason is None:
            match, reason = _match(row, loaded)
        if reason:
            result['deal_price_enrichment_excluded_records'].append(dict(
                id=row.get('id') if isinstance(row, dict) else None, reason=reason))
            continue
        old, missing = match
        sid = 'transaction-enrichment:fang:' + row['source_record_id'] + ':' + row['evidence_sha256'][:16]
        existing_source = builder.db.execute('SELECT path,sha256,url FROM sources WHERE id=?', (sid,)).fetchone()
        if existing_source and existing_source != (relative, reviewed_sha256, row['source_url']):
            result['deal_price_enrichment_excluded_records'].append(dict(id=row['id'], reason='enrichment_source_identity_conflict'))
            continue
        prepared.append((row, old, missing, capture, sid, existing_source is None))
    # RELEASE of an outermost SAVEPOINT commits in SQLite. Keep a caller-owned
    # transaction open even when this adapter is invoked after a prior commit.
    if not builder.db.in_transaction:
        builder.db.execute('BEGIN')
    builder.db.execute('SAVEPOINT transaction_price_enrichments')
    try:
        for row, old, missing, capture, sid, source_is_new in prepared:
            payload = dict(old['payload'])
            payload['price_enrichment_original_values'] = {k: old[k] for k in ('total_wan', 'unit_yuan_sqm')}
            payload['price_enrichments'] = [dict(source_id=sid, observed_at=row['observed_at'],
                applied_fields=missing, evidence=row, reviewed_bundle=relative, reviewed_bundle_sha256=reviewed_sha256,
                match_basis='exact original anonymous row ID + unique source-project/district/date/area + same name/layout/orientation; new undisclosed floor not inferred',
                note='公开平台完整价格补充证据；未获登记机构逐套核验。原始证据和标识保留不变。')]
            builder.source(sid, bundle, row['community'] + ' · 逐套成交补价证据', 'transaction_enrichment',
                row['observed_at'], row['event_date'],
                '仅填充已匹配原匿名记录的空价格，不新增交易，不改原来源。'
                + ' raw_sha256=' + row['evidence_sha256'], row['source_url'])
            cursor = builder.db.execute('''UPDATE prices SET total_wan=?,unit_yuan_sqm=?,payload=?
                WHERE id=? AND entity_id=? AND source_id=? AND kind='deal' AND payload=?
                AND total_wan IS ? AND unit_yuan_sqm IS ?''',
                (row['total_wan'] if 'total_wan' in missing else old['total_wan'],
                 row['unit_yuan_sqm'] if 'unit_yuan_sqm' in missing else old['unit_yuan_sqm'], dump(payload),
                 old['id'], old['entity_id'], old['source_id'], old['payload_raw'], old['total_wan'], old['unit_yuan_sqm']))
            if cursor.rowcount != 1:
                raise RuntimeError('Matched original deal changed before enrichment update')
            result['deal_price_enrichments'] += 1
            result['deal_price_enrichment_sources'] += int(source_is_new)
            result['deal_price_enrichment_applied'].append(dict(price_id=old['id'], original_record_id=old['payload']['id'],
                evidence_record_id=row['id'], source_id=sid, applied_fields=missing))
        builder.db.execute('RELEASE SAVEPOINT transaction_price_enrichments')
    except Exception:
        builder.db.execute('ROLLBACK TO SAVEPOINT transaction_price_enrichments')
        builder.db.execute('RELEASE SAVEPOINT transaction_price_enrichments')
        raise
    for row, old, missing, capture, sid, source_is_new in prepared:
        builder.hashes[str(capture.relative_to(ROOT))] = row['evidence_sha256']
    result['deal_price_enrichment_exclusion_counts'] = dict(Counter(
        r['reason'] for r in result['deal_price_enrichment_excluded_records']))
    return result
