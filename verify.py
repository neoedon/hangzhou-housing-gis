#!/usr/bin/env python3
"""Repeat the local audit and save an explicit receipt, including remaining gaps."""
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parent
QA = APP / "qa"
QA.mkdir(exist_ok=True)
checks = []
for label, command in (
    ("data_api", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]),
    ("frontend_model", ["node", "--test", "tests/model.test.mjs"]),
    ("frontend_syntax", ["node", "--check", "web/app.js"]),
):
    result = subprocess.run(command, cwd=APP, capture_output=True, text=True, timeout=60)
    output = result.stdout + result.stderr
    (QA / (label + ".log")).write_text(output)
    count = re.search(r"Ran (\d+) tests", output) or re.search(r"tests (\d+)", output)
    checks.append({"name":label,"passed":result.returncode == 0,"test_count":int(count.group(1)) if count else None,"log":label + ".log"})

receipt = json.loads((APP / "data/build_receipt.json").read_text())
database_hash = hashlib.sha256((APP / "data/housing.sqlite").read_bytes()).hexdigest()
profile_result = subprocess.run(
    [sys.executable, "scripts/audit_profile_completeness.py"],
    cwd=APP, capture_output=True, text=True, timeout=60,
)
(QA / "community_profile_completeness.log").write_text(profile_result.stdout + profile_result.stderr)
try:
    profile_audit = json.loads(profile_result.stdout)
except json.JSONDecodeError:
    profile_audit = {}
checks.append({
    "name": "community_profile_completeness",
    "passed": bool(
        profile_result.returncode == 0
        and profile_audit.get("acceptance", {}).get("passed") is True
        and not profile_audit.get("individual_failures")
    ),
    "profiles": profile_audit.get("overall", {}).get("profiles"),
    "missing_rate": profile_audit.get("overall", {}).get("missing_rate"),
    "binjiang_missing_rate": profile_audit.get("districts", {}).get("滨江区", {}).get("missing_rate"),
    "gongshu_missing_rate": profile_audit.get("districts", {}).get("拱墅区", {}).get("missing_rate"),
    "log": "community_profile_completeness.log",
})
notebook = json.loads((QA / "data_validation.ipynb").read_text())
cells = [c for c in notebook["cells"] if c["cell_type"] == "code"]
checks.append({"name":"executed_notebook","passed":all(c.get("execution_count") for c in cells) and not any(o.get("output_type") == "error" for c in cells for o in c.get("outputs", [])) and notebook["metadata"].get("gis_database_sha256") == database_hash,"code_cells":len(cells)})
checks.append({"name":"build_receipt_matches_database","passed":receipt["database_sha256"] == database_hash})
relationship_audit = json.loads((QA / "relationship-enrichment-audit.json").read_text())
checks.append({"name":"school_campus_relationship_coverage","passed":bool(
    receipt["metrics"].get("school_campus_links") == 18 and
    receipt["metrics"].get("official_located_school_records") == 93 and
    relationship_audit.get("relationship_policy", {}).get("forbidden_empty_copy_absent") is True and
    relationship_audit.get("typography", {}).get("passed") is True)})
browser = {}
for name in ("pointer-drag-audit", "browser-functional", "browser-final-interactions", "responsive-layout"):
    path = QA / (name + ".json")
    browser[name] = json.loads(path.read_text()) if path.exists() else {"missing":True}
drag = browser["pointer-drag-audit"]
checks.append({"name":"actual_pointer_drag_samples","passed":drag.get("samples",0)>=20 and drag.get("schoolHits")==drag.get("samples") and drag.get("baseHits")==drag.get("samples")})
layout = browser["responsive-layout"]
checks.append({"name":"responsive_selection_not_obscured","passed":all(layout.get(k,{}).get("schoolNotCovered") and layout[k].get("visibleMapPoint") and layout[k].get("width")==layout[k].get("bodyWidth") and layout[k].get("mapWidth")==layout[k].get("stageWidth") for k in ("mobile","tablet","resized"))})
interactions = browser["browser-final-interactions"]
by_check = {r["check"]:r for r in interactions} if isinstance(interactions,list) else {}
space = all(by_check.get(k,{}).get("allResultsInside") and by_check[k].get("count",0)>0 for k in ("rectangle","viewport"))
layer = by_check.get("only_selected_layers",{})
offline = by_check.get("local_layers_without_online_basemap",{})
search = by_check.get("whole_corpus_local_search",{})
expected_posts = receipt["metrics"]["posts"]
checks.append({"name":"browser_final_interactions","passed":bool(space and layer.get("drawnIds")==[layer.get("selected")] and offline.get("baseVisible")==0 and offline.get("gisLayers")==9 and search.get("indexed")==expected_posts and search.get("loadedBeforeSpatialFiltering")==expected_posts),"expected_posts":expected_posts})
draft = by_check.get("private_draft")
if draft:
    checks.append({"name":"private_draft_arithmetic_and_storage","passed":bool(draft.get("saved") and draft.get("arithmeticCorrect") and draft.get("onlyKnownTestData"))})
deal_metrics = receipt["metrics"].get("deal_completeness", {})
report = {"checked_at":datetime.now().astimezone().isoformat(timespec="seconds"), "assessment":"Share with caveats" if all(c["passed"] for c in checks) else "Needs revision",
          "checks":checks, "metrics":receipt["metrics"], "database_sha256":database_hash,
          "implementation_sha256":{str(p.relative_to(APP)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [APP/'build_data.py',APP/'server.py',APP/'web/app.js',APP/'web/model.js',APP/'web/index.html',APP/'web/app.css']},
          "browser":browser, "profile_completeness":profile_audit, "known_gaps":["Full-page screenshot capture timed out; canvas export is not whole-page pixel QA", "Address/campus/phase matches require human source verification", "New Puhe Primary School and Jiangpan Primary School have no coordinates in the captured official source", f"Deal records extend to {receipt['metrics'].get('deal_as_of')} but fully priced samples extend to {deal_metrics.get('latest_fully_priced_date')}; neither is current-market proof", "No official 2029 admission guarantee; online basemap needs network"]}
(QA / "validation_receipt.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"assessment":report["assessment"],"checks":checks,"receipt":str(QA/'validation_receipt.json')},ensure_ascii=False,indent=2))
raise SystemExit(0 if all(c["passed"] for c in checks) else 1)
