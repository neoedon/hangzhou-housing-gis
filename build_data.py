#!/usr/bin/env python3
"""Read completed project outputs into an atomic, local-only GIS index.

Source files are never modified. A name match is explicitly NOT a verified
address match; unresolved records stay searchable without invented coordinates.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
RESEARCH = ROOT / "杭州主城区小红书楼盘研究_2025-12-01至2026-09-07"


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else str(value).encode()).hexdigest()


def norm(value, school=False):
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = re.sub(r"[\s·•]", "", value)
    if school:
        value = re.sub(r"^(浙江省)?杭州市?", "", value)
    return value


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").strip())
        return result if result >= 0 and result != float("inf") else None
    except (ValueError, TypeError):
        return None


def day(value):
    text = str(value or "")
    if re.match(r"^\d{8}$", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def safe_url(value):
    from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
    try:
        parts = urlsplit(str(value or ""))
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username:
            return ""
        # Keep only the public project identifier needed to locate Fang's
        # community history. Auth/tracking/arbitrary query fields remain absent.
        query = ""
        if parts.hostname == "m.fang.com" and parts.path == "/chengjiao/hz/":
            identifiers = parse_qs(parts.query).get("projcode", [])
            if len(identifiers) == 1 and re.fullmatch(r"[0-9]{1,20}", identifiers[0]):
                query = urlencode({"projcode": identifiers[0]})
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except ValueError:
        return ""


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE sources(id TEXT PRIMARY KEY,label TEXT,kind TEXT,path TEXT,observed_at TEXT,source_as_of TEXT,url TEXT,sha256 TEXT,notes TEXT);
CREATE TABLE entities(id TEXT PRIMARY KEY,kind TEXT,name TEXT,district TEXT,lat REAL,lng REAL,location_status TEXT,aliases TEXT,address TEXT,primary_school INTEGER DEFAULT 0,osm_url TEXT);
CREATE TABLE mappings(id TEXT PRIMARY KEY,source_id TEXT REFERENCES sources(id),source_record TEXT,entity_id TEXT REFERENCES entities(id),status TEXT,options TEXT);
CREATE TABLE candidates(id TEXT PRIMARY KEY,entity_id TEXT REFERENCES entities(id),source_id TEXT REFERENCES sources(id),payload TEXT);
CREATE TABLE school_records(id TEXT PRIMARY KEY,entity_id TEXT REFERENCES entities(id),source_id TEXT REFERENCES sources(id),year TEXT,official_id TEXT,payload TEXT);
CREATE TABLE admissions(id TEXT PRIMARY KEY,school_id TEXT REFERENCES entities(id),home_id TEXT REFERENCES entities(id),source_id TEXT REFERENCES sources(id),year TEXT,admission_type TEXT,active INTEGER,payload TEXT);
CREATE TABLE school_campus_links(id TEXT PRIMARY KEY,campus_id TEXT REFERENCES entities(id),official_school_id TEXT REFERENCES entities(id),official_id TEXT,source_id TEXT REFERENCES sources(id),year TEXT,kind TEXT,confidence TEXT,distance_m REAL,payload TEXT);
CREATE TABLE school_groups(entity_id TEXT PRIMARY KEY REFERENCES entities(id),source_id TEXT REFERENCES sources(id),lead_school_id TEXT REFERENCES entities(id),founded_year TEXT,organization_model TEXT,official_declared_scale TEXT,reputation_label TEXT,reputation_summary TEXT,level_label TEXT,level_summary TEXT,payload TEXT);
CREATE TABLE school_group_memberships(id TEXT PRIMARY KEY,group_id TEXT REFERENCES entities(id),school_id TEXT REFERENCES entities(id),source_id TEXT REFERENCES sources(id),official_id TEXT,source_year TEXT,relation_type TEXT,active INTEGER,since_year TEXT,confidence TEXT,evidence TEXT,payload TEXT);
CREATE TABLE posts(id TEXT PRIMARY KEY,title TEXT,description TEXT,depth TEXT,posted_date TEXT,observed_at TEXT,url TEXT,district TEXT,topics TEXT);
CREATE TABLE post_places(post_id TEXT REFERENCES posts(id),entity_id TEXT REFERENCES entities(id),PRIMARY KEY(post_id,entity_id));
CREATE TABLE co_mentions(a TEXT REFERENCES entities(id),b TEXT REFERENCES entities(id),post_ids TEXT,distance REAL,PRIMARY KEY(a,b));
CREATE TABLE prices(id TEXT PRIMARY KEY,entity_id TEXT REFERENCES entities(id),source_id TEXT REFERENCES sources(id),kind TEXT,observed_at TEXT,event_date TEXT,total_wan REAL,unit_yuan_sqm REAL,area_sqm REAL,payload TEXT);
CREATE TABLE projects(id TEXT PRIMARY KEY,entity_id TEXT REFERENCES entities(id),source_id TEXT REFERENCES sources(id),observed_at TEXT,payload TEXT);
CREATE TABLE market_snapshots(id TEXT PRIMARY KEY,entity_id TEXT REFERENCES entities(id),source_id TEXT REFERENCES sources(id),observed_at TEXT,payload TEXT);
CREATE TABLE policy_texts(id TEXT PRIMARY KEY,source_id TEXT REFERENCES sources(id),year TEXT,label TEXT,body TEXT,school_ids TEXT,kind TEXT);
CREATE TABLE history(snapshot_date TEXT,entity_id TEXT REFERENCES entities(id),source_id TEXT REFERENCES sources(id),payload TEXT,PRIMARY KEY(snapshot_date,entity_id));
CREATE INDEX idx_admissions_school ON admissions(school_id,year,admission_type);
CREATE INDEX idx_admissions_home ON admissions(home_id,year,admission_type);
CREATE INDEX idx_school_campus ON school_campus_links(campus_id,year);
CREATE INDEX idx_school_canonical ON school_campus_links(official_school_id,year);
CREATE INDEX idx_school_group_member ON school_group_memberships(school_id,active);
CREATE INDEX idx_school_group_group ON school_group_memberships(group_id,active);
CREATE INDEX idx_prices_entity ON prices(entity_id,event_date);
CREATE INDEX idx_post_places_entity ON post_places(entity_id);
CREATE INDEX idx_candidates_entity ON candidates(entity_id);
CREATE INDEX idx_school_entity ON school_records(entity_id);
"""


