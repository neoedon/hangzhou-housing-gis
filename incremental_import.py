"""Import only reviewed immutable bundles, never live collector checkpoints."""
from __future__ import annotations
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit
from build_data import APP, ROOT, digest, dump, number, day, safe_url, norm

MANIFEST = APP / 'data/incremental/2026-09-10/accepted_manifest.json'


def market_project_key(row):
    """Project IDs are namespaced to the actual public source, never filenames."""
    url = safe_url(row.get('source_url'))
    cid = str(row.get('community_source_id') or '')
    if url and urlsplit(url).hostname == 'm.fang.com' and re.fullmatch(r'[0-9]{1,20}', cid):
        return ('fang', cid, row.get('district') or '')
    return None


def quarantine_reason(row):
    key = market_project_key(row)
    if key and key[1] == '2011155836':
        return 'district_conflict_2011155836'
    if row.get('possible_duplicate_group'):
        return 'possible_duplicate_unresolved'
    return None


def quarantine(builder, row):
    reason = quarantine_reason(row)
    if not reason:
        return False
    if not hasattr(builder, '_incremental_exclusions'):
        builder._incremental_exclusions = {}
    builder._incremental_exclusions[(row.get('record_kind'), row.get('id'))] = {
        'id': row.get('id'), 'kind': row.get('record_kind'), 'reason': reason,
        'community_source_id': row.get('community_source_id'), 'community': row.get('community'),
        'district': row.get('district'), 'possible_duplicate_group': row.get('possible_duplicate_group'),
        'source_url': safe_url(row.get('source_url')),
        'action': 'not_imported; original immutable bundle retained; no forced merge or coordinate reassignment'}
    return True


def prepare_market_identities(builder, records):
    """Use explicit source-directory identity only; phases stay separate."""
    grouped = defaultdict(list)
    for row in records:
        key = market_project_key(row)
        if key and not quarantine_reason(row):
            grouped[key].append(row)
    builder._market_identities = {}
    for key, rows in grouped.items():
        names = {str(r.get('community') or '').strip() for r in rows} - {''}
        directories = {str(r.get('community_directory_name') or '').strip() for r in rows} - {''}
        # Directory/listing names corroborate the explicit detail->directory link.
        canonical_names = directories | {r['community'] for r in rows
            if r.get('record_kind') in ('listing', 'community_reference') and r.get('community')}
        names |= directories
        entry = {'source': key[0], 'community_source_id': key[1], 'district': key[2],
                 'names': sorted(names), 'canonical_names': sorted(canonical_names),
                 'status': 'no_alias_change', 'target_id': None}
        if len(names) > 1:
            if any(re.search(r'(?:[一二三四五六七八九十百0-9]+期|[东西南北]区|[一二三四五六七八九十0-9]+组团|[0-9]+(?:幢|栋|号楼))', name) for name in names):
                entry['status'] = 'phase_names_preserved'
            elif not directories or len(canonical_names) != 1:
                entry['status'] = 'directory_identity_unresolved'
            else:
                matches = set().union(*(builder.lookup.get((key[2], 'residential', norm(name)), set()) for name in names))
                spatial = {eid for eid in matches if eid.startswith('osm:')}
                if len(spatial) > 1:
                    entry['status'] = 'ambiguous_catalogue_matches'
                else:
                    entry['status'] = 'source_project_marketing_alias_bridge'
                    entry['canonical_name'] = next(iter(canonical_names))
                    entry['target_id'] = next(iter(spatial)) if spatial else None
        builder._market_identities[key] = entry


