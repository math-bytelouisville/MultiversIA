"""Real bounded Python/C# runs and explicit fixtures for negative boundaries."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import types
import unittest
from unittest import mock
import uuid
import code_backends as backend
import math_core

RUN = backend.BASE / 'evidence' / ('backend-tests-' + uuid.uuid4().hex)
RUN.mkdir(parents=True, exist_ok=False)


def specification(post='result == a', pre='True', params=None, ret='int'):
    return {'schema_version': 'mia-math-spec-1', 'name': 'Independent backend test',
            'parameters': params or [{'name': 'a', 'type': 'int'}], 'return_type': ret,
            'precondition': pre, 'postcondition': post}


def expression(text, spec):
    return math_core.parse_expression(text, {p['name']: p['type'] for p in spec['parameters']})


class BackendTests(unittest.TestCase):
    def validate(self, spec, text, language='python'):
        candidate = expression(text, spec)
        artifact = backend.emit(candidate, spec, language)
        result = backend.validate_generated(artifact, candidate, spec, RUN / self._testMethodName)
        self.assertEqual(json.loads((Path(result['evidence_dir']) / 'report.json').read_text(encoding='utf-8')), result)
        return artifact, result

    def test_emit_all_three_deterministic_canonical_names(self):
        spec = specification('result == match', params=[{'name': 'match', 'type': 'int'}])
        candidate = expression('match', spec)
        for language, extension in backend.LANGUAGES.items():
            artifact = backend.emit(candidate, spec, language)
            self.assertEqual(artifact, backend.emit(candidate, spec, language))
            self.assertEqual(artifact['extension'], extension)
            self.assertEqual(artifact['parameter_mapping'], {'match': 'p0'})
            self.assertNotIn('match', artifact['source'])
            self.assertEqual(artifact['source_sha256'], hashlib.sha256(artifact['source'].encode('utf-8')).hexdigest())

    def test_real_python_max_and_int32_boundaries(self):
        spec = specification('result >= a and result >= b and (result == a or result == b)',
                             params=[{'name': 'a', 'type': 'int'}, {'name': 'b', 'type': 'int'}])
        _, result = self.validate(spec, 'a if a >= b else b')
        self.assertEqual(result['status'], 'TESTED_FINITE')
        self.assertEqual(result['execution_status'], 'EXECUTED')
        cases = json.loads((Path(result['evidence_dir']) / 'cases.json').read_text(encoding='utf-8'))
        self.assertIn([backend.LOW, backend.HIGH], [c['inputs'] for c in cases])
        self.assertTrue(all(c['actual'] == max(c['inputs']) for c in cases))

    def test_real_csharp_compilation_and_execution(self):
        if backend._find_tool('csharp') is None:
            self.skipTest('No local C# compiler; no download')
        _, result = self.validate(specification(), 'a', 'csharp')
        self.assertEqual(result['status'], 'TESTED_FINITE')
        self.assertEqual(result['compile_status'], 'COMPILED')
        self.assertEqual(result['execution_status'], 'EXECUTED')
        self.assertEqual(len(result['executable_sha256']), 64)

    def test_real_boolean_mixed_parameters_in_available_runtimes(self):
        spec = specification('result == (flag if a >= 0 else not flag)',
                             params=[{'name': 'a', 'type': 'int'}, {'name': 'flag', 'type': 'bool'}], ret='bool')
        for language in ('python', 'csharp'):
            if backend._find_tool(language) is None: continue
            _, result = self.validate(spec, 'flag if a >= 0 else not flag', language)
            self.assertEqual(result['status'], 'TESTED_FINITE')
            cases = json.loads((Path(result['evidence_dir']) / 'cases.json').read_text(encoding='utf-8'))
            self.assertTrue(all(type(c['actual']) is bool for c in cases))

    def test_int32_min_literal_real_python_and_csharp(self):
        for language in ('python', 'csharp'):
            if backend._find_tool(language) is None: continue
            _, result = self.validate(specification('result == -2147483648'), '-2147483648', language)
            self.assertEqual(result['status'], 'TESTED_FINITE')

    def test_source_tampering_refused_before_any_process(self):
        spec = specification(); candidate = expression('a', spec)
        artifact = backend.emit(candidate, spec, 'python')
        artifact['source'] += '\nraise RuntimeError("must never execute")\n'
        artifact['source_sha256'] = backend._sha(artifact['source'])
        with mock.patch.object(backend, '_process') as process:
            result = backend.validate_generated(artifact, candidate, spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'REFUSED'); process.assert_not_called()
        self.assertEqual(result['tests']['executed'], 0)

    def test_hash_metadata_tampering_refused(self):
        spec = specification(); candidate = expression('a', spec)
        artifact = backend.emit(candidate, spec, 'python'); artifact['source_sha256'] = '0' * 64
        with mock.patch.object(backend, '_process') as process:
            result = backend.validate_generated(artifact, candidate, spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'REFUSED'); process.assert_not_called()

    def test_invalid_ast_calls_fields_and_bool_int_confusion_refused(self):
        invalid = [
            {'kind': 'call', 'type': 'int', 'name': 'danger'},
            {'kind': 'constant', 'type': 'int', 'value': True},
            {'kind': 'constant', 'type': 'int', 'value': backend.HIGH + 1},
            {'kind': 'param', 'type': 'int', 'name': 'result'},
            {'kind': 'param', 'type': 'int', 'name': 'a', 'extra': 'payload'},
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                backend.emit(candidate, specification(), 'python')

    def test_normalized_asts_are_reparsed(self):
        normalized = math_core.validate_spec(specification())
        normalized['post_ast'] = {'kind': 'constant', 'type': 'bool', 'value': True}
        _, result = self.validate(normalized, '0')
        self.assertEqual(result['status'], 'COUNTEREXAMPLE')

    def test_independent_oracle_does_not_use_core_evaluate(self):
        with mock.patch.object(math_core, 'evaluate', side_effect=AssertionError('shared evaluator called')):
            _, result = self.validate(specification(), 'a')
        self.assertEqual(result['status'], 'TESTED_FINITE')

    def test_bad_candidate_has_real_counterexamples(self):
        _, result = self.validate(specification(), '0')
        self.assertEqual(result['status'], 'COUNTEREXAMPLE')
        self.assertTrue(result['tests']['counterexample_indices'])
        self.assertEqual(result['tests']['differential_mismatch_indices'], [])

    def test_differential_mismatch_fixture_never_passes(self):
        spec = specification(); candidate = expression('a', spec)
        count = len(backend._cases(math_core.validate_spec(spec), candidate))
        with mock.patch.object(backend, '_process', return_value={'exit_code': 0, 'stdout': '0\n' * count, 'stderr': ''}):
            result = backend.validate_generated(backend.emit(candidate, spec, 'python'), candidate, spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(result['tests']['differential_mismatch_indices'])

    def test_absent_compiler_explicit_no_execution(self):
        spec = specification(); candidate = expression('a', spec)
        with mock.patch.object(backend, '_find_tool', return_value=None), mock.patch.object(backend, '_process') as process:
            result = backend.validate_generated(backend.emit(candidate, spec, 'rust'), candidate, spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'NON_COMPILE')
        self.assertEqual(result['execution_status'], 'NOT_EXECUTED')
        self.assertEqual(result['tests']['executed'], 0); process.assert_not_called()
        self.assertTrue((Path(result['evidence_dir']) / 'artifact.json').is_file())

    def test_precondition_without_sample_witness_stays_inconclusive(self):
        spec = specification(pre='False'); candidate = expression('a', spec)
        with mock.patch.object(backend, '_process') as process:
            result = backend.validate_generated(backend.emit(candidate, spec, 'python'), candidate, spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'INCONCLUSIVE'); process.assert_not_called()

    def test_conditional_contract_only_checked_where_precondition_true(self):
        _, result = self.validate(specification('result >= a', pre='a <= 0'), '0')
        self.assertEqual(result['status'], 'TESTED_FINITE')
        self.assertLess(result['tests']['contract_checked'], result['tests']['executed'])

    def test_execution_timeout_is_not_success_or_nonexecution_claim(self):
        spec = specification(); candidate = expression('a', spec)
        with mock.patch.object(backend, '_process', side_effect=subprocess.TimeoutExpired(['fixture'], 5)):
            result = backend.validate_generated(backend.emit(candidate, spec, 'python'), candidate, spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertEqual(result['execution_status'], 'TIMEOUT')
        self.assertEqual(result['tests']['executed'], 0)

    def test_compiler_timeout_does_not_claim_execution_started(self):
        spec = specification(); candidate = expression('a', spec)
        with mock.patch.object(backend, '_find_tool', return_value=Path(__file__)), mock.patch.object(backend, '_process', side_effect=subprocess.TimeoutExpired(['fixture'], 20)):
            result = backend.validate_generated(backend.emit(candidate, spec, 'csharp'), candidate, spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertEqual(result['compile_status'], 'TIMEOUT')
        self.assertEqual(result['execution_status'], 'NOT_EXECUTED')

    def test_compiler_failure_is_preserved(self):
        spec = specification(); candidate = expression('a', spec)
        with mock.patch.object(backend, '_find_tool', return_value=Path(__file__)), mock.patch.object(backend, '_process', return_value={'exit_code': 1, 'stdout': '', 'stderr': 'Compiler fixture error'}):
            result = backend.validate_generated(backend.emit(candidate, spec, 'csharp'), candidate, spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['compile_status'], 'FAILED')
        self.assertEqual(result['execution_status'], 'NOT_EXECUTED')
        self.assertTrue((Path(result['evidence_dir']) / 'compilation.json').is_file())

    def test_archives_are_unique_and_previous_report_unchanged(self):
        _, first = self.validate(specification(), 'a')
        path = Path(first['evidence_dir']) / 'report.json'; saved = path.read_bytes()
        _, second = self.validate(specification(), 'a')
        self.assertNotEqual(first['evidence_dir'], second['evidence_dir'])
        self.assertEqual(path.read_bytes(), saved)

    def test_samples_bounded_for_three_parameters_and_constants(self):
        spec = specification('result >= a or result == b or result == c',
                             params=[{'name': name, 'type': 'int'} for name in ('a', 'b', 'c')])
        normalized = math_core.validate_spec(spec); candidate = expression('a', spec)
        records = backend._cases(normalized, candidate)
        self.assertLessEqual(len(records), 512)
        self.assertTrue(all(len(r['inputs']) == 3 for r in records))

    def test_long_requested_archive_uses_short_runtime_real_python_and_csharp(self):
        output = RUN / self._testMethodName / ('long-path-' * 5) / ('more-path-' * 5)
        self.assertGreater(len(str(output.resolve())), 260)
        spec = specification(); candidate = expression('a', spec)
        for language in ('python', 'csharp'):
            if backend._find_tool(language) is None: continue
            artifact = backend.emit(candidate, spec, language)
            result = backend.validate_generated(artifact, candidate, spec, output)
            self.assertEqual(result['status'], 'TESTED_FINITE')
            self.assertLess(len(result['runtime_dir']), 200)
            runtime = Path(result['runtime_dir'])
            self.assertTrue(runtime.is_relative_to(backend.BASE / 'runtime'))
            self.assertEqual((runtime / ('program' + artifact['extension'])).read_bytes(),
                             (Path(result['evidence_dir']) / ('program' + artifact['extension'])).read_bytes())

    def test_proof_adapter_constructs_canonical_implication_without_real_lean(self):
        spec = specification(pre='a > 0'); candidate = expression('a', spec); captured = {}
        def fake(source, contract, output_root):
            captured.update(source=source, contract=contract, output_root=str(output_root))
            return {'verdict': 'INCONCLUSIF', 'reason': 'Explicit bridge fixture; no Lean run'}
        with mock.patch('legacy_bridge.load_bridge', return_value=types.SimpleNamespace(analyze=fake)):
            result = backend.prove(candidate, spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'INCONCLUSIF')
        self.assertIn('int p0, int result', captured['contract'])
        self.assertIn('p0 > (0)', captured['contract'])
        self.assertIn('||', captured['contract'])
        self.assertIn('result == p0', captured['contract'])
        self.assertFalse(result['language_equivalence_proved'])
        self.assertEqual(captured['source'], backend.emit(candidate, spec, 'csharp')['source'])
        self.assertTrue(Path(captured['output_root']).is_relative_to(backend.BASE / 'runtime'))
        self.assertLess(len(captured['output_root']), 200)

    def test_proof_without_precondition_witness_never_calls_bridge(self):
        spec = specification(pre='False')
        with mock.patch('legacy_bridge.load_bridge') as bridge:
            result = backend.prove(expression('a', spec), spec, RUN / self._testMethodName)
        self.assertEqual(result['status'], 'INCONCLUSIVE'); bridge.assert_not_called()


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BackendTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    names = ('code_backends.py', 'test_code_backends.py', 'BACKEND_NOTES.txt')
    backend._save(RUN / 'result.json', {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                  'skipped': len(result.skipped), 'files_sha256': {n: hashlib.sha256((backend.BASE / n).read_bytes()).hexdigest() for n in names if (backend.BASE / n).is_file()},
                  'scope': 'Real bounded Python/C# execution where available; negative process/proof fixtures explicitly separate. No Lean campaign or Rust compilation claimed.'})
    print(str(RUN))
    raise SystemExit(not result.wasSuccessful())
