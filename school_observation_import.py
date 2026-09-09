"""Attach verified official progress observations without creating admissions."""
from build_data import dump, day, safe_url, norm


def add_school_observation(builder, bundle, row):
    if (row.get('evidence_type') != 'construction_progress'
            or row.get('creates_official_admission_relation') is not False
            or row.get('residential_names') or row.get('admission_year')
            or not day(row.get('published_at')) or not safe_url(row.get('source_url'))):
        return False
    matches = builder.db.execute('''SELECT DISTINCT s.entity_id,e.name FROM school_records s
        JOIN entities e ON e.id=s.entity_id WHERE s.official_id=? AND s.year=? AND e.district=?''',
        (row.get('baseline_official_id'), row.get('baseline_school_record_year'), row.get('district'))).fetchall()
    if len(matches) != 1 or norm(matches[0][1], True) != norm(row.get('canonical_school_name'), True):
        return False
    sid = 'school-observation:' + row['id']
    builder.source(sid, bundle, row['source_title'], 'official_school_observation', row['observed_at'],
                   row['published_at'], row['notes'], row['source_url'])
    body = (f"官方报道时间：{row['published_at']}；本轮读取：{row['observed_at']}。\n"
            f"报道记载：{row['source_quote']}。\n{row['notes']}\n"
            f"原始响应 SHA-256：{row['response_sha256']}")
    inserted = builder.db.execute('INSERT OR IGNORE INTO policy_texts VALUES(?,?,?,?,?,?,?)',
        (row['id'], sid, row['published_at'][:4], '学校建设观察 · 非招生关系 · ' + row['source_title'],
         body, dump([matches[0][0]]), 'construction_progress'))
    return inserted.rowcount == 1
