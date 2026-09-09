"""Import checkpointed public evidence without inventing campus/address matches."""
import json
import math


PI = math.pi
AXIS = 6378245.0
ECCENTRICITY = 0.00669342162296594323


def _transform_lat(x, y):
    value = -100 + 2 * x + 3 * y + .2 * y * y + .1 * x * y + .2 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * PI) + 20 * math.sin(2 * x * PI)) * 2 / 3
    value += (20 * math.sin(y * PI) + 40 * math.sin(y / 3 * PI)) * 2 / 3
    value += (160 * math.sin(y / 12 * PI) + 320 * math.sin(y * PI / 30)) * 2 / 3
    return value


def _transform_lng(x, y):
    value = 300 + x + 2 * y + .1 * x * x + .1 * x * y + .1 * math.sqrt(abs(x))
    value += (20 * math.sin(6 * x * PI) + 20 * math.sin(2 * x * PI)) * 2 / 3
    value += (20 * math.sin(x * PI) + 40 * math.sin(x / 3 * PI)) * 2 / 3
    value += (150 * math.sin(x / 12 * PI) + 300 * math.sin(x / 30 * PI)) * 2 / 3
    return value


def gcj02_to_wgs84(lng, lat):
    """Approximate inverse used only for aligning public portal pins to OSM."""
    lng, lat = float(lng), float(lat)
    delta_lat = _transform_lat(lng - 105, lat - 35)
    delta_lng = _transform_lng(lng - 105, lat - 35)
    radians = lat / 180 * PI
    magic = 1 - ECCENTRICITY * math.sin(radians) ** 2
    root = math.sqrt(magic)
    delta_lat = delta_lat * 180 / ((AXIS * (1 - ECCENTRICITY)) / (magic * root) * PI)
    delta_lng = delta_lng * 180 / (AXIS / root * math.cos(radians) * PI)
    return lng - delta_lng, lat - delta_lat


def distance_m(a, b):
    lat1, lng1, lat2, lng2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return round(6371000 * 2 * math.asin(min(1, math.sqrt(h))))


def portal_point(school):
    try:
        if school.get("portal_lat") in (None, "") or school.get("portal_lng") in (None, ""):
            return None
        return gcj02_to_wgs84(school["portal_lng"], school["portal_lat"])
    except (TypeError, ValueError):
        return None


def nearest_school(builder, district, point, maximum=180):
    candidates = []
    for entity in builder.entities.values():
        if entity["kind"] != "school" or entity["district"] != district or entity["lat"] is None:
            continue
        candidates.append((distance_m(point, (entity["lng"], entity["lat"])), entity["id"]))
    if not candidates:
        return None
    distance, eid = min(candidates)
    return (eid, distance) if distance <= maximum else None


def add_aliases(builder, eid, names):
    from build_data import dump, norm
    entity = builder.entities[eid]
    aliases = list(entity.get("aliases") or [])
    for name in names:
        if name and name != entity["name"] and name not in aliases:
            aliases.append(name)
        key = norm(name, True)
        if key:
            builder.lookup[(entity["district"], "school", key)].add(eid)
    entity["aliases"] = aliases
    builder.db.execute("UPDATE entities SET aliases=?,primary_school=1 WHERE id=?", (dump(aliases), eid))
    entity["primary_school"] = True


def link_campus(builder, official_id, official_eid, campus_eid, year, source_id, kind, confidence, distance, payload):
    from build_data import digest, dump
    builder.db.execute("INSERT OR REPLACE INTO school_campus_links VALUES(?,?,?,?,?,?,?,?,?,?)", (
        digest(f"{official_id}:{campus_eid}:{year}:{kind}"), campus_eid, official_eid, official_id,
        source_id, year, kind, confidence, distance, dump(payload)))


