#!/usr/bin/env python3
"""Build the public, read-only GitHub Pages payload from the validated SQLite index."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import server

APP = Path(__file__).resolve().parent
WEB = APP / "web"
OUTPUT = APP / "docs"
ENTITY_SHARDS = 128
PUBLIC_INCREMENT_KEYS = (
    "projects", "deals", "listings", "market_snapshots", "deal_price_enrichments",
    "official_local_policies", "official_local_policy_entity_mentions",
    "social_new_posts", "social_new_bodies",
)


def compact(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(compact(value), encoding="utf-8")


def entity_shard(entity_id):
    if not entity_id.isascii():
        raise ValueError(f"Static entity shard requires an ASCII id: {entity_id!r}")
    value = 0x811C9DC5
    for code in entity_id.encode("ascii"):
        value ^= code
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"{value % ENTITY_SHARDS:03d}"


def public_index(source):
    marker = '<meta name="gis-static-data" content="true">'
    local_status = '<span class="status-dot"></span>本地资料库 '
    public_status = '<span class="status-dot"></span>公开静态资料库 '
    security = (
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; '
        "script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https://tiles.openfreemap.org; "
        "connect-src 'self' https://tiles.openfreemap.org; font-src 'self' https://tiles.openfreemap.org; "
        "object-src 'none'; base-uri 'none'\">"
    )
    if source.count("<head>") != 1 or source.count(local_status) != 1:
        raise ValueError("Unexpected index template; refusing an ambiguous Pages transformation")
    return source.replace("<head>", "<head>" + marker + security, 1).replace(local_status, public_status, 1)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_pages():
    if not server.DB.is_file():
        raise SystemExit("Run build_data.py before exporting GitHub Pages")
    temporary = Path(tempfile.mkdtemp(prefix="pages-build-", dir=APP))
    try:
        shutil.copytree(WEB, temporary, dirs_exist_ok=True)
        index = public_index((temporary / "index.html").read_text(encoding="utf-8"))
        (temporary / "index.html").write_text(index, encoding="utf-8")
        (temporary / ".nojekyll").write_text("", encoding="utf-8")

        with closing(server.connection()) as db:
            bootstrap = server.bootstrap(db)
            # The public UI only needs accepted aggregate counts. Quarantined row-level
            # audit material stays in the local build receipt and is not a Pages payload.
            incremental = bootstrap["meta"]["metrics"].get("incremental", {})
            bootstrap["meta"]["metrics"]["incremental"] = {
                "source_manifest": "validated public snapshot",
                **{key: incremental.get(key, 0) for key in PUBLIC_INCREMENT_KEYS},
            }
            write_json(temporary / "data/bootstrap.json", bootstrap)

            shards = [dict() for _ in range(ENTITY_SHARDS)]
            for index, entity in enumerate(bootstrap["entities"], start=1):
                entity_id = entity["id"]
                shards[int(entity_shard(entity_id))][entity_id] = server.entity_detail(db, entity_id)
                if index % 1000 == 0:
                    print(f"exported {index}/{len(bootstrap['entities'])} entity details", flush=True)
            for index, shard in enumerate(shards):
                write_json(temporary / f"data/entities/{index:03d}.json", shard)

            places = defaultdict(list)
            for row in db.execute("SELECT post_id,entity_id FROM post_places ORDER BY post_id,entity_id"):
                places[row["post_id"]].append(row["entity_id"])
            posts = server.rows(db, "SELECT * FROM posts ORDER BY (depth='detail_description') DESC,posted_date DESC")
            for post in posts:
                post["place_ids"] = places[post["id"]]
            write_json(temporary / "data/posts.json", posts)

            snapshots = defaultdict(list)
            for row in server.rows(db, "SELECT * FROM history ORDER BY snapshot_date,entity_id"):
                snapshots[row["snapshot_date"]].append(server.unpack(row))
            write_json(temporary / "data/snapshots.json", {
                date: {"date": date, "records": records} for date, records in snapshots.items()
            })

            all_sources = [server.source_with_status(row) for row in server.rows(db, "SELECT * FROM sources ORDER BY id")]
            write_json(temporary / "data/sources.json", {source["id"]: source for source in all_sources})
            archives = {}
            for source in all_sources:
                archive = server.source_archive(db, source["id"])
                if archive:
                    archives[source["id"]] = archive
            write_json(temporary / "data/archives.json", archives)

        payloads = sorted(path for path in temporary.rglob("*") if path.is_file() and path.name != "manifest.json")
        manifest = {
            "schema": 1,
            "built_at": bootstrap["meta"]["built_at"],
            "entity_shards": ENTITY_SHARDS,
            "counts": {
                "entities": len(bootstrap["entities"]),
                "admissions": len(bootstrap["admissions"]),
                "posts": len(posts),
                "sources": len(all_sources),
                "archives": len(archives),
                "snapshots": len(snapshots),
            },
            "files": {
                path.relative_to(temporary).as_posix(): {"bytes": path.stat().st_size, "sha256": sha256(path)}
                for path in payloads
            },
        }
        write_json(temporary / "data/manifest.json", manifest)

        previous = APP / "docs.previous"
        if previous.exists():
            shutil.rmtree(previous)
        if OUTPUT.exists():
            os.replace(OUTPUT, previous)
        os.replace(temporary, OUTPUT)
        if previous.exists():
            shutil.rmtree(previous)
        size = sum(path.stat().st_size for path in OUTPUT.rglob("*") if path.is_file())
        print(compact({"ok": True, "output": str(OUTPUT), "bytes": size, **manifest["counts"]}))
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


if __name__ == "__main__":
    build_pages()
