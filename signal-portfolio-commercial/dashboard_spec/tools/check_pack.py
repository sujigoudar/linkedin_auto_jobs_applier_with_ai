"""Check artifact consistency only. No application readiness is inferred."""
from pathlib import Path
import json, sys
root = Path(__file__).resolve().parents[1]
def load(n): return json.loads((root / "catalog" / n).read_text())
errors=[]
screens=load("screens.json");forms=load("forms.json");apis=load("api_contracts.json")
actions=load("actions.json");panels=load("panels.json");cases=load("test_cases.json")
si={s["id"] for s in screens};fi={f["id"] for f in forms};ai={a["id"] for a in apis}
for name, records in [("screen",screens),("form",forms),("api",apis),("action",actions),("panel",panels),("case",cases)]:
    if len({x["id"] for x in records}) != len(records): errors.append("Duplicate "+name)
for s in screens:
    if not set(s["forms"])<=fi: errors.append("Missing form "+s["id"])
    if s["empty_next"] not in si: errors.append("Missing empty route "+s["id"])
    if not (root / "screens" / (s["id"]+".md")).exists(): errors.append("Missing screen spec "+s["id"])
    if not (root / "contracts" / (s["read_contract"]+".schema.json")).exists(): errors.append("Missing read schema "+s["id"])
    if len([p for p in panels if p["screen"]==s["id"]]) != len(s["panels"]): errors.append("Panel mismatch "+s["id"])
    if len([a for a in actions if a["screen"]==s["id"]]) != len(s["actions"]): errors.append("Action mismatch "+s["id"])
for a in actions:
    if a["screen"] not in si: errors.append("Unknown action screen "+a["id"])
    if a.get("api_id") and a["api_id"] not in ai: errors.append("Missing action API "+a["id"])
for c in cases:
    if not set(c["screens"])<=si or not set(c["forms"])<=fi: errors.append("Unbound case "+c["id"])
    if c["status"]!="NOT_RUN" or c["driver_ref"] is not None: errors.append("Design case incorrectly treated as execution "+c["id"])
    if not all(c[k] for k in ["steps","expected","prohibited","prerequisites","remedy","cleanup"]): errors.append("Incomplete case "+c["id"])
parent=json.loads((root/"inheritance/requirements.json").read_text())
expected={r["id"]:r["requirement"] for r in parent if 91<=int(r["id"][3:])<=114}
trace=load("cp_traceability.json")
if {r["requirement_id"]:r["requirement_text"] for r in trace} != expected: errors.append("Original GUI requirement drift")
if any(not t["screens"] or not set(t["screens"])<=si for t in trace):errors.append("Unmapped CP requirement")
if len({(a["service"],a["method"],a["path"]) for a in apis})!=len(apis): errors.append("Duplicate API route")
report={"artifact_consistency": "PASS" if not errors else "FAIL", "errors":errors,
 "screens":len(screens),"forms":len(forms),"fields":sum(len(f["fields"]) for f in forms),
 "actions":len(actions),"panels":len(panels),"api_bindings":len(apis),"case_specifications":len(cases),
 "application_tests_executed":0,"application_readiness":"NOT_ASSESSED"}
print(json.dumps(report, indent=2))
sys.exit(bool(errors))
