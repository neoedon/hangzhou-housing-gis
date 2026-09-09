import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sourcePage } from '../web/model.js';

const makeSources = (count = 1090) => Array.from({ length: count }, (_, index) => ({
  id: `source:${String(index + 1).padStart(4, '0')}`,
  label: `来源 ${index + 1}`,
  notes: index % 2 ? '拱墅区 公开成交' : '滨江区 官方学校资料',
  url: `https://example.test/evidence/${index + 1}`,
  source_as_of: '2026-05-01',
  observed_at: '2026-09-09T08:30:00+08:00',
  kind: index % 2 ? 'market_deal' : 'official_admissions',
}));
const ids = rows => rows.map(row => row.id);
const pageMetadata = ({ total, matched, page, pageCount, pageSize, start, end, focus, missing }) =>
  ({ total, matched, page, pageCount, pageSize, start, end, focus, missing });

test('source pagination reports the real 1090-source total and a 40-item first page', () => {
  const sources = makeSources();
  const result = sourcePage(sources);
  assert.deepEqual(pageMetadata(result), {
    total: 1090, matched: 1090, page: 1, pageCount: 28, pageSize: 40,
    start: 1, end: 40, focus: '', missing: false,
  });
  assert.deepEqual(result.items, sources.slice(0, 40));
});

test('all pages preserve every source exactly once and in original order', () => {
  const sources = makeSources();
  const allItems = [];
  for (let page = 1; page <= 28; page++) {
    const result = sourcePage(sources, { page });
    assert.equal(result.page, page);
    assert.equal(result.start, (page - 1) * 40 + 1);
    assert.equal(result.end, Math.min(page * 40, sources.length));
    assert.equal(result.items.length, result.end - result.start + 1);
    allItems.push(...result.items);
  }
  assert.deepEqual(allItems, sources);
  assert.equal(new Set(ids(allItems)).size, 1090);
});

test('last-page overflow clamps to 28 and keeps the final ten sources', () => {
  const sources = makeSources();
  const result = sourcePage(sources, { page: 9999 });
  assert.deepEqual(pageMetadata(result), {
    total: 1090, matched: 1090, page: 28, pageCount: 28, pageSize: 40,
    start: 1081, end: 1090, focus: '', missing: false,
  });
  assert.deepEqual(result.items, sources.slice(1080));
});

test('exact multiples of forty never create an extra empty page', () => {
  for (const count of [40, 80, 1080]) {
    const result = sourcePage(makeSources(count), { page: 999 });
    assert.equal(result.pageCount, count / 40);
    assert.equal(result.page, count / 40);
    assert.equal(result.items.length, 40);
    assert.equal(result.end, count);
  }
});

test('page size stays forty even if a caller supplies an unsupported override', () => {
  const result = sourcePage(makeSources(), { pageSize: 1090 });
  assert.equal(result.pageSize, 40);
  assert.equal(result.items.length, 40);
});

test('numeric page strings and fractions are floored before clamping', () => {
  const sources = makeSources();
  for (const page of [3.9, '3.9', ' 3.9 ']) {
    const result = sourcePage(sources, { page });
    assert.equal(result.page, 3);
    assert.deepEqual(result.items, sources.slice(80, 120));
  }
  for (const page of [0, -1, -9.4]) assert.equal(sourcePage(sources, { page }).page, 1);
});

test('invalid and non-finite page values default to the first page', () => {
  const sources = makeSources();
  for (const page of [undefined, null, '', 'not-a-page', 'Infinity', NaN, Infinity, -Infinity, {}]) {
    const result = sourcePage(sources, { page });
    assert.equal(result.page, 1, `page=${String(page)}`);
    assert.deepEqual(result.items, sources.slice(0, 40));
  }
});

test('search tokens use AND semantics across distinct supported fields', () => {
  const sources = [
    { id: 'one', label: '滨江区学校资料', notes: '完整公开名单', kind: 'official_admissions', source_as_of: '2026-05' },
    { id: 'two', label: '滨江区学校资料', notes: '完整公开名单', kind: 'official_admissions', source_as_of: '2025-05' },
    { id: 'three', label: '拱墅区学校资料', notes: '完整公开名单', kind: 'official_admissions', source_as_of: '2026-05' },
  ];
  const result = sourcePage(sources, { query: '滨江\t完整\n2026  official' });
  assert.deepEqual(ids(result.items), ['one']);
  assert.equal(result.total, 3);
  assert.equal(result.matched, 1);
});

test('every declared source text field can independently satisfy a search', () => {
  const fields = ['label', 'id', 'notes', 'url', 'source_as_of', 'observed_at', 'kind'];
  for (const field of fields) {
    const target = { id: 'target', [field]: 'uniqueNeedle' };
    const other = { id: 'other', label: 'not a match' };
    assert.deepEqual(sourcePage([other, target], { query: 'uniqueneedle' }).items, [target], field);
  }
});

test('sources sharing the same title and URL remain separately available', () => {
  const sources = [
    { id: 'observed:old', label: '同一学校招生通知', url: 'https://example.test/policy' },
    { id: 'observed:new', label: '同一学校招生通知', url: 'https://example.test/policy' },
  ];
  const result = sourcePage(sources, { query: '招生' });
  assert.equal(result.total, 2);
  assert.equal(result.matched, 2);
  assert.deepEqual(ids(result.items), ['observed:old', 'observed:new']);
});

