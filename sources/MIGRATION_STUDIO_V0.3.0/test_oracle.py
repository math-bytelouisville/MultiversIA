"""Independent oracle admission/evidence checks; optional three-case real HTTP smoke."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import argparse
import hashlib
import json
import unittest
from unittest import mock
import uuid

import csharp_oracle as oracle

RUN = oracle.BASE / 'evidence' / ('oracle-tests-' + uuid.uuid4().hex)
RUN.mkdir(parents=True)


def small_case():
    return {'questions': [
        {'id': 's', 'mode': 'single', 'options': [{'id': 's-good', 'correct': True}, {'id': 's-bad', 'correct': False}], 'selected': ['s-bad']},
        {'id': 'm', 'mode': 'multiple', 'options': [{'id': 'm-a', 'correct': True}, {'id': 'm-b', 'correct': True}, {'id': 'm-c', 'correct': False}], 'selected': ['m-a']},
    ], 'grading': 'partial', 'passing_percent': '60', 'show_correction': True}


class OracleTests(unittest.TestCase):
    def test_count_corpus_is_complete_before_extra_controls(self):
        cases = oracle.generate_cases()
        expected = set()
        for n in range(2, 9):
            for good in range(1, n + 1):
                for chosen_good in range(good + 1):
                    for chosen_bad in range(n - good + 1):
                        if chosen_good + chosen_bad: expected.add(('multiple', n, good, chosen_good, chosen_bad))
            expected.update({('single', n, 1, 1, 0), ('single', n, 1, 0, 1)})
        self.assertEqual(len(expected), 427)
        for grading in ('partial', 'all-or-nothing'):
            observed = set()
            for case in cases[:18]:
                if case['grading'] != grading: continue
                for q in case['questions']:
                    good = {o['id'] for o in q['options'] if o['correct']}; selected = set(q['selected'])
                    observed.add((q['mode'], len(q['options']), len(good), len(good & selected), len(selected - good)))
            self.assertEqual(observed, expected)
        self.assertEqual(len(cases), 25); self.assertEqual(sum(len(c['questions']) for c in cases), 922)
        self.assertTrue(all(len(c['questions']) <= 50 for c in cases))

    def test_case_identity_is_exact_standard_canonical_json(self):
        for case in oracle.generate_cases():
            expected = hashlib.sha256(json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
            self.assertEqual(oracle.case_sha256(case), expected)
        before = small_case(); changed = deepcopy(before); changed['questions'].reverse()
        self.assertNotEqual(oracle.case_sha256(before), oracle.case_sha256(changed))

    def test_threshold_preserves_exact_json_number(self):
        text = '14.285714285714285714285714285'
        raw = oracle._json({'passingPercent': Decimal(text)})
        self.assertEqual(raw, '{"passingPercent":' + text + '}')
        self.assertEqual(json.loads(raw, parse_float=Decimal)['passingPercent'], Decimal(text))
        for value in ['14.285714285714285714285714285', '14.285714285714285714285714286', '100.0', '0']:
            case = small_case(); case['passing_percent'] = value; oracle.validate_case(case)
        for value in ['1e2', '060', '-1', '100.01', '0.' + '1' * 29, '99.1234567890123456789012345678']:
            case = small_case(); case['passing_percent'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): oracle.validate_case(case)

    def test_transport_rejects_missing_empty_duplicate_and_boolean_confusion(self):
        base = small_case(); invalid = []
        x = deepcopy(base); x['questions'][0]['selected'] = []; invalid.append(x)
        x = deepcopy(base); x['questions'][0]['options'][0]['correct'] = 1; invalid.append(x)
        x = deepcopy(base); x['questions'][1]['id'] = 's-good'; invalid.append(x)
        x = deepcopy(base); x['questions'][0]['selected'] = ['s-good', 's-bad']; invalid.append(x)
        x = deepcopy(base); x['questions'][1]['selected'] = ['m-a', 'm-a']; invalid.append(x)
        x = deepcopy(base); x['questions'][1]['selected'] = ['m-a'] * 9; invalid.append(x)
        x = deepcopy(base); x['questions'][1]['selected'] = ['x' * 81]; invalid.append(x)
        x = deepcopy(base); x['show_correction'] = 'false'; invalid.append(x)
        x = deepcopy(base); x['unexpected'] = True; invalid.append(x)
        for case in invalid:
            with self.assertRaises(ValueError): oracle.validate_case(case)

    def test_projection_does_not_claim_unobserved_fractions(self):
        result = {'score': {'n': '1', 'd': '2'}, 'percent': {'n': '25', 'd': '1'}, 'score_display': '0.50', 'percent_display': '25.00',
                  'passed': False, 'correct_count': 0, 'points': [{'n': '0', 'd': '1', 'display': '0.00', 'is_correct': False}, {'n': '1', 'd': '2', 'display': '0.50', 'is_correct': False}]}
        projected = oracle.observed_projection(result, small_case())
        self.assertNotIn('score', projected); self.assertNotIn('n', projected['points'][1]); self.assertEqual(projected['points'][1]['display'], '0.50')
        hidden = small_case(); hidden['show_correction'] = False
        self.assertIsNone(oracle.observed_projection(result, hidden)['points'])
        result['passed'] = 'false'
        with self.assertRaises(ValueError): oracle.observed_projection(result, small_case())

    def test_observation_refuses_missing_question_leak_and_numeric_bool(self):
        case = small_case(); case['show_correction'] = False
        actual = {'score': Decimal('.5'), 'maxScore': 2, 'percent': 25, 'passed': False, 'correctCount': 0, 'details': []}
        observed = oracle._observe(actual, case); self.assertEqual(observed['score_display'], '0.50'); self.assertIsNone(observed['points'])
        actual['details'] = [{'unexpected': 'leaked correction'}]
        with self.assertRaises(ValueError): oracle._observe(actual, case)
        actual['details'] = []; actual['score'] = True
        with self.assertRaises(ValueError): oracle._observe(actual, case)
        with self.assertRaises(ValueError): oracle._display(Decimal('0.005'))

    def test_invalid_collection_never_reads_or_launches_reference(self):
        with mock.patch('csharp_oracle._reference') as reference, mock.patch('csharp_oracle._Server') as server:
            result = oracle.collect_cases([], RUN)
            self.assertEqual(result['status'], 'REFUSED'); reference.assert_not_called(); server.assert_not_called()
        with self.assertRaises(ValueError): oracle.collect_cases([small_case()], oracle.REFERENCE / 'evidence')

    def test_start_failure_always_stops_owned_process(self):
        class BrokenServer:
            stopped = False
            def __init__(self, build, folder): pass
            def start(self): raise TimeoutError('Fixture readiness failure after spawn')
            def stop(self):
                BrokenServer.stopped = True
                return {'process_exited': True, 'scope': 'fixture only'}
        with mock.patch('csharp_oracle._reference', return_value=(None, {}, {'fixture': True})), mock.patch('csharp_oracle._Server', BrokenServer):
            result = oracle.collect_cases([small_case()], RUN)
        self.assertEqual(result['status'], 'INCONCLUSIVE'); self.assertTrue(BrokenServer.stopped); self.assertTrue(result['stop']['process_exited'])

    def test_source_unreadable_at_final_check_preserves_stop_and_evidence(self):
        source = Path(oracle.__file__).read_bytes()
        class BrokenServer:
            stopped = False
            def __init__(self, build, folder): pass
            def start(self): raise TimeoutError('Fixture readiness failure after spawn')
            def stop(self):
                BrokenServer.stopped = True
                return {'process_exited': True, 'scope': 'fixture only'}
        def read_source_then_fail():
            if not BrokenServer.stopped:
                return source
            raise OSError('Fixture producer source unavailable')
        with mock.patch('csharp_oracle._reference', return_value=(None, {}, {'fixture': True})), mock.patch('csharp_oracle._Server', BrokenServer), mock.patch('pathlib.Path.read_bytes', side_effect=read_source_then_fail) as read:
            result = oracle.collect_cases([small_case()], RUN)
            self.assertEqual(read.call_count, 2)
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertIn('Oracle source unavailable after collection', result['reason'])
        self.assertTrue(BrokenServer.stopped)
        self.assertTrue(result['stop']['process_exited'])
        folder = Path(result['evidence_dir'])
        self.assertEqual(json.loads((folder / 'report.json').read_text(encoding='utf-8')), result)
        self.assertEqual(json.loads((folder / 'exchanges.json').read_text(encoding='utf-8')), [])
        self.assertEqual((folder / 'oracle-source.py').read_bytes(), source)


def smoke():
    first = small_case(); second = deepcopy(first); second['grading'] = 'all-or-nothing'
    third = {'questions': [{'id': 'q', 'mode': 'multiple', 'options': [{'id': 'o' + str(i), 'correct': True} for i in range(7)], 'selected': ['o0']}],
             'grading': 'partial', 'passing_percent': '14.285714285714285714285714285', 'show_correction': False}
    report = oracle.collect_cases([first, second, third], RUN)
    expected = [
        {'score_display': '0.50', 'percent_display': '25.00', 'passed': False, 'correct_count': 0,
         'points': [{'display': '0.00', 'is_correct': False}, {'display': '0.50', 'is_correct': False}]},
        {'score_display': '0.00', 'percent_display': '0.00', 'passed': False, 'correct_count': 0,
         'points': [{'display': '0.00', 'is_correct': False}, {'display': '0.00', 'is_correct': False}]},
        {'score_display': '0.14', 'percent_display': '14.29', 'passed': True, 'correct_count': 0, 'points': None},
    ]
    result = {'status': 'PASS' if report['status'] == 'COLLECTED' and [r['observed'] for r in report['results']] == expected else 'FAILED',
              'oracle_report': str(Path(report['evidence_dir']) / 'report.json'), 'expected': expected, 'actual': [r['observed'] for r in report['results']],
              'scope': 'Three actual HTTP cases, manually specified expected presentations; 25-case differential corpus reserved for coordinator pipeline.'}
    oracle._save(RUN / 'http-smoke.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--http-smoke', action='store_true'); args = parser.parse_args()
    tests = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OracleTests))
    result = {'tests': tests.testsRun, 'failures': len(tests.failures), 'errors': len(tests.errors), 'skipped': len(tests.skipped), 'evidence_dir': str(RUN)}
    oracle._save(RUN / 'unit-results.json', result)
    if args.http_smoke and tests.wasSuccessful(): result['http_smoke'] = smoke()
    print(json.dumps(result))
    raise SystemExit(not tests.wasSuccessful() or result.get('http_smoke', {}).get('status', 'PASS') != 'PASS')
