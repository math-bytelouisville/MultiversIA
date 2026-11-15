"""Verifier admission, independent contract diagnostics and optional real compilers."""
from copy import deepcopy
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time
import unittest
from unittest import mock
import uuid

import emitters
import math_core
import module_model
import rust_import
import verifier as v

SOURCE = ('fn main(a: i32, b: i32) -> i32 { if a >= b { a } else { b } }\n'
          'pub fn via(a: i32, b: i32) -> i32 { main(a,b) }\n'
          'fn gate(a: bool, b: bool) -> bool { a && !b } // trailing comment')
RUN = v.BASE / 'evidence' / ('verifier-tests-' + uuid.uuid4().hex)
RUN.mkdir(parents=True)


def imported(source=SOURCE):
    receipt = rust_import.import_rust(source)
    assert receipt['status'] == 'IMPORTED', receipt.get('reason')
    return receipt


def changed(receipt, body=None, contract=None):
    module = deepcopy(receipt['module'])
    module['parent_revision_id'] = module.pop('revision_id')
    module['intent'] = 'Verifier fixture: deliberate edit'
    if body is not None: module['units'][0]['body'] = body
    if contract is not None: module['units'][0]['contract'] = contract
    return module_model.validate_module(module)


def actual_like(records):
    return {'status': 'EXECUTED', 'values': [r['expected'] for r in records], 'compile_status': 'COMPILED',
            'execution_status': 'EXECUTED', 'evidence_dir': 'fixture only'}


