"""Real compiled Rust HTTP profile tests; explicit model/HTML fixtures, not MVC evidence."""
from copy import deepcopy
from pathlib import Path
import hashlib
import http.client
import itertools
import json
import os
import queue
import socket
import subprocess
import threading
import unittest
from unittest import mock
import uuid

import qcm_rust
from module_model import validate_module, revise_unit, SOURCE_FIELDS

RUN = qcm_rust.BASE / 'evidence' / ('qcm-rust-tests-' + uuid.uuid4().hex)
RUN.mkdir(parents=True)
HTML = '<!doctype html><html lang="fr"><title>Fixture explicite QCM</title><body>Épreuve 📐</body></html>'.encode('utf-8')
ASSETS = {'style.css': b'/* fixture only */ body { color: #123; }', 'app.js': b'// fixture only: no browser behavior claimed\n'}
DATA = {'title': 'Le petit QCM des maths', 'questions': [
    {'id': 'q1', 'text': 'Combien font 2 + 3 ?', 'options': ['4', '5', '6', '7'], 'correct': 1},
    {'id': 'q2', 'text': 'Quel est le carré de 4 ?', 'options': ['8', '12', '16', '20'], 'correct': 2},
    {'id': 'q3', 'text': 'Combien de côtés possède un triangle ?', 'options': ['2', '3', '4', '5'], 'correct': 1},
]}
DATA_BYTES = (json.dumps(DATA, ensure_ascii=False, indent=3) + '\r\n').encode('utf-8')


def fixture():
    p = lambda name, typ: {'kind': 'param', 'name': name, 'type': typ}
    n = lambda value: {'kind': 'constant', 'type': 'int', 'value': value}
    eq = {'kind': 'binary', 'type': 'bool', 'op': 'eq', 'left': p('answer', 'int'), 'right': p('correct', 'int')}
    def count(names, total=0):
        if not names:
            return n(total)
        return {'kind': 'ite', 'type': 'int', 'condition': p(names[0], 'bool'),
                'when_true': count(names[1:], total + 1), 'when_false': count(names[1:], total)}
    passed = {'kind': 'binary', 'type': 'bool', 'op': 'ge', 'left': p('score', 'int'), 'right': n(2)}
    source = '// Explicit model fixture; not Roslyn import evidence.'
    units = []
    for i, (name, signature, ret, body) in enumerate([
        ('IsCorrect', [('answer', 'int'), ('correct', 'int')], 'bool', eq),
        ('Score', [('a', 'bool'), ('b', 'bool'), ('c', 'bool')], 'int', count('abc')),
        ('Passed', [('score', 'int')], 'bool', passed),
    ]):
        units.append({'id': 'u' + str(i), 'name': name, 'qualified_name': 'QcmDemo.QuizRules.' + name,
                      'parameters': [{'name': k, 'type': t} for k, t in signature], 'return_type': ret,
                      'body': body, 'contract': None})
    return validate_module({'schema_version': 'mia-module-1', 'semantic_profile': 'pure-int32-bool-call-1',
                            'module_id': 'qcm-test-fixture', 'name': 'Explicit QCM fixture', 'parent_revision_id': None,
                            'intent': 'Fixture for generated Rust and HTTP testing, no MVC claim',
                            'provenance': {'source_text': source, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'import_id': 'explicit-fixture'},
                            'units': units})


def build(module=None, **changes):
    values = dict(module=module or fixture(), rendered_html=HTML, data=deepcopy(DATA), assets=dict(ASSETS), output=RUN / 'builds', data_bytes=DATA_BYTES)
    values.update(changes)
    return qcm_rust.build_qcm_rust(**values)


class Server:
    def __init__(self, receipt):
        self.receipt = receipt
        self.process = None
    def __enter__(self):
        receipt = self.receipt
        assert receipt['status'] == 'COMPILED', receipt
        assert hashlib.sha256(Path(receipt['executable']).read_bytes()).hexdigest() == receipt['executable_sha256']
        folder = Path(receipt['folder'])
        for name, expected in receipt['source_files_sha256'].items():
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
        env = dict(os.environ)
        env['PATH'] = os.pathsep.join(receipt['toolchain']['path_entries'] + [env.get('PATH', '')])
        self.process = subprocess.Popen([receipt['executable'], '0'], cwd=folder, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        messages = queue.Queue()
        threading.Thread(target=lambda: messages.put(self.process.stdout.readline()), daemon=True).start()
        try:
            line = messages.get(timeout=5).strip()
            assert line.startswith('READY '), line
            self.port = int(line.split()[1])
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise
    def get(self, path, method='GET'):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=4)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()
    def raw(self, request):
        with socket.create_connection(('127.0.0.1', self.port), timeout=4) as stream:
            stream.sendall(request)
            stream.shutdown(socket.SHUT_WR)
            pieces = []
            while True:
                try:
                    data = stream.recv(65536)
                except ConnectionResetError:
                    # Windows may reset after a rejection with unread body bytes.
                    # Still require an actual complete HTTP status below.
                    break
                if not data:
                    break
                pieces.append(data)
        return b''.join(pieces)
    def __exit__(self, *args):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait(timeout=3)
            self.process.stdout.close(); self.process.stderr.close()


class QcmRustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = fixture()
        cls.before = deepcopy(cls.original)
        cls.base = build(cls.original)
        if cls.base['status'] != 'COMPILED':
            raise AssertionError(cls.base)
        changed = revise_unit(cls.original, 'u2', {'schema_version': 'mia-math-spec-1', 'name': 'Require all three correct',
                    'parameters': [{'name': 'score', 'type': 'int'}], 'return_type': 'bool',
                    'precondition': 'True', 'postcondition': 'result == (score == 3)'}, 'Require 3/3 instead of 2/3 deliberately')
        if changed['status'] != 'REVISED':
            raise AssertionError(changed)
        cls.revision = changed
        cls.revised = build(changed['module'])
        if cls.revised['status'] != 'COMPILED':
            raise AssertionError(cls.revised)
        qcm_rust._save(RUN / 'builds-used.json', {'original': cls.base, 'revision': cls.revised, 'change': changed['change']})

    def test_all_64_combinations_and_intentional_revision(self):
        evidence = []
        for label, receipt, threshold in [('original', self.base, 2), ('revised', self.revised, 3)]:
            with Server(receipt) as server:
                for answers in itertools.product(range(4), repeat=3):
                    status, headers, raw = server.get('/api/quiz/grade?' + '&'.join(k + '=' + str(v) for k, v in zip('abc', answers)))
                    correct = [a == c for a, c in zip(answers, (1, 2, 1))]
                    score = sum(correct)
                    expected = {'score': score, 'total': 3, 'passed': score >= threshold, 'correct': correct, 'expected': [1, 2, 1]}
                    self.assertEqual(status, 200); self.assertEqual(headers.get('Cache-Control'), 'no-store')
                    self.assertEqual(json.loads(raw), expected)
                    evidence.append({'version': label, 'answers': answers, 'actual': json.loads(raw), 'expected': expected})
        qcm_rust._save(RUN / 'http-128-cases.json', evidence)
        self.assertEqual(self.original, self.before)
        self.assertEqual(self.revision['module']['parent_revision_id'], self.original['revision_id'])

    def test_static_bytes_public_questions_and_meta(self):
        with Server(self.base) as server:
            for route, body in [('/', HTML), ('/style.css', ASSETS['style.css']), ('/app.js', ASSETS['app.js'])]:
                status, _, actual = server.get(route); self.assertEqual((status, actual), (200, body))
            status, headers, body = server.get('/api/quiz/questions')
            self.assertEqual(status, 200); self.assertEqual(headers.get('Cache-Control'), 'no-store')
            public = json.loads(body); self.assertEqual(public['title'], DATA['title'])
            self.assertEqual(public['questions'], [{k: q[k] for k in ('id', 'text', 'options')} for q in DATA['questions']])
            self.assertTrue(all('correct' not in question for question in public['questions']))
            self.assertEqual(json.loads(server.get('/api/meta')[2]), {'language': 'Rust', 'framework': 'Rust std', 'profile': 'qcm-mvc-1'})
        folder = Path(self.base['folder'])
        self.assertEqual((folder / 'questions.json').read_bytes(), DATA_BYTES)

    def test_query_order_valid_and_malformed_queries_rejected(self):
        with Server(self.base) as server:
            self.assertEqual(json.loads(server.get('/api/quiz/grade?c=1&a=1&b=2')[2])['score'], 3)
            for query in ['', '?', '?a=1&b=2', '?a=1&b=2&c=1&d=0', '?a=1&b=2&c=1&a=0', '?a=01&b=2&c=1',
                          '?a=4&b=2&c=1', '?a=-1&b=2&c=1', '?a=+1&b=2&c=1', '?a=%31&b=2&c=1', '?%61=1&b=2&c=1',
                          '?a=1&b=2&c=1&', '?a=1;b=2;c=1', '?a=1&b=2&c=', '?a=1&b=2&c=1=0', '?A=1&b=2&c=1']:
                with self.subTest(query=query):
                    status, headers, raw = server.get('/api/quiz/grade' + query)
                    self.assertEqual(status, 400); self.assertEqual(json.loads(raw), {'error': 'INVALID_ANSWERS'})
                    self.assertEqual(headers.get('Cache-Control'), 'no-store')
            for route in ['/?', '/?a=1', '/style.css?x=1', '/app.js?', '/api/meta?', '/api/quiz/questions?x=1']:
                self.assertEqual(server.get(route)[0], 400)

    def test_methods_unknown_paths_and_raw_protocol_errors(self):
        with Server(self.base) as server:
            for method in ['POST', 'HEAD', 'PUT']:
                for route in ['/', '/style.css', '/app.js', '/api/meta', '/api/quiz/questions', '/api/quiz/grade?a=1&b=2&c=1']:
                    status, headers, _ = server.get(route, method); self.assertEqual(status, 405); self.assertEqual(headers.get('Allow'), 'GET')
            for route in ['/questions.json', '/rules.rs', '/unknown', '/%2e%2e/Rules.cs', '/API/meta']:
                self.assertEqual(server.get(route)[0], 404)
            for request in [b'bad\r\n\r\n', b'GET / HTTP/1.1\r\nBroken\r\n\r\n',
                            b'GET /api/meta HTTP/1.1\r\nContent-Length: 2\r\n\r\nab',
                            b'GET /api/meta HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n',
                            b'GET / HTTP/1.1\r\nX: ' + b'a' * 8200 + b'\r\n\r\n']:
                self.assertTrue(server.raw(request).startswith(b'HTTP/1.1 400 '))

    def test_builder_rejects_wrong_signatures_and_extra_units(self):
        for kind in ['name', 'type', 'extra']:
            model = {k: deepcopy(self.original[k]) for k in SOURCE_FIELDS}
            if kind == 'name':
                model['units'][0]['qualified_name'] = 'Other.IsCorrect'
            elif kind == 'type':
                model['units'][2]['parameters'][0]['name'] = 'value'
                model['units'][2]['body']['left']['name'] = 'value'; model['units'][2]['contract'] = None
            else:
                unit = deepcopy(model['units'][2]); unit.update(id='u3', name='Extra', qualified_name='QcmDemo.QuizRules.Extra'); model['units'].append(unit)
            with mock.patch('qcm_rust.module_backends._process') as process:
                self.assertEqual(build(model)['status'], 'REFUSED'); process.assert_not_called()

    def test_builder_rejects_bad_data_assets_and_bytes(self):
        cases = [dict(assets={**ASSETS, '../extra': b'extra'}), dict(assets={'style.css': ASSETS['style.css']}),
                 dict(rendered_html='not bytes'), dict(rendered_html=b'\xff'), dict(data_bytes=b'{}'),
                 dict(data_bytes=DATA_BYTES.replace(b'"correct": 1', b'"correct": true', 1))]
        for correct in [True, 4, -1, '1']:
            data = deepcopy(DATA); data['questions'][0]['correct'] = correct
            cases.append(dict(data=data, data_bytes=json.dumps(data).encode()))
        for change in cases:
            with mock.patch('qcm_rust.module_backends._process') as process:
                self.assertEqual(build(**change)['status'], 'REFUSED'); process.assert_not_called()

    def test_missing_compiler_timeout_and_failure_have_no_execution_claim(self):
        with mock.patch('qcm_rust.module_backends._toolchain', return_value=None):
            report = build(); self.assertEqual(report['status'], 'NON_COMPILE'); self.assertEqual(report['execution_status'], 'NOT_EXECUTED')
        with mock.patch('qcm_rust.module_backends._process', side_effect=subprocess.TimeoutExpired(['rustc'], 45)):
            self.assertEqual(build()['status'], 'INCONCLUSIVE')
        with mock.patch('qcm_rust.module_backends._process', return_value={'exit_code': 1, 'stdout': '', 'stderr': 'fixture compiler failure'}):
            report = build(); self.assertEqual(report['status'], 'FAILED'); self.assertEqual(report['execution_status'], 'NOT_EXECUTED')

    def test_sources_modified_during_compilation_are_detected(self):
        def change(command, folder, timeout, **kwargs):
            (folder / 'app.js').write_bytes(b'changed fixture')
            return {'exit_code': 0, 'stdout': '', 'stderr': ''}
        with mock.patch('qcm_rust.module_backends._process', side_effect=change):
            report = build(); self.assertEqual(report['status'], 'FAILED'); self.assertIn('changed', report['reason'])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__)))
    report = {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
              'evidence_dir': str(RUN), 'scope': 'Real Rust compilation and local HTTP; 64 original + 64 revised answers; independent sum/count oracle. Explicit module/HTML fixtures, not actual Razor/browser/source-project qualification.'}
    qcm_rust._save(RUN / 'results.json', report)
    print(json.dumps(report))
    raise SystemExit(not result.wasSuccessful())
