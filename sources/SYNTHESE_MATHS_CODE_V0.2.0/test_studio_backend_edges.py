"""Author boundary checks against one explicitly supplied, already compiled MVC build."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import hashlib
import json
import traceback
import urllib.request
import urllib.error
import uuid

import mvc_runtime as runtime


def qualify(build):
    root = runtime.BASE / 'evidence' / ('studio-backend-edges-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    data = root / 'data'
    checks, requests, servers = [], [], []
    report = {'status': 'INCONCLUSIVE', 'evidence_dir': str(root), 'checks': checks,
              'build': str(Path(build['folder']) / 'build.json'),
              'scope': 'Real local MVC, exact rational thresholds, simultaneous writes, abrupt stop/recovery and explicit corruption refusal. No browser or migration claim.'}
    def check(name, condition):
        checks.append({'name': name, 'pass': bool(condition)})
        if not condition:
            raise AssertionError(name)
    def start(directory=data):
        server = runtime.StudioProcess(build, directory)
        servers.append(server)
        return server.start()
    def call(server, method, route, value=None, raw=None):
        payload = raw.encode('utf-8') if raw is not None else None if value is None else json.dumps(value).encode('utf-8')
        req = urllib.request.Request(server.url + route, data=payload, method=method,
                                     headers={'Content-Type': 'application/json', 'Origin': server.url})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read()
        result = json.loads(body) if body else None
        requests.append({'method': method, 'route': route, 'request': value, 'raw_request': raw, 'status': status, 'result': result})
        return status, result
    try:
        server = start()
        # Seven questions worth 1/7 each: internal score is exactly 1, despite
        # individual displayed points of .14. Thresholds straddle exact 100/7.
        template = {'title': 'Fractions exactes', 'config': {'grading': 'partial', 'passingPercent': 'THRESHOLD',
                    'shuffleQuestions': True, 'shuffleOptions': True, 'showCorrection': True},
                    'questions': [{'id': 'q' + str(i), 'text': 'Choix partiel ' + str(i), 'mode': 'multiple', 'explanation': 'Sept options correctes.',
                        'options': [{'id': 'q' + str(i) + '-o' + str(j), 'text': str(j), 'correct': True} for j in range(7)]} for i in range(7)]}
        for threshold, expected in [('14.285714285714285714285714285', True), ('14.285714285714285714285714286', False)]:
            raw = json.dumps(template).replace('"THRESHOLD"', threshold)
            status, quiz = call(server, 'POST', '/api/quizzes', raw=raw)
            check('Precise decimal threshold accepted ' + threshold, status == 201)
            request = {'quizId': quiz['id'], 'quizRevision': quiz['revision'], 'requestId': str(uuid.uuid4())}
            status, attempt = call(server, 'POST', '/api/attempts', request)
            check('Start exact-threshold attempt', status == 201)
            check('Snapshot order contains each question exactly once', {q['id'] for q in attempt['questions']} == {'q' + str(i) for i in range(7)})
            answers = [{'questionId': 'q' + str(i), 'optionIds': ['q' + str(i) + '-o0']} for i in range(7)]
            status, attempt = call(server, 'PUT', '/api/attempts/' + attempt['id'] + '/answers', {'revision': attempt['revision'], 'answers': answers})
            check('Save fractional answers', status == 200)
            status, done = call(server, 'POST', '/api/attempts/' + attempt['id'] + '/complete', {'revision': attempt['revision']})
            check('Exact score preserved, no sum of rounded points', status == 200 and done['result']['score'] == 1 and done['result']['percent'] == 14.29)
            check('Threshold uses exact fraction ' + threshold, done['result']['passed'] is expected)
            check('Partial score is not count of wholly correct questions', done['result']['correctCount'] == 0)
        status, studio = call(server, 'GET', '/api/studio')
        seed = next(q for q in studio['quizzes'] if q['id'] == 'seed-maths')
        request = {'quizId': seed['id'], 'quizRevision': seed['revision'], 'requestId': str(uuid.uuid4())}
        _, attempt = call(server, 'POST', '/api/attempts', request)
        aid = attempt['id']
        payloads = [{'revision': attempt['revision'], 'answers': [{'questionId': 'q1', 'optionIds': ['q1-o' + str(i)]}]} for i in (0, 1)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(call, server, 'PUT', '/api/attempts/' + aid + '/answers', payload) for payload in payloads]
            outcomes = [future.result() for future in futures]
        check('Simultaneous same-revision writes have one winner', sorted(status for status, _ in outcomes) == [200, 409])
        winning = next(result for status, result in outcomes if status == 200)
        _, observed = call(server, 'GET', '/api/attempts/' + aid)
        check('Only acknowledged winner is persisted', observed == winning)
        # A second directory is allowed; identity and attempts are local to it.
        other = start(root / 'separate-data')
        _, isolated = call(other, 'GET', '/api/studio')
        check('Distinct data directories can run independently', isolated['attempts'] == [])
        other.stop()
        # Abrupt stop after an acknowledged write: the committed receipt survives.
        server.process.kill(); server.process.wait(timeout=5); server.stop()
        pending = data / 'studio-state.json.review.pending'
        pending.write_text('{"notCommitted":true}', encoding='utf-8')
        server = start()
        status, replay = call(server, 'POST', '/api/attempts', request)
        check('Start idempotency survives abrupt process stop', status == 200 and replay == winning)
        check('Uncommitted pending file is preserved and not replayed', pending.read_text(encoding='utf-8') == '{"notCommitted":true}')
        _, after = call(server, 'GET', '/api/studio')
        check('Restart does not create an extra attempt', sum(a['id'] == aid for a in after['attempts']) == 1)
        server.stop()
        primary = data / 'studio-state.json'
        original = primary.read_bytes()
        backup = Path(str(primary) + '.backup')
        check('Atomic replacement retains previous backup', backup.is_file() and isinstance(json.loads(backup.read_bytes()), dict))
        corrupt = b'{broken-json\n'
        primary.write_bytes(corrupt)
        try:
            start(); check('Corrupt primary refuses startup', False)
        except RuntimeError:
            check('Corrupt primary refuses startup explicitly', 'STORAGE_INVALID' in ''.join(servers[-1].logs))
        check('Corrupt primary is never silently reset', primary.read_bytes() == corrupt)
        primary.write_bytes(original)
        server = start()
        _, restored = call(server, 'GET', '/api/attempts/' + aid)
        check('Explicit restoration recovers committed attempt', restored == winning)
        server.stop()
        held = data / 'primary-held.json'
        primary.rename(held)
        try:
            start(); check('Missing primary plus backup refuses initialization', False)
        except RuntimeError:
            check('Missing primary plus backup requires explicit recovery', 'STORAGE_INVALID' in ''.join(servers[-1].logs))
        check('No seed overwrites missing primary history', not primary.exists() and held.read_bytes() == original)
        held.rename(primary)
        report['status'] = 'PASS'
    except Exception as exc:
        report.update(reason=type(exc).__name__ + ': ' + str(exc), traceback=traceback.format_exc())
    finally:
        for server in servers:
            server.stop()
        runtime.save(root / 'requests.json', requests)
        runtime.save(root / 'result.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', required=True)
    args = parser.parse_args()
    result = qualify(json.loads(Path(args.build).read_bytes()))
    print(json.dumps({'status': result['status'], 'checks': len(result['checks']), 'reason': result.get('reason'), 'evidence_dir': result['evidence_dir']}))
    raise SystemExit(result['status'] != 'PASS')