class VerifierTests(unittest.TestCase):
    def test_artifact_forgery_is_refused_before_tool_or_process(self):
        module = imported()['module']; artifact = emitters.emit_module(module, 'python')
        artifact['source'] += '\nraise RuntimeError("must never execute")\n'
        artifact['source_sha256'] = v._text_sha(artifact['source'])
        with mock.patch.object(v, '_toolchain') as tool, mock.patch.object(v, '_process') as process:
            report = v.verify(module, [artifact], RUN)
        self.assertEqual(report['status'], 'REFUSED'); tool.assert_not_called(); process.assert_not_called()
        saved = json.loads(Path(report['request_file']).read_text(encoding='utf-8'))
        self.assertEqual(saved['artifacts'][0], artifact); self.assertTrue(report['request_preserved'])

    def test_cyclic_or_oversize_request_has_explicit_nonpreservation_reason(self):
        cyclic = []; cyclic.append(cyclic)
        with mock.patch.object(v, '_toolchain') as tool:
            result = v.verify({}, cyclic, RUN)
        self.assertEqual(result['status'], 'REFUSED'); self.assertFalse(result['request_preserved'])
        self.assertIn('cycle', result['request_preservation_reason']); tool.assert_not_called()
        with mock.patch.object(v, 'MAX_REQUEST', 32):
            result = v.verify({'huge': 'x' * 33}, [], RUN)
        self.assertFalse(result['request_preserved']); self.assertIn('bound', result['request_preservation_reason'])

    def test_code_change_during_reparse_prevents_first_process(self):
        receipt = imported(); module = receipt['module']; artifact = emitters.emit_module(module, 'python')
        before_admission = True
        original = v._source_baseline
        def admission(*args):
            nonlocal before_admission
            result = original(*args); before_admission = False
            return result
        with mock.patch.object(v, '_source_baseline', side_effect=admission), mock.patch.object(v, '_unchanged', side_effect=lambda _: before_admission), mock.patch.object(v, '_toolchain', return_value=None), mock.patch.object(v, '_process') as process:
            result = v.verify(module, [artifact], RUN, receipt)
        self.assertEqual(result['status'], 'INCONCLUSIVE'); process.assert_not_called()

    def test_rust_baseline_reparse_rejects_forged_body_and_unsupported_source(self):
        receipt = imported(); bad = deepcopy(receipt)
        bad['module'] = changed(receipt, math_core.parse_expression('p0', {'p0': 'int', 'p1': 'int'}))
        with self.assertRaises(ValueError): v._source_baseline(bad['module'], bad)
        bad = deepcopy(receipt); module = bad['module']; module.pop('revision_id')
        module['provenance']['source_text'] += '\nfn injected(a: i32) -> i32 { panic!() }'
        module['provenance']['source_sha256'] = v._text_sha(module['provenance']['source_text'])
        bad['module'] = module_model.validate_module(module)
        with self.assertRaises(ValueError): v._source_baseline(bad['module'], bad)

    def test_revised_model_keeps_provenance_and_signature_but_may_differ(self):
        receipt = imported(); modified = changed(receipt, math_core.parse_expression('p0 if p0 <= p1 else p1', {'p0': 'int', 'p1': 'int'}))
        admission = v._source_baseline(modified, receipt)
        self.assertEqual(admission['status'], 'ADMITTED'); self.assertEqual(admission['baseline'], receipt['module'])
        bad = deepcopy(modified); bad.pop('revision_id'); bad['units'][0]['name'] = 'renamed'
        with self.assertRaises(ValueError): v._source_baseline(module_model.validate_module(bad), receipt)

    def test_source_harness_avoids_names_and_preserves_private_main_and_comment(self):
        receipt = imported(SOURCE.replace('via', '__mia_observe'))
        module = receipt['module']; admission = v._source_baseline(module, receipt); records, _, _ = v._records(module)
        harness = v._source_harness(admission, records)
        self.assertIn('pub fn __mia_observe_()', harness)
        self.assertIn(receipt['module']['provenance']['source_text'] + '\n', harness)
        self.assertIn('fn main() { __mia_source::', harness)
        self.assertIn('::std::println!', harness)

    def test_cases_boundaries_full_bool_and_original_revised_constants(self):
        receipt = imported('fn boundary(a: i32) -> bool { a >= 17 }')
        model = changed(receipt, math_core.parse_expression('p0 >= 101', {'p0': 'int'}), {'precondition': 'True', 'postcondition': 'result == (p0 >= 101)'})
        records, _, _ = v._records(model, receipt['module']); values = {r['inputs'][0] for r in records}
        self.assertTrue({v.LOW, v.HIGH, 16, 17, 18, 100, 101, 102}.issubset(values)); self.assertLessEqual(len(records), 512)
        records, _, _ = v._records(imported()['module'])
        self.assertEqual({tuple(r['inputs']) for r in records if r['unit_id'] == 'u2'}, {(False, False), (False, True), (True, False), (True, True)})

    def test_contract_counterexample_is_distinct_from_model_agreement(self):
        receipt = imported(); model = changed(receipt, math_core.parse_expression('p0 if p0 <= p1 else p1', {'p0': 'int', 'p1': 'int'}))
        records, specs, oracle = v._records(model)
        oracle.deadline = time.monotonic() - 100
        check = v._check_generated(actual_like(records), records, model, specs, oracle)
        self.assertEqual(check['model_agreement'], 'PASS'); self.assertEqual(check['status'], 'COUNTEREXAMPLE')
        self.assertEqual({r['unit_id'] for r in check['units'] if r['status'] == 'COUNTEREXAMPLE'}, {'u0', 'u1'})
        self.assertTrue(all(c['kind'] == 'POSTCONDITION_VIOLATION' for c in check['counterexamples']))

    def test_call_precondition_checks_only_admissible_root_inputs(self):
        receipt = imported('fn inner(a:i32)->i32 { a } fn outer(a:i32)->i32 { inner(a) }')
        model = changed(receipt, contract={'precondition': 'p0 >= 0', 'postcondition': 'result == p0'})
        records, specs, oracle = v._records(model); check = v._check_generated(actual_like(records), records, model, specs, oracle)
        self.assertFalse(check['units'][0]['tests']['call_precondition_violation_indices'])
        self.assertTrue(check['units'][1]['tests']['call_precondition_violation_indices'])
        again = deepcopy(model); again.pop('revision_id'); again['units'][1]['contract']['precondition'] = 'p0 >= 0'
        again = module_model.validate_module(again); records, specs, oracle = v._records(again)
        self.assertEqual(v._check_generated(actual_like(records), records, again, specs, oracle)['status'], 'TESTED_FINITE')

    def test_no_precondition_witness_cannot_pass(self):
        receipt = imported('fn identity(a:i32)->i32 { a }')
        model = changed(receipt, contract={'precondition': 'False', 'postcondition': 'result == p0'})
        records, specs, oracle = v._records(model)
        self.assertEqual(v._check_generated(actual_like(records), records, model, specs, oracle)['status'], 'INCONCLUSIVE')

    def test_bad_output_type_count_identity_and_unknown_status(self):
        module = imported()['module']; records, specs, oracle = v._records(module); units = {u['id']: u for u in module['units']}
        for text in ('', '["u0",true]\n', '["foreign",1]\n'):
            with self.assertRaises(ValueError): v._parse_output(text, records, units)
        one = [r for r in records if r['unit_id'] == 'u2'][:1]
        with self.assertRaises(ValueError): v._parse_output('["u2",1]', one, units)
        check = v._check_generated({'status': 'UNEXPECTED'}, records, module, specs, oracle)
        self.assertEqual(check['status'], 'INCONCLUSIVE')

    def test_original_receipt_requires_complete_exact_typed_execution(self):
        model = imported()['module']; records, _, _ = v._records(model); units = {u['id']: u for u in model['units']}
        good = actual_like(records)
        self.assertEqual(v._check_source(good, records, model, units)['baseline_model_agreement'], 'PASS')
        bad_cases = []
        bad = deepcopy(good); bad['values'].pop(); bad_cases.append(bad)
        bad = deepcopy(good); bad['values'][0] = True; bad_cases.append(bad)
        bad = deepcopy(good); bad['values'][0] = v.HIGH + 1; bad_cases.append(bad)
        bad = deepcopy(good); bad['values'][next(i for i, r in enumerate(records) if r['unit_id'] == 'u2')] = 1; bad_cases.append(bad)
        bad = deepcopy(good); bad['compile_status'] = 'NON_COMPILE'; bad_cases.append(bad)
        bad = deepcopy(good); bad['execution_status'] = 'NOT_EXECUTED'; bad_cases.append(bad)
        bad = deepcopy(good); bad['status'] = 'UNKNOWN'; bad_cases.append(bad)
        for bad in bad_cases:
            result = v._check_source(bad, records, model, units)
            self.assertEqual(result['status'], 'INCONCLUSIVE')
            self.assertEqual(result['agreement'], 'NOT_TESTED')
            self.assertEqual(result['baseline_model_agreement'], 'NOT_TESTED')

    def test_original_baseline_agreement_and_intentional_revision_are_separate(self):
        receipt = imported(); baseline = receipt['module']
        current = changed(receipt, math_core.parse_expression('p0 if p0 <= p1 else p1', {'p0': 'int', 'p1': 'int'}))
        records, _, _ = v._records(current, baseline)
        original = v.foundation._Oracle({u['id']: u for u in baseline['units']})
        execution = actual_like(records)
        execution['values'] = [original.call(r['unit_id'], r['inputs']) for r in records]
        result = v._check_source(execution, records, baseline, {u['id']: u for u in current['units']})
        self.assertEqual(result['baseline_model_agreement'], 'PASS'); self.assertEqual(result['agreement'], 'DIFFERENT')
        self.assertTrue(result['differences']); self.assertEqual(result['baseline_differences'], [])

    def test_wrong_original_method_association_cannot_pass_sentinels(self):
        baseline = imported('fn first(a:i32)->i32 { 17 } fn second(a:i32)->i32 { -23 }')['module']
        records, _, _ = v._records(baseline)
        execution = actual_like(records)
        execution['values'] = [-23 if row['unit_id'] == 'u0' else 17 for row in records]
        result = v._check_source(execution, records, baseline, {u['id']: u for u in baseline['units']})
        self.assertEqual(result['baseline_model_agreement'], 'DIFFERENT'); self.assertEqual(result['agreement'], 'DIFFERENT')
        self.assertEqual(len(result['baseline_differences']), len(records))

    def test_csharp_admission_failure_is_archived_before_any_target(self):
        import original_csharp
        receipt = imported(); model = receipt['module']; model.pop('revision_id')
        model['provenance']['origin']['language'] = 'csharp'
        model = module_model.validate_module(model); receipt['module'] = model
        for status in ('REFUSED', 'INCONCLUSIVE'):
            admission = {'status': status, 'language': 'csharp', 'reason': 'Fixture: readmission failed',
                         'evidence_dir': 'fixture admission evidence'}
            with mock.patch.object(original_csharp, 'admit', return_value=admission), mock.patch.object(v, '_execute') as execute, mock.patch.object(v, '_toolchain') as tool:
                report = v.verify(model, [emitters.emit_module(model, 'python')], RUN, receipt)
            self.assertEqual(report['status'], status); execute.assert_not_called(); tool.assert_not_called()
            self.assertEqual(report['source_admission'], admission)
            self.assertEqual(report['source_check']['execution_status'], 'NOT_EXECUTED')
            saved = json.loads(Path(report['request_file']).read_text(encoding='utf-8'))
            self.assertEqual(saved['source_import'], receipt)

    def test_csharp_forged_signature_is_rejected_before_helper_or_target(self):
        import original_csharp
        receipt = imported(); baseline = receipt['module']; baseline.pop('revision_id')
        baseline['provenance']['origin']['language'] = 'csharp'
        baseline = module_model.validate_module(baseline); receipt['module'] = baseline
        model = deepcopy(baseline); model.pop('revision_id'); model['units'][0]['name'] = 'forged_association'
        model = module_model.validate_module(model)
        with mock.patch.object(original_csharp, 'admit') as admit, mock.patch.object(v, '_execute') as execute:
            report = v.verify(model, [emitters.emit_module(model, 'python')], RUN, receipt)
        self.assertEqual(report['status'], 'REFUSED'); admit.assert_not_called(); execute.assert_not_called()

    def test_csharp_dispatch_keeps_shared_cases_and_source_failure_fail_closed(self):
        import original_csharp
        receipt = imported(); model = receipt['module']; model.pop('revision_id')
        model['provenance']['origin']['language'] = 'csharp'
        model = module_model.validate_module(model); receipt['module'] = model
        admission = {'status': 'ADMITTED', 'language': 'csharp', 'baseline': model, 'current_model': model}
        target_records = []
        def generated(language, source, harness, records, units, folder, tool, separate):
            target_records.extend(deepcopy(records)); folder.mkdir()
            return dict(actual_like(records), evidence_dir=str(folder), language=language)
        for malformed in (False, True):
            target_records.clear()
            def source(admitted, records, folder):
                self.assertEqual(admitted, admission); self.assertEqual(records, target_records); folder.mkdir()
                result = dict(actual_like(records), evidence_dir=str(folder), language='csharp')
                if malformed: result['values'].pop()
                return result
            with mock.patch.object(original_csharp, 'admit', return_value=admission), mock.patch.object(original_csharp, 'execute', side_effect=source), mock.patch.object(v, '_execute', side_effect=generated), mock.patch.object(v, '_toolchain', return_value=None):
                report = v.verify(model, [emitters.emit_module(model, 'python')], RUN, receipt)
            self.assertEqual(report['generated_checks'][0]['model_agreement'], 'PASS')
            self.assertEqual(report['source_check']['language'], 'csharp')
            self.assertEqual(report['status'], 'INCONCLUSIVE' if malformed else 'TESTED_FINITE')
            self.assertEqual(report['source_check']['agreement'], 'NOT_TESTED' if malformed else 'PASS')

    def test_csharp_admission_cannot_substitute_a_model(self):
        import original_csharp
        receipt = imported(); model = receipt['module']; model.pop('revision_id')
        model['provenance']['origin']['language'] = 'csharp'
        model = module_model.validate_module(model); receipt['module'] = model
        admission = {'status': 'ADMITTED', 'language': 'csharp', 'baseline': model, 'current_model': {}}
        with mock.patch.object(original_csharp, 'admit', return_value=admission), mock.patch.object(v, '_execute') as execute:
            report = v.verify(model, [emitters.emit_module(model, 'python')], RUN, receipt)
        self.assertEqual(report['status'], 'INCONCLUSIVE'); execute.assert_not_called()

    def test_missing_tools_and_direct_json_never_claim_source_execution(self):
        module = imported()['module']; artifact = emitters.emit_module(module, 'csharp')
        with mock.patch.object(v, '_toolchain', return_value=None), mock.patch.object(v, '_process') as process:
            report = v.verify(module, [artifact], RUN)
        self.assertEqual(report['status'], 'NON_COMPILE'); self.assertEqual(report['source_check']['status'], 'NOT_TESTED'); process.assert_not_called()

    def test_output_outside_workspace_refused(self):
        with self.assertRaises(ValueError): v.verify({}, [], v.PREVIOUS / 'evidence')

    def test_owned_timeout_stops_actual_python_process_and_preserves_evidence(self):
        folder = RUN / ('timeout-' + uuid.uuid4().hex); folder.mkdir()
        tool = {'path_entries': []}
        result = v._process([sys.executable, '-I', '-S', '-c', 'import time; time.sleep(10)'], folder, 'fixture-timeout', 0.15, tool)
        self.assertTrue(result['timed_out']); self.assertTrue(result['process_exited'])
        saved = json.loads((folder / 'fixture-timeout.json').read_text(encoding='utf-8'))
        self.assertTrue(saved['process_exited']); self.assertEqual(saved['tree_stop_exit_code'], 0)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows process-tree verification')
    def test_owned_timeout_stops_an_actual_descendant_process(self):
        import ctypes
        folder = RUN / ('timeout-tree-' + uuid.uuid4().hex); folder.mkdir()
        script = ('import subprocess,sys,time; '
                  'p=subprocess.Popen([sys.executable,"-I","-S","-c","import time; time.sleep(10)"]); '
                  'print(p.pid,flush=True); time.sleep(10)')
        result = v._process([sys.executable, '-I', '-S', '-c', script], folder, 'fixture-tree-timeout', 1.0, {'path_entries': []})
        child_pid = int(result['stdout'].strip())
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, 0, child_pid)
        if handle:
            try: child_exited = kernel.WaitForSingleObject(handle, 0) == 0
            finally: kernel.CloseHandle(handle)
        else:
            child_exited = ctypes.get_last_error() == 87  # PID no longer exists.
        evidence = {'child_pid': child_pid, 'child_exited': child_exited, 'parent_exited': result['process_exited'],
                    'tree_stop_exit_code': result['tree_stop_exit_code'], 'timed_out': result['timed_out']}
        v._save(folder / 'descendant-check.json', evidence)
        self.assertTrue(result['timed_out']); self.assertTrue(result['process_exited'])
        self.assertEqual(result['tree_stop_exit_code'], 0); self.assertTrue(child_exited)