class Builder:
    def __init__(self, connection):
        self.db = connection
        self.hashes = {}
        self.entities = {}
        self.lookup = defaultdict(set)
        self.source_count = Counter()
        self.warnings = []

    def read(self, path, kind="json"):
        path = Path(path).resolve()
        path.relative_to(ROOT)
        raw = path.read_bytes()
        self.hashes[str(path.relative_to(ROOT))] = digest(raw)
        if kind == "csv":
            return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        return json.loads(raw)

    def source(self, sid, path, label, kind, observed=None, as_of=None, notes="", url=""):
        rel = str(Path(path).resolve().relative_to(ROOT))
        self.db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?,?,?)", (
            sid, label, kind, rel, observed, as_of, safe_url(url), self.hashes.get(rel, ""), notes))
        return sid

    def entity(self, eid, name, kind, district="", lat=None, lng=None,
               status="unlocated", aliases=None, address="", primary=False, url=""):
        district = district + "区" if district in {"滨江", "拱墅", "西湖", "上城", "萧山", "余杭", "临平", "钱塘"} else district
        if eid in self.entities:
            return eid
        # Accept explicit coordinates only from the WGS84 geographical catalogue.
        if lat is not None and not (29.5 <= lat <= 31 and 118.9 <= lng <= 121):
            raise ValueError(f"Coordinate outside catalogue coverage: {eid}")
        record = dict(id=eid, name=name, kind=kind, district=district, lat=lat, lng=lng,
                      location_status=status, aliases=aliases or [], address=address,
                      primary_school=bool(primary), osm_url=safe_url(url))
        self.entities[eid] = record
        self.db.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            eid, kind, name, district, lat, lng, status, dump(aliases or []), address,
            int(primary), safe_url(url)))
        for alias in [name, *(aliases or [])]:
            if norm(alias, kind == "school"):
                self.lookup[(district, kind, norm(alias, kind == "school"))].add(eid)
        return eid

    def resolve(self, sid, record_id, name, kind="residential", district="滨江区", aliases=None, address="", primary=False):
        district = district + "区" if district in {"滨江", "拱墅", "西湖", "上城", "萧山", "余杭", "临平", "钱塘"} else district
        mid = f"{sid}:{record_id}"
        candidates = set()
        for alias in [name, *(aliases or [])]:
            key = norm(alias, kind == "school")
            if not key:
                continue
            if district:
                candidates.update(self.lookup.get((district, kind, key), set()))
            else:
                for (d, k, n), ids in self.lookup.items():
                    if k == kind and n == key:
                        candidates.update(ids)
        # Prefer unique catalogue matches; do not choose a point from ambiguous names.
        spatial = {i for i in candidates if i.startswith("osm:")}
        chosen = spatial if spatial else candidates
        if len(chosen) == 1:
            eid = next(iter(chosen))
            status = "name_match_review_required" if self.entities[eid]["lat"] is not None else "same_name_unlocated"
        else:
            entity_key = f"{district}|{norm(name, kind == 'school')}"
            if sid == "admissions" and kind == "school":
                eid = f"official:school:{record_id}"
            elif sid == "admissions" and str(record_id).startswith("home:") and str(record_id)[5:].isdigit():
                eid = "official:residential:" + str(record_id)[5:]
            elif sid == "candidates":
                eid = "candidate:residential:" + digest(record_id)[:20]
            else:
                eid = f"local:{kind}:{digest(entity_key)[:20]}"
            status = "ambiguous" if len(chosen) > 1 else "unmatched"
            self.entity(eid, name, kind, district, status=status, address=address, primary=primary)
        self.db.execute("INSERT OR REPLACE INTO mappings VALUES(?,?,?,?,?,?)", (
            digest(mid), sid, str(record_id), eid, status, dump(sorted(chosen))))
        if primary:
            self.db.execute("UPDATE entities SET primary_school=1 WHERE id=?", (eid,))
            self.entities[eid]["primary_school"] = True
        return eid

    def build(self):
        geo_path = RESEARCH / "map/places_index.json"
        places = self.read(geo_path)
        if isinstance(places, dict):
            places = places.get("places", places.get("items", []))
        if len(places) < 5000:
            raise ValueError("Geographical catalogue incomplete")
        self.source("geo", geo_path, "OSM 地名与坐标名录", "geography", "2026-09-07", "2026-09-07",
                    "WGS84 对象中心；非门牌/入口核验。行政边界不是学区边界。", "https://www.openstreetmap.org/")
        for p in places:
            if p.get("coordinate_system") != "WGS84":
                raise ValueError("Unknown catalogue CRS")
            self.entity(p["key"], p["name"], p["kind"], p["district"], p["lat"], p["lng"],
                        "osm_object_center", p.get("aliases"),
                        "".join(str(p.get(k) or "") for k in ("address_street", "address_housenumber")),
                        p.get("primary_school_name_signal"), p.get("source_url", ""))

        candidate_paths = sorted(ROOT.glob("reports/automation-3-*/binjiang_candidate_pool_update_*.json"))
        valid_candidates = []
        for path in candidate_paths:
            try:
                rows = self.read(path)
                if isinstance(rows, list) and len(rows) >= 100 and all(r.get("name") for r in rows):
                    stamp = re.search(r"(20\d{2}-\d{2}-\d{2})", path.name).group(1)
                    valid_candidates.append((stamp, path, rows))
                else:
                    self.warnings.append(f"跳过未完整候选快照：{path.name}")
            except (ValueError, KeyError, AttributeError):
                self.warnings.append(f"无法验证候选快照：{path.name}")
        if not valid_candidates:
            raise ValueError("No completed candidate snapshot")
        candidate_date, candidate_path, candidates = valid_candidates[-1]
        self.source("candidates", candidate_path, "滨江候选池", "candidate", candidate_date, candidate_date,
                    "研究候选与估算口径；快照日期不代表每项价格重新采集。学校字段为研究线索。")
        candidate_ids = {}
        for index, row in enumerate(candidates):
            rid = str(row.get("fang_id") or digest(norm(row["name"]))[:16])
            eid = self.resolve("candidates", rid, row["name"], aliases=[row.get("matched_name", "")], address=row.get("address", ""))
            candidate_ids[norm(row["name"])] = eid
            self.db.execute("INSERT INTO candidates VALUES(?,?,?,?)", (f"candidate:{rid}:{index}", eid, "candidates", dump(row)))

        # Latest complete snapshot per day, not every repeated monitoring report.
        daily = {stamp: (path, rows) for stamp, path, rows in valid_candidates}
        for stamp, (path, rows) in sorted(daily.items()):
            sid = f"candidate-history:{stamp}"
            self.source(sid, path, f"候选观察快照 {stamp}", "candidate_history", stamp, stamp,
                        "仅回放候选观察字段，不是历史成交价，也不是全库的 as-of 回溯。")
            for row in rows:
                eid = candidate_ids.get(norm(row["name"]))
                if not eid:
                    eid = self.resolve(sid, row["name"], row["name"], aliases=[row.get("matched_name", "")])
                self.db.execute("INSERT OR REPLACE INTO history VALUES(?,?,?,?)", (stamp, eid, sid, dump(row)))

        pointer = self.read(ROOT / "reports/automation-4-latest.json")
        probe_ref = pointer.get("official_warning_structured_api_probe", {}).get("json_report")
        if not probe_ref:
            raise ValueError("Missing validated official-school pointer")
        probe_path = Path(probe_ref)
        if not probe_path.is_absolute():
            probe_path = ROOT / probe_path
        probe = self.read(probe_path)
        if not probe.get("valid") or probe.get("errors") or probe.get("detail_success_count") != probe.get("school_row_count"):
            raise ValueError("Official school snapshot incomplete; keep previous database")
        self.source("admissions", probe_path, "滨江官方年度招生小区明细", "official_admissions",
                    probe.get("checked_at_cst"), probe.get("data_cutoff_year_month"),
                    "官方名单关系；地理挂接另需核验。预警为空不代表低风险；无 2029 保证。",
                    "https://rxyj.hzedu.gov.cn/")
        official_school_names = {}
        for school in probe["schools"]:
            eid = self.resolve("admissions", school["xqbsm"], school["school_name"], "school", address=school.get("address", ""), primary=True)
            official_school_names[norm(school["school_name"], True)] = eid
            public_school = {k: school.get(k) for k in (
                "school_name", "show_school_name", "xqbsm", "year", "school_type", "school_nature", "school_tel",
                "address", "warning_by_year", "target_year_warning", "hukou_district_count", "new_hangzhou_resident_district_count")}
            self.db.execute("INSERT INTO school_records VALUES(?,?,?,?,?,?)", (
                f"{school['xqbsm']}:{school['year']}", eid, "admissions", str(school["year"]), school["xqbsm"], dump(public_school)))
            for field in ("hukou_district_entries", "new_hangzhou_resident_district_entries"):
                for i, entry in enumerate(school.get(field, [])):
                    name = entry.get("residential_name") or entry.get("display_name")
                    if not name:
                        raise ValueError("Official relation missing residential name")
                    hid = self.resolve("admissions", "home:" + str(entry.get("residential_code") or name), name)
                    rid = digest(f"{school['xqbsm']}:{field}:{i}:{entry.get('entry_sha256')}")
                    self.db.execute("INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?)", (
                        rid, eid, hid, "admissions", str(entry["year"]), entry["admission_type"], int(bool(entry.get("active"))), dump(entry)))

        snapshot_paths = sorted(probe_path.parent.glob("source_snapshot_*.json"))
        if snapshot_paths:
            policy_path = snapshot_paths[-1]
            policy = self.read(policy_path)
            official = policy.get("official_sources", {})
            for key in ("service_area", "admission_plan", "city_policy_notice", "new_schools_boundary_result"):
                source = official.get(key, {})
                self.source(f"policy:{key}", policy_path, {
                    "service_area": "2026 滨江公办小学服务区文本", "admission_plan": "2026 滨江招生流程",
                    "city_policy_notice": "2026 招生政策依据", "new_schools_boundary_result": "新学校服务区结果"}[key],
                    "official_policy", probe.get("checked_at_cst"), "2026", "只导入公开政策段落，不导入家庭身份与资产内容。", source.get("url", ""))
            for label, entry in policy.get("service_area_structured_context", {}).get("entries", {}).items():
                ids = sorted({official_school_names[norm(n, True)] for n in entry.get("schools", []) if norm(n, True) in official_school_names})
                self.db.execute("INSERT INTO policy_texts VALUES(?,?,?,?,?,?,?)", (
                    "service:" + digest(label)[:16], "policy:service_area", "2026", label, entry["service_area"], dump(ids), "service_area_text"))
            for key, entry in policy.get("admission_timeline_context", {}).get("entries", {}).items():
                self.db.execute("INSERT INTO policy_texts VALUES(?,?,?,?,?,?,?)", (
                    "timeline:" + digest(key)[:16], "policy:admission_plan", "2026", entry["label"], entry["text"], "[]", "timeline"))

        post_path = RESEARCH / "analysis_posts.json"
        posts = self.read(post_path)
        self.source("posts", post_path, "小红书研究索引与重点深读", "social_evidence", "2026-09-07", "2025-12-01 至 2026-09-07",
                    "3021 条去重索引中 514 条含正文/说明；其余不可视作正文深读。内容为观点，不是招生或成交证明。")
        for post in posts:
            pid = str(post["id"])
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", pid):
                raise ValueError("Invalid post identifier")
            self.db.execute("INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?)", (
                pid, post.get("title", ""), post.get("description") or "", post.get("content_depth", ""),
                post.get("posted_date_shanghai") or post.get("created_date_shanghai"), post.get("detail_snapshot_date") or "2026-09-07",
                "https://www.xiaohongshu.com/explore/" + pid, post.get("primary_district", ""), dump(post.get("topics", []))))
        mentions_path = RESEARCH / "map/post_place_mentions.json"
        mentions = self.read(mentions_path)
        if isinstance(mentions, dict):
            mentions = mentions.get("mentions", [])
        post_ids = {str(p["id"]) for p in posts}
        for row in mentions:
            if row["place_key"] in self.entities and row["post_id"] in post_ids:
                self.db.execute("INSERT OR IGNORE INTO post_places VALUES(?,?)", (row["post_id"], row["place_key"]))
        links = self.read(RESEARCH / "map/school_home_links.json")
        for a, rows in links.get("linked", {}).items():
            for row in rows:
                b = row["key"]
                if a in self.entities and b in self.entities:
                    left, right = sorted((a, b))
                    self.db.execute("INSERT OR IGNORE INTO co_mentions VALUES(?,?,?,?)", (left, right, dump(row.get("post_ids", [])), row.get("distance_metres")))

        deal_path = ROOT / "reports/automation-5-2026-06-24/latest_loushi_hangzhou_transactions_dedup_2026-06-03_to_2026-06-23.csv"
        deals = self.read(deal_path, "csv")
        deal_as_of = max(day(r["date"]) for r in deals)
        self.source("deals", deal_path, "楼市信息去重成交历史样本", "transaction", "2026-06-24", deal_as_of,
                    "截至 6 月 23 日的 1542 条历史样本；不是今天成交，不是登记机构逐笔核验结果。")
        for index, row in enumerate(deals):
            district = row.get("district", "")
            eid = self.resolve("deals", f"{district}:{row['community']}", row["community"], district=district)
            clean = {k: v for k, v in row.items() if k != "source_file"}
            self.db.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?)", (
                "deal:" + digest(dump(row)), eid, "deals", "deal", "2026-06-24", day(row["date"]),
                number(row.get("total_wan")), number(row.get("unit_yuan_sqm")), number(row.get("area_sqm")), dump(clean)))

        lead_paths = [ROOT / "reports/automation-5-2026-06-24/priority_listing_leads_2026-06-24-1420.csv",
                      ROOT / "data/automation5_current_listing_overlay.csv"]
        price_raw_counts = {}
        for index, path in enumerate(lead_paths):
            rows = self.read(path, "csv")
            sid = ("listing-base", "listing-overlay")[index]
            dates = [day(r.get("observed_at")) or "2026-06-24" for r in rows]
            self.source(sid, path, ("基础挂牌与参考价线索", "后续公开挂牌与参考价线索")[index], "price_lead",
                        max(dates), max(dates), "搜索摘要/公开线索，非在售核验；小区参考价与单套报价分开，不把线索行数当房源套数。")
            price_raw_counts[sid] = len(rows)
            for row, observed in zip(rows, dates):
                eid = self.resolve(sid, row["community"], row["community"], district="")
                kind = "listing" if row.get("signal_type") == "挂牌房源" else "reference"
                clean = dict(row, url=safe_url(row.get("url")))
                # Same source observation can be quoted by more than one report.
                fingerprint = digest(dump([eid, observed, clean.get("url"), kind, row.get("area_sqm"), row.get("total_wan"), row.get("reference_price_yuan_sqm")]))
                self.db.execute("INSERT OR IGNORE INTO prices VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    "lead:" + fingerprint, eid, sid, kind, observed, None,
                    number(row.get("total_wan")), number(row.get("unit_yuan_sqm")) or number(row.get("reference_price_yuan_sqm")),
                    number(row.get("area_sqm")), dump(clean)))

        from admission_enrichment import integrate
        enriched_admissions = integrate(self)
        from school_group_import import integrate as integrate_school_groups
        school_group_metrics = integrate_school_groups(self)
        from incremental_import import integrate as integrate_incremental
        increments = integrate_incremental(self)
        metrics = {table: self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "entities", "candidates", "school_records", "admissions", "school_campus_links", "school_groups", "school_group_memberships", "posts", "post_places", "co_mentions", "prices", "projects", "market_snapshots", "policy_texts", "history", "sources")}
        metrics['school_group_catalog'] = school_group_metrics
        metrics['incremental'] = increments
        metrics["post_details"] = self.db.execute("SELECT count(*) FROM posts WHERE depth='detail_description'").fetchone()[0]
        metrics["located_entities"] = self.db.execute("SELECT count(*) FROM entities WHERE lat IS NOT NULL").fetchone()[0]
        metrics["price_kinds"] = dict(self.db.execute("SELECT kind,count(*) FROM prices GROUP BY kind"))
        metrics["deal_completeness"] = {
            "fully_priced": self.db.execute("SELECT count(*) FROM prices WHERE kind='deal' AND total_wan>0 AND unit_yuan_sqm>0").fetchone()[0],
            "price_incomplete": self.db.execute("SELECT count(*) FROM prices WHERE kind='deal' AND (total_wan IS NULL OR unit_yuan_sqm IS NULL OR total_wan<=0 OR unit_yuan_sqm<=0)").fetchone()[0],
            "latest_fully_priced_date": self.db.execute("SELECT max(event_date) FROM prices WHERE kind='deal' AND total_wan>0 AND unit_yuan_sqm>0").fetchone()[0],
        }
        metrics["price_raw_counts"] = price_raw_counts
        metrics["admission_types"] = dict(self.db.execute("SELECT admission_type,count(*) FROM admissions GROUP BY admission_type"))
        metrics["candidate_mapping"] = dict(self.db.execute("SELECT status,count(*) FROM mappings WHERE source_id='candidates' GROUP BY status"))
        metrics["school_mapping"] = dict(self.db.execute("SELECT status,count(*) FROM mappings WHERE source_id='admissions' AND source_record NOT LIKE 'home:%' GROUP BY status"))
        metrics["years"] = [row[0] for row in self.db.execute("SELECT DISTINCT year FROM school_records ORDER BY year")]
        metrics["snapshot_dates"] = sorted(daily)
        metrics["candidate_snapshot"] = candidate_date
        metrics["deal_as_of"] = self.db.execute("SELECT max(event_date) FROM prices WHERE kind='deal'").fetchone()[0] or deal_as_of
        metrics["school_as_of"] = probe.get("data_cutoff_year_month")
        metrics["unlocated_entities"] = metrics["entities"] - metrics["located_entities"]
        metrics["official_located_home_entities"] = self.db.execute("SELECT count(DISTINCT a.home_id) FROM admissions a JOIN entities e ON e.id=a.home_id WHERE e.lat IS NOT NULL").fetchone()[0]
        metrics["official_home_entities"] = self.db.execute("SELECT count(DISTINCT home_id) FROM admissions").fetchone()[0]
        metrics["official_located_school_records"] = self.db.execute("""
            SELECT count(DISTINCT sr.id) FROM school_records sr JOIN entities e ON e.id=sr.entity_id
            WHERE e.lat IS NOT NULL OR EXISTS(
              SELECT 1 FROM school_campus_links l JOIN entities c ON c.id=l.campus_id
              WHERE l.official_school_id=sr.entity_id AND l.year=sr.year AND c.lat IS NOT NULL)
        """).fetchone()[0]
        if metrics["posts"] != 3021 + increments['social_new_posts'] or metrics["post_details"] != 514 + increments['social_new_bodies']:
            raise ValueError("Frozen social corpus count changed; requires review")
        if metrics["admissions"] != probe["hukou_district_entry_count"] + probe["new_hangzhou_resident_district_entry_count"] + enriched_admissions:
            raise ValueError("Official relation count mismatch")
        if self.db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Dangling entity/source references")
        for path, expected in self.hashes.items():
            if digest((ROOT / path).read_bytes()) != expected:
                raise ValueError(f"Source changed while building: {path}")
        built_at = datetime.now().astimezone().isoformat(timespec="seconds")
        meta = dict(schema_version=1, built_at=built_at, metrics=metrics, warnings=self.warnings,
                    source_fingerprints=self.hashes, identity_policy="district+kind+exact normalized name; no campus/phase removal; match requires review",
                    time_policy="candidate snapshots only; admission year and transaction dates remain independent")
        for key, value in meta.items():
            self.db.execute("INSERT INTO meta VALUES(?,?)", (key, dump(value)))
        return meta