def market_entity(builder, sid, row):
    key = market_project_key(row)
    entry = getattr(builder, '_market_identities', {}).get(key)
    if not entry or entry['status'] != 'source_project_marketing_alias_bridge':
        eid = builder.resolve(sid, row['id'], row['community'], district=row['district'])
        return eid, entry['status'] if entry else 'source_name_only'
    if not entry['target_id']:
        # No arbitrary coordinate is introduced. A unique canonical unlocated
        # identity can couple the source's marketing aliases without moving it.
        entry['target_id'] = builder.resolve(sid, row['id'], entry['canonical_name'], district=row['district'])
    eid = entry['target_id']
    builder.db.execute('INSERT OR REPLACE INTO mappings VALUES(?,?,?,?,?,?)',
                       (digest(f"{sid}:{row['id']}"), sid, str(row['id']), eid,
                        'source_project_alias_bridge_review_required', dump([eid])))
    return eid, entry['status']


def source_record(builder, sid, bundle, row, label, kind):
    observed = row.get('detail_observed_at') or row.get('observed_at')
    builder.source(sid, bundle, label, kind, observed, day(row.get('event_date')),
                   '本轮公开页面观察；保留来源、缺失值和原始价格口径，不等于登记机构或当前在售核验。',
                   row.get('source_url', ''))


def add_project(builder, bundle, row):
    # Font-encoded catalogue digits, failed detail pages and commercial projects
    # are kept in the research bundle, never presented as verified housing prices.
    if (row.get('detail_fetch_status') != 'ok' or row.get('district_consistent') is not True
            or row.get('is_residential') is not True or not row.get('name')
            or not safe_url(row.get('source_url'))):
        return False
    sid = 'new-project:' + str(row['project_id'])
    source_record(builder, sid, bundle, row, row['name'] + ' · 新房项目资料', 'new_project')
    eid = builder.resolve(sid, row['project_id'], row['name'], district=row['district'],
                          aliases=row.get('aliases', []), address=row.get('address') or '')
    inserted = builder.db.execute('INSERT OR IGNORE INTO projects VALUES(?,?,?,?,?)',
                       (row['project_id'], eid, sid, row.get('detail_observed_at'), dump(row))).rowcount
    target_entity_id = row.get('target_entity_id')
    merge_project_ids = row.get('merge_project_ids', [])
    if target_entity_id or merge_project_ids:
        if target_entity_id != eid or not eid.startswith('osm:'):
            raise ValueError('Reviewed project identity bridge did not resolve to its declared OSM target')
        if not isinstance(merge_project_ids, list):
            raise ValueError('merge_project_ids must be a reviewed list')
        if not hasattr(builder, '_reviewed_project_identity_merges'):
            builder._reviewed_project_identity_merges = []
        for project_id in merge_project_ids:
            prior = builder.db.execute('''
                SELECT p.entity_id,p.source_id,e.district
                FROM projects p JOIN entities e ON e.id=p.entity_id
                WHERE p.id=?
            ''', (project_id,)).fetchone()
            if not prior or prior[2] != row['district']:
                raise ValueError('Reviewed project identity bridge target is missing or crosses districts')
            previous_entity_id, previous_source_id, _ = prior
            if previous_entity_id == eid:
                continue
            builder.db.execute('UPDATE projects SET entity_id=? WHERE id=?', (eid, project_id))
            builder.db.execute('UPDATE prices SET entity_id=? WHERE source_id=?', (eid, previous_source_id))
            builder.db.execute('''
                UPDATE mappings SET entity_id=?,status=?,options=?
                WHERE source_id=? AND source_record=?
            ''', (eid, 'reviewed_project_identity_bridge', dump([eid]), previous_source_id, project_id))
            builder._reviewed_project_identity_merges.append({
                'project_id': project_id,
                'from_entity_id': previous_entity_id,
                'to_entity_id': eid,
                'evidence_project_id': row['project_id'],
            })
    for index, price in enumerate(row.get('prices', [])):
        amount = number(price.get('amount'))
        # Only explicit unencoded detail-page reference units; totals and ranges
        # remain in the project record, not disguised as a single unit quote.
        if (amount is None or amount <= 0 or price.get('font_encoded')
                or price.get('amount_high') or price.get('unit') not in ('元/㎡', '元/平方米')
                or price.get('price_type') == 'directory_model_reference'):
            continue
        payload = dict(price, market='new', price_basis=price.get('price_type'),
                       url=safe_url(price.get('source_url')), note='新房项目历史/平台参考口径，非核实当前在售或网签成交。',
                       price_as_of=price.get('price_as_of'), project_id=row['project_id'])
        pid = 'project-reference:' + digest(dump([row['project_id'], price.get('source_url'),
                price.get('price_type'), amount, price.get('price_as_of')]))
        builder.db.execute('INSERT OR IGNORE INTO prices VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (pid, eid, sid, 'reference', price.get('observed_at'), None,
                            None, amount, None, dump(payload)))
    return inserted == 1


