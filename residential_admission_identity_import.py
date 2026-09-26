"""Apply reviewed residential identities to official admission relations only.

Each bridge is constrained to one district, one OSM target, one reviewed
project record, one exact identity rule, and one hash-locked set of 2026
admission IDs.  Supported identity rules are a verbatim project name/alias, an
exact official-name-plus-project-suffix composition, or an exact
developer-corroborated brand-prefix-plus-official-name composition.  When the
target already has official relations, that pre-existing ID set is
independently hash-locked before the reviewed identity is attached.  No prices,
projects, posts, candidates, or history are moved by this importer.
"""
from __future__ import annotations

import json


CATALOGUE = "residential-admission-bridges-reviewed-2026-09-27.json"


def _entity(builder, entity_id):
    row = builder.db.execute(
        "SELECT id,name,kind,district FROM entities WHERE id=?", (entity_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Residential admission identity entity missing: {entity_id}")
    return dict(zip(("id", "name", "kind", "district"), row))


def _project(builder, project_id, target_entity_id):
    row = builder.db.execute(
        "SELECT entity_id,payload FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    if not row or row[0] != target_entity_id:
        raise ValueError(
            f"Residential admission project evidence changed: {project_id}"
        )
    return json.loads(row[1])


def _verify_identity_basis(bridge, source, target, project):
    from build_data import norm

    basis = bridge.get("identity_basis")
    source_name = norm(source["name"])
    target_name = norm(target["name"])
    project_names = {
        norm(value)
        for value in [project.get("name"), *(project.get("aliases") or [])]
        if value
    }

    if basis == "exact_official_name_in_project_name_or_alias":
        if source_name not in project_names:
            raise ValueError(
                "Official residential name is absent from target project name or aliases"
            )
        return {"basis": basis}

    if basis == "official_name_plus_project_suffix":
        suffix = norm(bridge.get("project_suffix"))
        expected = source_name + suffix
        if not suffix or target_name != expected or expected not in project_names:
            raise ValueError(
                "Official residential name and reviewed project suffix do not exactly compose the target"
            )
        return {"basis": basis, "project_suffix": bridge["project_suffix"]}

    if basis == "project_brand_prefix_plus_official_name":
        brand = norm(bridge.get("brand_prefix"))
        expected = brand + source_name
        developer = norm(project.get("developer"))
        if (
            not brand
            or target_name != expected
            or expected not in project_names
            or brand not in developer
        ):
            raise ValueError(
                "Reviewed developer brand prefix and official residential name do not exactly compose the target"
            )
        return {"basis": basis, "brand_prefix": bridge["brand_prefix"]}

    raise ValueError("Unsupported residential admission identity basis")


def _reviewed_admission_ids(builder, bridge, source_entity_id, year):
    from build_data import digest, dump, norm

    rows = builder.db.execute(
        """
        SELECT a.id,s.kind,a.payload
        FROM admissions a JOIN sources s ON s.id=a.source_id
        WHERE a.home_id=? AND a.year=?
        ORDER BY a.id
        """,
        (source_entity_id, year),
    ).fetchall()
    actual = [row[0] for row in rows]
    lock = bridge.get("admission_id_set")
    if not isinstance(lock, dict) or set(lock) != {"count", "sha256"}:
        raise ValueError("Residential admission identity needs one exact ID-set lock")
    if lock["count"] != len(actual) or lock["sha256"] != digest(dump(actual)):
        raise ValueError(
            f"Residential admission reviewed IDs changed for {source_entity_id}"
        )
    if not actual or any(row[1] != "official_admissions" for row in rows):
        raise ValueError("Residential admission bridge only accepts official relations")
    expected_name = norm(bridge["source_name"])
    for _, _, raw_payload in rows:
        payload = json.loads(raw_payload or "{}")
        names = {
            norm(payload.get("residential_name")),
            norm(payload.get("display_name")),
        } - {""}
        if expected_name not in names:
            raise ValueError("Official residential name changed inside admission evidence")
    return actual


def _locked_target_admission_ids(builder, bridge, target_entity_id, year):
    from build_data import digest, dump

    rows = builder.db.execute(
        """
        SELECT a.id,s.kind
        FROM admissions a JOIN sources s ON s.id=a.source_id
        WHERE a.home_id=? AND a.year=?
        ORDER BY a.id
        """,
        (target_entity_id, year),
    ).fetchall()
    actual = [row[0] for row in rows]
    lock = bridge.get("target_admission_id_set")
    if actual:
        if not isinstance(lock, dict) or set(lock) != {"count", "sha256"}:
            raise ValueError(
                "Residential admission target with existing relations needs one exact ID-set lock"
            )
        if lock["count"] != len(actual) or lock["sha256"] != digest(dump(actual)):
            raise ValueError(
                f"Residential admission target IDs changed for {target_entity_id}"
            )
        if any(row[1] != "official_admissions" for row in rows):
            raise ValueError(
                "Residential admission target lock only accepts official relations"
            )
    elif lock not in (None, {"count": 0, "sha256": digest(dump([]))}):
        raise ValueError("Residential admission target lock expected records that are absent")
    return actual


def integrate(builder, catalogue_path=None):
    from build_data import APP, digest, dump

    path = catalogue_path or APP / "data" / CATALOGUE
    if not path.is_file():
        raise FileNotFoundError(
            f"Required residential admission identity catalogue is missing: {path}"
        )
    catalogue = builder.read(path)
    bridges = catalogue.get("bridges")
    if (
        catalogue.get("schema_version") != 1
        or catalogue.get("reviewed") is not True
        or not bridges
    ):
        raise ValueError(
            "Residential admission identity catalogue is not reviewed or is incomplete"
        )

    source_id = "residential-admission-identity-bridges"
    builder.source(
        source_id,
        path,
        "滨江、拱墅官方小区名与楼盘实体核验表",
        "reviewed_admission_identity_bridge",
        catalogue.get("reviewed_at"),
        catalogue.get("source_as_of"),
        catalogue.get("scope_policy", ""),
    )

    seen_sources = set()
    applied = []
    moved = 0
    for bridge in bridges:
        source_entity_id = bridge.get("source_entity_id")
        target_entity_id = bridge.get("target_entity_id")
        year = str(bridge.get("year") or "")
        if not source_entity_id or source_entity_id in seen_sources:
            raise ValueError(
                f"Duplicate or missing residential admission source: {source_entity_id}"
            )
        seen_sources.add(source_entity_id)
        source = _entity(builder, source_entity_id)
        target = _entity(builder, target_entity_id)
        if not target_entity_id.startswith("osm:"):
            raise ValueError("Residential admission target must be a reviewed OSM entity")
        if source["kind"] != "residential" or target["kind"] != "residential":
            raise ValueError("Residential admission bridge cannot cross entity kinds")
        if source["district"] != target["district"] or target["district"] != bridge.get("district"):
            raise ValueError("Residential admission bridge cannot cross districts")
        if source["name"] != bridge.get("source_name") or target["name"] != bridge.get("target_name"):
            raise ValueError("Residential admission reviewed entity name changed")
        if year != "2026":
            raise ValueError("Residential admission bridge is limited to reviewed 2026 relations")
        project = _project(builder, bridge.get("evidence_project_id"), target_entity_id)
        identity_evidence = _verify_identity_basis(
            bridge, source, target, project
        )
        target_admission_ids = _locked_target_admission_ids(
            builder, bridge, target_entity_id, year
        )
        admission_ids = _reviewed_admission_ids(
            builder, bridge, source_entity_id, year
        )
        placeholders = ",".join("?" for _ in admission_ids)
        result = builder.db.execute(
            f"UPDATE admissions SET home_id=? WHERE home_id=? AND id IN ({placeholders})",
            (target_entity_id, source_entity_id, *admission_ids),
        )
        if result.rowcount != len(admission_ids):
            raise ValueError("Residential admission bridge did not move its complete ID set")
        moved += result.rowcount

        mapping_id = "residential-admission-identity:" + digest(
            dump([source_entity_id, target_entity_id, year, bridge["evidence_project_id"]])
        )[:24]
        builder.db.execute(
            "INSERT INTO mappings VALUES(?,?,?,?,?,?)",
            (
                mapping_id,
                source_id,
                source_entity_id,
                target_entity_id,
                "reviewed_residential_admission_identity_bridge",
                dump(
                    {
                        "source_name": source["name"],
                        "target_name": target["name"],
                        "year": year,
                        "evidence_project_id": bridge["evidence_project_id"],
                        "identity_basis": bridge["identity_basis"],
                        "identity_evidence": identity_evidence,
                        "admission_record_count": len(admission_ids),
                        "admission_record_sha256": digest(dump(admission_ids)),
                        "preexisting_target_admission_count": len(target_admission_ids),
                        "preexisting_target_admission_sha256": digest(
                            dump(target_admission_ids)
                        ),
                        "postmerge_target_admission_count": len(target_admission_ids)
                        + len(admission_ids),
                        "scope": "admissions_only",
                    }
                ),
            ),
        )
        applied.append(
            {
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
                "year": year,
                "evidence_project_id": bridge["evidence_project_id"],
                "admissions": len(admission_ids),
                "preexisting_target_admissions": len(target_admission_ids),
                "postmerge_target_admissions": len(target_admission_ids)
                + len(admission_ids),
            }
        )

    by_district = {}
    for item in applied:
        district = _entity(builder, item["target_entity_id"])["district"]
        by_district[district] = by_district.get(district, 0) + item["admissions"]
    return {
        "bridges": len(applied),
        "admissions": moved,
        "admissions_by_district": by_district,
        "applied": applied,
    }
