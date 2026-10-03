#!/usr/bin/env python3
"""Fail-closed runner for the two restricted planning suites.
The application adapter must invoke the real wired application in an isolated, non-live
fixture and return observations. No application adapter is provided or presumed here.
Race-vector execution and the 292 named scenarios require separate application suites.
"""
from __future__ import annotations
import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Callable
from generate_cases import all_cases

ROOT=Path(__file__).resolve().parent

def compare(expected: dict[str, Any], actual: Any) -> list[str]:
    if not isinstance(actual,dict): return ['Actual observations must be an object']
    errors=[]
    for key,value in expected.items():
        if key not in actual: errors.append(f'Missing observed field: {key}'); continue
        if type(actual[key]) is not type(value) or actual[key]!=value:
            errors.append(f'{key}: expected {value!r}, observed {actual[key]!r}')
    return errors

def load_adapter(spec: str) -> Callable[[dict[str, Any]],dict[str, Any]]:
    module,sep,name=spec.partition(':')
    if not sep or not module or not name: raise ValueError('Use module_name:callable_name')
    fn=getattr(importlib.import_module(module),name)
    if not callable(fn): raise ValueError('Configured adapter is not callable')
    return fn

def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adapter',help='Application test adapter, module_name:callable_name')
    p.add_argument('--isolated-non-live',action='store_true',help='Confirm fixture has no production credentials or network effects')
    p.add_argument('--suite',choices=['admission','linear_sizing','both'],default='both')
    p.add_argument('--results',type=Path,default=ROOT/'APPLICATION_RESULTS.json')
    args=p.parse_args()
    if not args.adapter:
        print('NOT_BOUND: No application adapter configured. Zero application tests executed.',file=sys.stderr)
        return 2
    if not args.isolated_non_live:
        print('ISOLATION_REQUIRED: Use an isolated non-live fixture; this runner does not sandbox arbitrary imported code.',file=sys.stderr)
        return 2
    try: adapter=load_adapter(args.adapter)
    except Exception as exc:
        print(f'NOT_BOUND: {type(exc).__name__}: {exc}',file=sys.stderr); return 2
    suites=['admission','linear_sizing'] if args.suite=='both' else [args.suite]
    results=[]
    for suite in suites:
        for case in all_cases(suite):
            # Do not pass the expected answer to the application adapter.
            request={'id':case['id'],'suite':case['suite'],'inputs':case['inputs']}
            try:
                response=adapter(request)
                if not isinstance(response,dict): raise ValueError('Adapter response must be an object')
                errors=compare(case['expected'],response.get('actual'))
                if not response.get('implementation_paths'): errors.append('Missing actual implementation path evidence')
                if not response.get('evidence'): errors.append('Missing application observation evidence')
                results.append({'id':case['id'],'status':'FAIL' if errors else 'PASS','errors':errors,
                                'evidence':response.get('evidence',[]),'implementation_paths':response.get('implementation_paths',[])})
            except Exception as exc:
                results.append({'id':case['id'],'status':'FAIL','errors':[f'{type(exc).__name__}: {exc}']})
    failed=sum(x['status']=='FAIL' for x in results)
    report={'scope':'RESTRICTED_PLANNING_SUITES_ONLY_NOT_WHOLE_APPLICATION_CERTIFICATION',
            'total':len(results),'passed':len(results)-failed,'failed':failed,
            'named_scenarios_status':'NOT_ESTABLISHED_BY_THIS_RUNNER',
            'race_suites_status':'NOT_ESTABLISHED_BY_THIS_RUNNER','results':results}
    args.results.parent.mkdir(parents=True,exist_ok=True)
    args.results.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
    return 1 if failed else 0

if __name__=='__main__': sys.exit(main())