test('NFKC, mixed casing and Unicode whitespace normalize search tokens', () => {
  const sources = [
    { id: 'width-match', label: 'BinJiang', notes: '２０２６ 年 官方名单' },
    { id: 'wrong-year', label: 'binjiang', notes: '2025 年 官方名单' },
  ];
  assert.deepEqual(ids(sourcePage(sources, { query: '　ＢＩＮＪＩＡＮＧ　2026　名单 ' }).items), ['width-match']);
});

test('search punctuation is a literal substring rather than a regular expression', () => {
  const sources = [
    { id: 'literal', notes: 'source [2026] a.*b' },
    { id: 'not-literal', notes: 'source 2026 axxxxxb' },
  ];
  assert.deepEqual(ids(sourcePage(sources, { query: 'a.*b' }).items), ['literal']);
  assert.deepEqual(ids(sourcePage(sources, { query: '[' }).items), ['literal']);
  assert.equal(sourcePage(sources, { query: '^source$' }).matched, 0);
});

test('one token cannot be invented by concatenating unrelated field boundaries', () => {
  const sources = [{ id: 'cd', label: 'ab' }, { id: 'actual', notes: 'abcd' }];
  assert.deepEqual(ids(sourcePage(sources, { query: 'abcd' }).items), ['actual']);
});

test('search runs over all sources before pagination and clamps against matched pages', () => {
  const sources = makeSources();
  const result = sourcePage(sources, { query: '拱墅 公开成交', page: 99 });
  assert.equal(result.total, 1090);
  assert.equal(result.matched, 545);
  assert.equal(result.pageCount, 14);
  assert.equal(result.page, 14);
  assert.equal(result.start, 521);
  assert.equal(result.end, 545);
  assert.deepEqual(result.items, sources.filter((_, index) => index % 2).slice(520));
  assert.deepEqual(ids(sourcePage(sources, { query: 'source:1090' }).items), ['source:1090']);
});

test('empty and whitespace-only searches retain the entire collection', () => {
  const sources = makeSources(81);
  for (const query of ['', ' ', '\t\n　']) {
    const result = sourcePage(sources, { query, page: 3 });
    assert.equal(result.matched, 81);
    assert.equal(result.pageCount, 3);
    assert.deepEqual(result.items, sources.slice(80));
  }
});

test('no search matches uses a stable empty first-page state without a missing-focus error', () => {
  const result = sourcePage(makeSources(), { query: 'unfindable-source-token', page: 28 });
  assert.deepEqual(result.items, []);
  assert.deepEqual(pageMetadata(result), {
    total: 1090, matched: 0, page: 1, pageCount: 1, pageSize: 40,
    start: 0, end: 0, focus: '', missing: false,
  });
});

test('an empty collection has one navigable empty page, not zero or negative pages', () => {
  assert.deepEqual(pageMetadata(sourcePage([], { page: 99 })), {
    total: 0, matched: 0, page: 1, pageCount: 1, pageSize: 40,
    start: 0, end: 0, focus: '', missing: false,
  });
});

test('focused mode finds an exact late source and ignores both query and requested page', () => {
  const sources = makeSources();
  const result = sourcePage(sources, { focus: 'source:1090', query: 'unfindable', page: 999 });
  assert.deepEqual(result.items, [sources[1089]]);
  assert.deepEqual(pageMetadata(result), {
    total: 1090, matched: 1, page: 1, pageCount: 1, pageSize: 40,
    start: 1, end: 1, focus: 'source:1090', missing: false,
  });
});

test('missing focus never falls back to all sources or regular text search', () => {
  const sources = [{ id: 'source:12', label: 'source:missing' }, { id: 'SOURCE:12', notes: 'alias' }];
  for (const focus of ['source:missing', 'source:1', 'Source:12', 'ｓｏｕｒｃｅ:12']) {
    const result = sourcePage(sources, { focus, query: '', page: 99 });
    assert.deepEqual(result.items, []);
    assert.deepEqual(pageMetadata(result), {
      total: 2, matched: 0, page: 1, pageCount: 1, pageSize: 40,
      start: 0, end: 0, focus, missing: true,
    });
  }
  assert.equal(sourcePage(sources, { focus: 'SOURCE:12' }).items[0].id, 'SOURCE:12');
});

test('null or absent text fields do not create artificial searchable null/undefined strings', () => {
  const sources = [{ id: 'minimal' }, { id: 'missing-fields', notes: null, source_as_of: null, observed_at: null }];
  for (const query of ['null', 'undefined']) assert.equal(sourcePage(sources, { query }).matched, 0);
  assert.equal(sourcePage(sources).matched, 2);
});

test('pagination, filtering and focus never mutate frozen sources or options', () => {
  const sources = Object.freeze(makeSources(90).map(source => Object.freeze(source)));
  const before = structuredClone(sources);
  const options = Object.freeze({ query: '滨江', page: 2, focus: '' });
  sourcePage(sources, options);
  sourcePage(sources, { page: 3 });
  sourcePage(sources, { focus: 'source:0090', query: 'no-match' });
  assert.deepEqual(sources, before);
  assert.deepEqual(options, { query: '滨江', page: 2, focus: '' });
});
