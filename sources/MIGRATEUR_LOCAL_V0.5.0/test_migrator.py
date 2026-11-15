"""Root qualification: real compilers, provenance, contracts and repair stops."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch
import common
import migrator
import module_model

SIMPLE = 'fn maximum(a:i32,b:i32)->i32 { if a>b { a } else { b } }'
LINKED = '''// Source conservée, noms et appels réels.
pub fn choisir(a:i32,b:i32,actif:bool)->i32 { if actif { maximum(a,b) } else { a } }
fn maximum(a:i32,b:i32)->i32 { if a>=b { a } else { b } }
fn positif(a:i32)->bool { a>0 }
'''
EVIDENCE = None


def damaged(model):
    result = deepcopy(model)
    result.pop('revision_id')
    result['parent_revision_id'] = model['revision_id']
    result['intent'] = 'Fixture de défaut : le maximum retourne toujours le premier argument.'
    result['units'][0]['body'] = {'kind': 'param', 'type': 'int', 'name': 'p0'}
    return module_model.validate_module(result)


class MigratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global EVIDENCE
        EVIDENCE = common.campaign(kind='root-tests')
        cls.source = migrator.import_source(SIMPLE, 'rust', EVIDENCE)
        assert cls.source['status'] == 'IMPORTED', cls.source

    def test_real_rust_model_three_targets_and_source(self):
        source = migrator.import_source(LINKED, 'rust', EVIDENCE)
        self.assertEqual(source['status'], 'IMPORTED')
        result = migrator.translate(source['module'], ['csharp', 'rust', 'python'], source, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        self.assertEqual(result['verification']['source_check']['agreement'], 'PASS')
        self.assertEqual(len(result['verification']['generated_checks']), 3)
        self.assertEqual(result['repair']['status'], 'NOT_NEEDED')
        self.assertFalse(result['repair']['promoted'])

    def test_real_contract_preserving_automatic_repair(self):
        bad = damaged(self.source['module'])
        before = deepcopy(bad)
        result = migrator.translate(bad, ['csharp'], self.source, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        self.assertEqual(result['repair']['status'], 'REPAIRED_FINITE')
        self.assertTrue(result['repair']['promoted'])
        self.assertEqual(len(result['repair']['attempts']), 1)
        self.assertEqual(result['module']['units'][0]['contract'], bad['units'][0]['contract'])
        self.assertEqual(result['module']['provenance'], bad['provenance'])
        self.assertEqual(bad, before)
        self.assertEqual(result['module']['parent_revision_id'], bad['revision_id'])
        self.assertEqual(result['verification']['source_check']['agreement'], 'PASS')
        self.assertTrue((Path(result['evidence_dir']) / 'attempt-0' / 'repair.json').is_file())

    def test_zero_budget_preserves_original(self):
        bad = damaged(self.source['module'])
        result = migrator.translate(bad, ['python'], max_repairs=0, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'COUNTEREXAMPLE', result['reason'])
        self.assertEqual(result['repair']['status'], 'BUDGET_EXHAUSTED')
        self.assertEqual(result['module'], bad)
        self.assertFalse(result['repair']['promoted'])

    def test_disabled_preserves_original(self):
        bad = damaged(self.source['module'])
        result = migrator.translate(bad, ['python'], auto_correct=False, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'COUNTEREXAMPLE')
        self.assertEqual(result['repair']['status'], 'DISABLED')
        self.assertEqual(result['repair']['attempts'], [])

    def test_missing_tool_is_not_a_repair(self):
        bad = damaged(self.source['module'])
        with patch('verifier._toolchain', return_value=None):
            result = migrator.translate(bad, ['csharp'], output_root=EVIDENCE)
        self.assertEqual(result['status'], 'NON_COMPILE', result['reason'])
        self.assertFalse(result['repair']['promoted'])
        self.assertEqual(result['repair']['attempts'], [])

    def test_forged_source_receipt_refused_before_repair(self):
        forged = deepcopy(self.source)
        forged['module'] = damaged(self.source['module'])
        result = migrator.translate(forged['module'], ['python'], forged, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'REFUSED', result['reason'])
        self.assertEqual(result['repair']['attempts'], [])

    def test_intentional_revision_keeps_source_difference_visible(self):
        model = self.source['module']
        spec = module_model.unit_spec(model, 'u0')
        spec['postcondition'] = 'result == (p0 if p0 < p1 else p1)'
        changed = migrator.revise(model, 'u0', spec, 'Choisir volontairement le minimum.', EVIDENCE)
        self.assertEqual(changed['status'], 'REVISED', changed['reason'])
        result = migrator.translate(changed['module'], ['csharp'], self.source, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        self.assertEqual(result['verification']['source_check']['agreement'], 'DIFFERENT')
        self.assertEqual(result['verification']['source_check']['baseline_model_agreement'], 'PASS')
        self.assertEqual(result['repair']['status'], 'NOT_NEEDED')

    def test_invalid_options_never_execute(self):
        with patch('verifier.verify', side_effect=AssertionError('Must not run')):
            for targets, enabled, budget in [([], True, 2), (['rust', 'rust'], True, 2),
                (['go'], True, 2), (['python'], 1, 2), (['python'], True, True),
                (['python'], True, 4), ('rust', True, 2)]:
                with self.subTest(targets=targets, enabled=enabled, budget=budget):
                    result = migrator.translate(self.source['module'], targets, auto_correct=enabled,
                                                max_repairs=budget, output_root=EVIDENCE)
                    self.assertEqual(result['status'], 'REFUSED')
                    self.assertTrue(result['request_archive']['preserved'])
                    self.assertEqual(common.load_json(Path(result['evidence_dir']) / 'request.json')['targets'], targets)

    def test_refused_revision_keeps_requested_property(self):
        spec = {'postcondition': 'result == unknown'}
        result = migrator.revise(self.source['module'], 'u0', spec, 'Demande invalide conservée.', EVIDENCE)
        self.assertEqual(result['status'], 'REFUSED')
        request = common.load_json(Path(result['evidence_dir']) / 'request.json')
        self.assertEqual(request['spec'], spec)
        self.assertEqual(request['intent'], 'Demande invalide conservée.')

    def test_csharp_real_import_and_generation(self):
        source = 'public static class Test { public static int Max(int a,int b) { return a>b?a:b; } }'
        receipt = migrator.import_source(source, 'csharp', EVIDENCE)
        self.assertEqual(receipt['status'], 'IMPORTED', receipt['reason'])
        result = migrator.translate(receipt['module'], ['rust'], receipt, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        self.assertEqual(result['verification']['source_check']['status'], 'TESTED_FINITE')
        self.assertEqual(result['verification']['source_check']['agreement'], 'PASS')
        self.assertEqual(result['verification']['source_check']['baseline_model_agreement'], 'PASS')
        self.assertEqual(result['verification']['source_check']['execution_status'], 'EXECUTED')
        self.assertEqual(Path(receipt['evidence_dir'], 'source.cs').read_bytes(), source.encode())

    def test_rejected_csharp_bytes_are_preserved(self):
        receipt = migrator.import_source(b'\xffinvalid', 'csharp', EVIDENCE)
        self.assertEqual(receipt['status'], 'REFUSED')
        self.assertTrue(receipt['source_preserved'])
        self.assertEqual(Path(receipt['evidence_dir'], 'source.cs').read_bytes(), b'\xffinvalid')

    def test_output_escape_refused(self):
        with self.assertRaises(ValueError):
            migrator.translate(self.source['module'], ['python'], output_root=common.BASE.parent)

    def test_exponential_alias_request_rejected_before_encoding(self):
        value = [1]
        for _ in range(25): value = [value, value]
        with patch('common.json.dumps', side_effect=AssertionError('Must be bounded before encoding')):
            with self.assertRaises(ValueError):
                common.archive_request(EVIDENCE, {'module': value})

    def test_cycle_request_rejected_before_encoding(self):
        value = []
        value.append(value)
        with patch('common.json.dumps', side_effect=AssertionError('Must be bounded before encoding')):
            with self.assertRaises(ValueError):
                common.archive_request(EVIDENCE, {'module': value})

    def test_incomplete_success_receipt_never_becomes_success(self):
        forged = dict(status='TESTED_FINITE', reason='Fixture incomplete', generated_checks=[],
                      source_check={'status': 'NOT_TESTED'}, evidence_dir=str(EVIDENCE))
        with patch('verifier.verify', return_value=forged):
            result = migrator.translate(self.source['module'], ['python'], output_root=EVIDENCE)
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertFalse(result['repair']['promoted'])

    def test_original_csharp_private_calls_and_modern_syntax(self):
        source = '''namespace Atelier;
internal static class Choix {
    private static int PlusGrand(int gauche,int droite) => gauche>=droite ? gauche : droite;
    internal static int Selon(int a,int b,bool actif) => actif ? PlusGrand(b,a) : a;
    public static bool Negatif(int nombre) => nombre<0;
}'''
        receipt = migrator.import_source(source, 'csharp', EVIDENCE)
        self.assertEqual(receipt['status'], 'IMPORTED', receipt['reason'])
        result = migrator.translate(receipt['module'], ['rust','python','csharp'], receipt, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        original = result['verification']['source_check']
        self.assertEqual(original['language'], 'csharp')
        self.assertEqual(original['agreement'], 'PASS')
        self.assertEqual(original['baseline_model_agreement'], 'PASS')
        self.assertEqual(original['compile_status'], 'COMPILED')
        self.assertEqual(original['execution_status'], 'EXECUTED')
        self.assertEqual(len(original['values']), result['verification']['tests']['planned'])

    def test_original_csharp_sentinels_have_distinct_mapping(self):
        source = '''namespace MesTests { public static class Sentinelles {
    private static int Gauche(int x) { return 17; }
    private static int Droite(int x) { return -23; }
    public static int Choisir(int x,bool cote) { return cote ? Gauche(x) : Droite(x); }
} }'''
        receipt = migrator.import_source(source, 'csharp', EVIDENCE)
        self.assertEqual(receipt['status'], 'IMPORTED', receipt['reason'])
        result = migrator.translate(receipt['module'], ['rust'], receipt, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        original = result['verification']['source_check']
        cases = common.load_json(Path(result['verification']['evidence_dir']) / 'cases.json')
        observed = {'u0':set(), 'u1':set()}
        for case,value in zip(cases, original['values']):
            if case['unit_id'] in observed: observed[case['unit_id']].add(value)
        self.assertEqual(observed, {'u0':{17},'u1':{-23}})
        self.assertEqual(original['agreement'], 'PASS')

    def test_original_csharp_intentional_revision(self):
        receipt = migrator.import_source('public static class C { public static int Maximum(int a,int b) { return a>=b?a:b; } }','csharp',EVIDENCE)
        self.assertEqual(receipt['status'], 'IMPORTED', receipt['reason'])
        spec = module_model.unit_spec(receipt['module'], 'u0')
        spec['postcondition'] = 'result == (a if a < b else b)'
        revised = migrator.revise(receipt['module'], 'u0', spec, 'Demande explicite du minimum.', EVIDENCE)
        self.assertEqual(revised['status'], 'REVISED', revised['reason'])
        result = migrator.translate(revised['module'], ['rust'], receipt, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        self.assertEqual(result['verification']['source_check']['agreement'], 'DIFFERENT')
        self.assertEqual(result['verification']['source_check']['baseline_model_agreement'], 'PASS')

    def test_original_csharp_repair_keeps_contract_and_source(self):
        receipt = migrator.import_source('public static class C { public static int Maximum(int a,int b) { return a>=b?a:b; } }','csharp',EVIDENCE)
        self.assertEqual(receipt['status'], 'IMPORTED', receipt['reason'])
        bad = deepcopy(receipt['module']);bad.pop('revision_id')
        bad['units'][0]['body']={'kind':'param','type':'int','name':'a'}
        bad['parent_revision_id']=receipt['module']['revision_id'];bad['intent']='Fixture de défaut du corps.'
        bad=module_model.validate_module(bad)
        result = migrator.translate(bad, ['rust'], receipt, output_root=EVIDENCE)
        self.assertEqual(result['status'], 'TESTED_FINITE', result['reason'])
        self.assertEqual(result['repair']['status'], 'REPAIRED_FINITE')
        self.assertTrue(result['repair']['promoted'])
        self.assertEqual(result['module']['units'][0]['contract'],bad['units'][0]['contract'])
        self.assertEqual(result['verification']['source_check']['agreement'],'PASS')

    def test_original_csharp_unicode_namespace_and_escaped_names(self):
        source = '''namespace Démo { public static class @class {
    private static int @event(int @params) { return @params>=0 ? @params : -2147483648; }
    public static bool Ready(bool a,bool b) { return a && !b; }
} }'''
        receipt = migrator.import_source(source,'csharp',EVIDENCE)
        self.assertEqual(receipt['status'],'IMPORTED',receipt['reason'])
        result = migrator.translate(receipt['module'],['python'],receipt,output_root=EVIDENCE)
        self.assertEqual(result['status'],'TESTED_FINITE',result['reason'])
        self.assertEqual(result['verification']['source_check']['agreement'],'PASS')
        self.assertEqual(result['verification']['source_check']['baseline_model_agreement'],'PASS')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MigratorTests))
    if EVIDENCE:
        common.save(EVIDENCE / 'summary.json', {'status': 'PASS' if result.wasSuccessful() else 'FAIL',
                    'tests_run': result.testsRun, 'failures': [(str(t), err) for t, err in result.failures],
                    'errors': [(str(t), err) for t, err in result.errors],
                    'files_sha256': {name: common.sha(common.BASE / name) for name in
                        ('migrator.py', 'common.py', 'csharp_import.py', 'emitters.py', 'test_migrator.py')}})
        print('EVIDENCE=' + str(EVIDENCE))
    raise SystemExit(0 if result.wasSuccessful() else 1)