def real_checks():
    receipt = imported(); baseline = receipt['module']
    artifacts = [emitters.emit_module(baseline, language) for language in ('python', 'csharp', 'rust')]
    first = v.verify(baseline, artifacts, RUN, receipt)
    checks = [{'name': 'baseline generated three languages and original Rust', 'pass': first['status'] == 'TESTED_FINITE'
               and all(c['model_agreement'] == 'PASS' and c['execution_status'] == 'EXECUTED' for c in first['generated_checks'])
               and first['source_check']['agreement'] == 'PASS' and first['source_check']['baseline_model_agreement'] == 'PASS'}]
    modified = changed(receipt, math_core.parse_expression('p0 if p0 <= p1 else p1', {'p0': 'int', 'p1': 'int'}))
    second = v.verify(modified, [emitters.emit_module(modified, 'csharp')], RUN, receipt)
    checks.append({'name': 'C# agrees with changed model but old contracts fail and original Rust differs',
                   'pass': second['status'] == 'COUNTEREXAMPLE' and second['generated_checks'][0]['model_agreement'] == 'PASS'
                   and second['source_check']['agreement'] == 'DIFFERENT' and second['source_check']['baseline_model_agreement'] == 'PASS'
                   and {u['unit_id'] for u in second['generated_checks'][0]['units'] if u['status'] == 'COUNTEREXAMPLE'} == {'u0', 'u1'}})
    result = {'status': 'PASS' if all(c['pass'] for c in checks) else 'FAIL', 'checks': checks,
              'reports': [str(Path(r['evidence_dir']) / 'report.json') for r in (first, second)],
              'source_sha256': hashlib.sha256(SOURCE.encode('utf-8')).hexdigest(),
              'scope': 'Actual admitted source Rust, Python/C#/Rust baseline targets and C# changed body; finite pure profile only.'}
    v._save(RUN / 'real-results.json', result)
    return result