def add_price(builder, bundle, row):
    kind = row.get('record_kind')
    if kind not in ('deal', 'listing') or row.get('baseline_status') == 'matched_existing_business_key':
        return False
    if not row.get('community') or not row.get('district') or not safe_url(row.get('source_url')):
        return False
    if kind == 'deal' and (not day(row.get('event_date')) or not number(row.get('area_sqm'))):
        return False
    if quarantine(builder, row):
        return False
    sid = 'incremental-price:' + str(row['id'])
    source_record(builder, sid, bundle, row, row['community'] + (' · 逐套成交记录' if kind == 'deal' else ' · 挂牌线索'), 'transaction' if kind == 'deal' else 'price_lead')
    eid, identity_status = market_entity(builder, sid, row)
    payload = dict(row, market='resale', url=safe_url(row['source_url']),
                   market_identity_status=identity_status,
                   note='公开平台逐套记录；打码或未公布的价格保留未知。')
    # Collector IDs already prefer source record IDs, then stable anonymous
    # business fields. Price/area enrichment must not turn one record into two.
    pid = 'incremental-' + kind + ':' + digest(str(row['id']))
    before = builder.db.total_changes
    builder.db.execute('INSERT OR IGNORE INTO prices VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (pid, eid, sid, kind, row.get('observed_at'), day(row.get('event_date')) if kind == 'deal' else None,
                        number(row.get('total_wan')) or None, number(row.get('unit_yuan_sqm')) or None,
                        number(row.get('area_sqm')) or None, dump(payload)))
    return builder.db.total_changes > before


def add_market_snapshot(builder, bundle, row):
    if (row.get('record_kind') != 'community_reference' or not row.get('community')
            or not row.get('district') or not safe_url(row.get('source_url'))):
        return False
    if quarantine(builder, row):
        return False
    sid = 'market-reference:' + str(row['id'])
    source_record(builder, sid, bundle, row, row['community'] + ' · 小区行情参考', 'market_reference')
    # Source-page address/year are observations, not verified entity identity facts.
    eid, identity_status = market_entity(builder, sid, row)
    inserted = builder.db.execute('INSERT OR IGNORE INTO market_snapshots VALUES(?,?,?,?,?)',
                       (row['id'], eid, sid, row.get('observed_at'), dump(dict(row, market_identity_status=identity_status))))
    return inserted.rowcount == 1


