"""Independent arithmetic oracle, schema refusals, lazy AST and revision tests."""
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
import json,unittest,uuid,time
import scoring_model as m
import scoring_rust as r

def question(g=2,b=2,sg=1,sb=1,mode='multiple',prefix='q'):
    return {'id':prefix,'mode':mode,'options':[{'id':prefix+'-o'+str(i),'correct':i<g} for i in range(g+b)],'selected':[prefix+'-o'+str(i) for i in [*range(sg),*range(g,g+sb)]]}
def case(q=None,grading='partial',threshold='25'):
    return {'questions':[question() if q is None else q],'grading':grading,'passing_percent':threshold,'show_correction':True}
def reseal(model):
    value=deepcopy(model);value.pop('revision_id');return m.validate_model(value)
class ModelTests(unittest.TestCase):
    def setUp(self):self.model=m.default_model({'profile':'explicit test fixture, not source import'})
    def test_exhaustive_count_profiles_independent_fraction_oracle(self):
        checked=0
        for size in range(2,9):
            for g in range(1,size+1):
                b=size-g
                for sg in range(g+1):
                    for sb in range(b+1):
                        if sg+sb==0:continue
                        for grading in ('partial','all-or-nothing'):
                            q=question(g,b,sg,sb);actual=m.evaluate(self.model,case(q,grading))
                            exact=sg==g and sb==0
                            expected=Fraction(int(exact)) if grading=='all-or-nothing' or exact else max(Fraction(0),Fraction(sg,g)-(Fraction(sb,b) if b else 0))
                            self.assertEqual(Fraction(int(actual['score']['n']),int(actual['score']['d'])),expected)
                            self.assertEqual(actual['correct_count'],int(exact));checked+=1
        self.assertEqual(checked,826)
    def test_single_and_hidden_correction(self):
        c=case(question(1,2,0,1,'single'));c['show_correction']=False;v=m.evaluate(self.model,c);self.assertEqual(v['score_display'],'0.00');self.assertEqual(len(v['points']),1)
    def test_all_good_no_bad_denominator_lazy(self):
        v=m.evaluate(self.model,case(question(6,0,1,0),threshold='17'));self.assertEqual(v['score_display'],'0.17');self.assertEqual(v['percent_display'],'16.67');self.assertFalse(v['passed'])
    def test_exact_sum_not_sum_rounded_and_adjacent_decimal_thresholds(self):
        c=case();c['questions']=[question(7,0,1,0,prefix='q'+str(i)) for i in range(7)]
        for threshold,passed in [('14.285714285714285714285714285',True),('14.285714285714285714285714286',False)]:
            c['passing_percent']=threshold;v=m.evaluate(self.model,c);self.assertEqual(v['score_display'],'1.00');self.assertEqual(v['percent_display'],'14.29');self.assertIs(v['passed'],passed)
    def test_maximum_questions_and_rounding_half_up(self):
        c=case();c['questions']=[question(8,0,1,0,prefix='q'+str(i)) for i in range(50)];v=m.evaluate(self.model,c);self.assertEqual(v['score_display'],'6.25');self.assertEqual(v['points'][0]['display'],'0.13')
    def test_immutable_revision_and_intended_divergence(self):
        original=deepcopy(self.model);modified=m.revise_model(self.model,intent='Halve wrong-answer penalty');self.assertEqual(self.model,original);self.assertEqual(modified['parent_revision_id'],original['revision_id']);self.assertEqual(modified['provenance'],original['provenance']);self.assertNotEqual(modified['revision_id'],original['revision_id']);self.assertEqual(m.evaluate(original,case())['score_display'],'0.00');self.assertEqual(m.evaluate(modified,case())['score_display'],'0.25');self.assertNotEqual(r.emit_rust(original),r.emit_rust(modified))
    def test_ast_edit_changes_emission_and_calculation_not_only_weight(self):
        edit=deepcopy(self.model);edit['expressions']['point']=m.rat(1);edit=reseal(edit);self.assertEqual(m.evaluate(edit,case())['score_display'],'1.00');self.assertNotEqual(r.emit_rust(edit),r.emit_rust(self.model))
    def test_case_unknown_nan_and_duplicate_ids_refused(self):
        bad=case();bad['unknown']=1
        with self.assertRaises(m.Refused):m.validate_case(bad)
        bad=case();bad['passing_percent']=float('nan')
        with self.assertRaises(m.Refused):m.validate_case(bad)
        bad=case();bad['questions'][0]['options'][0]['id']='q'
        with self.assertRaises(m.Refused):m.validate_case(bad)
    def test_empty_unknown_duplicate_selection_refused(self):
        for selected in ([],['missing'],['q-o0','q-o0']):
            bad=case();bad['questions'][0]['selected']=selected
            with self.assertRaises(m.Refused):m.validate_case(bad)
    def test_boolean_integer_confusion_refused(self):
        bad=case();bad['questions'][0]['options'][0]['correct']=1
        with self.assertRaises(m.Refused):m.validate_case(bad)
        bad=deepcopy(self.model);bad['penalty_weight']['n']=True;bad.pop('revision_id')
        with self.assertRaises(m.Refused):m.validate_model(bad)
    def test_decimal_bounds_exponents_and_nonfinite_refused(self):
        for text in ('NaN','Infinity','1e2','-1','101','00.1','0.'+'1'*29,'99.'+'9'*28):
            with self.assertRaises(m.Refused):m.threshold_fraction(text)
        self.assertEqual(m.threshold_fraction('100.'+'0'*28),Fraction(100))
        self.assertEqual(m.threshold_fraction('0.'+'0'*27+'1'),Fraction(1,10**28))
    def test_revision_integrity_and_unknown_node_fields(self):
        bad=deepcopy(self.model);bad['intent']='altered'
        with self.assertRaises(m.Refused):m.validate_model(bad)
        bad=deepcopy(self.model);bad.pop('revision_id');bad['expressions']['point']['source']='arbitrary'
        with self.assertRaises(m.Refused):m.validate_model(bad)
    def test_branch_type_unknown_variables_and_cycles_refused(self):
        for node in (m.var('sum'),m.cond(m.boolean(True),m.rat(1),m.boolean(False)),{'op':'system','type':'rat','args':[]}):
            bad=deepcopy(self.model);bad.pop('revision_id');bad['expressions']['point']=node
            with self.assertRaises(m.Refused):m.validate_model(bad)
        bad=deepcopy(self.model);bad['provenance']['cycle']=bad
        with self.assertRaises(m.Refused):m.validate_model(bad)
    def test_lazy_division_but_unchosen_syntax_validated(self):
        bad=deepcopy(self.model);bad['expressions']['point']=m.cond(m.boolean(True),m.rat(1),m.op('div',m.rat(1),m.rat(0)));good=reseal(bad);self.assertEqual(m.evaluate(good,case())['score_display'],'1.00')
        bad['expressions']['point']['args'][0]=m.boolean(False)
        with self.assertRaises(m.Refused):m.evaluate(reseal(bad),case())
    def test_depth_and_result_range_refused(self):
        bad=deepcopy(self.model);node=m.rat(1)
        for _ in range(26):node=m.op('add',node,m.rat(0))
        bad['expressions']['point']=node
        with self.assertRaises(m.Refused):reseal(bad)
        bad['expressions']['point']=m.rat(2)
        with self.assertRaises(m.Refused):m.evaluate(reseal(bad),case())
    def test_checked_intermediate_overflow(self):
        bad=deepcopy(self.model);node=m.rat(1000000)
        for _ in range(7):node=m.op('mul',node,m.rat(1000000))
        bad['expressions']['point']=node
        with self.assertRaisesRegex(m.Refused,'overflow'):m.evaluate(reseal(bad),case())
    def test_result_bound_comparison_uses_same_checked_i128(self):
        bad=deepcopy(self.model);den=m.rat(100)
        for _ in range(6):den=m.op('mul',den,m.rat(1000000))
        bad['expressions']['score']=m.op('div',m.rat(1),den);bad['expressions']['percent']=m.rat(0)
        c=case();c['questions']=[question(prefix='q'+str(i)) for i in range(50)]
        with self.assertRaisesRegex(m.Refused,'overflow'):m.evaluate(reseal(bad),c)
    def test_closed_encoding_and_result_roundtrip(self):
        value=m.evaluate(self.model,case());self.assertEqual(r.parse_result(json.dumps(value)),value);self.assertEqual(r.encode_case(case()),'1 1 25 1 2 2 1 1 0 0');self.assertNotIn('\n',r.encode_case(case()))
    def test_result_parasites_duplicates_error_and_false_display_refused(self):
        valid=json.dumps(m.evaluate(self.model,case()))
        for text in (valid+'\n'+valid,'{"error":"REFUSED"}','{"passed":true,"passed":false}','NaN'):
            with self.assertRaises(m.Refused):r.parse_result(text)
        bad=m.evaluate(self.model,case());bad['score_display']='1.00'
        with self.assertRaises(m.Refused):r.parse_result(json.dumps(bad))
    def test_provenance_preserved_and_detached(self):
        source={'mapping':[{'file':'StudioStore.cs','start_line':167,'end_line':189}]};model=m.default_model(source);source['mapping'].clear();self.assertEqual(len(model['provenance']['mapping']),1);self.assertEqual(model,m.validate_model(model))

if __name__=='__main__':
    out=Path(__file__).resolve().parent/'evidence'/('model-tests-'+uuid.uuid4().hex);out.mkdir(parents=True)
    before=time.perf_counter();result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ModelTests));report={'status':'PASS' if result.wasSuccessful() else 'FAIL','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'seconds':time.perf_counter()-before,'scope':'Python arithmetic oracle/schema/lazy/identity/emission tests; Rust execution and real C# are separate.'};(out/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(out);raise SystemExit(not result.wasSuccessful())
