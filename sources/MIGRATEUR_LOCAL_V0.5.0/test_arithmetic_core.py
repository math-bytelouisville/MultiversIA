"""Pure arithmetic/profile/oracle/emission qualification; no compiler processes."""
import ast
from copy import deepcopy
import hashlib
import io
import itertools
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch
import uuid

import math_core as core
import module_model as model
import module_backends as backend
import code_backends as single

BASE = Path(__file__).resolve().parent
OUT = BASE / 'evidence' / ('arithmetic-core-' + uuid.uuid4().hex)
OUT.mkdir(parents=True)
LOW, HIGH = -2147483648, 2147483647
P = lambda name: {'kind':'param','type':'int','name':name}
C = lambda value: {'kind':'constant','type':'int','value':value}
BIN = lambda op,a,b: {'kind':'binary','type':'int','op':op,'left':a,'right':b}
NEG = lambda a: {'kind':'neg32','type':'int','operand':a}


def signed_bytes(value):
    raw = (value % (1 << 32)).to_bytes(4, 'little')
    return int.from_bytes(raw, 'little', signed=True)


def spec(post='result == add32(a,b)', parameters=None, ret='int'):
    return {'schema_version':'mia-math-spec-1','name':'Rule','parameters':parameters or [{'name':'a','type':'int'},{'name':'b','type':'int'}],
            'return_type':ret,'precondition':'True','postcondition':post}


def module(body=None, profile=model.WRAP_PROFILE, parameters=None, contract=None):
    text='Fixture pure arithmetic model; original source preserved.'
    return {'schema_version':'mia-module-1','semantic_profile':profile,'module_id':'arithmetic-test','name':'Arithmetic',
            'parent_revision_id':None,'intent':'Qualify explicit modulo operations.',
            'provenance':{'source_text':text,'source_sha256':hashlib.sha256(text.encode()).hexdigest(),'import_id':'fixture'},
            'units':[{'id':'u0','name':'Rule','qualified_name':'Arithmetic.Rule',
                      'parameters':parameters or [{'name':'a','type':'int'},{'name':'b','type':'int'}],
                      'return_type':'int','body':body or BIN('add32',P('a'),P('b')),'contract':contract}]}


