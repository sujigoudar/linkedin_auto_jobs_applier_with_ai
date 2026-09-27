"""Tests of the DESIGN PACKAGE, not the connected trading application."""
import copy, hashlib, json, re, subprocess, sys
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
ROOT=Path(__file__).resolve().parents[1]
def load(path): return json.loads((ROOT/path).read_text())
SC=load("catalog/screens.json"); F=load("catalog/forms.json"); CASE=load("catalog/test_cases.json")
SCHEMAS={p.name:json.loads(p.read_text()) for p in (ROOT/"contracts").glob("*.json")}
REG=Registry().with_resources([(f"https://local.test/{name}",Resource.from_contents({**value,"$id":f"https://local.test/{name}"})) for name,value in SCHEMAS.items()])
def validator(name):
    return Draft202012Validator({**SCHEMAS[name],"$id":"https://local.test/"+name},registry=REG,format_checker=FormatChecker())
@pytest.mark.parametrize("s",SC,ids=lambda x:x["id"])
def test_empty_response_is_buildable_for_every_screen(s):
    payload={"screen_id":s["id"],"state":"empty","snapshot_id":"isolated-zero-snapshot","schema_version":"ui-v2",
      "generated_at":"2026-09-27T00:00:00Z","origin_label":"isolated test fixture","summary":s["empty_heading"],
      "rows":[],"metrics":[],"capabilities":[],"next_cursor":None,"total_rows":0,"panels":[]}
    validator(s["read_contract"]+".schema.json").validate(payload)
    bad={**payload,"screen_id":"UNRELATED"}
    assert list(validator(s["read_contract"]+".schema.json").iter_errors(bad))
@pytest.mark.parametrize("name",sorted(SCHEMAS))
def test_schemas_are_well_formed(name): Draft202012Validator.check_schema(SCHEMAS[name])
@pytest.mark.parametrize("f",F,ids=lambda x:x["id"])
def test_form_schema_matches_every_declared_field(f):
    schema=SCHEMAS[f["id"]+".schema.json"]
    assert set(schema["properties"])=={x["key"] for x in f["fields"]}
    assert set(schema["required"])=={x["key"] for x in f["fields"] if x["required"]}
    assert schema["additionalProperties"] is False
    assert f["save_effect"] and f["preview_effect"] and f["confirm_effect"]
def test_exact_original_cp_requirement_text():
    inherited=load("inheritance/requirements.json")
    wanted={r["id"]:r["requirement"] for r in inherited if int(r["id"][3:])>=91}
    got={r["requirement_id"]:r["requirement_text"] for r in load("catalog/cp_traceability.json")}
    assert wanted==got and len(got)==24
@pytest.mark.parametrize("kind",["screen_state","form","journey","cross_boundary"])
def test_cases_remain_unexecuted_and_have_remedies(kind):
    cases=[c for c in CASE if c["kind"]==kind]
    assert cases
    for c in cases:
        assert c["status"]=="NOT_RUN" and c["driver_ref"] is None
        assert all(c[k] for k in ["steps","expected","prohibited","prerequisites","remedy","cleanup"])
def test_full_state_denominator_and_projects():
    states=load("catalog/ui_states.json")
    cases=[c for c in CASE if c["kind"]=="screen_state"]
    assert len(cases)==len(SC)*len(states)==792
    assert len(load("catalog/browser_projects.json"))==9
    assert len({c["id"] for c in CASE})==len(CASE)
def test_every_action_and_panel_bound():
    actions=load("catalog/actions.json");panels=load("catalog/panels.json");apis=load("catalog/api_contracts.json")
    ids={a["id"] for a in apis}
    for s in SC:
        assert len([a for a in actions if a["screen"]==s["id"]])==len(s["actions"])
        assert len([a for a in panels if a["screen"]==s["id"]])==len(s["panels"])
    assert all(not a.get("api_id") or a["api_id"] in ids for a in actions)
def test_private_api_never_customer_permission():
    for a in load("catalog/api_contracts.json"):
        if a["service"]=="private_execution":
            assert "customer" not in a["permission"]
            assert not a["path"].startswith("/api/v1")
def test_subaction_support_cannot_reconcile():
    p=load("catalog/subaction_permissions.json")["F-INCIDENT"]
    assert "support_readonly" in p["acknowledge"]
    assert "support_readonly" not in p["reconcile"]
def test_metric_boolean_rejected_and_null_allowed():
    schema={"$ref":"https://local.test/ui-contracts.schema.json#/$defs/Metric"}
    v=Draft202012Validator(schema,registry=REG,format_checker=FormatChecker())
    payload={"id":"m","value":None,"unit":"USD","origin":"actual","quality":"unavailable","as_of":None,"definition_id":"m-v1"}
    v.validate(payload)
    for value in [True,False,0,1.2,float("nan")]: assert list(v.iter_errors({**payload,"value":value}))
    v.validate({**payload,"value":"0"})
def test_no_unsafe_atlas_innerhtml_or_external_resource():
    src=(ROOT/"design/atlas.js").read_text()
    assert ".innerHTML" not in src and "eval(" not in src and "fetch(" not in src
    html=(ROOT/"SCREEN_ATLAS.html").read_text()
    assert not re.search(r'<(?:script|link|img)[^>]+(?:src|href)=["\']https?://',html)
def test_atlas_payload_matches_final_catalog():
    html=(ROOT/"SCREEN_ATLAS.html").read_text()
    raw=re.search(r'<script type="application/json" id="atlas-data">(.*?)</script>',html,re.S).group(1)
    data=json.loads(raw)
    for name,file in [("screens","screens"),("forms","forms"),("actions","actions"),("api","api_contracts"),("panels","panels")]:
        assert data[name]==load("catalog/"+file+".json")
def test_validator_subprocess():
    p=subprocess.run([sys.executable,str(ROOT/"tools/check_pack.py")],capture_output=True,text=True,timeout=15)
    assert p.returncode==0,p.stdout+p.stderr
    assert json.loads(p.stdout)["application_tests_executed"]==0
def test_scope_expansion_exact_and_refuses_overwrite(tmp_path):
    dest=tmp_path/"scope.json";cmd=[sys.executable,str(ROOT/"tools/build_scope.py"),"--output",str(dest)]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=20);assert p.returncode==0,p.stderr
    scope=json.loads(dest.read_text());assert scope["count"]==len(CASE)*9==len(CASE)*9
    assert len({x["instance_id"] for x in scope["instances"]})==scope["count"]
    assert all(x["status"]=="NOT_RUN" for x in scope["instances"])
    assert subprocess.run(cmd,capture_output=True,timeout=20).returncode!=0
@pytest.mark.parametrize("theme",["dark","light"])
def test_text_colors_meet_contrast(theme):
    colors=load("design/tokens.json")["colors"][theme]
    def lum(hexvalue):
        rgb=[int(hexvalue[i:i+2],16)/255 for i in (1,3,5)]
        r,g,b=[x/12.92 if x<=0.04045 else ((x+0.055)/1.055)**2.4 for x in rgb]
        return .2126*r+.7152*g+.0722*b
    for foreground in ["text","muted","accent","positive","warning","negative"]:
        for background in ["bg","surface","raised"]:
            a,b=sorted([lum(colors[foreground]),lum(colors[background])],reverse=True)
            assert (a+.05)/(b+.05)>=4.5,(theme,foreground,background,(a+.05)/(b+.05))
