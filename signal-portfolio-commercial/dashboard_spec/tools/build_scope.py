"""Materialize the complete declared browser scope. This does not execute tests."""
from pathlib import Path
import argparse, hashlib, json
root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--output", required=True)
args = p.parse_args()
case_path = root / "catalog/test_cases.json"
cases = json.loads(case_path.read_text())
projects = json.loads((root / "catalog/browser_projects.json").read_text())
instances = [{"instance_id": c["id"] + "::" + b["id"], "case_id": c["id"],
 "project_id": b["id"], "driver_ref": None, "status": "NOT_RUN"}
 for c in cases for b in projects]
if len({i["instance_id"] for i in instances}) != len(instances):
    raise SystemExit("Duplicate instance ID")
output = Path(args.output)
if output.exists():
    raise SystemExit("Refusing to overwrite an existing scope; select a new output path")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({"catalog_sha256": hashlib.sha256(case_path.read_bytes()).hexdigest(),
 "count": len(instances), "instances": instances}, indent=2) + "\n")
print(f"Created {len(instances)} NOT_RUN instances at {output}")
