"""Real original-source arithmetic, independent expected values and repair."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

import common
import emitters
import migrator
import module_model
import verifier

CSHARP = '''namespace Calculs {
public static class Ring {
    public static int Add(int a,int b) { return unchecked(a+b); }
    private static int Sub(int a,int b) { return unchecked(a-b); }
    public static int Mul(int a,int b) { return unchecked(a*b); }
    internal static int Neg(int a) { return unchecked(-a); }
    public static int Mix(int a,int b,int c) { return unchecked(Add(a,b)*c); }
    public static bool Negative(int a,int b) { return unchecked(a+b)<0; }
} }
'''
RUST = '''pub fn add(a:i32,b:i32)->i32 { a.wrapping_add(b) }
fn sub(a:i32,b:i32)->i32 { a.wrapping_sub(b) }
pub fn mul(a:i32,b:i32)->i32 { a.wrapping_mul(b) }
fn neg(a:i32)->i32 { a.wrapping_neg() }
fn mix(a:i32,b:i32,c:i32)->i32 { add(a,b).wrapping_mul(c) }
fn negative(a:i32,b:i32)->bool { a.wrapping_add(b)<0 }
'''
ADD = 'public static class AddOnly { public static int Add(int a,int b) { return unchecked(a+b); } }'
EVIDENCE = None


def signed_bytes(value):
    """Fixture oracle via fixed-width byte decoding, separate from production."""
    return int.from_bytes((value % (1 << 32)).to_bytes(4, 'little'), 'little', signed=True)


def expected(name, args):
    name = name.lower()
    if name == 'add': return signed_bytes(args[0] + args[1])
    if name == 'sub': return signed_bytes(args[0] - args[1])
    if name == 'mul': return signed_bytes(args[0] * args[1])
    if name == 'neg': return signed_bytes(-args[0])
    if name == 'mix': return signed_bytes(signed_bytes(args[0] + args[1]) * args[2])
    if name == 'negative': return signed_bytes(args[0] + args[1]) < 0
    raise AssertionError(name)


class ArithmeticIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global EVIDENCE
        EVIDENCE = common.campaign(kind='arithmetic-integration')
        cls.add = migrator.import_source(ADD, 'csharp', EVIDENCE)
        assert cls.add['status'] == 'IMPORTED', cls.add['reason']

    def check_fixture(self, receipt, result, target_count):
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        v = result['verification']
        original = v['source_check']
        self.assertEqual(original['compile_status'], 'COMPILED')
        self.assertEqual(original['execution_status'], 'EXECUTED')
        self.assertEqual(original['baseline_model_agreement'], 'PASS')
        self.assertEqual(original['agreement'], 'PASS')
        cases = common.load_json(Path(v['evidence_dir']) / 'cases.json')
        units = {u['id']: u for u in receipt['module']['units']}
        wants = [expected(units[c['unit_id']]['name'], c['inputs']) for c in cases]
        self.assertEqual(original['values'], wants)
        self.assertEqual([type(x) for x in original['values']], [type(x) for x in wants])
        self.assertEqual([c['expected'] for c in cases], wants)
        self.assertEqual(len(v['generated_checks']), target_count)
        for check in v['generated_checks']:
            self.assertEqual(check['compile_status'], 'NOT_APPLICABLE' if check['language'] == 'python' else 'COMPILED')
            self.assertEqual(check['execution_status'], 'EXECUTED')
            self.assertEqual(check['model_agreement'], 'PASS')
            self.assertEqual(check['values'], wants)
        for uid, unit in units.items():
            inputs = {tuple(c['inputs']) for c in cases if c['unit_id'] == uid}
            self.assertLessEqual(len(inputs), 512)
            self.assertTrue(inputs)
            n = unit['name'].lower()
            if n == 'add': self.assertIn((2147483647, 1), inputs)
            if n == 'sub': self.assertIn((-2147483648, 1), inputs)
            if n == 'mul': self.assertIn((46341, 46341), inputs)
            if n == 'neg': self.assertIn((-2147483648,), inputs)
            if n == 'mix': self.assertIn((2147483647, 2147483647, 2147483647), inputs)
        common.save(Path(result['evidence_dir']) / 'independent-fixture-oracle.json', {
            'status': 'PASS', 'method': 'fixed_width_signed_byte_decoding', 'cases': len(cases),
            'original_and_targets_match': True, 'not_universal_proof': True,
        })

    def test_csharp_original_three_targets_and_independent_oracle(self):
        receipt = migrator.import_source(CSHARP, 'csharp', EVIDENCE)
        self.assertEqual(receipt['status'], 'IMPORTED', receipt['reason'])
        self.assertEqual(receipt['module']['semantic_profile'], 'pure-int32-bool-wrap-call-1')
        result = migrator.translate(receipt['module'], ['rust', 'python', 'csharp'], receipt, output_root=EVIDENCE)
        self.check_fixture(receipt, result, 3)
        self.assertEqual(Path(receipt['evidence_dir'], 'source.cs').read_bytes(), CSHARP.encode())

    def test_rust_original_three_targets_and_independent_oracle(self):
        receipt = migrator.import_source(RUST, 'rust', EVIDENCE)
        self.assertEqual(receipt['status'], 'IMPORTED', receipt['reason'])
        result = migrator.translate(receipt['module'], ['csharp', 'python', 'rust'], receipt, output_root=EVIDENCE)
        self.check_fixture(receipt, result, 3)

    def test_arithmetic_repair_retains_contract_and_source(self):
        receipt = self.add
        bad = deepcopy(receipt['module'])
        bad['parent_revision_id'] = bad.pop('revision_id')
        bad['intent'] = 'Fixture: addition mistakenly became subtraction.'
        bad['units'][0]['body']['op'] = 'sub32'
        bad = module_model.validate_module(bad)
        result = migrator.translate(bad, ['rust'], receipt, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        self.assertEqual(result['repair']['status'], 'REPAIRED_FINITE')
        self.assertTrue(result['repair']['promoted'])
        self.assertEqual(result['module']['units'][0]['contract'], bad['units'][0]['contract'])
        self.assertEqual(result['module']['provenance'], bad['provenance'])
        self.assertEqual(result['module']['parent_revision_id'], bad['revision_id'])
        self.assertTrue(Path(result['evidence_dir'], 'attempt-0', 'repair.json').is_file())
        self.check_fixture(receipt, result, 1)

    def test_intentional_subtraction_is_distinct_from_original_addition(self):
        receipt = self.add
        spec = module_model.unit_spec(receipt['module'], 'u0')
        spec['postcondition'] = 'result == sub32(a,b)'
        change = migrator.revise(receipt['module'], 'u0', spec, 'Choisir la soustraction.', EVIDENCE)
        self.assertEqual(change['status'], 'REVISED', change['reason'])
        result = migrator.translate(change['module'], ['python'], receipt, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        self.assertEqual(result['repair']['status'], 'NOT_NEEDED')
        self.assertEqual(result['verification']['source_check']['baseline_model_agreement'], 'PASS')
        self.assertEqual(result['verification']['source_check']['agreement'], 'DIFFERENT')
        self.assertTrue(result['verification']['source_check']['differences'])

    def test_generated_rust_with_minimum_constant_can_be_reimported_and_run(self):
        original = migrator.import_source(
            'fn bound(a:i32)->i32 { (-2147483648i32).wrapping_sub(a) }', 'rust', EVIDENCE)
        self.assertEqual(original['status'], 'IMPORTED', original['reason'])
        artifact = emitters.emit_module(original['module'], 'rust')
        again = migrator.import_source(artifact['source'], 'rust', EVIDENCE)
        self.assertEqual(again['status'], 'IMPORTED', again['reason'])
        self.assertEqual(again['module']['units'][0]['body'], original['module']['units'][0]['body'])
        self.assertNotEqual(again['module']['provenance'], original['module']['provenance'])
        result = migrator.translate(again['module'], ['csharp'], again, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        v = result['verification']
        cases = common.load_json(Path(v['evidence_dir']) / 'cases.json')
        wants = [signed_bytes(-2147483648 - case['inputs'][0]) for case in cases]
        self.assertEqual(v['source_check']['values'], wants)
        self.assertEqual(v['source_check']['agreement'], 'PASS')
        self.assertEqual(v['generated_checks'][0]['values'], wants)

    def test_unsupported_arithmetic_refused_without_execution(self):
        sources = [
            ('csharp', 'public static class C { public static int F(int a,int b) { return checked(a+b); } }'),
            ('csharp', 'public static class C { public static int F(int a,int b) { return a+b; } }'),
            ('csharp', 'public static class C { public static int F(int a,int b) { return unchecked(a/b); } }'),
            ('rust', 'fn f(a:i32,b:i32)->i32 { a+b }'),
            ('rust', 'fn f(a:i32,b:i32)->i32 { a.wrapping_div(b) }'),
            ('rust', 'fn f(a:i32,b:i32)->i32 { a.checked_add(b) }'),
        ]
        with patch('verifier.verify', side_effect=AssertionError('No verification before import admission')):
            for language, source in sources:
                with self.subTest(language=language, source=source):
                    receipt = migrator.import_source(source, language, EVIDENCE)
                    self.assertEqual(receipt['status'], 'REFUSED', receipt['reason'])
                    self.assertFalse(receipt['input_execution'])

    def test_missing_compiler_does_not_trigger_arithmetic_repair(self):
        with patch('verifier._toolchain', return_value=None):
            result = migrator.translate(self.add['module'], ['rust'], output_root=EVIDENCE)
        self.assertEqual(result['status'], 'NON_COMPILE', result['reason'])
        self.assertFalse(result['repair']['promoted'])
        self.assertEqual(result['repair']['attempts'], [])

    def test_current_foundation_manifest_is_required_and_hashed(self):
        current = common.load_json(common.BASE / 'FOUNDATION_V050.json')
        self.assertEqual(common.foundations(), verifier._foundation_hashes())
        for altered in [dict(current, version='0.4.1'), dict(current, schema='wrong'),
                        dict(current, files_sha256={}),
                        dict(current, files_sha256=list(current['files_sha256']))]:
            with self.subTest(altered=altered.get('schema')):
                with patch('common.load_json', return_value=altered):
                    with self.assertRaises((ValueError, KeyError)): common.foundations()
                with patch('verifier._load', return_value=altered):
                    with self.assertRaises((ValueError, KeyError)): verifier._foundation_hashes()
        with patch('common.sha', return_value='0' * 64):
            with self.assertRaises(ValueError): common.foundations()


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ArithmeticIntegrationTests))
    if EVIDENCE:
        common.save(EVIDENCE / 'summary.json', {
            'status': 'PASS' if result.wasSuccessful() else 'FAIL', 'tests_run': result.testsRun,
            'failures': [(str(t), e) for t, e in result.failures], 'errors': [(str(t), e) for t, e in result.errors],
            'files_sha256': {n: common.sha(common.BASE / n) for n in (
                'migrator.py', 'common.py', 'verifier.py', 'emitters.py', 'test_migrator.py',
                'test_verifier.py', 'test_arithmetic_integration.py')},
            'foundation_files_sha256': common.foundations(),
        })
        print('EVIDENCE=' + str(EVIDENCE))
    raise SystemExit(0 if result.wasSuccessful() else 1)
