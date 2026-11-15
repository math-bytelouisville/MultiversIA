"""Finite synthesis and strict parser tests; independent arithmetic oracles."""
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import random
import unittest
from unittest import mock
import uuid
import math_core as core

BASE=Path(__file__).resolve().parent


def spec(post, names=('a','b'), typ='int', pre='True', name='Unnamed property', ret=None):
    return {'schema_version':'mia-math-spec-1','name':name,'parameters':[{'name':n,'type':typ} for n in names],
            'return_type':ret or typ,'precondition':pre,'postcondition':post}


MAX='result >= a and result >= b and (result == a or result == b)'
MIN='result <= a and result <= b and (result == a or result == b)'


class ParserTests(unittest.TestCase):
    def test_comparisons_boolean_conditional_and_unicode(self):
        n=core.parse_expression('a if a ≥ b ∧ not flag else b',{'a':'int','b':'int','flag':'bool'})
        self.assertEqual(core.evaluate(n,{'a':8,'b':-3,'flag':False}),8)
        self.assertEqual(core.evaluate(n,{'a':8,'b':-3,'flag':True}),-3)
        n=core.parse_expression('0 <= a <= 10',{'a':'int'})
        self.assertTrue(core.evaluate(n,{'a':8}));self.assertFalse(core.evaluate(n,{'a':-1}))

    def test_int32_boundaries_and_strict_types(self):
        for value in (core.LOW,core.HIGH):
            n=core.parse_expression(str(value),{});self.assertEqual(core.evaluate(n,{}),value)
        for text in ('2147483648','-2147483649','True < False','a and True','a if 1 else 0','True if True else 0','1 == True'):
            with self.subTest(text=text), self.assertRaises(ValueError):core.parse_expression(text,{'a':'int'})

    def test_calls_access_arithmetic_and_python_constructs_rejected(self):
        forbidden=['__import__("os")','open("x")','a.real','a[0]','a + 1','a * 2','-a','+a','(a := 1)',
                   '[a for a in b]','lambda: a','{"x": a}','a is b','a in b','1.0','"x"','None','f"{a}"']
        for text in forbidden:
            with self.subTest(text=text),self.assertRaises(ValueError):core.parse_expression(text,{'a':'int','b':'int'})

    def test_depth_size_and_unknown_names(self):
        for text in ['not '*40+'True','True and '*600+'True','x', '('*300+'True'+')'*300]:
            with self.subTest(size=len(text)),self.assertRaises(ValueError):core.parse_expression(text,{})

    def test_ir_whitelist_exact_fields_and_cycles(self):
        for n in [{'kind':'constant','type':[],'value':1},{'kind':'constant','type':'int','value':True},
                  {'kind':'call','type':'int'},{'kind':'constant','type':'int','value':0,'extra':True}]:
            with self.subTest(node=n),self.assertRaises(ValueError):core.validate_node(n,{})
        cycle={'kind':'not','type':'bool'};cycle['operand']=cycle
        with self.assertRaises(ValueError):core.validate_node(cycle,{})

    def test_environment_exact_types_and_ranges(self):
        n=core.parse_expression('a',{'a':'int'})
        for env in ({'a':True},{'a':core.HIGH+1},{'a':1.0},{'a':'1'},{'b':1}):
            with self.subTest(env=env),self.assertRaises(ValueError):core.evaluate(n,env)

    def test_supplied_asts_never_authoritative(self):
        s=spec('result == a');s['pre_ast']={'kind':'call'};s['post_ast']={'kind':'constant','type':'bool','value':True}
        out=core.validate_spec(s)
        self.assertEqual(out['pre_ast'],{'kind':'constant','type':'bool','value':True})
        self.assertEqual(out['post_ast']['kind'],'binary')
        s['postcondition']='open("forbidden")'
        with self.assertRaises(ValueError):core.validate_spec(s)

    def test_invalid_spec_and_parameter_scope(self):
        bad=[]
        for edit in [{'schema_version':'unknown'},{'precondition':'result == a'},{'postcondition':'a == b'},
                     {'precondition':'a'},{'return_type':'float'},{'name':'x\nignored'}, {'parameters':[]},
                     {'parameters':[{'name':'result','type':'int'}]}, {'parameters':[{'name':'class','type':'int'}]},
                     {'parameters':[{'name':'a','type':'int'}]*2}, {'unknown':1}]:
            s=spec(MAX);s.update(edit);bad.append(s)
        for s in bad:
            with self.subTest(spec=s),self.assertRaises(ValueError):core.validate_spec(s)