def integrate(builder):
    from build_data import APP, digest, dump
    path = APP / "data/admission-enrichment-2026-09-08/manifest.json"
    if not path.is_file():
        return 0
    manifest = builder.read(path)
    if not manifest.get("valid"):
        raise ValueError("Admission enrichment is not complete")
    added = 0
    for district in manifest["districts"]:
        sid = "education-directory:" + district["code"]
        builder.source(sid, path, district["district"] + "官方学校目录与招生范围", "official_admissions",
                       manifest["collected_at"], district["source_as_of"],
                       "实时读取公开目录；逐校保留源年度、招生类型、范围和电话。旧年度不改写；官方统计截止月不等于所有招生字段更新时间。",
                       "https://rxyj.hzedu.gov.cn/")
        for school in district["school_records"]:
            official_id, year = school["xqbsm"], str(school["year"])
            existing = builder.db.execute("SELECT entity_id FROM school_records WHERE official_id=? AND year=?", (official_id, year)).fetchone()
            aliases = [school.get(k) for k in ("show_school_name", "school_other_name") if school.get(k)]
            eid = existing[0] if existing else builder.resolve(sid, official_id, school["school_name"], "school",
                            district["district"], aliases=aliases, address=school.get("address", ""), primary=True)
            point = portal_point(school)
            entity = builder.entities[eid]
            if point and entity["lat"] is None:
                nearest = nearest_school(builder, district["district"], point)
                if nearest:
                    campus_id, distance = nearest
                    add_aliases(builder, campus_id, [school["school_name"], *aliases])
                    link_campus(builder, official_id, eid, campus_id, year, sid,
                                "portal_coordinate_match", "reviewed_high", distance,
                                {"portal_gcj02": [school["portal_lng"], school["portal_lat"]],
                                 "converted_wgs84": point, "distance_m": distance,
                                 "rule": "nearest same-district school within 180m after GCJ-02 to WGS84 conversion"})
                else:
                    builder.db.execute("UPDATE entities SET lat=?,lng=?,location_status=? WHERE id=?",
                                       (point[1], point[0], "official_portal_gcj02_to_wgs84", eid))
                    entity.update(lat=point[1], lng=point[0], location_status="official_portal_gcj02_to_wgs84")
            builder.db.execute("INSERT OR REPLACE INTO school_records VALUES(?,?,?,?,?,?)", (
                f"{official_id}:{year}", eid, sid, year, official_id, dump(school)))
            for field, label in (("school_scope", "户籍招生服务范围"), ("school_scope_newhzr", "新杭州人报名服务范围")):
                if school.get(field):
                    builder.db.execute("INSERT OR REPLACE INTO policy_texts VALUES(?,?,?,?,?,?,?)", (
                        f"directory:{official_id}:{year}:{field}", sid, year, school["school_name"] + " · " + label,
                        school[field], dump([eid]), "service_area_text"))
            # The 933 Binjiang records already have stable IDs used by bookmarks.
            # Keep that verified source; add fresh metadata and the missing district.
            if district["district"] == "滨江区":
                current = builder.db.execute("SELECT count(*) FROM admissions WHERE school_id=? AND year=?", (eid, year)).fetchone()[0]
                expected = sum(len(school[k]) for k in ("hukou_district_entries", "new_hangzhou_resident_district_entries"))
                if current != expected:
                    raise ValueError(f"Binjiang source changed and requires reconciliation: {official_id}: {current}/{expected}")
                continue
            for field in ("hukou_district_entries", "new_hangzhou_resident_district_entries"):
                for index, item in enumerate(school[field]):
                    name = item["residential_name"] or item["display_name"]
                    if not name or not item["year"]:
                        raise ValueError("Missing official residential name/year")
                    hid = builder.resolve(sid, "home:" + (item["residential_code"] or name), name, district=district["district"],
                                          aliases=[item["display_name"]])
                    rid = digest(f"{sid}:{official_id}:{field}:{index}:{item['entry_sha256']}")
                    builder.db.execute("INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?)", (
                        rid, eid, hid, sid, item["year"], item["admission_type"], int(item["active"]), dump(item)))
                    added += 1
    crosswalk = APP / "data/school-campus-crosswalk-2026-09-08.json"
    if crosswalk.is_file():
        records = builder.read(crosswalk)
        crosswalk_source = "school-campus-crosswalk"
        builder.source(crosswalk_source, crosswalk, "学校校区与现名交叉核验", "school_campus_crosswalk",
                       records["observed_at"], records["source_as_of"],
                       "仅传播已记录的校区/机构关联；招生明细仍保留在官方学校编号下。",
                       records["source_url"])
        for item in records["links"]:
            row = builder.db.execute("SELECT entity_id FROM school_records WHERE official_id=? AND year=?",
                                     (item["official_id"], item["year"])).fetchone()
            if not row or item["campus_id"] not in builder.entities:
                raise ValueError("School campus crosswalk target missing")
            add_aliases(builder, item["campus_id"], item.get("aliases", []))
            link_campus(builder, item["official_id"], row[0], item["campus_id"], item["year"],
                        crosswalk_source, item["kind"], item["confidence"], item.get("distance_m"), item)
    return added
