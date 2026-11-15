"""Module execution, independent expected values and refusal boundaries."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock
import uuid

import math_core
import module_model
import module_backends as backend

RUN = backend.BASE / 'evidence' / ('module-backend-tests-' + uuid.uuid4().hex)
RUN.mkdir(parents=True, exist_ok=False)


def param(name, typ='int'):
    return {'kind': 'param', 'type': typ, 'name': name}


def call(target, arguments, typ='int'):
    return {'kind': 'call', 'type': typ, 'target': target, 'arguments': arguments}


def unit(uid, name, params, body, ret='int', contract=None):
    return {'id': uid, 'name': name, 'qualified_name': 'BackendFixture.' + name,
            'parameters': [{'name': p, 'type': t} for p, t in params],
            'return_type': ret, 'body': body, 'contract': contract}


def fixture(units=None):
    source = '// Fixture modèle indépendante ; aucune extraction Roslyn prétendue.\r\n'
    return {'schema_version': 'mia-module-1', 'semantic_profile': 'pure-int32-bool-call-1',
            'module_id': 'backend-module-fixture', 'name': 'BackendFixture', 'parent_revision_id': None,
            'intent': 'Fixture de validation des backends',
            'provenance': {'source_text': source, 'source_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                           'import_id': 'backend-fixture-import', 'origin': 'controlled model fixture'},
            'units': units or [
                unit('u0', 'Max2', [('a', 'int'), ('b', 'int')], math_core.parse_expression('a if a >= b else b', {'a': 'int', 'b': 'int'})),
                unit('u1', 'Max3', [('a', 'int'), ('b', 'int'), ('c', 'int')], call('u0', [call('u0', [param('a'), param('b')]), param('c')]))]}


def cases(report):
    return json.loads((Path(report['evidence_dir']) / 'cases.json').read_text(encoding='utf-8'))


class ModuleBackendTests(unittest.TestCase):
    def run_module(self, module=None, language='python'):
        module = fixture() if module is None else module
        artifact = backend.emit_module(module, language)
        report = backend.validate_module_generated(artifact, module, RUN / self._testMethodName / language)
        self.assertEqual(json.loads((Path(report['evidence_dir']) / 'report.json').read_text(encoding='utf-8')), report)
        return artifact, report

    def test_all_targets_preserve_calls_names_and_revision_identity(self):
        module = module_model.validate_module(fixture())
        for language in backend.LANGUAGES:
            artifact = backend.emit_module(module, language)
            self.assertEqual(artifact, backend.emit_module(module, language))
            self.assertEqual(artifact['revision_id'], module['revision_id'])
            self.assertEqual(artifact['unit_mapping']['u0']['source_name'], 'Max2')
            self.assertEqual(artifact['unit_mapping']['u1']['qualified_name'], 'BackendFixture.Max3')
            self.assertIn(('F0(F0(' if language == 'csharp' else 'f0(f0('), artifact['source'])
            self.assertEqual(artifact['source_sha256'], hashlib.sha256(artifact['source'].encode()).hexdigest())

    def test_python_real_module_every_unit_matches_independent_max(self):
        _, r = self.run_module()
        self.assertEqual(r['status'], 'TESTED_FINITE')
        self.assertEqual([u['tests']['executed'] for u in r['units']], [49, 343])
        self.assertTrue(all(c['actual'] == max(c['inputs']) for c in cases(r)))
        self.assertGreater(r['oracle']['calls_by_unit']['u0'], 49)

    def test_csharp_real_module_compiles_and_executes_every_unit(self):
        if backend.legacy._find_tool('csharp') is None: self.skipTest('C# local absent; aucune installation')
        _, r = self.run_module(language='csharp')
        self.assertEqual(r['status'], 'TESTED_FINITE'); self.assertEqual(r['compile_status'], 'COMPILED')
        self.assertEqual(r['execution_status'], 'EXECUTED'); self.assertEqual(r['tests']['executed'], 392)
        self.assertTrue(all(c['actual'] == max(c['inputs']) for c in cases(r)))

    def test_rust_available_toolchain_real_or_explicit_absence(self):
        _, r = self.run_module(language='rust')
        self.assertIn(r['status'], {'TESTED_FINITE', 'NON_COMPILE'})
        if r['status'] == 'TESTED_FINITE':
            self.assertEqual(r['compile_status'], 'COMPILED'); self.assertEqual(r['tests']['executed'], 392)
            self.assertTrue(all(c['actual'] == max(c['inputs']) for c in cases(r)))
        else:
            self.assertEqual(r['execution_status'], 'NOT_EXECUTED'); self.assertEqual(r['tests']['executed'], 0)

    def test_arguments_substitute_simultaneously_and_call_scope_is_fresh(self):
        m = fixture([
            unit('u0', 'First', [('a', 'int'), ('b', 'int')], param('a')),
            unit('u1', 'Swapped', [('a', 'int'), ('b', 'int')], call('u0', [param('b'), param('a')]))])
        for language in ('python', 'csharp', 'rust'):
            if backend.legacy._find_tool(language) is None and not (language == 'rust' and backend.RUST_MANIFEST.is_file()): continue
            _, r = self.run_module(m, language); self.assertEqual(r['status'], 'TESTED_FINITE')
            for c in cases(r): self.assertEqual(c['actual'], c['inputs'][0 if c['unit_id'] == 'u0' else 1])

    def test_boolean_calls_and_mixed_parameters_real(self):
        m = fixture([
            unit('u0', 'Invert', [('flag', 'bool')], math_core.parse_expression('not flag', {'flag': 'bool'}), 'bool'),
            unit('u1', 'Choose', [('flag', 'bool'), ('a', 'int'), ('b', 'int')],
                 {'kind': 'ite', 'type': 'int', 'condition': call('u0', [param('flag', 'bool')], 'bool'),
                  'when_true': param('a'), 'when_false': param('b')})])
        for language in ('python', 'csharp', 'rust'):
            if backend.legacy._find_tool(language) is None and not (language == 'rust' and backend.RUST_MANIFEST.is_file()): continue
            _, r = self.run_module(m, language); self.assertEqual(r['status'], 'TESTED_FINITE')
            for c in cases(r):
                v = c['inputs']; expected = not v[0] if c['unit_id'] == 'u0' else v[1] if not v[0] else v[2]
                self.assertEqual(c['actual'], expected); self.assertIs(type(c['actual']), type(expected))

    def test_int32_low_constant_compiles_for_real(self):
        body = {'kind': 'constant', 'type': 'int', 'value': backend.LOW}
        m = fixture([unit('u0', 'Low', [('a', 'int')], body)])
        for language in ('python', 'csharp', 'rust'):
            if backend.legacy._find_tool(language) is None and not (language == 'rust' and backend.RUST_MANIFEST.is_file()): continue
            _, r = self.run_module(m, language); self.assertEqual(r['status'], 'TESTED_FINITE')
            self.assertTrue(all(c['actual'] == backend.LOW for c in cases(r)))

    def test_intentional_callee_change_exposes_old_caller_contract(self):
        original = module_model.validate_module(fixture()); old_bytes = json.dumps(original, sort_keys=True)
        spec = module_model.unit_spec(original, 'u0')
        spec['postcondition'] = 'result <= a and result <= b and (result == a or result == b)'
        changed = module_model.revise_unit(original, 'u0', spec, 'Transformer le maximum en minimum')
        self.assertEqual(changed['status'], 'REVISED'); self.assertEqual(json.dumps(original, sort_keys=True), old_bytes)
        for language in ('python', 'csharp', 'rust'):
            if backend.legacy._find_tool(language) is None and not (language == 'rust' and backend.RUST_MANIFEST.is_file()): continue
            _, r = self.run_module(changed['module'], language)
            self.assertEqual(r['status'], 'COUNTEREXAMPLE')
            by_id = {u['unit_id']: u for u in r['units']}
            self.assertEqual(by_id['u0']['status'], 'TESTED_FINITE'); self.assertEqual(by_id['u1']['status'], 'COUNTEREXAMPLE')
            self.assertTrue(by_id['u1']['tests']['counterexample_indices'])
            self.assertTrue(all(c['actual'] == min(c['inputs']) for c in cases(r)))

    def test_non_witnessed_unit_precondition_never_omitted(self):
        m = fixture(); m['units'][0]['contract'] = {'precondition': 'False', 'postcondition': 'result == a'}
        m['units'] = m['units'][:1]
        _, r = self.run_module(m)
        self.assertEqual(r['status'], 'INCONCLUSIVE'); self.assertEqual(len(r['units']), 1)
        self.assertEqual(r['units'][0]['tests']['contract_checked'], 0)

    def test_callee_precondition_violation_even_when_caller_result_matches(self):
        m = fixture([
            unit('u0', 'Positive', [('a', 'int')], param('a'), contract={'precondition': 'a >= 0', 'postcondition': 'result == a'}),
            unit('u1', 'Caller', [('a', 'int')], call('u0', [param('a')]), contract={'precondition': 'True', 'postcondition': 'result == a'})])
        _, r = self.run_module(m); self.assertEqual(r['status'], 'COUNTEREXAMPLE')
        row = next(x for x in r['units'] if x['unit_id'] == 'u1')
        self.assertFalse(row['tests']['counterexample_indices']); self.assertTrue(row['tests']['call_precondition_violation_indices'])
        for c in cases(r):
            if c['unit_id'] == 'u1' and c['inputs'][0] < 0:
                self.assertEqual(c['actual'], c['inputs'][0])
                self.assertEqual(c['call_precondition_violations'][0]['call_stack'], ['u1', 'u0'])

    def test_outside_root_precondition_does_not_refute_composition(self):
        for caller_pre, expected in [('a >= 0', 'TESTED_FINITE'), ('False', 'INCONCLUSIVE')]:
            m = fixture([
                unit('u0', 'Positive', [('a', 'int')], param('a'), contract={'precondition': 'a >= 0', 'postcondition': 'result == a'}),
                unit('u1', 'Caller', [('a', 'int')], call('u0', [param('a')]), contract={'precondition': caller_pre, 'postcondition': 'result == a'})])
            _, r = self.run_module(m); self.assertEqual(r['status'], expected)
            self.assertTrue(all(not c['call_precondition_violations'] for c in cases(r)))

    def test_slow_external_execution_does_not_spend_oracle_time_window(self):
        m = fixture(); a = backend.emit_module(m, 'python'); captured = []
        original_oracle, original_process = backend._Oracle, backend.legacy._process
        class ObservedOracle(original_oracle):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs); captured.append(self)
        def expired_after_external_work(command, folder, timeout, stdin=None):
            result = original_process(command, folder, timeout, stdin)
            captured[-1].deadline = 0  # Simulated elapsed compiler/executor time, no sleep.
            return result
        with mock.patch.object(backend, '_Oracle', ObservedOracle), mock.patch.object(backend.legacy, '_process', side_effect=expired_after_external_work):
            r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
        self.assertEqual(r['status'], 'TESTED_FINITE'); self.assertGreater(captured[0].steps, 392)

    def test_unqualified_rust_registry_cannot_activate(self):
        m = fixture(); a = backend.emit_module(m, 'rust')
        for value in ([], {'schema_version': 'mia-rust-toolchain-1', 'status': 'DOWNLOADED'},
                      {'schema_version': 'mia-rust-toolchain-1', 'status': 'QUALIFIED_LOCAL', 'qualification': []}):
            local = RUN / 'unqualified-rust.json'; local.write_text(json.dumps(value))
            with mock.patch.object(backend, 'RUST_MANIFEST', local), mock.patch.object(backend, '_process') as process:
                r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
            self.assertEqual(r['status'], 'REFUSED'); process.assert_not_called()

    def test_unapproved_rust_flags_rejected_before_execution(self):
        if not backend.RUST_MANIFEST.is_file(): self.skipTest('Registre portable absent')
        value = json.loads(backend.RUST_MANIFEST.read_text(encoding='utf-8')); value['extra_arguments'] += ['--extern', 'malicious']
        local = RUN / 'wrong-rust-flags.json'; local.write_text(json.dumps(value))
        m = fixture(); a = backend.emit_module(m, 'rust')
        with mock.patch.object(backend, 'RUST_MANIFEST', local), mock.patch.object(backend, '_process') as process:
            r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
        self.assertEqual(r['status'], 'REFUSED'); process.assert_not_called()

    def test_input_and_previous_reports_remain_unchanged(self):
        m = fixture(); before = deepcopy(m)
        _, r1 = self.run_module(m); path = Path(r1['evidence_dir']) / 'report.json'; old = path.read_bytes()
        _, r2 = self.run_module(m)
        self.assertEqual(m, before); self.assertEqual(path.read_bytes(), old)
        self.assertNotEqual(r1['evidence_dir'], r2['evidence_dir'])

    def test_tampered_source_rejected_before_process(self):
        m = fixture(); a = backend.emit_module(m, 'python'); a['source'] += '\nraise RuntimeError("never")\n'
        a['source_sha256'] = backend._sha(a['source'])
        with mock.patch.object(backend.legacy, '_process') as process:
            r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
        self.assertEqual(r['status'], 'REFUSED'); process.assert_not_called()

    def test_tampered_unit_mapping_or_revision_rejected(self):
        m = fixture()
        for field in ('revision_id', 'unit_mapping'):
            a = backend.emit_module(m, 'python'); a[field] = 'invalid'
            with mock.patch.object(backend.legacy, '_process') as process:
                r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
            self.assertEqual(r['status'], 'REFUSED'); process.assert_not_called()

    def test_model_revision_mismatch_refused(self):
        m = module_model.validate_module(fixture()); a = backend.emit_module(m, 'python')
        m['intent'] = 'Tampered while revision unchanged'
        with mock.patch.object(backend.legacy, '_process') as process:
            r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
        self.assertEqual(r['status'], 'REFUSED'); process.assert_not_called()

    def test_recursion_and_external_target_refused(self):
        for target in ('u1', 'outside'):
            m = fixture(); m['units'][1]['body'] = call(target, [param('a'), param('b'), param('c')])
            with self.assertRaises(ValueError): backend.emit_module(m, 'python')

    def test_absent_compiler_retains_all_planned_units_without_execution(self):
        m = fixture(); a = backend.emit_module(m, 'rust')
        with mock.patch.object(backend, '_toolchain', return_value=None), mock.patch.object(backend.legacy, '_process') as process:
            r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
        self.assertEqual(r['status'], 'NON_COMPILE'); self.assertEqual(len(r['units']), 2)
        self.assertEqual(r['tests']['planned'], 392); self.assertEqual(r['tests']['executed'], 0); process.assert_not_called()

    def test_process_timeout_preserved_as_inconclusive(self):
        m = fixture(); a = backend.emit_module(m, 'python')
        with mock.patch.object(backend.legacy, '_process', side_effect=subprocess.TimeoutExpired('fixture', 10)):
            r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
        self.assertEqual(r['status'], 'INCONCLUSIVE'); self.assertEqual(r['execution_status'], 'TIMEOUT')
        self.assertTrue((Path(r['evidence_dir']) / 'timeout.json').is_file())

    def test_wrong_output_unit_label_is_failed_not_misattributed(self):
        m = fixture(); a = backend.emit_module(m, 'python')
        def wrong_labels(command, folder, timeout, stdin=None):
            pairs = json.loads(stdin)
            return {'exit_code': 0, 'stderr': '', 'stdout': '\n'.join(json.dumps(['wrong', 0]) for _ in pairs)}
        with mock.patch.object(backend.legacy, '_process', side_effect=wrong_labels):
            r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
        self.assertEqual(r['status'], 'FAILED')

    def test_runtime_mutation_after_real_execution_invalidates_result(self):
        m = fixture(); a = backend.emit_module(m, 'python'); real_process = backend.legacy._process
        def run_then_mutate(command, folder, timeout, stdin=None):
            result = real_process(command, folder, timeout, stdin)
            (Path(folder) / 'harness.py').write_text('changed after execution', encoding='utf-8')
            return result
        with mock.patch.object(backend.legacy, '_process', side_effect=run_then_mutate):
            r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
        self.assertEqual(r['status'], 'INCONCLUSIVE'); self.assertEqual(r['execution_status'], 'EXECUTED')

    def test_all_archived_and_runtime_file_hashes_match(self):
        _, r = self.run_module()
        for field, directory in (('files_sha256', Path(r['evidence_dir'])), ('runtime_files_sha256', Path(r['runtime_dir']))):
            for name, expected in r[field].items():
                self.assertEqual(backend._sha((directory / name).read_bytes()), expected)

    def test_oracle_budget_exhaustion_honestly_inconclusive(self):
        m = fixture(); a = backend.emit_module(m, 'python')
        with mock.patch.object(backend, 'MAX_ORACLE_STEPS', 1), mock.patch.object(backend.legacy, '_process') as process:
            r = backend.validate_module_generated(a, m, RUN / self._testMethodName)
        self.assertEqual(r['status'], 'INCONCLUSIVE'); process.assert_not_called()


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ModuleBackendTests))
    backend._save(RUN / 'results.json', {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                  'skipped': len(result.skipped), 'all_pass': result.wasSuccessful(),
                  'source_sha256': {name: backend._sha((backend.BASE / name).read_bytes()) for name in ('module_backends.py', 'test_module_backends.py', 'module_model.py')},
                  'scope': 'Real generated module execution where compiler exists; oracle independent max/min; labelled fixtures for refusal/tampering. No Roslyn import or formal module proof claimed.'})
    print(json.dumps({'evidence': str(RUN), 'tests': result.testsRun, 'all_pass': result.wasSuccessful()}))
    raise SystemExit(not result.wasSuccessful())
