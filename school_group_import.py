"""Import reviewed education-group relations backed by official school profiles."""
from __future__ import annotations

import json
import re
import unicodedata


def _evidence_text(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def _school_record(builder, official_id):
    row = builder.db.execute(
        "SELECT entity_id,year,payload FROM school_records WHERE official_id=? "
        "ORDER BY CAST(year AS INTEGER) DESC LIMIT 1", (official_id,)).fetchone()
    if not row:
        raise ValueError(f"Education-group school missing: {official_id}")
    return row[0], str(row[1]), json.loads(row[2])


def _verify_profile(builder, official_id, needle, label):
    school_id, year, payload = _school_record(builder, official_id)
    if _evidence_text(needle) not in _evidence_text(payload.get("school_detail")):
        raise ValueError(f"Education-group evidence changed: {label}: {official_id}: {needle}")
    return school_id, year, payload


def integrate(builder):
    from build_data import APP, digest, dump

    path = APP / "data/school-groups-reviewed-2026-09-11.json"
    if not path.is_file():
        raise FileNotFoundError(f"Required education-group catalogue is missing: {path}")
    catalog = builder.read(path)
    if catalog.get("schema_version") != 1 or not catalog.get("groups") or not catalog.get("memberships"):
        raise ValueError("Education-group catalogue is incomplete")
    source_id = "school-group-catalog"
    builder.source(
        source_id, path, "滨江、拱墅教育集团关系核验表", "official_school_group",
        catalog.get("reviewed_at"), catalog.get("source_as_of"),
        catalog.get("assessment_policy", ""), "https://rxyj.hzedu.gov.cn/")

    groups = {}
    for group in catalog["groups"]:
        group_id = group.get("id", "")
        if not re.fullmatch(r"group:[a-z0-9-]+:[a-z0-9-]+", group_id):
            raise ValueError(f"Invalid education-group id: {group_id}")
        if group_id in groups or group.get("district") not in {"滨江区", "拱墅区"}:
            raise ValueError(f"Invalid education-group record: {group_id}")
        lead_id = _school_record(builder, group["lead_official_id"])[0]
        evidence = _verify_profile(
            builder, group["profile_evidence_official_id"], group["profile_evidence_contains"], group_id)
        entity_id = builder.entity(
            group_id, group["name"], "school_group", group["district"],
            aliases=group.get("aliases") or [], status="group_without_single_point")
        payload = dict(group)
        payload.update(profile_evidence_school_id=evidence[0], profile_evidence_year=evidence[1],
                       assessment_policy=catalog.get("assessment_policy"))
        builder.db.execute(
            "INSERT INTO school_groups VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (entity_id, source_id, lead_id, group.get("founded_year"), group.get("organization_model"),
             group.get("official_declared_scale"), group.get("reputation_label"), group.get("reputation_summary"),
             group.get("level_label"), group.get("level_summary"), dump(payload)))
        groups[group_id] = group

    active = historical = 0
    seen = set()
    for item in catalog["memberships"]:
        group_id, official_id = item.get("group_id"), item.get("official_id")
        if group_id not in groups:
            raise ValueError(f"Education-group membership target missing: {group_id}")
        evidence = _verify_profile(
            builder, item["evidence_official_id"], item["evidence_contains"], f"{group_id}:{official_id}")
        member_id, member_year, member_payload = _school_record(builder, official_id)
        if member_payload.get("district") and member_payload.get("district") != groups[group_id]["district"]:
            raise ValueError(f"Education-group cross-district membership: {group_id}:{official_id}")
        key = (group_id, official_id, bool(item.get("current")))
        if key in seen:
            raise ValueError(f"Duplicate education-group membership: {key}")
        seen.add(key)
        membership_id = "group-member:" + digest(dump(key))[:24]
        payload = dict(item)
        payload.update(member_record_year=member_year, evidence_school_id=evidence[0],
                       evidence_school_name=evidence[2].get("school_name"))
        is_active = int(bool(item.get("current")))
        active += is_active
        historical += 1 - is_active
        builder.db.execute(
            "INSERT INTO school_group_memberships VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (membership_id, group_id, member_id, source_id, official_id, evidence[1],
             item["relation_type"], is_active, item.get("since_year"),
             item.get("confidence", "official_profile_explicit"), item["evidence_contains"], dump(payload)))
    return {"groups": len(groups), "memberships": len(seen), "active": active, "historical": historical}
