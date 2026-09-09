"""Generate and execute a bounded, reader-facing companion notebook with nbformat."""
from pathlib import Path
import hashlib
import nbformat as nbf
from nbclient import NotebookClient

APP = Path(__file__).resolve().parent
NOTEBOOK = APP / "qa/data_validation.ipynb"
NOTEBOOK.parent.mkdir(exist_ok=True)
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
nb = nbf.v4.new_notebook(cells=[
    md("# 杭州住房 GIS · 数据整合验证\n\n## tl;dr\n\n本笔记是本地 GIS 的可复算伴随文件。执行后核对：3,021 条原帖（514 条正文/说明）、179 行候选、95 条学校档案、3,701 条年度招生明细；价格分为 1,542 条历史成交、67 条挂牌线索和 24 条小区参考价线索。\n\n这些是索引覆盖，不是全部点位和业务事实已核实。"),
    md("## Context & Methods\n\n读者：本地住房与入学研究者。目标：确认整合没有丢失来源、混淆粒度、伪造坐标或把当前数据回填未来年份。只查询 `../data/housing.sqlite`，不发起外部采集。\n\n### Key Assumptions\n\n- 名称归一只去空白、间隔点和学校名称的杭州前缀；保留校区/分期限定。唯一名称匹配仍待地址核验。\n- 官方明细包括小区、楼栋、道路、门牌和社区范围，3,701 条不等于 3,701 个独立住宅小区。\n- 候选观察日期、官方招生年度、成交日期独立，不进行整库历史回溯。\n- OSM 点位为 WGS84 对象中心；官方门户学校坐标明确经 GCJ-02 → WGS84 转换。仅在同区 180 米内建立地图校区桥接，原官方编号与招生明细不改写。"),
    md("## Data\n\n### 1. 打开只读索引"),
    code("from pathlib import Path\nimport sqlite3, json, hashlib\nfrom IPython.display import display\napp = Path.cwd().parent\nif not (app / 'data/housing.sqlite').exists():\n    raise RuntimeError('请在 housing-gis/qa 作为工作目录执行')\ndb = sqlite3.connect((app / 'data/housing.sqlite').as_uri() + '?mode=ro', uri=True)\ndb.row_factory = sqlite3.Row\ndb.execute('PRAGMA query_only=ON')\ndef table(sql, args=()):\n    return [dict(row) for row in db.execute(sql, args)]\nmeta = {row['key']:json.loads(row['value']) for row in db.execute('SELECT * FROM meta')}\nprint('构建日期：', meta['built_at'])\nprint('源文件数量：',len(meta['source_fingerprints']))"),
    md("### 2. 来源与真实日期\n\n报告生成日期不能刷新陈旧样本的有效期。文件路径与 SHA-256 同时保留在来源登记表中。"),
    code("display(table(\"SELECT label,source_as_of,observed_at,path FROM sources WHERE id IN ('candidates','admissions','deals','listing-base','listing-overlay','posts') ORDER BY id\"))\nassert db.execute(\"SELECT max(event_date) FROM prices WHERE kind='deal'\").fetchone()[0] == '2026-06-23'"),
    md("## Results\n\n### 3. 对账数量与粒度"),
    code("counts = {name:db.execute(f'SELECT count(*) FROM {name}').fetchone()[0] for name in ['posts','candidates','school_records','admissions','prices']}\nassert counts == {'posts':3021,'candidates':179,'school_records':95,'admissions':3701,'prices':1633}\nassert db.execute(\"SELECT count(*) FROM posts WHERE depth='detail_description'\").fetchone()[0] == 514\nprint(counts)\ndisplay(table('SELECT kind,count(*) AS records FROM prices GROUP BY kind'))\ndisplay(table('SELECT year,admission_type,count(*) AS records FROM admissions GROUP BY year,admission_type'))"),
    md("### 4. 地点匹配覆盖与未定位队列"),
    code("display(table(\"SELECT status,count(*) AS records FROM mappings WHERE source_id='candidates' GROUP BY status\"))\ndisplay(table(\"SELECT status,count(*) AS records FROM mappings WHERE source_id='admissions' AND source_record NOT LIKE 'home:%' GROUP BY status\"))\nassert db.execute(\"SELECT count(*) FROM entities WHERE id NOT LIKE 'osm:%' AND lat IS NOT NULL AND location_status!='official_portal_gcj02_to_wgs84'\").fetchone()[0] == 0\nassert db.execute(\"SELECT count(*) FROM school_campus_links\").fetchone()[0] == 18\nassert meta['metrics']['official_located_school_records'] == 93\nprint('未定位实体数：',db.execute('SELECT count(*) FROM entities WHERE lat IS NULL').fetchone()[0])\nprint('学校地图桥接 / 可落图学校档案：',db.execute('SELECT count(*) FROM school_campus_links').fetchone()[0],meta['metrics']['official_located_school_records'])\nprint('官方招生名称关联实体 / 有地图点位：',meta['metrics']['official_home_entities'],meta['metrics']['official_located_home_entities'])"),
    md("### 5. 年度隔离、引用完整性和原文件保护"),
    code("assert db.execute(\"SELECT count(*) FROM admissions WHERE year='2029'\").fetchone()[0] == 0\nassert db.execute(\"SELECT year FROM school_records WHERE official_id='3133000614001'\").fetchone()[0] == '2024'\nassert db.execute('PRAGMA foreign_key_check').fetchall() == []\nassert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'\nfor relative, expected in meta['source_fingerprints'].items():\n    actual = hashlib.sha256((app.parent / relative).read_bytes()).hexdigest()\n    assert actual == expected, relative\nprint('PASS：未来年份不回填；历史年份保留；外键完整；源文件哈希全部一致。')\nprint('候选日快照：', db.execute('SELECT count(DISTINCT snapshot_date) FROM history').fetchone()[0])\ndb.close()"),
    md("## Takeaways\n\n1. 来源数量、外键、年份和原文件哈希通过对账。可以把索引用于本地检索和证据导航。\n2. 候选 94 行唯一名称匹配、5 行歧义、80 行未匹配；官方目录已覆盖滨江、拱墅。18 条学校—地图校区桥接使 95 条学校档案中的 93 条可落图；桥接仍不是单套住房资格核验。\n3. 成交仍截至 2026-06-23；挂牌是未核实线索；空预警不是低风险。2029 年没有官方年度关系。\n4. 地图提供行政边界，不生成猜测学区。直接名单未命中时只列有证据等级的核验入口。"),
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