def deep_path_check():
    receipt = imported('fn choose(a:i32,b:i32)->i32 { if a>=b {a} else {b} }')
    model = receipt['module']
    deep = RUN / ('translation-' + uuid.uuid4().hex) / ('attempt-' + uuid.uuid4().hex) / ('ui-worker-' + uuid.uuid4().hex)
    report = v.verify(model, [emitters.emit_module(model, 'csharp')], deep, receipt)
    check = report['generated_checks'][0]
    evidence = Path(check['evidence_dir']); runtime = Path(check['runtime_dir'])
    names = ('program.cs', 'harness.cs', 'run.exe', 'compilation.json', 'execution.json', 'execution-stdout.txt')
    equal = all((evidence / name).read_bytes() == (runtime / name).read_bytes() for name in names)
    result = {'status': 'PASS' if report['status'] == 'TESTED_FINITE' and equal and len(str(evidence)) > 260 and len(str(runtime)) < 200 else 'FAIL',
              'report': str(Path(report['evidence_dir']) / 'report.json'), 'source_and_generated_agree': report['status'] == 'TESTED_FINITE',
              'archive_runtime_bytes_equal': equal, 'archive_path_length': len(str(evidence)), 'runtime_path_length': len(str(runtime))}
    v._save(RUN / 'deep-path-result.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--real', action='store_true'); args = parser.parse_args()
    tests = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(VerifierTests))
    result = {'status': 'PASS' if tests.wasSuccessful() else 'FAIL', 'tests': tests.testsRun, 'failures': len(tests.failures),
              'errors': len(tests.errors), 'skipped': len(tests.skipped), 'evidence_dir': str(RUN)}
    v._save(RUN / 'unit-results.json', result)
    if args.real and tests.wasSuccessful(): result['real'] = real_checks()
    print(json.dumps(result))
    raise SystemExit(not tests.wasSuccessful() or result.get('real', {}).get('status', 'PASS') != 'PASS')
