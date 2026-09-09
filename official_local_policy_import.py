"""Import reviewed local policy archives, never annual school/admission rows.

The outer immutable manifest is the approval authority. A projection's original
``approved:false`` records its candidate state, not a later manifest decision.
Only sources and policy_texts are inserted. The legacy school_ids column holds
document-relevant existing school OR residential IDs; it is not a relation graph.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from build_data import APP, ROOT, day, digest, dump, norm, safe_url

HASH = re.compile(r'[0-9a-f]{64}')
SOURCE_KIND = 'official_local_policy_archive'
POLICY_KIND = 'local_policy_archive'
SOURCE_HOSTS = {'滨江区': 'www.hhtz.gov.cn', '拱墅区': 'www.gongshu.gov.cn'}
ORIGINAL_ROOTS = (ROOT / 'reports', ROOT / '杭州主城区小红书楼盘研究_2025-12-01至2026-09-07',
                  APP / 'data/incremental')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _compact(text):
    return re.sub(r'\s+', '', text)


def _within(path, roots):
    return any(path.is_relative_to(root.resolve()) for root in roots)


def _read_registered(builder, path, expected, cache):
    """Verify every read; cache parsed JSON, not a stale hash assertion."""
    path = Path(path).resolve()
    _require(_within(path, ORIGINAL_ROOTS), 'Local policy evidence path is outside allowed roots')
    _require(path.suffix in ('.json', '.body', '.headers') and path.is_file(), 'Missing local policy evidence file')
    _require(isinstance(expected, str) and HASH.fullmatch(expected), 'Invalid local policy evidence hash')
    raw = path.read_bytes()
    _require(digest(raw) == expected, 'Local policy evidence hash mismatch')
    relative = str(path.relative_to(ROOT))
    key = (relative, expected)
    if key not in cache:
        if path.suffix == '.json':
            parsed = builder.read(path)
            _require(builder.hashes.get(relative) == expected, 'Local policy JSON changed during read')
        else:
            parsed = None
            builder.hashes[relative] = expected
        cache[key] = (raw, parsed)
    return cache[key]


def _bundle(builder, bundle, records, reviewed_sha256):
    path = Path(bundle).resolve()
    _require(path.is_relative_to((APP / 'data/incremental').resolve()), 'Policy bundle outside increment tree')
    _require(isinstance(records, list), 'Policy bundle requires a records list')
    _require(isinstance(reviewed_sha256, str) and HASH.fullmatch(reviewed_sha256), 'Reviewed manifest hash required')
    raw = path.read_bytes()
    relative = str(path.relative_to(ROOT))
    _require(digest(raw) == reviewed_sha256 and builder.hashes.get(relative) == reviewed_sha256,
             'Policy bundle changed or has not passed registered manifest hash gate')
    _require(json.loads(raw) == records, 'Policy arguments differ from immutable reviewed bundle')
    return path


def _original(builder, source, base, cache):
    descriptor = source.get('original_file') or {}
    _require(isinstance(descriptor.get('path'), str), 'Original policy path missing')
    path = (ROOT / descriptor['path']).resolve()
    if descriptor.get('absolute_path'):
        _require(Path(descriptor['absolute_path']).resolve() == path, 'Original path declarations conflict')
    raw, original = _read_registered(builder, path, descriptor.get('sha256'), cache)
    _require(len(raw) == descriptor.get('bytes'), 'Original evidence byte count mismatch')
    copy = (base / str(source.get('preserved_copy', ''))).resolve()
    _require(copy.is_relative_to(base), 'Preserved policy copy escaped source bundle')
    _read_registered(builder, copy, descriptor['sha256'], cache)
    article = source.get('full_original_article_text')
    visible = source.get('full_original_visible_page_text')
    _require(isinstance(article, str) and len(article.strip()) >= 100, 'Complete policy article missing')
    _require(isinstance(visible, str) and article.strip() and _compact(article) in _compact(visible),
             'Article does not occur in retained complete visible text')
    expected_text = source.get('text_sha256')
    _require(expected_text in (digest(article), digest(visible)), 'Extracted policy text hash mismatch')
    observation = source.get('original_observation') or {}
    _require(isinstance(observation, dict), 'Original observation metadata missing')
    exact = observation.get('exact_client_observed_at')
    if exact is not None:
        try:
            stamp = datetime.fromisoformat(exact.replace('Z', '+00:00'))
            _require(stamp.utcoffset() is not None, 'Original observation time needs timezone')
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError('Invalid original observation time') from error
    if path.suffix == '.body':
        # Reuse the existing pure-text parser; importing server starts no server.
        from server import ArticleTextParser
        parser = ArticleTextParser()
        html = raw.decode('utf-8')
        parser.feed(html)
        parser.close()
        _require(_compact(parser.text()) == _compact(article), 'Article is not the complete original HTML article')
        _require(parser.title == source.get('title'), 'Original HTML title mismatch')
        meta_descriptor = source.get('metadata_source') or {}
        _, metadata = _read_registered(builder, ROOT / str(meta_descriptor.get('path', '')),
                                       meta_descriptor.get('sha256'), cache)
        matches = [entry for entry in metadata.get('official_dynamic_detail_sources', {}).values()
                   if entry.get('url') == source['url'] and entry.get('raw_sha256') == descriptor['sha256']]
        _require(len(matches) == 1, 'Original HTTP metadata source is ambiguous or missing')
        captured = matches[0]
        _require(source['tls_verified'] is False and captured.get('tls_verified') is False,
                 'HTML archive TLS limitation differs from original capture')
        _require(exact is None and observation.get('original_snapshot_run_at') == metadata.get('run_at_cst')
                 and observation.get('server_response_date_header') == captured.get('date_header'),
                 'Original capture time changed or server time presented as client time')
        published = re.search(r'<meta\s+name="PubDate"\s+content="([^"]+)"', html)
        _require(published and day(published.group(1)) == source['publication_date'], 'Original publication date mismatch')
        headers = source.get('original_headers_file') or {}
        _read_registered(builder, ROOT / str(headers.get('path', '')), headers.get('sha256'), cache)
        return article, exact
    _require(path.suffix == '.json' and isinstance(original, dict), 'Unsupported policy capture format')
    _require(source['tls_verified'] is None, 'Visible-text JSON has no TLS verification evidence')
    _require(original.get('title') == source['title'] and original.get('url') == source['url'],
             'Original JSON title or URL mismatch')
    _require(original.get('text') == visible and original.get('observed_at') == exact and exact is not None,
             'Original visible text or observation time changed')
    _require(source['publication_date'] in visible, 'Publication date absent from original visible page')
    # Preserve the complete observed JSON text, not just a selected excerpt.
    return visible, exact


def _entity_ids(builder, row, source):
    mentions = row.get('related_entities')
    _require(isinstance(mentions, list) and mentions, 'Policy document needs explicit reviewed entity attachments')
    result = []
    for item in mentions:
        _require(isinstance(item, dict) and item.get('kind') in ('school', 'residential'), 'Invalid document attachment kind')
        _require(item.get('district') == source['district'], 'Policy attachment district mismatch')
        name = item.get('source_name')
        _require(isinstance(name, str) and name and name in source['full_original_article_text'],
                 'Attached place name absent from source article')
        options = []
        for eid, actual_name, aliases in builder.db.execute('SELECT id,name,aliases FROM entities WHERE kind=? AND district=?',
                                                           (item['kind'], item['district'])):
            names = [actual_name, *json.loads(aliases or '[]')]
            if norm(name, item['kind'] == 'school') in {norm(n, item['kind'] == 'school') for n in names}:
                options.append((eid, actual_name))
        _require(options == [(item.get('entity_id'), item.get('entity_name'))], 'Policy attachment is missing, ambiguous, or changed')
        _require(item['entity_id'] not in result, 'Duplicate policy entity attachment')
        result.append(item['entity_id'])
    return result


def integrate_local_policies(builder, bundle, records, *, reviewed_sha256=None):
    """Return archive counts only. Fail closed before writes; never commit."""
    bundle = _bundle(builder, bundle, records, reviewed_sha256)
    cache, prepared, seen = {}, [], set()
    for row in records:
        _require(isinstance(row, dict) and row.get('creates_official_admission_relation') is False,
                 'Local archive must explicitly create no admissions')
        identity = row.get('id')
        _require(isinstance(identity, str) and re.fullmatch(r'[a-z0-9:._-]{1,160}', identity), 'Invalid policy identity')
        _require(identity not in seen, 'Duplicate policy identity in bundle')
        seen.add(identity)
        source = row.get('source')
        _require(isinstance(source, dict) and source.get('id') == identity, 'Policy source identity mismatch')
        base = (ROOT / str(row.get('source_bundle_path', ''))).resolve()
        _require(base.is_relative_to((APP / 'data/incremental').resolve()), 'Policy source bundle outside increment tree')
        _, sealed = _read_registered(builder, base / 'sources.json', row.get('source_bundle_sources_sha256'), cache)
        matches = [s for s in sealed.get('sources', []) if s.get('id') == identity]
        _require(len(matches) == 1 and matches[0] == source, 'Policy source differs from sealed source bundle')
        _require(source.get('district') in SOURCE_HOSTS and safe_url(source.get('url')) == source.get('url')
                 and urlsplit(source['url']).hostname == SOURCE_HOSTS[source['district']], 'Policy source URL outside declared district authority')
        _require(source.get('this_run_network_fetch') is False and source.get('tls_verified') in (False, None)
                 and source.get('tls_verified') is not True, 'Local archive cannot claim a new or TLS-verified fetch')
        _require(isinstance(source.get('tls_note'), str) and source['tls_note'].strip(), 'Missing TLS evidence limitation')
        year = source.get('source_year')
        _require(isinstance(year, str) and re.fullmatch(r'20\d{2}', year)
                 and day(source.get('publication_date')) == source.get('publication_date')
                 and source['publication_date'].startswith(year), 'Invalid policy year or publication date')
        full_text, observed = _original(builder, source, base, cache)
        _require(year + '学年' in _compact(source['full_original_article_text']), 'Policy applicable school year absent from article')
        ids = _entity_ids(builder, row, source)
        tls = '原采集 TLS 证书未核验' if source['tls_verified'] is False else '原可见文本副本缺少 TLS 握手／HTTP 验证证据'
        observation = source['original_observation']
        notes = ('本地历史官方页副本；不是本轮联网或当前招生核验。' + tls + '。'
                 '只关联文档明确提及的已有地点，不新增招生关系、学校年度档案、校区桥接或坐标。'
                 ' 自愿报名与转引范围保留原文；不得当作无条件对口。\n'
                 '原始观察元数据：' + dump(observation) + '\nTLS说明：' + source['tls_note'] + '\n'
                 '适用年度：' + year + '；网页发布日期：' + source['publication_date']
                 + '；正文落款日期：' + str(source.get('document_signed_date') or '原文件未另行记录') + '\n'
                 '原始文件：' + source['original_file']['path'] + '\n原始 SHA-256：' + source['original_file']['sha256'])
        title = '本地政策档案 · ' + year + ' · ' + source['title']
        sid = 'official-local-policy:' + identity
        pid = 'local-policy:' + identity
        body = notes + '\n\n完整原文（纯文本；不包含图片或附件）：\n' + full_text
        policy = (pid, sid, year, title, body, dump(ids), POLICY_KIND)
        source_values = (sid, title, SOURCE_KIND, str(bundle.relative_to(ROOT)), observed,
                         source['publication_date'], source['url'], reviewed_sha256, notes)
        old_policy = builder.db.execute('SELECT * FROM policy_texts WHERE id=?', (pid,)).fetchone()
        old_source = builder.db.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone()
        _require(old_policy is None or tuple(old_policy) == policy, 'Existing policy identity would be overwritten')
        _require(old_source is None or tuple(old_source) == source_values, 'Existing local policy source would be overwritten')
        prepared.append((policy, source_values, old_policy is None, old_source is None))
    result = dict(official_local_policies=0, official_local_policy_sources=0, official_local_policy_entity_mentions=0)
    if not builder.db.in_transaction:
        builder.db.execute('BEGIN')
    builder.db.execute('SAVEPOINT official_local_policies')
    try:
        for policy, source_values, new_policy, new_source in prepared:
            builder.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?,?,?)', source_values)
            builder.db.execute('INSERT OR IGNORE INTO policy_texts VALUES(?,?,?,?,?,?,?)', policy)
            result['official_local_policies'] += int(new_policy)
            result['official_local_policy_sources'] += int(new_source)
            result['official_local_policy_entity_mentions'] += len(json.loads(policy[5])) if new_policy else 0
        builder.db.execute('RELEASE SAVEPOINT official_local_policies')
    except Exception:
        builder.db.execute('ROLLBACK TO SAVEPOINT official_local_policies')
        builder.db.execute('RELEASE SAVEPOINT official_local_policies')
        raise
    return result
