#!/usr/bin/env python3
"""Checkpoint public education-directory reads; never change monitoring tasks.

The POST below is the portal's read-only paginated search, not a record write.
No authentication, household identities, or non-public endpoint is used.
"""
import hashlib
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
BASE = "https://rxyj.hzedu.gov.cn/hzjyAppServer/api/"
OUT = APP / "data/admission-enrichment-2026-09-08"
DISTRICTS = {"滨江区": "330108", "拱墅区": "330105"}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def fetch(key, endpoint, params=None, payload=None):
    path = OUT / "responses" / (key + ".json")
    if path.exists():
        cached = json.loads(path.read_text())
        if cached.get("response", {}).get("code") == 2000:
            return cached["response"], cached
    url = BASE + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    # macOS curl uses the configured system trust store; do not disable TLS checks.
    args = ["curl", "--fail", "--silent", "--show-error", "--max-time", "25", url,
            "-H", "Referer: https://rxyj.hzedu.gov.cn/", "-H", "Accept: application/json",
            "-H", "Content-Type: application/json;charset=UTF-8"]
    if body is not None:
        args += ["--data-binary", "@-"]
    raw = subprocess.run(args, input=body, check=True, capture_output=True).stdout
    response = json.loads(raw)
    if response.get("code") != 2000 or not response.get("success"):
        raise RuntimeError(f"Public source unavailable: {key}: {response.get('message')}")
    receipt = {"url": url, "request": payload, "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "sha256": hashlib.sha256(raw).hexdigest(), "response": response}
    save(path, receipt)
    time.sleep(.12)
    return response, receipt


def entry(item, admission_type):
    fields = {"year": "year", "school_name": "schoolName", "xqbsm": "xqbsm", "street_name": "streetName",
              "community_name": "communityName", "residential_name": "xqmc", "display_name": "name",
              "residential_code": "xqbm", "building_number": "buildingNumberZh", "scope_note": "fwsm",
              "household_flag": "bak1", "is_show": "isShow"}
    row = {key: str(item.get(raw) or "").strip() for key, raw in fields.items()}
    row.update(admission_type=admission_type, active=item.get("active") is True,
               recruit_type=str(item.get("bak2") or admission_type),
               building_number=str(item.get("buildingNumberZh") or item.get("buildingNumber") or ""))
    row["entry_sha256"] = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return row


def collect():
    results = []
    for district, code in DISTRICTS.items():
        desc, desc_receipt = fetch(code + "-description", "AppSchoolInfo/getDesc", {"areaName": district})
        result = desc["result"]
        scope_notes = [r.get("settingValue", "").strip() for key in ("rxyjsm", "rxyjsmqx") for r in result.get(key, [])]
        cutoff = re.search(r"数据截至(20\d{2})年(\d+)月", " ".join(scope_notes))
        district_result = {"district": district, "code": code, "school_records": [], "lists": [],
                           "portal_notes": list(dict.fromkeys(scope_notes)), "contact_phone": result.get("phone", ""),
                           "source_as_of": f"{cutoff[1]}-{int(cutoff[2]):02d}" if cutoff else None}
        for nature in ("非民办", "民办"):
            start, total = 0, None
            while total is None or start < total:
                payload = {"expressions": {
                    "active": {"op": "eq", "value": "1"}, "schoolType": {"op": "in", "value": [2, 5, 6]},
                    "hideFlagJnxx": {"op": "eq", "value": "1"}, "hideFlagJncz": {"op": "eq", "value": ""},
                    "gmblx": {"op": "eq", "value": nature}, "szqdm": {"op": "lk", "value": code},
                    "hideFlag": {"column": "hideFlag", "op": "eq", "value": "true"}},
                    "start": start, "limit": 80,
                    "orderByExpressions": [{"column": "gmblxSort", "orderByType": "asc"}, {"column": "visitTimes", "orderByType": "desc"}]}
                listing, receipt = fetch(f"{code}-{nature}-list-{start}", "AppSchoolInfo/custom/paginate", payload=payload)
                records = listing["result"].get("records", [])
                total = listing["result"]["total"]
                district_result["lists"].append({"nature": nature, "start": start, "total": total, "returned": len(records), "url": receipt["url"]})
                if not records and start < total:
                    raise RuntimeError("Incomplete public source pagination")
                for record in records:
                    listed = record["appSchoolInfoEntity"]
                    sid, year = listed["xqbsm"], listed["year"]
                    detail, receipt = fetch(f"{year}-{sid}", "AppSchoolInfo/getSchoolInfo", {"year": year, "schoolName": sid})
                    data = detail["result"]
                    info = data["appSchoolInfoEntity"]
                    if info["xqbsm"] != sid or info["year"] != year or not str(info.get("szqdm", code)).startswith(code):
                        raise RuntimeError(f"School source identity mismatch: {sid}")
                    public = {key: info.get(raw, "") for key, raw in {
                        "xqbsm": "xqbsm", "year": "year", "school_name": "schoolName", "show_school_name": "showSchoolName",
                        "school_other_name": "schoolOtherName", "school_type": "schoolType", "school_nature": "gmblx",
                        "address": "address", "portal_lat": "lat", "portal_lng": "lng",
                        "school_tel": "schoolTel", "school_scope": "schoolScope",
                        "school_scope_newhzr": "schoolScopeNewhzr", "school_detail": "schoolDetail",
                        "updated_at": "updateTime", "direct_middle_school": "directMiddleSchoolName",
                        "enroll_file_name": "enrollFileName", "enroll_file_url": "enrollFileUrl",
                    }.items()}
                    public.update(district=district, url=receipt["url"], observed_at=receipt["observed_at"], response_sha256=receipt["sha256"],
                                  warning_by_year=data.get("earlyWarningStatus"),
                                  hukou_district_entries=[entry(r, "户籍生") for r in data.get("appSchoolDistrictInfoEntityList") or []],
                                  new_hangzhou_resident_district_entries=[entry(r, "新杭州人") for r in data.get("appSchoolDistrictInfoEntityListNewHZR") or []])
                    district_result["school_records"].append(public)
                    print(f"{district} {len(district_result['school_records'])}: {public['school_name']} {year} · "
                          f"{len(public['hukou_district_entries'])}+{len(public['new_hangzhou_resident_district_entries'])}", flush=True)
                start += len(records)
                if total == 0:
                    break
        unique = {(s["year"], s["xqbsm"]) for s in district_result["school_records"]}
        if len(unique) != len(district_result["school_records"]):
            raise RuntimeError("Duplicate school primary key")
        alias_list, _ = fetch(code + "-school-alias-list", "AppSchoolInfo/list/district/schoolAll",
                              payload={"gmblx": "非民办", "schoolType": ["2", "5", "6"], "qxdm": code})
        district_result["official_alias_directory"] = alias_list["result"]
        homes, _ = fetch(code + "-residential-directory", "AppSchoolInfo/list/district/xq", {"qxbm": code})
        district_result["residential_directory"] = homes["result"]
        save(OUT / (code + ".json"), district_result)
        results.append(district_result)
    manifest = {"schema_version": 1, "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "valid": True, "districts": results,
                "limits": "Public visible portal directory only; absence is not evidence of no catchment. Source year retained; no future-year backfill."}
    save(OUT / "manifest.json", manifest)
    print(json.dumps({"path": str(OUT / "manifest.json"), "schools": sum(len(d["school_records"]) for d in results)}, ensure_ascii=False))


if __name__ == "__main__":
    collect()
