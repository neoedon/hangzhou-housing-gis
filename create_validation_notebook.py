"""Generate and execute a bounded, reader-facing companion notebook with nbformat."""
from pathlib import Path
import hashlib
import json
import nbformat as nbf
from nbclient import NotebookClient

APP = Path(__file__).resolve().parent
NOTEBOOK = APP / "qa/data_validation.ipynb"
NOTEBOOK.parent.mkdir(exist_ok=True)
receipt = json.loads((APP / "data/build_receipt.json").read_text())
metrics = receipt["metrics"]
expected_counts = {
    "posts": metrics["posts"],
    "candidates": metrics["candidates"],
    "school_records": metrics["school_records"],
    "admissions": metrics["admissions"],
    "prices": metrics["prices"],
}
expected_details = metrics["post_details"]
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
nb = nbf.v4.new_notebook(cells=[
    md(f"# 杭州住房 GIS · 数据整合验证\n\n## tl;dr\n\n本笔记是本地 GIS 的可复算伴随文件。执行后核对：{metrics['posts']:,} 条帖子（{expected_details:,} 条正文/说明）、{metrics['candidates']:,} 行候选、{metrics['school_records']:,} 条学校档案、{metrics['admissions']:,} 条年度招生明细；价格分为 {metrics['price_kinds']['deal']:,} 条历史成交、{metrics['price_kinds']['listing']:,} 条挂牌线索和 {metrics['price_kinds']['reference']:,} 条小区参考价线索。\n\n这些是索引覆盖，不是全部点位和业务事实已核实。"),
    md("## Context & Methods\n\n读者：本地住房与入学研究者。目标：确认整合没有丢失来源、混淆粒度、伪造坐标或把当前数据回填未来年份。只查询 `../data/housing.sqlite`，不发起外部采集。\n\n### Key Assumptions\n\n- 名称归一只去空白、间隔点和学校名称的杭州前缀；保留校区/分期限定。唯一名称匹配仍待地址核验。\n- 官方明细包括小区、楼栋、道路、门牌和社区范围，3,701 条不等于 3,701 个独立住宅小区。\n- 候选观察日期、官方招生年度、成交日期独立，不进行整库历史回溯。\n- OSM 点位为 WGS84 对象中心；官方门户学校坐标明确经 GCJ-02 → WGS84 转换。仅在同区 180 米内建立地图校区桥接，原官方编号与招生明细不改写。"),
    md("## Data\n\n### 1. 打开只读索引"),
    code("from pathlib import Path\nimport sqlite3, json, hashlib\nfrom IPython.display import display\napp = Path.cwd().parent\nif not (app / 'data/housing.sqlite').exists():\n    raise RuntimeError('请在 housing-gis/qa 作为工作目录执行')\ndb = sqlite3.connect((app / 'data/housing.sqlite').as_uri() + '?mode=ro', uri=True)\ndb.row_factory = sqlite3.Row\ndb.execute('PRAGMA query_only=ON')\ndef table(sql, args=()):\n    return [dict(row) for row in db.execute(sql, args)]\nmeta = {row['key']:json.loads(row['value']) for row in db.execute('SELECT * FROM meta')}\nprint('构建日期：', meta['built_at'])\nprint('源文件数量：',len(meta['source_fingerprints']))"),
    md("### 2. 来源与真实日期\n\n报告生成日期不能刷新陈旧样本的有效期。文件路径与 SHA-256 同时保留在来源登记表中。"),
    code("display(table(\"SELECT label,source_as_of,observed_at,path FROM sources WHERE id IN ('candidates','admissions','deals','listing-base','listing-overlay','posts') ORDER BY id\"))\nassert db.execute(\"SELECT max(event_date) FROM prices WHERE kind='deal'\").fetchone()[0] == '2026-08-09'\nassert db.execute(\"SELECT max(event_date) FROM prices WHERE kind='deal' AND total_wan IS NOT NULL AND unit_yuan_sqm IS NOT NULL\").fetchone()[0] == '2026-07-16'"),
    md("## Results\n\n### 3. 对账数量与粒度"),
    code("counts = {name:db.execute(f'SELECT count(*) FROM {name}').fetchone()[0] for name in ['posts','candidates','school_records','admissions','prices']}\nassert counts == " + repr(expected_counts) + "\nassert db.execute(\"SELECT count(*) FROM posts WHERE depth='detail_description'\").fetchone()[0] == " + str(expected_details) + "\nprint(counts)\ndisplay(table('SELECT kind,count(*) AS records FROM prices GROUP BY kind'))\ndisplay(table('SELECT year,admission_type,count(*) AS records FROM admissions GROUP BY year,admission_type'))"),
    md("### 4. 地点匹配覆盖与未定位队列"),
    code("display(table(\"SELECT status,count(*) AS records FROM mappings WHERE source_id='candidates' GROUP BY status\"))\ndisplay(table(\"SELECT status,count(*) AS records FROM mappings WHERE source_id='admissions' AND source_record NOT LIKE 'home:%' GROUP BY status\"))\nassert db.execute(\"SELECT count(*) FROM entities WHERE id NOT LIKE 'osm:%' AND lat IS NOT NULL AND location_status!='official_portal_gcj02_to_wgs84'\").fetchone()[0] == 0\nassert db.execute(\"SELECT count(*) FROM school_campus_links\").fetchone()[0] == 18\nassert meta['metrics']['official_located_school_records'] == 93\nmarket_bridges = meta['metrics']['residential_identity_bridges']\nassert market_bridges['bridges'] == 16 and market_bridges['prices'] == 90 and market_bridges['market_snapshots'] == 2\nadmission_bridges = meta['metrics']['residential_admission_identity_bridges']\nassert admission_bridges['bridges'] == 51 and admission_bridges['admissions'] == 119\nassert admission_bridges['admissions_by_district'] == {'滨江区':44,'拱墅区':75}\nassert db.execute(\"SELECT count(*) FROM mappings WHERE source_id='residential-admission-identity-bridges'\").fetchone()[0] == 51\nprint('未定位实体数：',db.execute('SELECT count(*) FROM entities WHERE lat IS NULL').fetchone()[0])\nprint('学校地图桥接 / 可落图学校档案：',db.execute('SELECT count(*) FROM school_campus_links').fetchone()[0],meta['metrics']['official_located_school_records'])\nprint('官方招生名称关联实体 / 有地图点位：',meta['metrics']['official_home_entities'],meta['metrics']['official_located_home_entities'])\nprint('逐条审核的价格身份桥 / 价格记录：',market_bridges['bridges'],market_bridges['prices'])\nprint('逐条审核的小区名身份桥 / 官方关系：',admission_bridges['bridges'],admission_bridges['admissions'])"),
    md("### 5. 年度隔离、引用完整性和原文件保护"),
    code("assert db.execute(\"SELECT count(*) FROM admissions WHERE year='2029'\").fetchone()[0] == 0\nassert db.execute(\"SELECT year FROM school_records WHERE official_id='3133000614001'\").fetchone()[0] == '2024'\nassert db.execute('PRAGMA foreign_key_check').fetchall() == []\nassert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'\nfor relative, expected in meta['source_fingerprints'].items():\n    actual = hashlib.sha256((app.parent / relative).read_bytes()).hexdigest()\n    assert actual == expected, relative\nprint('PASS：未来年份不回填；历史年份保留；外键完整；源文件哈希全部一致。')\nprint('候选日快照：', db.execute('SELECT count(DISTINCT snapshot_date) FROM history').fetchone()[0])\ndb.close()"),
    md("## Takeaways\n\n1. 来源数量、外键、年份和原文件哈希通过对账。可以把索引用于本地检索和证据导航。\n2. 候选 94 行唯一名称匹配、5 行歧义、80 行未匹配；官方目录已覆盖滨江、拱墅。18 条学校—地图校区桥接使 95 条学校档案中的 93 条可落图；51 组小区名身份桥将 119 条锁定的 2026 年官方关系挂到经审核的同区 OSM 实体。新增 17 组要求官方名严格等于项目名或明确别名再加允许的住宅后缀，且由测试与记录哈希约束。这些桥接仍不是单套住房资格核验。\n3. 16 组价格身份桥共回接 90 条价格和 2 条行情快照；可计算单套价格的历史成交最近日期为 2026-07-16。挂牌是未核实线索；空预警不是低风险。2029 年没有官方年度关系。\n4. 地图提供行政边界，不生成猜测学区。直接名单未命中时只列有证据等级的核验入口。"),
])
nb.metadata.kernelspec = {"display_name":"Python 3", "language":"python", "name":"python3"}
nb.metadata.language_info = {"name":"python"}
nb.metadata.gis_database_sha256 = hashlib.sha256((APP / "data/housing.sqlite").read_bytes()).hexdigest()
nbf.validate(nb)
client = NotebookClient(nb, timeout=60, kernel_name="python3", resources={"metadata":{"path":str(NOTEBOOK.parent)}})
client.execute()
nbf.validate(nb)
nbf.write(nb, NOTEBOOK)
print(f"Executed {len([c for c in nb.cells if c.cell_type == 'code'])} code cells: {NOTEBOOK}")
