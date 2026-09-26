"""Apply explicit, reviewed residential identity bridges.

The importer is intentionally narrow: it only reattaches the exact price or
market-snapshot record IDs listed in the reviewed catalogue.  It never moves
admission, social, candidate, or history edges, because those relationships may
refer to a phase or official-name boundary that needs a separate review.
"""
from __future__ import annotations

import json


CATALOGUE = "residential-identity-bridges-reviewed-2026-09-27.json"
ALLOWED_TABLES = {
    "price_ids": ("prices", "id"),
    "market_snapshot_ids": ("market_snapshots", "id"),
}


def _entity(builder, entity_id):
    row = builder.db.execute(
        "SELECT id,name,kind,district FROM entities WHERE id=?", (entity_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Residential identity entity missing: {entity_id}")
    return dict(zip(("id", "name", "kind", "district"), row))


def _project_evidence(builder, project_id, target_entity_id):
    row = builder.db.execute(
        "SELECT entity_id,payload FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    if not row or row[0] != target_entity_id:
        raise ValueError(f"Residential identity project evidence changed: {project_id}")
    return json.loads(row[1])


def _verify_exact_records(builder, field, source_entity_id, declared_ids):
    table, key = ALLOWED_TABLES[field]
    if not isinstance(declared_ids, list) or len(declared_ids) != len(set(declared_ids)):
        raise ValueError(f"Residential identity {field} must be a unique reviewed list")
    actual = {
        row[0] for row in builder.db.execute(
            f"SELECT {key} FROM {table} WHERE entity_id=?", (source_entity_id,)
        )
    }
    declared = set(declared_ids)
    if actual != declared:
        raise ValueError(
            f"Residential identity reviewed {field} changed for {source_entity_id}"
        )


def integrate(builder, catalogue_path=None):
    from build_data import APP, digest, dump, norm

    path = catalogue_path or APP / "data" / CATALOGUE
    if not path.is_file():
        raise FileNotFoundError(f"Required residential identity catalogue is missing: {path}")
    catalogue = builder.read(path)
    bridges = catalogue.get("bridges")
    if catalogue.get("schema_version") != 1 or catalogue.get("reviewed") is not True or not bridges:
        raise ValueError("Residential identity catalogue is not reviewed or is incomplete")

    source_id = "residential-identity-bridges"
    builder.source(
        source_id,
        path,
        "滨江、拱墅小区别名身份核验表",
        "reviewed_identity_bridge",
        catalogue.get("reviewed_at"),
        catalogue.get("source_as_of"),
        catalogue.get("scope_policy", ""),
    )

    seen_sources = set()
    moved = {"prices": 0, "market_snapshots": 0}
    applied = []
    for bridge in bridges:
        source_entity_id = bridge.get("source_entity_id")
        target_entity_id = bridge.get("target_entity_id")
        if not source_entity_id or source_entity_id in seen_sources:
            raise ValueError(f"Duplicate or missing residential identity source: {source_entity_id}")
        seen_sources.add(source_entity_id)
        source = _entity(builder, source_entity_id)
        target = _entity(builder, target_entity_id)
        if not target_entity_id.startswith("osm:"):
            raise ValueError("Residential identity target must be a reviewed OSM entity")
        if source["kind"] != "residential" or target["kind"] != "residential":
            raise ValueError("Residential identity bridge cannot cross entity kinds")
        if source["district"] != target["district"] or target["district"] != bridge.get("district"):
            raise ValueError("Residential identity bridge cannot cross districts")
        if source["name"] != bridge.get("source_name") or target["name"] != bridge.get("target_name"):
            raise ValueError("Residential identity reviewed entity name changed")

        project = _project_evidence(builder, bridge.get("evidence_project_id"), target_entity_id)
        project_names = [project.get("name"), *(project.get("aliases") or [])]
        if norm(source["name"]) not in {norm(value) for value in project_names if value}:
            raise ValueError("Residential identity source name is absent from target project evidence")

        for field, (table, key) in ALLOWED_TABLES.items():
            declared_ids = bridge.get(field, [])
            _verify_exact_records(builder, field, source_entity_id, declared_ids)
            if declared_ids:
                placeholders = ",".join("?" for _ in declared_ids)
                result = builder.db.execute(
                    f"UPDATE {table} SET entity_id=? WHERE entity_id=? AND {key} IN ({placeholders})",
                    (target_entity_id, source_entity_id, *declared_ids),
                )
                moved[table] += result.rowcount

        mapping_id = "residential-identity:" + digest(dump([
            source_entity_id, target_entity_id, bridge.get("evidence_project_id")
        ]))[:24]
        builder.db.execute(
            "INSERT INTO mappings VALUES(?,?,?,?,?,?)",
            (
                mapping_id,
                source_id,
                source_entity_id,
                target_entity_id,
                "reviewed_residential_identity_bridge",
                dump({
                    "source_name": source["name"],
                    "target_name": target["name"],
                    "evidence_project_id": bridge.get("evidence_project_id"),
                    "price_ids": bridge.get("price_ids", []),
                    "market_snapshot_ids": bridge.get("market_snapshot_ids", []),
                    "scope": "prices_and_market_snapshots_only",
                }),
            ),
        )
        applied.append({
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "evidence_project_id": bridge.get("evidence_project_id"),
            "prices": len(bridge.get("price_ids", [])),
            "market_snapshots": len(bridge.get("market_snapshot_ids", [])),
        })

    return {"bridges": len(applied), **moved, "applied": applied}
