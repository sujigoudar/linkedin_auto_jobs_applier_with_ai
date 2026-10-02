#!/usr/bin/env python3
"""Exhaustively generate DECLARED FINITE fixtures. No network or application trading.
These vectors do not certify real adapters, financial policy, or infinite input spaces.
"""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent
ADMISSION_AXES = {
    'authorization': ['authorized', 'unknown', 'disabled'],
    'interpretation': ['entry', 'conditional_unmet', 'ambiguous', 'observation'],
    'routes': ['zero', 'one', 'two', 'duplicate_bindings_one_account'],
    'budget': ['enough', 'zero', 'unknown', 'oversubscribed'],
    'margin_regime': ['legacy_verified', 'new_intraday_verified', 'unknown'],
    'halt': ['clear', 'account_halt', 'portfolio_halt', 'owner_halt'],
    'uncertain_effect': [False, True],
}
SIZING_AXES = {
    'risk_budget_cents': [0, 1, 99, 100, 1500],
    'unit_risk_cents': [1, 3, 25, 100, 105],
    'cash_capacity_cents': [0, 1, 4999, 10000, 600000],
    'entry_price_cents': [1, 99, 100, 4999, 5000, 10000],
    'source_max_units': [0, 1, 2, 10, 14, 100],
}
RACES = {
    'cancel_fill': ['cancel_requested', 'partial_fill_observed', 'cancel_final_observed', 'duplicate_fill_observed'],
    'submit_recovery': ['dispatch_attempted', 'timeout_observed', 'broker_fill_observed', 'restart_reconcile'],
    'stop_provider_exit': ['stop_fill_observed', 'provider_exit_received', 'exit_ack_observed', 'position_reconciled'],
    'add_exit': ['add_trigger_received', 'add_fill_observed', 'provider_exit_received', 'stop_replace_observed'],
    'competing_reservation': ['candidate_A_ready', 'candidate_B_ready', 'reservation_A_attempt', 'reservation_B_attempt'],
}

def rows(axes: dict[str, list[Any]]) -> Iterator[dict[str, Any]]:
    keys=list(axes)
    for values in itertools.product(*(axes[k] for k in keys)):
        yield dict(zip(keys, values))

def admission_oracle(x: dict[str, Any]) -> dict[str, Any]:
    """Restricted planning-only gate: all unlisted requirements are valid fixture facts."""
    reasons=[]
    if x['authorization']!='authorized': reasons.append('SOURCE_AUTHORITY')
    if x['interpretation']!='entry': reasons.append('NOT_CURRENT_ACTIONABLE_ENTRY')
    if x['routes']=='zero': reasons.append('NO_ELIGIBLE_ROUTE')
    if x['budget']!='enough': reasons.append('BUDGET_NOT_ADMISSIBLE')
    if x['margin_regime']=='unknown': reasons.append('REGIME_UNKNOWN')
    if x['halt']!='clear': reasons.append('HALTED')
    if x['uncertain_effect']: reasons.append('UNCERTAIN_EFFECT')
    allow=not reasons
    return {'admit_new_entry':allow,
            'selected_physical_account_count':int(allow),
            'broker_call_count':0, # Planning fixtures must not send orders.
            'blocking_reasons':reasons}

def sizing_oracle(x: dict[str, Any]) -> dict[str, Any]:
    """Synthetic long linear whole-unit fixture with all unlisted constraints slack.
    unit_risk_cents already includes the specified per-unit adverse costs.
    source_max_units=0 is a hard zero, not absence of a source ceiling.
    """
    for key,value in x.items():
        if isinstance(value,bool) or not isinstance(value,int) or value<0:
            raise ValueError(f'Invalid exact nonnegative integer: {key}')
    if x['unit_risk_cents']<=0 or x['entry_price_cents']<=0:
        raise ValueError('Denominators must be positive in this restricted fixture')
    q=min(x['risk_budget_cents']//x['unit_risk_cents'],
          x['cash_capacity_cents']//x['entry_price_cents'],x['source_max_units'])
    return {'quantity_units':q,'planned_risk_cents':q*x['unit_risk_cents'],
            'notional_cents':q*x['entry_price_cents'],'broker_call_count':0}

def all_cases(suite: str) -> Iterator[dict[str, Any]]:
    if suite=='admission':
        for n,x in enumerate(rows(ADMISSION_AXES),1):
            yield {'id':f'ADM-{n:05d}','suite':suite,'inputs':x,'expected':admission_oracle(x),'status':'NOT_RUN_AGAINST_APPLICATION'}
    elif suite=='linear_sizing':
        for n,x in enumerate(rows(SIZING_AXES),1):
            yield {'id':f'LIN-{n:05d}','suite':suite,'inputs':x,'expected':sizing_oracle(x),'status':'NOT_RUN_AGAINST_APPLICATION'}
    elif suite=='race_sequences':
        for family,events in RACES.items():
            for n,sequence in enumerate(itertools.permutations(events),1):
                yield {'id':f'RACE-{family}-{n:02d}','suite':suite,
                       'inputs':{'family':family,'observed_event_order':sequence},
                       'expected':{'required_invariants':['I01','I04','I05','I06','I07','I08','I10','I12','I13','I14','I16','I19'],
                                   'causal_impossibility':'Must reject/quarantine unsupported ordering or supply reviewed unreachability proof; never silently skip.',
                                   'concrete_oracle':'Bind to actual independent lifecycle/reference-ledger model; not provided by these observation vectors.'},
                       'status':'REQUIRES_APPLICATION_REFERENCE_MODEL_NOT_RUN'}
    else: raise ValueError(f'Unknown suite: {suite}')

def generate(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True,exist_ok=True)
    entries={}
    for suite in ['admission','linear_sizing','race_sequences']:
        path=output/f'{suite}.jsonl'; count=0
        with path.open('w',encoding='utf-8') as f:
            for case in all_cases(suite):
                f.write(json.dumps(case,separators=(',',':'))+'\n'); count+=1
        entries[suite]={'count':count,'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                        'application_status':'NOT_RUN'}
    manifest={'schema_version':'1.0','purpose':'GENERATED_FINITE_ACCEPTANCE_INPUTS_NOT_APPLICATION_TEST_RESULTS',
              'total':sum(e['count'] for e in entries.values()),'suites':entries,
              'domains':{'admission':ADMISSION_AXES,'linear_sizing':SIZING_AXES,'race_sequences':RACES},
              'no_financial_actions':True}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'generated')
    args=parser.parse_args()
    m=generate(args.output)
    print(json.dumps({'total_generated':m['total'],'suite_counts':{k:v['count'] for k,v in m['suites'].items()},
                      'application_test_status':'NOT_RUN'},indent=2))
