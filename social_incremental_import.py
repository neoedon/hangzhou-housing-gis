"""Import explicitly reviewed XHS bundles; never reads a live checkpoint.

The parent reviewed-manifest gate must register ``bundle`` with Builder.read
and supply its separately reviewed ``baseline_sha256``. This module neither
discovers collector records nor modifies the original corpus or commits a DB.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from build_data import APP, ROOT, day, digest, dump, safe_url

BASELINE = APP / 'data/incremental/2026-09-09/xiaohongshu/baseline.json'
MIN_MEANINGFUL = 40
NOTE_ID = re.compile(r'[0-9a-f]{24}')
HASH = re.compile(r'[0-9a-f]{64}')
EMOJI_TOKEN = re.compile(r'\[[^\]\n]{1,20}R\]')
CORE_DISTRICTS = {'滨江区', '拱墅区', '西湖区', '上城区', '钱塘区'}


def strip_topic_tags(value):
    """Same two replacements as legacy post_text_depth.mjs."""
    text = value if isinstance(value, str) else ''
    text = re.sub(r'#[^#\n]+?\[话题\]#', ' ', text)
    return re.sub(r'(^|\s)#[^\s#]+', ' ', text)


def letter_numbers(value):
    return ''.join(c for c in value if unicodedata.category(c)[0] in 'LN')


def body_hash(value):
    # Order matters: topic removal -> NFKC -> emoji-token removal -> L/N -> lower.
    text = unicodedata.normalize('NFKC', strip_topic_tags(value))
    return digest(letter_numbers(EMOJI_TOKEN.sub('', text)).lower())


def meaningful_characters(value):
    text = letter_numbers(EMOJI_TOKEN.sub('', strip_topic_tags(value)))
    # JavaScript .length counts UTF-16 code units, including astral letters.
    return len(text.encode('utf-16-le')) // 2


def new_post_district(title, description, hints=None):
    """Conservative district label for new posts, never a map coordinate.

    Only complete administrative names in the title/prose supplement legacy
    hints. Bare developer names, topics, search queries and nearby places do
    not supply a district. A mixed-district text overrides no hint as certain.
    """
    hints = hints or []
    explicit_hints = {d if d.endswith('区') else d + '区' for d in hints if isinstance(d, str)}
    text = str(title or '') + '\n' + strip_topic_tags(description)
    mentioned = set(re.findall(r'滨江区|拱墅区|上城区|西湖区|钱塘区', text))
    if len(mentioned) > 1:
        return ''
    if mentioned:
        district = next(iter(mentioned))
        return district if not (explicit_hints - {district}) else ''
    # Without full administrative names, keep the existing explicit-hint path.
    districts = explicit_hints & CORE_DISTRICTS
    return next(iter(districts)) if len(districts) == 1 else ''


def _rental_only(title, description):
    content = title + ' ' + description
    intent = re.sub(r'投资出租|出租(?:流通性|回报率?|收益|难易|需求|周期)', '', content)
    rental = re.search(r'租房|出租|整租|合租|转租|直租|月租|租期|已租|租出|短租|长租|招租|租赁|必租|押[一二三四五六七八九0-9]+付|租金[^\n]*/月', intent)
    sale = re.search(r'出售|卖房|买房|购房|二手房|诚售|直售|急售|售房|销售价|挂牌价|成交价', title + ' ' + strip_topic_tags(description))
    purchased = re.search(r'(?:买了|买下|买入|购入|入手|置换)[^，。！？\n]{0,16}(?:学区房|房)', title)
    return bool(rental and not sale and not purchased)


def _verified_baseline(builder, bundle, records, baseline_sha256, baseline_path):
    if not isinstance(records, list):
        raise ValueError('Social reviewed bundle must contain a records list')
    path = Path(bundle).resolve()
    path.relative_to(APP / 'data/incremental')
    raw = path.read_bytes()
    relative = str(path.relative_to(ROOT))
    if builder.hashes.get(relative) != digest(raw):
        raise ValueError('Social bundle must pass the parent reviewed-manifest hash gate')
    if json.loads(raw) != records:
        raise ValueError('Social arguments do not match the reviewed immutable bundle')
    if not isinstance(baseline_sha256, str) or not HASH.fullmatch(baseline_sha256):
        raise ValueError('Reviewed manifest must supply baseline_sha256')
    baseline_path = Path(baseline_path or BASELINE).resolve()
    baseline_path.relative_to(APP / 'data/incremental')
    if digest(baseline_path.read_bytes()) != baseline_sha256:
        raise ValueError('Frozen social baseline changed after review')
    baseline = builder.read(baseline_path)
    if builder.hashes[str(baseline_path.relative_to(ROOT))] != baseline_sha256:
        raise ValueError('Frozen social baseline changed while reading')
    for key in ('valid_ids', 'body_ids', 'body_hashes'):
        if (not isinstance(baseline.get(key), list) or any(not isinstance(x, str) for x in baseline[key])
                or len(set(baseline[key])) != len(baseline[key])):
            raise ValueError('Invalid frozen social baseline: ' + key)
    valid_ids, body_ids, hashes = map(set, (baseline['valid_ids'], baseline['body_ids'], baseline['body_hashes']))
    if (not body_ids <= valid_ids or any(not isinstance(x, str) or not NOTE_ID.fullmatch(x) for x in valid_ids)
            or any(not isinstance(x, str) or not HASH.fullmatch(x) for x in hashes)
            or baseline.get('valid_count') != len(valid_ids) or baseline.get('body_count') != len(body_ids)):
        raise ValueError('Frozen social baseline IDs, counts, or hashes are inconsistent')
    loaded = {r[0] for r in builder.db.execute('SELECT id FROM posts')}
    if not valid_ids <= loaded:
        raise ValueError('Load the original frozen post index before social increments')
    return baseline, valid_ids, body_ids, hashes


def _mention_index(builder, *, include_aliases=True):
    names = defaultdict(set)
    for eid, entity in builder.entities.items():
        if entity['kind'] not in ('school', 'residential'):
            continue
        for name in [entity['name'], *(entity.get('aliases', []) if include_aliases else [])]:
            text = unicodedata.normalize('NFKC', str(name or '')).strip()
            # Short brand/district-like aliases are not reliable place mentions.
            if len(text) >= 3:
                names[text].add(eid)
    return names


def exact_place_mentions(text, names):
    """Literal full-name spans, no fuzzy matching or district/name invention."""
    text = unicodedata.normalize('NFKC', text)
    found = []
    for name, ids in names.items():
        start = text.find(name)
        while start >= 0:
            found.append((start, start + len(name), name, ids))
            start = text.find(name, start + 1)
    retained = [item for item in found if not any(
        other[0] <= item[0] and other[1] >= item[1] and other[1] - other[0] > item[1] - item[0]
        for other in found)]
    matched = set().union(*(ids for start, end, name, ids in retained if len(ids) == 1)) if retained else set()
    ambiguous = sorted({name for start, end, name, ids in retained if len(ids) != 1})
    return matched, ambiguous


def _named_home_for_co_mention(entity):
    # The legacy residential kind also contains service-area roads and generic
    # housing terms. They remain in that catalogue, but are not named homes.
    name = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(entity.get('name') or '')))
    if name in {'安置房', '回迁房', '商品房', '学区房', '二手房', '新房', '保障房',
                '公租房', '廉租房', '住宅', '公寓', '别墅', '小区', '社区', '楼盘', '写字楼'}:
        return False
    return not re.search(r'(?:大道|公路|路|街|巷|弄)$|[0-9一二三四五六七八九十百]+(?:[-—之][0-9一二三四五六七八九十百]+)?号(?:楼|栋|幢)?$', name)


def _append_body_co_mentions(builder, pid, places):
    """Same-body evidence only; never create an admission or infer a distance."""
    schools = sorted(eid for eid in places if builder.entities[eid]['kind'] == 'school')
    candidate_homes = sorted(eid for eid in places if builder.entities[eid]['kind'] == 'residential')
    homes = [eid for eid in candidate_homes if _named_home_for_co_mention(builder.entities[eid])]
    excluded = [builder.entities[eid]['name'] for eid in candidate_homes if eid not in homes] if schools else []
    added_pairs = added_evidence = 0
    for school in schools:
        for home in homes:
            a, b = sorted((school, home))
            previous = builder.db.execute('SELECT post_ids FROM co_mentions WHERE a=? AND b=?', (a, b)).fetchone()
            if previous is None:
                builder.db.execute('INSERT INTO co_mentions VALUES(?,?,?,?)', (a, b, dump([pid]), None))
                added_pairs += 1
                added_evidence += 1
                continue
            try:
                old_ids = json.loads(previous[0])
            except (ValueError, TypeError) as exc:
                raise ValueError('Invalid existing co-mention post_ids; relation was not overwritten') from exc
            if not isinstance(old_ids, list) or any(not isinstance(old_id, str) for old_id in old_ids):
                raise ValueError('Invalid existing co-mention post_ids; relation was not overwritten')
            merged = sorted(set(old_ids) | {pid})
            added_evidence += int(pid not in old_ids)
            if merged != old_ids:
                # Keep the existing endpoints, distance, and every legacy post ID.
                builder.db.execute('UPDATE co_mentions SET post_ids=? WHERE a=? AND b=?', (dump(merged), a, b))
    return added_pairs, added_evidence, sorted(set(excluded))


def _validate_record(row, valid_ids, body_ids, baseline_hashes):
    if not isinstance(row, dict) or not isinstance(row.get('record'), dict):
        return 'invalid_record_shape'
    record, pid = row['record'], row.get('id')
    if not isinstance(pid, str) or not NOTE_ID.fullmatch(pid) or record.get('id') != pid:
        return 'exact_note_id_mismatch'
    url = safe_url(row.get('source_url'))
    parts = urlsplit(url)
    if parts.hostname not in ('www.xiaohongshu.com', 'xiaohongshu.com') or parts.path != '/explore/' + pid:
        return 'exact_source_url_mismatch'
    description = record.get('desc')
    if not isinstance(description, str) or not description.strip() or meaningful_characters(description) == 0:
        return 'no_prose'
    count = meaningful_characters(description)
    if count < MIN_MEANINGFUL:
        return 'short_or_low_content'
    if row.get('accepted') is not True:
        return str(row.get('exclusion_reason') or 'not_accepted')
    if record.get('valid') is not True or record.get('exclusion_reasons'):
        return 'out_of_scope'
    if record.get('description_kind') != 'prose_or_caption':
        return 'not_prose_or_caption'
    title = str(record.get('title') or '')
    if _rental_only(title, description):
        return 'rental_only'
    if row.get('body_sha256') != body_hash(description):
        return 'body_hash_mismatch'
    if row.get('meaningful_characters') != count:
        return 'meaningful_character_count_mismatch'
    if 'baseline_index' in row and row['baseline_index'] is not (pid in valid_ids):
        return 'baseline_index_mismatch'
    if pid in body_ids:
        return 'baseline_existing_body'
    if row['body_sha256'] in baseline_hashes:
        return 'duplicate_body_baseline'
    try:
        observed = datetime.fromisoformat(str(row.get('observed_at') or '').replace('Z', '+00:00'))
        if observed.utcoffset() is None:
            return 'invalid_observation_time'
    except ValueError:
        return 'invalid_observation_time'
    return None


def _semantic_review_reason(builder, row, cache):
    """Verify the attested local decision, not only the row's self-declaration.

    One document is parsed/registered once per import. Its stat signature is
    checked on cache hits so ordinary changes during the import fail closed.
    """
    claim = row.get('semantic_review')
    if not isinstance(claim, dict) or claim.get('decision') != 'accept':
        return 'semantic_review_acceptance_required'
    if claim.get('body_sha256') != row.get('body_sha256'):
        return 'semantic_review_claim_body_hash_mismatch'
    expected = claim.get('review_sha256')
    if not isinstance(expected, str) or not HASH.fullmatch(expected):
        return 'semantic_review_file_hash_required'
    source = claim.get('review_source')
    if not isinstance(source, str) or not source.strip():
        return 'semantic_review_source_required'
    try:
        path = (ROOT / source).resolve()
        path.relative_to(APP / 'data/incremental')
        if path.suffix != '.json' or not path.is_file():
            return 'semantic_review_source_missing_or_outside'
        stat = path.stat()
        signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except (ValueError, OSError):
        return 'semantic_review_source_missing_or_outside'
    key = str(path)
    previous = cache.get(key)
    if previous is not None:
        if previous['sha256'] != expected or previous['signature'] != signature:
            return 'semantic_review_file_changed_or_hash_mismatch'
        if previous.get('reason'):
            return previous['reason']
        decisions = previous['decisions']
    else:
        entry = dict(sha256=expected, signature=signature, decisions={}, reason=None)
        cache[key] = entry
        try:
            if digest(path.read_bytes()) != expected:
                entry['reason'] = 'semantic_review_file_changed_or_hash_mismatch'
            else:
                document = builder.read(path)
                stat = path.stat()
                after = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                if (builder.hashes.get(str(path.relative_to(ROOT))) != expected or after != signature):
                    entry['reason'] = 'semantic_review_file_changed_or_hash_mismatch'
                elif not isinstance(document, dict) or not isinstance(document.get('records'), list):
                    entry['reason'] = 'semantic_review_invalid_document'
                elif document.get('reviewed') is False or document.get('status') in ('draft', 'unreviewed', 'pending'):
                    entry['reason'] = 'semantic_review_file_unreviewed'
                else:
                    for decision in document['records']:
                        if (not isinstance(decision, dict) or not isinstance(decision.get('id'), str)
                                or not NOTE_ID.fullmatch(decision['id'])
                                or decision.get('decision') not in ('accept', 'exclude', 'review')
                                or not isinstance(decision.get('body_sha256'), str)
                                or not HASH.fullmatch(decision['body_sha256'])):
                            entry['reason'] = 'semantic_review_invalid_decision'
                            break
                        if decision['id'] in entry['decisions']:
                            entry['reason'] = 'semantic_review_duplicate_decision'
                            break
                        entry['decisions'][decision['id']] = decision
        except (ValueError, OSError, TypeError):
            entry['reason'] = 'semantic_review_unreadable_document'
        if entry['reason']:
            return entry['reason']
        decisions = entry['decisions']
    decision = decisions.get(row['id'])
    if decision is None:
        return 'semantic_review_note_not_found'
    if (decision['decision'] != 'accept' or decision.get('body_read_complete') is False
            or decision.get('count_toward_unique_in_scope_bodies') is False):
        return 'semantic_review_source_does_not_accept'
    if decision['body_sha256'] != row['body_sha256']:
        return 'semantic_review_source_body_hash_mismatch'
    return None


def integrate_social(builder, bundle, records, *, baseline_sha256=None, baseline_path=None,
                     require_semantic_review=False):
    """Return count deltas; existing indexes receive body/depth/observation only."""
    baseline, valid_ids, body_ids, hashes = _verified_baseline(
        builder, bundle, records, baseline_sha256, baseline_path)
    existing = {r[0]: {'description': r[1], 'depth': r[2]} for r in builder.db.execute('SELECT id,description,depth FROM posts')}
    known_hashes = {body_hash(r['description']) for r in existing.values()
                    if r['depth'] == 'detail_description' and r['description']}
    names = _mention_index(builder)
    canonical_names = _mention_index(builder, include_aliases=False)
    seen_ids, seen_hashes = set(), set()
    semantic_review_cache = {}
    result = dict(social_new_posts=0, social_new_bodies=0, social_existing_index_deep_reads=0,
                  social_new_mentions=0, social_new_co_mentions=0, social_new_co_mention_evidence=0,
                  social_excluded_records=[], social_ambiguous_mentions=[],
                  social_excluded_co_mention_places=[],
                  social_imported_ids=[], baseline_sha256=baseline_sha256, baseline_frozen_at=baseline.get('frozen_at'))
    for row in records:
        reason = _validate_record(row, valid_ids, body_ids, hashes)
        pid = row.get('id') if isinstance(row, dict) else None
        if reason is None and require_semantic_review:
            reason = _semantic_review_reason(builder, row, semantic_review_cache)
        if reason is None:
            if pid in seen_ids:
                reason = 'duplicate_note_id_in_bundle'
            elif row['body_sha256'] in seen_hashes:
                reason = 'duplicate_body_in_bundle'
            elif pid in existing and existing[pid]['depth'] == 'detail_description' and existing[pid]['description']:
                reason = 'existing_body_not_overwritten'
            elif row['body_sha256'] in known_hashes:
                reason = 'duplicate_body_in_loaded_database'
        if reason:
            result['social_excluded_records'].append({'id': pid, 'reason': reason})
            continue
        record, description = row['record'], row['record']['desc']
        title = str(record.get('title') or row.get('visible_title') or '')
        posted_date = day(record.get('display_date'))
        if posted_date is None and isinstance(record.get('detail_time'), (int, float)):
            try:
                from zoneinfo import ZoneInfo
                posted_date = datetime.fromtimestamp(record['detail_time'] / 1000, timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
            except (OverflowError, OSError, ValueError):
                pass
        url = safe_url(row['source_url'])
        sid = 'incremental-social:' + pid + ':' + row['body_sha256'][:16]
        builder.source(sid, bundle, '小红书正文 · ' + (title or pid), 'social_evidence',
                       row['observed_at'], posted_date,
                       '明确帖子ID的正文/说明；仅为帖子观点，不是招生、成交或在售证明。'
                       f" normalized_body_sha256={row['body_sha256']}; baseline_sha256={baseline_sha256}"
                       + (f"; semantic_review_sha256={row['semantic_review']['review_sha256']}"
                          f"; semantic_review_source={row['semantic_review']['review_source']}" if require_semantic_review else ''), url)
        if pid in existing:
            builder.db.execute("UPDATE posts SET description=?,depth='detail_description',observed_at=? WHERE id=?",
                               (description, row['observed_at'], pid))
            result['social_existing_index_deep_reads'] += 1
        else:
            district = new_post_district(title, description, record.get('district_hints'))
            topics = record.get('keywords')
            topics = [x for x in topics if isinstance(x, str)] if isinstance(topics, list) else []
            builder.db.execute('INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?)',
                               (pid, title, description, 'detail_description', posted_date, row['observed_at'], url, district, dump(topics)))
            result['social_new_posts'] += 1
        places, ambiguous = exact_place_mentions(title + '\n' + description, names)
        for eid in sorted(places):
            result['social_new_mentions'] += builder.db.execute('INSERT OR IGNORE INTO post_places VALUES(?,?)', (pid, eid)).rowcount
        # Do not reuse old post_places, titles, topic tags, nearby points, or an
        # alias-only hit as evidence from this newly accepted prose body.
        body_text = EMOJI_TOKEN.sub(' ', strip_topic_tags(description))
        body_places, _ = exact_place_mentions(body_text, names)
        canonical_places, _ = exact_place_mentions(body_text, canonical_names)
        pairs, evidence, excluded_co_places = _append_body_co_mentions(builder, pid, body_places & canonical_places)
        result['social_new_co_mentions'] += pairs
        result['social_new_co_mention_evidence'] += evidence
        if excluded_co_places:
            result['social_excluded_co_mention_places'].append({
                'id': pid, 'names': excluded_co_places, 'reason': 'generic_housing_term_or_address_only',
                'action': 'no_new_co_mention_pair; original_places_and_relations_preserved'})
        if ambiguous:
            result['social_ambiguous_mentions'].append({'id': pid, 'names': ambiguous, 'action': 'no_automatic_place_assignment'})
        seen_ids.add(pid)
        seen_hashes.add(row['body_sha256'])
        known_hashes.add(row['body_sha256'])
        existing[pid] = {'description': description, 'depth': 'detail_description'}
        result['social_new_bodies'] += 1
        result['social_imported_ids'].append(pid)
    result['social_exclusion_counts'] = dict(Counter(r['reason'] for r in result['social_excluded_records']))
    return result