class ArithmeticTests(unittest.TestCase):
    def test_parse_explicit_calls_and_round_trip_contract(self):
        node=core.parse_expression('mul32(add32(a,b),neg32(sub32(a,b)))',{'a':'int','b':'int'})
        self.assertEqual(core.validate_node(node,{'a':'int','b':'int'}),'int')
        normalized=model.validate_module(module(node))
        parsed=core.validate_spec(model.unit_spec(normalized,'u0'))
        self.assertEqual(parsed['post_ast']['right'],node)

    def test_known_overflow_results(self):
        cases=[('add32',HIGH,1,LOW),('sub32',LOW,1,HIGH),('mul32',46340,46340,2147395600),
               ('mul32',46341,46341,-2147479015),('mul32',LOW,-1,LOW),('add32',LOW,LOW,0)]
        for op,a,b,want in cases:
            node=BIN(op,P('a'),P('b'));env={'a':a,'b':b}
            self.assertEqual(core.evaluate(node,env),want)
            self.assertEqual(single._oracle(node,env),want)
            self.assertEqual(backend._Oracle({}).evaluate(node,env),want)
        self.assertEqual(core.evaluate(NEG(P('a')),{'a':LOW}),LOW)
        self.assertEqual(core.evaluate(NEG(P('a')),{'a':HIGH}),-HIGH)

    def test_random_binary_agreement_with_signed_bytes(self):
        rng=random.Random(15891)
        pairs=[(rng.randint(LOW,HIGH),rng.randint(LOW,HIGH)) for _ in range(300)]
        for op in ('add32','sub32','mul32'):
            node=BIN(op,P('a'),P('b'));oracle=backend._Oracle({})
            for a,b in pairs:
                exact=a+b if op=='add32' else a-b if op=='sub32' else a*b
                expected=signed_bytes(exact);env={'a':a,'b':b}
                self.assertEqual(core.evaluate(node,env),expected)
                self.assertEqual(single._oracle(node,env),expected)
                self.assertEqual(oracle.evaluate(node,env),expected)

    def test_nested_wrap_before_comparison_changes_branch(self):
        node=core.parse_expression('a if add32(a,1) < 0 else b',{'a':'int','b':'int'})
        self.assertEqual(core.evaluate(node,{'a':HIGH,'b':17}),HIGH)
        self.assertEqual(backend._Oracle({}).evaluate(node,{'a':HIGH,'b':17}),HIGH)

    def test_negation_bounds_random_and_intermediate(self):
        node=NEG(BIN('mul32',P('a'),P('b')))
        rng=random.Random(17);oracle=backend._Oracle({})
        for _ in range(100):
            a,b=rng.randint(LOW,HIGH),rng.randint(LOW,HIGH)
            expected=signed_bytes(-signed_bytes(a*b));env={'a':a,'b':b}
            self.assertEqual(core.evaluate(node,env),expected)
            self.assertEqual(oracle.evaluate(node,env),expected)

    def test_bool_operands_and_wrong_result_types_refused(self):
        for op in ('add32','sub32','mul32'):
            node=BIN(op,{'kind':'constant','type':'bool','value':True},C(1))
            for validator in (core.validate_node,single._validate_ast):
                with self.assertRaises(ValueError):validator(node,{})
            with self.assertRaises(ValueError):model.validate_module(module(node))
            node=BIN(op,C(1),C(2));node['type']='bool'
            with self.assertRaises(ValueError):core.validate_node(node,{})
        with self.assertRaises(ValueError):core.parse_expression('neg32(True)',{})

    def test_closed_call_grammar_refuses_implicit_and_dynamic(self):
        bad=['a+b','a-b','a*b','-a','a//b','a%b','a/b','abs(a)','a.wrapping_add(b)',
             'add32(a)','add32(a,b,0)','neg32(a,b)','add32(a=b,b=a)','add32(*(a,b))',
             '__import__("os")','(lambda x:x)(a)','add32(a,1.5)','add32(a,True)']
        for text in bad:
            with self.subTest(text=text),self.assertRaises(ValueError):core.parse_expression(text,{'a':'int','b':'int'})

    def test_constant_min_and_out_of_range(self):
        self.assertEqual(core.parse_expression('-2147483648',{}),C(LOW))
        self.assertEqual(core.evaluate(core.parse_expression('neg32(-2147483648)',{}),{}),LOW)
        for text in ['2147483648','-2147483649','add32(2147483648,0)','neg32(-2147483649)']:
            with self.assertRaises(ValueError):core.parse_expression(text,{})

    def test_unknown_fields_ops_and_cycles_refused(self):
        invalid=[dict(NEG(C(1)),op='neg32'),dict(BIN('add32',C(1),C(2)),extra=1),BIN('div32',C(1),C(2))]
        cycle=NEG(C(1));cycle['operand']=cycle;invalid.append(cycle)
        for node in invalid:
            with self.assertRaises(ValueError):core.validate_node(node,{})
            with self.assertRaises(ValueError):model.validate_module(module(node))

    def test_legacy_profile_accepts_old_body_and_rejects_new_body(self):
        old=model.validate_module(module(P('a'),model.LEGACY_PROFILE))
        self.assertEqual(model.evaluate_unit(old,'u0',[HIGH,LOW]),HIGH)
        for node in [BIN(op,P('a'),P('b')) for op in ('add32','sub32','mul32')]+[NEG(P('a'))]:
            with self.assertRaisesRegex(ValueError,'WRAP_PROFILE'):model.validate_module(module(node,model.LEGACY_PROFILE))

    def test_legacy_profile_rejects_arithmetic_pre_or_post(self):
        contracts=[{'precondition':'add32(a,b) >= 0','postcondition':'result == a'},
                   {'precondition':'True','postcondition':'result == neg32(a)'}]
        for contract in contracts:
            with self.assertRaisesRegex(ValueError,'WRAP_PROFILE'):
                model.validate_module(module(P('a'),model.LEGACY_PROFILE,contract=contract))

    def test_profile_is_part_of_revision_identity(self):
        old=model.validate_module(module(P('a'),model.LEGACY_PROFILE))
        new=deepcopy(old);new.pop('revision_id');new['semantic_profile']=model.WRAP_PROFILE
        new=model.validate_module(new)
        self.assertNotEqual(old['revision_id'],new['revision_id'])
        changed=deepcopy(new);changed['units'][0]['body']=NEG(P('a'))
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):model.validate_module(changed)

    def test_calls_inline_and_independent_oracle(self):
        m=module();m['units'].append({'id':'u1','name':'Mix','qualified_name':'Arithmetic.Mix',
            'parameters':[{'name':'x','type':'int'},{'name':'y','type':'int'}],'return_type':'int','contract':None,
            'body':NEG({'kind':'call','type':'int','target':'u0','arguments':[BIN('mul32',P('x'),P('y')),P('x')]})})
        normalized=model.validate_module(m);oracle=backend._Oracle({u['id']:u for u in normalized['units']})
        for x,y in [(HIGH,HIGH),(LOW,-1),(46341,46341),(0,1)]:
            expected=signed_bytes(-signed_bytes(signed_bytes(x*y)+x))
            self.assertEqual(model.evaluate_unit(normalized,'u1',[x,y]),expected)
            self.assertEqual(oracle.call('u1',[x,y]),expected)
        self.assertEqual(normalized['dependencies']['u1'],['u0'])
        self.assertNotIn("'kind': 'call'",repr(model.inline_unit(normalized,'u1')))

    def test_emission_has_explicit_wrapping_and_typed_rust_literals(self):
        node=NEG(BIN('mul32',BIN('add32',P('a'),C(HIGH)),BIN('sub32',P('b'),C(LOW))))
        normalized=model.validate_module(module(node))
        csharp=backend.emit_module(normalized,'csharp')['source']
        rust=backend.emit_module(normalized,'rust')['source']
        python=backend.emit_module(normalized,'python')['source']
        self.assertEqual(csharp.count('unchecked('),4)
        for method in ('wrapping_neg','wrapping_add','wrapping_sub','wrapping_mul'):self.assertIn(method,rust)
        self.assertIn('2147483647i32',rust);self.assertIn('(-2147483648i32)',rust)
        self.assertEqual(python.count('% 4294967296'),4)
        tree=ast.parse(python);self.assertEqual(len(tree.body),1)
        self.assertFalse(any(isinstance(n,ast.Call) for n in ast.walk(tree)))

    def test_standalone_emit_matches_module_arithmetic_expression(self):
        node=NEG(BIN('sub32',P('a'),P('b')));s=spec('result == neg32(sub32(a,b))')
        for language in ('python','csharp','rust'):
            emitted=single.emit(node,s,language)
            self.assertEqual(emitted['source_sha256'],hashlib.sha256(emitted['source'].encode()).hexdigest())
            self.assertIn(single._render(node,{'a':'p0','b':'p1'},language),emitted['source'])

    def test_oracles_do_not_call_core_wrap_or_evaluate(self):
        node=NEG(BIN('add32',P('a'),P('b')))
        with patch.object(core,'_wrap32',side_effect=AssertionError('Shared arithmetic implementation')),patch.object(core,'evaluate',side_effect=AssertionError('Shared interpreter')):
            self.assertEqual(single._oracle(node,{'a':HIGH,'b':1}),LOW)
            self.assertEqual(backend._Oracle({}).evaluate(node,{'a':HIGH,'b':1}),LOW)

    def test_constructive_synthesis_preserves_arithmetic(self):
        s=spec('result == mul32(add32(a,b),neg32(a))')
        result=core.solve(s,{'max_seconds':2})
        self.assertEqual(result['status'],'CANDIDATE',result['reason'])
        self.assertEqual(result['candidate'],core.parse_expression('mul32(add32(a,b),neg32(a))',{'a':'int','b':'int'}))
        self.assertEqual(result['stats']['method'],'constructive_equality')

    def test_revision_keeps_original_and_caller_contract(self):
        normalized=model.validate_module(module());before=deepcopy(normalized)
        result=model.revise_unit(normalized,'u0',spec('result == sub32(a,b)'),'Explicit subtract revision.',{'max_seconds':2})
        self.assertEqual(result['status'],'REVISED',result['reason'])
        self.assertEqual(normalized,before)
        self.assertEqual(result['module']['parent_revision_id'],before['revision_id'])
        self.assertEqual(result['module']['provenance'],before['provenance'])
        self.assertEqual(result['module']['units'][0]['body']['op'],'sub32')

    def test_legacy_revision_cannot_smuggle_arithmetic(self):
        old=model.validate_module(module(P('a'),model.LEGACY_PROFILE))
        result=model.revise_unit(old,'u0',spec(),'No silent profile upgrade.',{'max_seconds':2})
        self.assertEqual(result['status'],'REFUSED');self.assertIsNone(result['module'])

    def test_sampling_bound_and_required_witnesses(self):
        two={'parameters':[{'name':'a','type':'int'},{'name':'b','type':'int'}]}
        rows=backend._sample_inputs(two,set())
        for pair in [(HIGH,1),(LOW,1),(46341,46341),(LOW,-1)]:self.assertIn(pair,rows)
        three={'parameters':two['parameters']+[{'name':'c','type':'int'}]}
        rows=backend._sample_inputs(three,set())
        self.assertIn((HIGH,HIGH,HIGH),rows);self.assertIn((46341,46341,0),rows)
        self.assertLessEqual(len(rows),512);self.assertEqual(len(rows),len(set(rows)))
        self.assertEqual(rows,backend._sample_inputs(three,set()))

    def test_sampling_critical_values_survive_many_constants(self):
        unit={'parameters':[{'name':'a','type':'int'}]}
        rows=backend._sample_inputs(unit,set(range(-10000,10000,57)))
        self.assertLessEqual(len(rows),33)
        for value in single.CRITICAL_INTS:self.assertIn((value,),rows)

    def test_bool_sampling_remains_exhaustive(self):
        params=[{'name':n,'type':'bool'} for n in ('a','b','c')]
        self.assertEqual(set(backend._sample_inputs({'parameters':params},set())),set(itertools.product((False,True),repeat=3)))

    def test_records_postconditions_agree_and_keep_bound(self):
        normalized=model.validate_module(module(BIN('mul32',P('a'),P('b'))))
        records,specs,oracle=backend._records(normalized)
        self.assertLessEqual(len(records),512)
        self.assertTrue(all(row['expected_postcondition'] for row in records))
        self.assertTrue(any(row['inputs']==[46341,46341] and row['expected']==-2147479015 for row in records))

    def test_formal_arithmetic_returns_inconclusive_without_bridge(self):
        with patch('legacy_bridge.load_bridge',side_effect=AssertionError('No Lean arithmetic claim')) as bridge:
            result=single.prove(BIN('add32',P('a'),P('b')),spec(),OUT)
        self.assertEqual(result['status'],'INCONCLUSIVE')
        self.assertIn('NOT_SUPPORTED',result['reason']);bridge.assert_not_called()

    def test_finite_arithmetic_validation_reaches_tool_admission(self):
        node=BIN('add32',P('a'),P('b'));s=spec();artifact=single.emit(node,s,'rust')
        with patch.object(single,'_find_tool',return_value=None) as tool,patch.object(single,'_process') as process:
            result=single.validate_generated(artifact,node,s,OUT)
        tool.assert_called_once_with('rust');process.assert_not_called()
        self.assertEqual(result['status'],'NON_COMPILE')
        self.assertGreater(result['tests']['planned'],0)

    def test_generated_rust_min_literal_reimports_without_path_extension(self):
        import rust_import
        original=model.validate_module(module(BIN('add32',C(LOW),P('a'))))
        source=backend.emit_module(original,'rust')['source']
        receipt=rust_import.import_rust(source)
        self.assertEqual(receipt['status'],'IMPORTED',receipt['reason'])
        for a in (LOW,HIGH,-1,0,1):
            self.assertEqual(model.evaluate_unit(receipt['module'],'u0',[a,0]),signed_bytes(LOW+a))

    def test_int32_input_bound_is_not_silently_wrapped(self):
        normalized=model.validate_module(module())
        for value in [True,2147483648,-2147483649,1.0,float('nan')]:
            with self.assertRaises(ValueError):model.evaluate_unit(normalized,'u0',[value,0])


if __name__=='__main__':
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ArithmeticTests))
    (OUT/'tests.txt').write_text(stream.getvalue(),encoding='utf-8')
    files=['math_core.py','module_model.py','module_backends.py','code_backends.py','test_arithmetic_core.py','CONTRAT_ARITHMETIQUE.txt']
    receipt={'status':'PASS' if result.wasSuccessful() else 'FAIL','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'compiled_or_executed_targets':False,
             'files_sha256':{n:hashlib.sha256((BASE/n).read_bytes()).hexdigest() for n in files}}
    (OUT/'result.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(stream.getvalue());print(OUT)
    raise SystemExit(not result.wasSuccessful())
