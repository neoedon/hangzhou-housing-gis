#!/usr/bin/env python3
"""Loopback-only, read-only API. Does not expose the surrounding workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

APP = Path(__file__).resolve().parent
DB = APP / "data/housing.sqlite"
SOURCE_LINK_CHECKS = APP / "data/source_link_checks.json"
POLICY_ARCHIVE_KEYS = frozenset(("service_area", "admission_plan", "city_policy_notice", "new_schools_boundary_result"))
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/model.js": "model.js", "/controls.js": "controls.js", "/icons.js": "icons.js", "/static-api.js": "static-api.js", "/app.css": "app.css"}
for asset in ("maplibre-gl.js", "maplibre-gl.css", "districts.geojson", "style.json", "hugeicons-LICENSE.txt"):
    STATIC["/assets/" + asset] = "assets/" + asset


def connection():
    db = sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def rows(db, sql, values=()):
    return [dict(r) for r in db.execute(sql, values)]


def unpack(record, keys=("payload",)):
    for key in keys:
        if key in record:
            record[key] = json.loads(record[key] or "null")
    return record


class ArticleTextParser(HTMLParser):
    """Extract the first article-content as text; never return executable markup."""
    void_tags = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"))
    ignored_tags = frozenset(("script", "style", "noscript", "iframe", "object", "template"))
    block_tags = frozenset(("p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "tr", "br", "hr"))

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.title = ""
        self.finished = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name", "").lower() == "articletitle":
            self.title = attributes.get("content", "")
        if not self.stack:
            if not self.finished and "article-content" in attributes.get("class", "").split():
                self.stack.append(tag)
            return
        if not any(t in self.ignored_tags for t in self.stack):
            if tag in self.block_tags:
                self.parts.append("\n")
            elif tag in ("td", "th"):
                self.parts.append("\t")
        if tag not in self.void_tags:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void_tags:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        if tag in self.block_tags and not any(t in self.ignored_tags for t in self.stack):
            self.parts.append("\n")
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        self.stack = self.stack[:index]
        if not self.stack:
            self.finished = True

    def handle_data(self, value):
        if self.stack and not any(t in self.ignored_tags for t in self.stack):
            self.parts.append(value)

    def text(self):
        return "\n".join(line for part in "".join(self.parts).splitlines()
                         if (line := re.sub(r"[\t \u00a0]+", " ", part).strip()))


def policy_archive_path(source):
    key = source.get("id", "").removeprefix("policy:")
    if source.get("id") != "policy:" + key or key not in POLICY_ARCHIVE_KEYS:
        return None
    reports = (APP.parent / "reports").resolve()
    snapshot = (APP.parent / source.get("path", "")).resolve()
    if not snapshot.is_relative_to(reports) or snapshot.suffix != ".json":
        return None
    archive = (snapshot.parent / "fetch_browser_headers" / (key + ".body")).resolve()
    if not archive.is_relative_to(reports) or not archive.is_file():
        return None
    return archive


def source_with_status(source):
    source = dict(source)
    if source.get("kind") == "official_local_policy_archive":
        source["link_status"] = {
            "status": "unverified", "checked_at": None,
            "detail": "本地政策归档副本；TLS 证书未核验或无核验记录，不代表当前原站链接与内容已复核。"}
        # The archive adapter creates a matching policy_texts row for this kind.
        source["archive_available"] = True
        return source
    if source.get("id") in {"policy:" + key for key in POLICY_ARCHIVE_KEYS}:
        try:
            checks = json.loads(SOURCE_LINK_CHECKS.read_text(encoding="utf-8")).get("sources", {})
        except (OSError, ValueError):
            checks = {}
        source["link_status"] = checks.get(source["id"], {
            "status": "unverified", "checked_at": None, "detail": "本次尚未核验原站链接可用性。"})
        source["archive_available"] = policy_archive_path(source) is not None
    return source


def source_archive(db, source_id):
    local = rows(db, "SELECT * FROM sources WHERE id=? AND kind='official_local_policy_archive'", (source_id,))
    if local:
        source = local[0]
        policies = rows(db, "SELECT label AS title,year,body FROM policy_texts WHERE source_id=? AND kind='local_policy_archive' LIMIT 2", (source_id,))
        if len(policies) != 1 or not isinstance(policies[0]["body"], str) or not policies[0]["body"].strip():
            return None
        policy = policies[0]
        text = policy["body"]
        return dict(title=policy["title"] or source["label"], year=policy["year"], text=text,
                    source_url=source["url"], archived_at=source.get("observed_at"),
                    archive_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    archive_hash_scope="归档展示文本", tls_verified=False,
                    limitations="这是已保存的本地政策归档展示文本，不代表当前原站内容、链接可用性或现行政策已复核。tls_verified=false 表示原始采集未核验 TLS 证书或没有核验记录，不一定发生过 TLS 握手失败；原始采集时间与原始文件哈希以正文说明为准。此处 SHA256 仅校验归档展示文本，不是原始文件哈希。")
    if source_id not in {"policy:" + key for key in POLICY_ARCHIVE_KEYS}:
        return None
    found = rows(db, "SELECT * FROM sources WHERE id=?", (source_id,))
    if not found:
        return None
    source = found[0]
    archive = policy_archive_path(source)
    if archive is None:
        return None
    content = archive.read_bytes()
    parser = ArticleTextParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    parser.close()
    text = parser.text()
    if not text:
        return None
    snapshot = json.loads((APP.parent / source["path"]).read_text(encoding="utf-8"))
    metadata = snapshot.get("official_sources", {}).get(source_id.removeprefix("policy:"), {})
    archived_at = source.get("observed_at")
    if metadata.get("date_header"):
        archived_at = parsedate_to_datetime(metadata["date_header"]).astimezone().isoformat(timespec="seconds")
    return dict(title=parser.title or source["label"], text=text, source_url=source["url"],
                archived_at=archived_at, archive_sha256=hashlib.sha256(content).hexdigest(),
                tls_verified=False,
                limitations="这是 2026-09-08 08:33 留存的本地正文副本；当次采集未核验 TLS 证书，不能作为当前原站内容或链接可用性确认。仅提取正文纯文本，表格转为文字，不包含图片、学区图、附件或原版排版。")


def distance(a, b):
    if a["lat"] is None or b["lat"] is None:
        return None
    rad = math.pi / 180
    dlat, dlng = (b["lat"] - a["lat"]) * rad, (b["lng"] - a["lng"]) * rad
    h = math.sin(dlat / 2)**2 + math.cos(a["lat"] * rad) * math.cos(b["lat"] * rad) * math.sin(dlng / 2)**2
    return round(6371000 * 2 * math.asin(min(1, math.sqrt(h))))


def has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def bootstrap(db):
    meta = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM meta WHERE key!='source_fingerprints'")}
    entities = [unpack(r, ("aliases",)) for r in rows(db, "SELECT * FROM entities")]
    by_id = {e["id"]: e for e in entities}
    for r in rows(db, "SELECT * FROM candidates"):
        e = by_id[r["entity_id"]]
        e["candidate"] = json.loads(r["payload"])
        e["candidate_source"] = r["source_id"]
    for r in rows(db, "SELECT entity_id,count(*) n FROM post_places GROUP BY entity_id"):
        by_id[r["entity_id"]]["post_count"] = r["n"]
    for r in rows(db, "SELECT entity_id,kind,count(*) n FROM prices GROUP BY entity_id,kind"):
        by_id[r["entity_id"]].setdefault("price_counts", {})[r["kind"]] = r["n"]
    if has_table(db, 'projects'):
        for r in rows(db, 'SELECT entity_id,count(*) n FROM projects GROUP BY entity_id'):
            by_id[r['entity_id']]['project_count'] = r['n']
        for r in rows(db, 'SELECT entity_id,payload FROM projects'):
            p = json.loads(r['payload'])
            # Search aliases do not mutate catalogue identity or coordinates.
            by_id[r['entity_id']].setdefault('project_names', []).extend(
                name for name in [p.get('name'), *(p.get('aliases') or [])] if isinstance(name, str) and name)
    price_rows = rows(db, "SELECT entity_id,source_id,kind,observed_at,event_date,total_wan,unit_yuan_sqm,area_sqm,payload FROM prices")
    for r in price_rows:
        payload = json.loads(r.pop('payload'))
        r['price_as_of'] = payload.get('price_as_of')
        r['source_as_of'] = payload.get('source_as_of')
        r['possible_duplicate'] = bool(payload.get('possible_duplicate_group') or
                                       int(payload.get('baseline_possible_business_matches') or 0) > 0)
        by_id[r["entity_id"]].setdefault("price_filter", []).append({k: v for k, v in r.items() if k != "entity_id"})
    for r in rows(db, "SELECT entity_id,year,official_id FROM school_records"):
        by_id[r["entity_id"]].setdefault("official_years", []).append(r["year"])
        by_id[r["entity_id"]].setdefault("official_ids", []).append(r["official_id"])
    school_campus_links = [unpack(r) for r in rows(db, "SELECT * FROM school_campus_links")]
    for link in school_campus_links:
        campus = by_id[link["campus_id"]]
        campus.setdefault("official_years", []).append(link["year"])
        campus.setdefault("official_ids", []).append(link["official_id"])
        campus.setdefault("official_link_kinds", []).append(link["kind"])
    for entity in entities:
        for key in ("official_years", "official_ids", "official_link_kinds"):
            if key in entity:
                entity[key] = list(dict.fromkeys(entity[key]))
    admissions = rows(db, "SELECT id,school_id,home_id,year,admission_type,active FROM admissions")
    return dict(meta=meta, entities=entities, admissions=admissions, school_campus_links=school_campus_links,
                sources=[source_with_status(r) for r in rows(db, "SELECT * FROM sources WHERE kind!='candidate_history' ORDER BY kind,label")])


def entity_detail(db, eid):
    found = rows(db, "SELECT * FROM entities WHERE id=?", (eid,))
    if not found:
        return None
    entity = unpack(found[0], ("aliases",))
    linked_records = set()
    if entity["kind"] == "school":
        linked_records = {(r["entity_id"], r["official_id"], r["year"]) for r in rows(db, """
            SELECT DISTINCT r.entity_id,r.official_id,r.year
            FROM school_campus_links l JOIN school_records r
              ON r.entity_id=l.official_school_id AND r.official_id=l.official_id AND r.year=l.year
            WHERE l.campus_id=?
        """, (eid,))}
    linked_years = {(school_id, year) for school_id, _, year in linked_records}
    canonical_ids = list(dict.fromkeys([eid, *(r[0] for r in sorted(linked_records))]))
    placeholders = ",".join("?" for _ in canonical_ids)
    # A campus bridge establishes a school identity only for its recorded year.
    # The selected entity's own historical records remain available in full.
    admissions = [unpack(r) for r in rows(db, f"SELECT * FROM admissions WHERE school_id IN ({placeholders}) OR home_id=? ORDER BY year DESC,admission_type", (*canonical_ids, eid))
                  if r["school_id"] == eid or r["home_id"] == eid or (r["school_id"], r["year"]) in linked_years]
    posts = rows(db, "SELECT p.* FROM posts p JOIN post_places pp ON p.id=pp.post_id WHERE pp.entity_id=? ORDER BY (p.depth='detail_description') DESC,p.posted_date DESC", (eid,))
    prices = [unpack(r) for r in rows(db, "SELECT * FROM prices WHERE entity_id=? ORDER BY coalesce(event_date,observed_at) DESC", (eid,))]
    policy = [unpack(r, ("school_ids",)) for r in rows(db, "SELECT * FROM policy_texts")]
    co = [unpack(r, ("post_ids",)) for r in rows(db, "SELECT * FROM co_mentions WHERE a=? OR b=?", (eid, eid))]
    nearby = []
    if entity["lat"] is not None:
        opposite = "residential" if entity["kind"] == "school" else "school"
        for item in rows(db, "SELECT id,name,lat,lng,primary_school FROM entities WHERE kind=? AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?", (
            opposite, entity["lat"] - .019, entity["lat"] + .019, entity["lng"] - .023, entity["lng"] + .023)):
            if opposite == "school" and not item["primary_school"]:
                continue
            d = distance(entity, item)
            if d is not None and d <= 2000:
                nearby.append({"entity_id": item["id"], "distance": d})
    school_links = [unpack(r) for r in rows(db, f"SELECT * FROM school_campus_links WHERE campus_id=? OR official_school_id IN ({placeholders}) ORDER BY year DESC,kind", (eid, *canonical_ids))
                    if r["campus_id"] == eid or r["official_school_id"] == eid or
                    (r["official_school_id"], r["official_id"], r["year"]) in linked_records]
    return dict(entity=entity, candidates=[unpack(r) for r in rows(db, "SELECT * FROM candidates WHERE entity_id=?", (eid,))],
                school_records=[unpack(r) for r in rows(db, f"SELECT * FROM school_records WHERE entity_id IN ({placeholders})", canonical_ids)
                                if r["entity_id"] == eid or (r["entity_id"], r["official_id"], r["year"]) in linked_records],
                admissions=admissions, posts=posts, prices=prices, co_mentions=co,
                projects=[unpack(r) for r in rows(db, 'SELECT * FROM projects WHERE entity_id=? ORDER BY observed_at DESC', (eid,))] if has_table(db, 'projects') else [],
                market_snapshots=[unpack(r) for r in rows(db, 'SELECT * FROM market_snapshots WHERE entity_id=? ORDER BY observed_at DESC', (eid,))] if has_table(db, 'market_snapshots') else [],
                school_links=school_links,
                nearby=sorted(nearby, key=lambda r: r["distance"]),
                policies=[p for p in policy if eid in p["school_ids"] or any((sid, p["year"]) in linked_years for sid in p["school_ids"]) or
                          (entity["kind"] == "school" and entity["district"] == "滨江区" and p["kind"] == "timeline")],
                mappings=[unpack(r, ("options",)) for r in rows(db, f"SELECT * FROM mappings WHERE entity_id IN ({placeholders}) AND source_id NOT LIKE 'candidate-history:%'", canonical_ids)],
                history=[unpack(r) for r in rows(db, "SELECT * FROM history WHERE entity_id=? ORDER BY snapshot_date", (eid,))])


class Handler(BaseHTTPRequestHandler):
    server_version = "HousingGIS/1.0"

    def headers_safe(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://tiles.openfreemap.org; connect-src 'self' https://tiles.openfreemap.org; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

    def respond(self, value, status=200):
        content = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.headers_safe()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in allowed_hosts:
            return self.respond({"error": "Invalid local host"}, 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in allowed_hosts}:
            return self.respond({"error": "Cross-origin access disabled"}, 403)
        route = urlsplit(self.path)
        if route.path in STATIC:
            path = APP / "web" / STATIC[route.path]
            if not path.is_file():
                return self.respond({"error": "Static asset not built"}, 503)
            content = path.read_bytes()
            self.send_response(200)
            self.headers_safe()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".geojson":
                mime = "application/geo+json"
            self.send_header("Content-Type", mime + ("; charset=utf-8" if path.suffix in (".js", ".css", ".html", ".txt") else ""))
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            return self.wfile.write(content)
        query = parse_qs(route.query)
        if any(len(v) > 500 for values in query.values() for v in values):
            return self.respond({"error": "Query too long"}, 400)
        try:
            with closing(connection()) as db:
                revision = query.get("revision", [""])[0]
                current_revision = json.loads(db.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()[0])
                if revision and revision != current_revision:
                    return self.respond({"error":"Dataset rebuilt. Reload the GIS to use one consistent revision."},409)
                if route.path == "/api/health":
                    return self.respond(dict(status="ok", database="read-only", built_at=json.loads(db.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()[0])))
                if route.path == "/api/bootstrap":
                    return self.respond(bootstrap(db))
                if route.path == "/api/entity":
                    detail = entity_detail(db, query.get("id", [""])[0])
                    return self.respond(detail or {"error": "Unknown entity"}, 200 if detail else 404)
                if route.path == "/api/snapshot":
                    date = query.get("date", [""])[0]
                    data = [unpack(r) for r in rows(db, "SELECT * FROM history WHERE snapshot_date=?", (date,))]
                    return self.respond({"date": date, "records": data})
                if route.path == "/api/posts":
                    term = query.get("q", [""])[0].strip()
                    pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    clause = "title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\'"
                    found = rows(db, f"SELECT * FROM posts WHERE {clause} ORDER BY (depth='detail_description') DESC,posted_date DESC LIMIT 5000", (pattern, pattern))
                    for post in found:
                        post["place_ids"] = [r[0] for r in db.execute("SELECT entity_id FROM post_places WHERE post_id=?", (post["id"],))]
                    total = db.execute(f"SELECT count(*) FROM posts WHERE {clause}", (pattern, pattern)).fetchone()[0]
                    return self.respond({"total": total, "limit": 5000, "posts": found})
                if route.path == "/api/source":
                    found = rows(db, "SELECT * FROM sources WHERE id=?", (query.get("id", [""])[0],))
                    return self.respond(source_with_status(found[0]) if found else {"error": "Unknown source"}, 200 if found else 404)
                if route.path == "/api/source-archive":
                    archive = source_archive(db, query.get("id", [""])[0])
                    return self.respond(archive or {"error": "No local archive for this source"}, 200 if archive else 404)
                return self.respond({"error": "Not found"}, 404)
        except (sqlite3.Error, OSError, ValueError):
            return self.respond({"error": "Data index unavailable. Run build_data.py; previous sources remain untouched."}, 503)

    def do_POST(self):
        self.respond({"error": "Read-only API; user drafts stay in this browser"}, 405)

    do_PUT = do_POST
    do_DELETE = do_POST


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not DB.is_file():
        raise SystemExit("Run python3 housing-gis/build_data.py first")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"杭州住房 GIS → http://127.0.0.1:{args.port} (local, read-only source index)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
