"""Print only the selected screen's contracts to keep coding-agent context bounded."""
from pathlib import Path
import json, sys
root = Path(__file__).resolve().parents[1]
if len(sys.argv) != 2:
    raise SystemExit("Usage: python tools/inspect_screen.py TR-03")
def load(name):
    return json.loads((root / "catalog" / name).read_text())
screen = next((s for s in load("screens.json") if s["id"] == sys.argv[1]), None)
if screen is None:
    raise SystemExit("Unknown screen ID")
print(json.dumps({"screen": screen,
  "forms": [f for f in load("forms.json") if f["id"] in screen["forms"]],
  "actions": [a for a in load("actions.json") if a["screen"] == screen["id"]],
  "panels": [p for p in load("panels.json") if p["screen"] == screen["id"]],
  "metrics": [m for m in load("metrics.json") if m["screen"] == screen["id"]]}, indent=2))
