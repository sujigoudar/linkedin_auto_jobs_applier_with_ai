"""Package-integrity/reference tests ONLY. These do not test signal-copier code."""
import json
import tempfile
import unittest
from decimal import Decimal, localcontext
from pathlib import Path
from generate_cases import all_cases, admission_oracle, sizing_oracle, generate
from run_contracts import compare
from validate_evidence import validate

ROOT=Path(__file__).resolve().parent
class PackageHelperTests(unittest.TestCase):
    def test_exact_suite_counts(self):
        self.assertEqual([sum(1 for _ in all_cases(x)) for x in ['admission','linear_sizing','race_sequences']],[4608,4500,120])
    def test_unique_generated_ids(self):
        ids=[x['id'] for s in ['admission','linear_sizing','race_sequences'] for x in all_cases(s)]
        self.assertEqual(len(ids),len(set(ids)))
    def test_single_destination_all_admitted_cases(self):
        for c in all_cases('admission'):
            self.assertIn(c['expected']['selected_physical_account_count'],[0,1])
            self.assertEqual(c['expected']['broker_call_count'],0)
    def test_duplicate_binding_does_not_add_account(self):
        x={'authorization':'authorized','interpretation':'entry','routes':'duplicate_bindings_one_account','budget':'enough','margin_regime':'new_intraday_verified','halt':'clear','uncertain_effect':False}
        self.assertTrue(admission_oracle(x)['admit_new_entry'])
        self.assertEqual(admission_oracle(x)['selected_physical_account_count'],1)
    def test_unknown_effect_blocks_entry(self):
        x={'authorization':'authorized','interpretation':'entry','routes':'two','budget':'enough','margin_regime':'legacy_verified','halt':'clear','uncertain_effect':True}
        self.assertFalse(admission_oracle(x)['admit_new_entry'])
    def test_linear_reference_example(self):
        x={'risk_budget_cents':1500,'unit_risk_cents':105,'cash_capacity_cents':600000,'entry_price_cents':5000,'source_max_units':100}
        self.assertEqual(sizing_oracle(x),{'quantity_units':14,'planned_risk_cents':1470,'notional_cents':70000,'broker_call_count':0})
    def test_every_size_obeys_all_three_restricted_caps(self):
        for case in all_cases('linear_sizing'):
            x=case['inputs']; y=case['expected']
            self.assertLessEqual(y['planned_risk_cents'],x['risk_budget_cents'])
            self.assertLessEqual(y['notional_cents'],x['cash_capacity_cents'])
            self.assertLessEqual(y['quantity_units'],x['source_max_units'])
    def test_risk_cap_reduction_cannot_increase_quantity(self):
        for case in all_cases('linear_sizing'):
            x=case['inputs']; lower={**x,'risk_budget_cents':max(0,x['risk_budget_cents']-1)}
            self.assertLessEqual(sizing_oracle(lower)['quantity_units'],case['expected']['quantity_units'])
    def test_bool_money_invalid(self):
        with self.assertRaises(ValueError): sizing_oracle({'risk_budget_cents':True,'unit_risk_cents':1,'cash_capacity_cents':1,'entry_price_cents':1,'source_max_units':1})
    def test_kelly_example_and_cap(self):
        with localcontext() as ctx:
            ctx.prec=40
            p=Decimal('.55'); b=Decimal('1.4'); full=p-(1-p)/b
            self.assertLess(abs(full-Decimal('0.2285714285714285714285714285714285714286')),Decimal('1e-38'))
            capped=min(Decimal('.25')*full,Decimal('.0025'))
            self.assertEqual(Decimal('6000')*capped,Decimal('15.0000'))
    def test_pyramid_distinct_risk_measures(self):
        old_q,new_q,old_e,new_e,stop,mark,gap=10,5,Decimal('50'),Decimal('52'),Decimal('50.50'),Decimal('52'),Decimal('48')
        original=old_q*max(Decimal(0),old_e-stop)+new_q*max(Decimal(0),new_e-stop)
        self.assertEqual(original,Decimal('7.50'))
        self.assertEqual((old_q+new_q)*(mark-stop),Decimal('22.50'))
        self.assertEqual((old_q+new_q)*(mark-gap),Decimal('60'))
    def test_missing_actual_fields_fail(self):
        self.assertTrue(compare({'quantity_units':14},{}))
        self.assertTrue(compare({'quantity_units':1},{'quantity_units':True}))
    def test_catalog_has_292_not_run_cases(self):
        cases=json.loads((ROOT/'SCENARIO_CATALOG.json').read_text())['scenarios']
        self.assertEqual(len(cases),292)
        self.assertEqual(len({c['id'] for c in cases}),292)
        self.assertTrue(all(c['status']=='NOT_RUN' for c in cases))
    def test_distributed_evidence_cannot_pass(self):
        data=json.loads((ROOT/'SCENARIO_CATALOG.json').read_text())
        self.assertEqual(len(validate(data,ROOT)),292)
    def test_manifest_files_and_count(self):
        with tempfile.TemporaryDirectory() as d:
            m=generate(Path(d))
            self.assertEqual(m['total'],9228)
            self.assertEqual(sum(v['count'] for v in m['suites'].values()),9228)
            self.assertTrue(all((Path(d)/v['file']).is_file() for v in m['suites'].values()))

if __name__=='__main__': unittest.main(verbosity=2)
