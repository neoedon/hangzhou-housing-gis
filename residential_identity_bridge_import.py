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


def _verified_record_ids(builder, field, source_entity_id, bridge):
    from build_data import digest, dump

    table, key = ALLOWED_TABLES[field]
    actual = sorted({
        row[0] for row in builder.db.execute(
            f"SELECT {key} FROM {table} WHERE entity_id=?", (source_entity_id,)
        )
    })
    declared_ids = bridge.get(field)
    set_field = field.replace("_ids", "_id_set")
    declared_set = bridge.get(set_field)
    if declared_ids is not None and declared_set is not None:
        raise ValueError(f"Residential identity {field} cannot use two review locks")
    if declared_ids is not None:
        if not isinstance(declared_ids, list) or len(declared_ids) != len(set(declared_ids)):
            raise ValueError(f"Residential identity {field} must be a unique reviewed list")
        matches = set(actual) == set(declared_ids)
    elif isinstance(declared_set, dict):
        matches = (
            declared_set.get("count") == len(actual)
            and declared_set.get("sha256") == digest(dump(actual))
        )
    else:
        raise ValueError(f"Residential identity {field} needs an explicit review lock")
    if not matches:
        raise ValueError(
            f"Residential identity reviewed {field} changed for {source_entity_id}"
        )
    return actual


def _verify_identity_basis(source, target, project, bridge, norm):
    basis = bridge.get("identity_basis", "exact_project_name_or_alias")
    project_names = [project.get("name"), *(project.get("aliases") or [])]
    if basis == "exact_project_name_or_alias":
        if norm(source["name"]) not in {norm(value) for value in project_names if value}:
            raise ValueError("Residential identity source name is absent from target project evidence")
        return
    if basis == "brand_prefix_plus_project_name":
        brand = bridge.get("brand_prefix")
        base = bridge.get("base_name")
        if not brand or not base or norm(source["name"]) != norm(brand) + norm(base):
            raise ValueError("Residential identity brand and base name no longer compose the source name")
        allowed_bases = {norm(target["name"]), *(norm(value) for value in project_names if value)}
        if norm(base) not in allowed_bases:
            raise ValueError("Residential identity base name is absent from target project evidence")
        corroboration = [
            project.get("name"),
            *(project.get("aliases") or []),
            project.get("developer"),
            (project.get("field_values") or {}).get("项目介绍"),
        ]
        if not any(norm(brand) in norm(value) for value in corroboration if value):
            raise ValueError("Residential identity brand is absent from target project evidence")
        return
    if basis == "project_text_contains_source_name":
        field = bridge.get("project_evidence_field")
        if field != "field_values.项目介绍":
            raise ValueError("Residential identity project text field is not approved")
        evidence = bridge.get("evidence_contains")
        text = (project.get("field_values") or {}).get("项目介绍")
        if not evidence or norm(evidence) not in norm(text) or norm(source["name"]) not in norm(evidence):
            raise ValueError("Residential identity reviewed project text evidence changed")
        return
    raise ValueError(f"Unsupported residential identity basis: {basis}")


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
        _verify_identity_basis(source, target, project, bridge, norm)

        moved_ids = {}
        for field, (table, key) in ALLOWED_TABLES.items():
            record_ids = _verified_record_ids(builder, field, source_entity_id, bridge)
            moved_ids[field] = record_ids
            if record_ids:
                placeholders = ",".join("?" for _ in record_ids)
                result = builder.db.execute(
                    f"UPDATE {table} SET entity_id=? WHERE entity_id=? AND {key} IN ({placeholders})",
                    (target_entity_id, source_entity_id, *record_ids),
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
                    "identity_basis": bridge.get("identity_basis", "exact_project_name_or_alias"),
                    "price_record_count": len(moved_ids["price_ids"]),
                    "price_record_sha256": digest(dump(moved_ids["price_ids"])),
                    "market_snapshot_record_count": len(moved_ids["market_snapshot_ids"]),
                    "market_snapshot_record_sha256": digest(dump(moved_ids["market_snapshot_ids"])),
                    "scope": "prices_and_market_snapshots_only",
                }),
            ),
        )
        applied.append({
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "evidence_project_id": bridge.get("evidence_project_id"),
            "prices": len(moved_ids["price_ids"]),
            "market_snapshots": len(moved_ids["market_snapshot_ids"]),
        })

    return {"bridges": len(applied), **moved, "applied": applied}