def build():
    data_dir = APP / "data"
    data_dir.mkdir(exist_ok=True)
    web_assets = APP / "web/assets"
    web_assets.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="housing-gis-build-", dir=data_dir) as temp:
        tmp = Path(temp) / "housing.sqlite"
        db = sqlite3.connect(tmp)
        db.executescript(SCHEMA)
        builder = Builder(db)
        meta = builder.build()
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        db.close()
        # Vendor files already cached by the project. No network installation.
        for name in ("maplibre-gl.js", "maplibre-gl.css"):
            url = "https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/" + name
            cached = RESEARCH / "map/vendor" / digest(url)
            if not cached.is_file():
                # Some earlier collectors put URL-hash files in cache/.
                matches = list((RESEARCH / "map").rglob(digest(url)))
                if len(matches) != 1:
                    raise FileNotFoundError(f"Cached MapLibre asset missing: {name}")
                cached = matches[0]
            if name == "maplibre-gl.js" and digest(cached.read_bytes()) != "45a9b07a9189ce56054c620a947ccf41e291e58c95e9b61533b740aaa65ee5cb":
                raise ValueError("Cached MapLibre code hash changed; requires review")
            shutil.copyfile(cached, web_assets / name)
        for source, dest in (("osm_urban_districts.geojson", "districts.geojson"), ("basemap_style.json", "style.json")):
            shutil.copyfile(RESEARCH / "map" / source, web_assets / dest)
        # Only replace the last-good index after all validation/copy steps pass.
        tmp.replace(data_dir / "housing.sqlite")
        receipt = dict(meta, database_sha256=digest((data_dir / "housing.sqlite").read_bytes()), original_sources_unchanged=True)
        (data_dir / "build_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"database": str(data_dir / "housing.sqlite"), "built_at": meta["built_at"], "metrics": meta["metrics"], "warnings": meta["warnings"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
