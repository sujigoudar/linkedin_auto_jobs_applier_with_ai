#!/usr/bin/env python3
"""Check complete named-scenario evidence; distributed catalog intentionally fails.
This checks traceability fields/files, not the truth of a test or real broker behavior.
"""
import argparse
import json
from pathlib import Path

def validate(data: dict, root: Path) -> list[str]:
    errors=[]
    scenarios=data.get('scenarios')
    if not isinstance(scenarios,list) or not scenarios: return ['Missing nonempty scenarios list']
    seen=set()
    for case in scenarios:
        cid=case.get('id','MISSING_ID')
        if cid in seen: errors.append(f'{cid}: duplicate ID')
        seen.add(cid)
        if case.get('status')=='NOT_APPLICABLE_WITH_REASON':
            if not case.get('applicability_reason') or not case.get('review_evidence'):
                errors.append(f'{cid}: unreviewed applicability waiver')
            continue
        if case.get('status') not in {'TESTED_SIMULATOR','TESTED_BROKER_PAPER','TESTED_OWNER_LIVE'}:
            errors.append(f'{cid}: not tested ({case.get("status")})')
            continue
        for field in ['implementation_paths','test_paths','evidence_paths']:
            values=case.get(field)
            if not isinstance(values,list) or not values:
                errors.append(f'{cid}: missing {field}'); continue
            for value in values:
                path=(root/value).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    errors.append(f'{cid}: missing or out-of-root {field}: {value}')
        for field in ['code_hash','config_hash','environment','executed_at','result']:
            if not case.get(field): errors.append(f'{cid}: missing {field}')
        if case.get('result')!='PASS': errors.append(f'{cid}: result not PASS')
    return errors

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('catalog',type=Path)
    p.add_argument('--evidence-root',type=Path,default=Path.cwd())
    args=p.parse_args()
    data=json.loads(args.catalog.read_text())
    errors=validate(data,args.evidence_root)
    print(json.dumps({'status':'INCOMPLETE' if errors else 'TRACEABILITY_FIELDS_PRESENT_NOT_BROKER_CERTIFICATION',
                      'issues':len(errors),'first_20':errors[:20]},indent=2))
    raise SystemExit(1 if errors else 0)
