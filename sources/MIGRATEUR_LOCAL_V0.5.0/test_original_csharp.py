"""Boundaries of exact original C# admission and the closed runner protocol."""
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import original_csharp as helper
from common import BASE, save, sha
import csharp_import
import module_model

EVIDENCE = BASE / 'evidence' / ('original-tests-' + uuid.uuid4().hex)
EVIDENCE.mkdir(parents=True)
SOURCE = 'namespace A; internal class C { private static int Pick(int a) => a; }'


def fixture():
    digest = hashlib.sha256(SOURCE.encode()).hexdigest()
    module = module_model.validate_module({
        'schema_version': 'mia-module-1', 'semantic_profile': 'pure-int32-bool-wrap-call-1',
        'module_id': 'csharp-' + digest, 'name': 'A.C', 'parent_revision_id': None,
        'intent': 'Import C# automatique dans le profil pur déclaré.',
        'provenance': {'source_text': SOURCE, 'source_sha256': digest, 'import_id': 'csharp-' + digest,
                       'origin': {'language': 'csharp', 'extractor_sha256': sha(BASE / 'module_import.ps1')},
                       'limitations': csharp_import.LIMITATIONS},
        'units': [{'id': 'u0', 'name': 'Pick', 'qualified_name': 'A.C.Pick',
                   'parameters': [{'name': 'a', 'type': 'int'}], 'return_type': 'int',
                   'body': {'kind': 'param', 'type': 'int', 'name': 'a'}, 'contract': None}]})
    receipt = {'status': 'IMPORTED', 'module': module, 'evidence_dir': str(EVIDENCE), 'reason': 'Fixture'}
    return module, receipt