class SynthesisTests(unittest.TestCase):
    def assert_candidate(self,s,limits=None):
        before=deepcopy(s);r=core.solve(s,limits)
        self.assertEqual(s,before);self.assertEqual(r['status'],'CANDIDATE',r['reason'])
        self.assertIsNotNone(r['candidate']);self.assertTrue(all(x['postcondition'] for x in r['evidence']['candidate_validation']))
        return r

    def test_max_min_synthesized_without_expression_or_name_lookup(self):
        cases=[(spec(MAX,name='minimum'),max),(spec(MIN,name='maximum'),min)]
        rng=random.Random(20261115)
        inputs=[(core.LOW,core.HIGH),(core.LOW,core.LOW),(core.HIGH,core.HIGH)]+[(rng.randint(core.LOW,core.HIGH),rng.randint(core.LOW,core.HIGH)) for _ in range(500)]
        for s,oracle in cases:
            r=self.assert_candidate(s);self.assertEqual(r['stats']['method'],'bounded_decision_tree')
            self.assertEqual(r['candidate']['kind'],'ite')
            for a,b in inputs:self.assertEqual(core.evaluate(r['candidate'],{'a':a,'b':b}),oracle(a,b))

    def test_three_parameter_maximum_and_minimum(self):
        for comparison,oracle in [('>=',max),('<=',min)]:
            post=' and '.join('result '+comparison+' '+n for n in 'abc')+' and (result == a or result == b or result == c)'
            r=self.assert_candidate(spec(post,names=tuple('abc')))
            self.assertEqual(r['stats']['solution_depth'],2)
            for values in itertools.product([-100,-2,0,2,100],repeat=3):
                self.assertEqual(core.evaluate(r['candidate'],dict(zip('abc',values))),oracle(values))

    def test_threshold_clamp_from_property(self):
        post='result >= -7 and result <= 13 and ((a < -7 and result == -7) or (a > 13 and result == 13) or (a >= -7 and a <= 13 and result == a))'
        r=self.assert_candidate(spec(post,names=('a',)))
        for a in range(-100,101):self.assertEqual(core.evaluate(r['candidate'],{'a':a}),max(-7,min(13,a)))
        self.assertIn(-8,r['evidence']['sampling']['integer_values']);self.assertIn(14,r['evidence']['sampling']['integer_values'])

    def test_boolean_relation_search_and_full_bool_inputs(self):
        post='(a == b and result == False) or (a != b and result == True)'
        r=self.assert_candidate(spec(post,typ='bool'))
        self.assertEqual(r['stats']['samples'],4)
        for a,b in itertools.product([False,True],repeat=2):self.assertEqual(core.evaluate(r['candidate'],{'a':a,'b':b}),a!=b)

    def test_constructive_equality_is_explicit(self):
        r=self.assert_candidate(spec('result == (a if a < b else b)'))
        self.assertEqual(r['stats']['method'],'constructive_equality')
        self.assertEqual(core.evaluate(r['candidate'],{'a':5,'b':-9}),-9)
        # Same property with equality reversed remains constructive.
        r=self.assert_candidate(spec('(a if a < b else b) == result'))
        self.assertEqual(r['stats']['method'],'constructive_equality')

    def test_precondition_enforced_and_rejected_cases_visible(self):
        r=self.assert_candidate(spec('result == a',pre='a >= 0 and a <= 100'))
        self.assertGreater(sum(not row['precondition'] for row in r['evidence']['samples']),0)
        self.assertLess(r['stats']['precondition_witnesses'],r['stats']['samples'])
        valid={x['sample_id'] for x in r['evidence']['samples'] if x['precondition']}
        self.assertEqual({x['sample_id'] for x in r['evidence']['candidate_validation']},valid)

    def test_false_and_contradictory_precondition_inconclusive(self):
        for pre in ('False','a > 10 and a < 0'):
            r=core.solve(spec(MAX,pre=pre));self.assertEqual(r['status'],'INCONCLUSIVE');self.assertIn('NO_WITNESS',r['reason']);self.assertIsNone(r['candidate'])

    def test_tautology_and_contradictory_postcondition_not_candidate(self):
        r=core.solve(spec('result == result'));self.assertEqual(r['reason'],'POSTCONDITION_NONDISCRIMINATING_ON_SAMPLE')
        r=core.solve(spec('result > a and result < a'));self.assertEqual(r['status'],'INCONCLUSIVE');self.assertIn('NO_OUTPUT',r['reason']);self.assertTrue(r['evidence']['no_allowed_sample_ids'])

    def test_range_edge_impossibility_not_promoted_to_universal_proof(self):
        r=core.solve(spec('result > a',names=('a',)))
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertIsNone(r['candidate']);self.assertIn('finite',r['evidence']['no_allowed_output_scope'])

    def test_candidate_budget_is_global_and_exact(self):
        r=core.solve(spec(MAX),{'max_candidates':1})
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertEqual(r['reason'],'MAX_CANDIDATES');self.assertEqual(r['stats']['candidates_considered'],1)

    def test_depth_bound_does_not_return_a_hidden_deeper_tree(self):
        r=core.solve(spec(MAX),{'max_depth':0});self.assertEqual(r['status'],'INCONCLUSIVE');self.assertEqual(r['reason'],'NO_CANDIDATE_IN_BOUNDED_GRAMMAR')

    def test_time_limit_exits_before_unbounded_work(self):
        with mock.patch.object(core.time,'monotonic',side_effect=[0,.02,.03]):
            r=core.solve(spec(MAX),{'max_seconds':.01})
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertEqual(r['reason'],'MAX_SECONDS')

    def test_samples_budget_and_strict_limits(self):
        r=core.solve(spec(MAX),{'max_samples':8});self.assertLessEqual(r['stats']['samples'],8)
        for limit in [{'max_candidates':True},{'max_depth':10},{'max_seconds':float('nan')},{'max_samples':0},{'unknown':1}]:
            self.assertEqual(core.solve(spec(MAX),limit)['status'],'REFUSED')

    def test_trace_has_counterexamples_and_final_checks(self):
        r=self.assert_candidate(spec(MAX));e=r['evidence']
        self.assertTrue(any(t['first_counterexample'] is not None for t in e['terminals']))
        self.assertTrue(any(t['outcome']=='CONDITIONAL' for t in e['search_trace']))
        self.assertEqual(len(e['candidate_validation']),r['stats']['precondition_witnesses'])

    def test_result_is_json_serializable_and_deterministic_except_time(self):
        a=core.solve(spec(MAX));b=core.solve(spec(MAX));a['stats'].pop('elapsed_seconds');b['stats'].pop('elapsed_seconds')
        self.assertEqual(a,b);json.dumps(a,allow_nan=False)


if __name__=='__main__':
    run=BASE/'evidence'/('core-tests-'+uuid.uuid4().hex);run.mkdir(parents=True,exist_ok=False)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__)))
    report={'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),
            'sources_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (BASE/'math_core.py',BASE/'test_math_core.py')},
            'scope':'Parser strict et synthèse finie ; sorties comparées à oracles indépendants sur cas limites et aléatoires ; aucune preuve universelle.'}
    (run/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(run)
    raise SystemExit(not result.wasSuccessful())