def integrate(builder, manifest_path=MANIFEST):
    result = dict(projects=0, deals=0, listings=0, market_snapshots=0, social_new_posts=0, social_new_bodies=0,
                  school_observations=0, deal_price_enrichments=0, official_local_policies=0,
                  official_local_policy_sources=0, official_local_policy_entity_mentions=0,
                  source_manifest=None, excluded_records=[], exclusion_counts={},
                  unresolved_market_projects=[], bridged_market_projects=[])
    if not Path(manifest_path).is_file():
        return result
    manifest = builder.read(manifest_path)
    if manifest.get('schema_version') != 1 or manifest.get('reviewed') is not True:
        raise ValueError('Incremental bundle has not passed review')
    result['source_manifest'] = str(Path(manifest_path).relative_to(ROOT))
    builder._incremental_exclusions = {}
    prepared = []
    for entry in manifest.get('bundles', []):
        bundle = (ROOT / entry['path']).resolve()
        bundle.relative_to(APP / 'data/incremental')
        if digest(bundle.read_bytes()) != entry['sha256']:
            raise ValueError('Reviewed incremental bundle changed: ' + entry['path'])
        records = builder.read(bundle)
        if entry['kind'] == 'school_observations' and isinstance(records, dict):
            records = records.get('records')
        if not isinstance(records, list) or len(records) != entry['records']:
            raise ValueError('Incremental record count mismatch')
        prepared.append((entry, bundle, records))
    prepare_market_identities(builder, [row for entry, bundle, records in prepared
        if entry['kind'] in ('deals', 'listings', 'market_snapshots') for row in records])
    for entry, bundle, records in prepared:
        if entry['kind'] == 'official_local_policies':
            from official_local_policy_import import integrate_local_policies
            policy_counts = integrate_local_policies(builder, bundle, records, reviewed_sha256=entry['sha256'])
            for key, value in policy_counts.items():
                result[key] += value
            continue
        if entry['kind'] == 'deal_price_enrichments':
            # A supplement may only target a previously loaded deal. Process it
            # after every ordinary deal bundle, regardless of manifest order.
            continue
        if entry['kind'] == 'social_bodies':
            from social_incremental_import import integrate_social
            social = integrate_social(builder, bundle, records, baseline_sha256=entry.get('baseline_sha256'),
                                      require_semantic_review=entry.get('require_semantic_review', False))
            for key, value in social.items():
                if isinstance(value, int):
                    result[key] = result.get(key, 0) + value
                elif isinstance(value, list):
                    result.setdefault(key, []).extend(value)
                else:
                    result[key] = value
            continue
        for row in records:
            if entry['kind'] == 'new_projects':
                result['projects'] += add_project(builder, bundle, row)
            elif entry['kind'] in ('deals', 'listings'):
                if row.get('record_kind') != {'deals': 'deal', 'listings': 'listing'}[entry['kind']]:
                    raise ValueError('Incremental record grain does not match manifest')
                if add_price(builder, bundle, row):
                    result[entry['kind']] += 1
            elif entry['kind'] == 'market_snapshots':
                result['market_snapshots'] += add_market_snapshot(builder, bundle, row)
            elif entry['kind'] == 'school_observations':
                from school_observation_import import add_school_observation
                result['school_observations'] += add_school_observation(builder, bundle, row)
            else:
                raise ValueError('Unsupported incremental record grain: ' + entry['kind'])
    for entry, bundle, records in prepared:
        if entry['kind'] != 'deal_price_enrichments':
            continue
        from transaction_enrichment_import import integrate_enrichments
        enrichment = integrate_enrichments(builder, bundle, records, reviewed_sha256=entry['sha256'])
        for key, value in enrichment.items():
            if isinstance(value, int):
                result[key] = result.get(key, 0) + value
            elif isinstance(value, list):
                result.setdefault(key, []).extend(value)
            elif key.endswith('_counts'):
                result[key] = dict(Counter(result.get(key, {})) + Counter(value))
            else:
                result[key] = value
    result['excluded_records'] = list(builder._incremental_exclusions.values())
    result['exclusion_counts'] = dict(Counter(row['reason'] for row in result['excluded_records']))
    result['excluded_kinds'] = dict(Counter(row['kind'] for row in result['excluded_records']))
    result['unresolved_market_projects'] = [entry for entry in builder._market_identities.values()
        if entry['status'] in ('phase_names_preserved', 'directory_identity_unresolved', 'ambiguous_catalogue_matches')]
    result['bridged_market_projects'] = [entry for entry in builder._market_identities.values()
        if entry['status'] == 'source_project_marketing_alias_bridge']
    result['reviewed_project_identity_merges'] = getattr(builder, '_reviewed_project_identity_merges', [])
    return result