class Tests(unittest.TestCase):
    def setUp(self):
        self.module, self.receipt = fixture()
        self.cases = helper._cases([{'unit_id': 'u0', 'inputs': [1]}], self.module)

    def admission(self):
        with patch.object(helper, '_tools', return_value={'pwsh': 'fixture'}), patch.object(csharp_import, 'import_csharp', return_value=self.receipt):
            return helper.admit(self.module, self.receipt, EVIDENCE)

    def test_admit_reparses_and_preserves_exact(self):
        with patch.object(helper, '_tools', return_value={}), patch.object(csharp_import, 'import_csharp', return_value=self.receipt) as parser:
            result = helper.admit(self.module, self.receipt, EVIDENCE)
        self.assertEqual(result['status'], 'ADMITTED')
        self.assertEqual(parser.call_args.args[0], SOURCE.encode())
        self.assertEqual((Path(result['evidence_dir']) / 'source.cs').read_bytes(), SOURCE.encode())

    def test_forged_baseline_refused(self):
        altered = copy.deepcopy(self.module); altered.pop('revision_id')
        altered['units'][0]['body'] = {'kind': 'constant', 'type': 'int', 'value': 17}
        altered = module_model.validate_module(altered)
        supplied = dict(self.receipt, module=altered)
        with patch.object(helper, '_tools', return_value={}), patch.object(csharp_import, 'import_csharp', return_value=self.receipt):
            result = helper.admit(altered, supplied, EVIDENCE)
        self.assertEqual(result['status'], 'REFUSED')
        self.assertTrue(result['request_preserved'])

    def test_variant_body_preserves_original_baseline(self):
        changed = copy.deepcopy(self.module); changed.pop('revision_id')
        changed['parent_revision_id'] = self.module['revision_id']
        changed['units'][0]['body'] = {'kind': 'constant', 'type': 'int', 'value': -23}
        changed = module_model.validate_module(changed)
        with patch.object(helper, '_tools', return_value={}), patch.object(csharp_import, 'import_csharp', return_value=self.receipt):
            result = helper.admit(changed, self.receipt, EVIDENCE)
        self.assertEqual(result['status'], 'ADMITTED')
        self.assertEqual(result['baseline'], self.module)
        self.assertNotEqual(result['baseline'], result['current_model'])

    def test_signature_change_refused_before_reparse(self):
        changed = copy.deepcopy(self.module); changed.pop('revision_id'); changed['units'][0]['name'] = 'Other'
        with patch.object(csharp_import, 'import_csharp') as parser:
            result = helper.admit(changed, self.receipt, EVIDENCE)
        self.assertEqual(result['status'], 'REFUSED'); parser.assert_not_called()

    def test_reparse_refusal_stays_refused(self):
        rejected = {'status': 'REFUSED', 'reason': 'outside profile', 'evidence_dir': str(EVIDENCE)}
        with patch.object(helper, '_tools', return_value={}), patch.object(csharp_import, 'import_csharp', return_value=rejected):
            result = helper.admit(self.module, self.receipt, EVIDENCE)
        self.assertEqual(result['status'], 'REFUSED')

    def test_tool_change_inconclusive(self):
        with patch.object(helper, '_tools', side_effect=[{'hash': 1}, {'hash': 2}]), patch.object(csharp_import, 'import_csharp', return_value=self.receipt):
            result = helper.admit(self.module, self.receipt, EVIDENCE)
        self.assertEqual(result['status'], 'INCONCLUSIVE')

    def test_bad_request_preserved(self):
        result = helper.admit(None, {'status': 'FORGED'}, EVIDENCE)
        self.assertEqual(result['status'], 'REFUSED'); self.assertTrue(result['request_preserved'])
        self.assertEqual(json.loads(Path(result['request_file']).read_text())['source_import']['status'], 'FORGED')

    def test_cycle_explicit_nonpreservation(self):
        cycle = {}; cycle['self'] = cycle
        result = helper.admit(cycle, {}, EVIDENCE)
        self.assertEqual(result['status'], 'REFUSED'); self.assertFalse(result['request_preserved'])

    def test_input_types_bounds_ids(self):
        for record in ({'unit_id': 'u1', 'inputs': [1]}, {'unit_id': 'u0', 'inputs': [True]},
                       {'unit_id': 'u0', 'inputs': [1.0]}, {'unit_id': 'u0', 'inputs': [2147483648]},
                       {'unit_id': 'u0', 'inputs': []}):
            with self.assertRaises(ValueError): helper._cases([record], self.module)

    def test_case_budget(self):
        row = {'unit_id': 'u0', 'inputs': [-2147483648]}
        self.assertEqual(len(helper._cases([row] * 512, self.module)), 512)
        with self.assertRaises(ValueError): helper._cases([row] * 513, self.module)
        with self.assertRaises(ValueError): helper._cases([], self.module)

    def test_output_index_identity_types(self):
        good = {'index': 0, 'unit_id': 'u0', 'value': -2147483648}
        self.assertEqual(helper._parse_output(json.dumps(good), self.cases, self.module), [-2147483648])
        for key, value in [('index', True), ('index', 1), ('unit_id', 'u1'), ('value', True), ('value', 1.0), ('value', 2147483648)]:
            with self.assertRaises(ValueError): helper._parse_output(json.dumps(dict(good, **{key: value})), self.cases, self.module)

    def test_output_duplicates_unknown_and_missing(self):
        for text in ['', '{}', '{"index":0,"unit_id":"u0","value":1,"value":2}',
                     '{"index":0,"unit_id":"u0","value":NaN}',
                     '{"index":0,"unit_id":"u0","value":1,"extra":0}']:
            with self.assertRaises(ValueError): helper._parse_output(text, self.cases, self.module)

    def test_bool_return_strict(self):
        model = copy.deepcopy(self.module); model['units'][0]['return_type'] = 'bool'
        self.assertEqual(helper._parse_output('{"index":0,"unit_id":"u0","value":false}', self.cases, model), [False])
        with self.assertRaises(ValueError): helper._parse_output('{"index":0,"unit_id":"u0","value":0}', self.cases, model)

    def test_execute_forged_admission_never_processes(self):
        with patch.object(helper, '_process') as process:
            result = helper.execute({'status': 'ADMITTED', 'current_model': None, 'source_import': {}}, [], EVIDENCE)
        self.assertEqual(result['status'], 'REFUSED'); process.assert_not_called()

    def test_execute_stale_admission_never_processes(self):
        admission = self.admission(); altered = copy.deepcopy(admission); altered['source'] += ' '
        with patch.object(helper, 'admit', return_value=admission), patch.object(helper, '_process') as process:
            result = helper.execute(altered, [{'unit_id': 'u0', 'inputs': [1]}], EVIDENCE)
        self.assertEqual(result['status'], 'REFUSED'); process.assert_not_called()

    def test_mapping_permutation_and_signature_refused(self):
        row = {'id': 'u0', 'name': 'Pick', 'qualified_name': 'A.C.Pick', 'declaring_type': 'A.C',
               'parameter_types': ['int'], 'return_type': 'int'}
        self.assertEqual(helper._mapping([row], self.module), [row])
        for key, value in [('id', 'u1'), ('name', 'Other'), ('qualified_name', 'B.C.Pick'),
                           ('parameter_types', ['bool']), ('return_type', 'bool')]:
            with self.assertRaises(ValueError): helper._mapping([dict(row, **{key: value})], self.module)

    def test_producer_change_before_compile(self):
        admission = self.admission()
        with patch.object(helper, 'admit', return_value=admission), patch.object(helper, '_tools', return_value={'pwsh':'fixture'}), \
             patch.object(helper, '_producer_hashes', side_effect=[{'old':'hash'}, {'new':'hash'}, {'new':'hash'}]), patch.object(helper, '_process') as process:
            result = helper.execute(admission, [{'unit_id': 'u0', 'inputs': [1]}], EVIDENCE)
        self.assertEqual(result['status'], 'INCONCLUSIVE'); process.assert_not_called()

    def test_original_compile_options_must_match_unchecked_profile(self):
        admission = self.admission()
        for settings in ({'language_version':'CSharp12','check_overflow':True},
                         {'language_version':'CSharp12'},
                         {'language_version':'Default','check_overflow':False}):
            compiled={'status':'COMPILED',**settings}
            response={'timed_out':False,'exit_code':0,'stderr':'','stdout':json.dumps(compiled)}
            with patch.object(helper,'admit',return_value=admission), patch.object(helper,'_tools',return_value={'pwsh':'fixture'}), patch.object(helper,'_process',return_value=response) as process:
                result=helper.execute(admission,[{'unit_id':'u0','inputs':[1]}],EVIDENCE)
            self.assertEqual(result['status'],'INCONCLUSIVE')
            self.assertEqual(process.call_count,1)
            self.assertEqual(result['execution_status'],'NOT_STARTED')
            self.assertIn('Options de compilation',result['reason'])


if __name__ == '__main__':
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    (EVIDENCE / 'tests.txt').write_text(stream.getvalue(), encoding='utf-8')
    save(EVIDENCE / 'result.json', {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                                  'status': 'PASS' if result.wasSuccessful() else 'FAIL',
                                  'files_sha256': {n: sha(BASE/n) for n in (*helper.FILES, 'test_original_csharp.py', 'ORIGINAL_CSHARP_CONTRACT.txt')}})
    print(stream.getvalue()); print(str(EVIDENCE))
    raise SystemExit(not result.wasSuccessful())
