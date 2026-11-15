"""Read-only use of the delivered C# build, with new isolated HTTP campaigns.

This oracle observes public numeric presentations and decisions. C# internal
rational numerators/denominators are not exposed by this interface.
"""
from pathlib import Path
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

BASE = Path(__file__).resolve().parent
REFERENCE = BASE.parent / 'SYNTHESE_MATHS_CODE_V0.2.0'
RELEASE_SHA = '281cb997210c3c0217537f784ec135b630c4677a123af1de7dbf2820cbb9c4cc'
RUNTIME_SHA = '92188186957566be6b0d443261ca5fe1f26f241396f2988d9b718d7e8a877173'
BUILD_RELATIVE = 'runtime/MVC-41d468e0b26a491cb98758894eb76fa6/build.json'
BUILD_SHA = '2b27193026984aa21ba5b1b2d12a6326c0e9ad29fe441a206b1ecee84520e520'
MAX_CASES = 64
MAX_RESPONSE = 2_000_000
MAX_SECONDS = 240
ID = re.compile(r'[A-Za-z0-9-]{1,80}\Z')
THRESHOLD = re.compile(r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,28})?\Z')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    """Exact JSON numbers for Decimal, including thresholds beyond float precision."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('Non-finite decimal')
        return format(value, 'f')
    if type(value) is dict:
        return '{' + ','.join(json.dumps(k, ensure_ascii=False) + ':' + _json(value[k]) for k in sorted(value)) + '}'
    if type(value) in (list, tuple):
        return '[' + ','.join(_json(x) for x in value) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _save(path, value):
    Path(path).write_text(_json(value) + '\n', encoding='utf-8')


def case_sha256(case):
    """UTF-8 JSON, sorted keys, compact separators, Unicode preserved; list order kept."""
    return _sha(_json(case).encode('utf-8'))


def validate_case(case):
    """Independent transport admission matching the actual completed-quiz domain."""
    if type(case) is not dict or set(case) != {'questions', 'grading', 'passing_percent', 'show_correction'}:
        raise ValueError('Closed scoring case required')
    if case['grading'] not in ('partial', 'all-or-nothing') or type(case['show_correction']) is not bool:
        raise ValueError('Invalid scoring configuration')
    threshold = case['passing_percent']
    if type(threshold) is not str or len(threshold) > 32 or not THRESHOLD.fullmatch(threshold):
        raise ValueError('Exact decimal threshold required')
    value = Decimal(threshold)
    digits = threshold.replace('.', '').lstrip('0') or '0'
    if '.' in threshold:
        digits = digits.rstrip('0') or '0'
    if not 0 <= value <= 100 or int(digits) > 79228162514264337593543950335:
        raise ValueError('Threshold is outside exact C# Decimal domain')
    questions = case['questions']
    if type(questions) is not list or not 1 <= len(questions) <= 50:
        raise ValueError('One to fifty questions required per HTTP case')
    ids = set()
    def identity(value):
        if type(value) is not str or not ID.fullmatch(value) or value in ids:
            raise ValueError('Invalid or repeated question/option ID')
        ids.add(value)
    for q in questions:
        if type(q) is not dict or set(q) != {'id', 'mode', 'options', 'selected'} or q['mode'] not in ('single', 'multiple'):
            raise ValueError('Invalid question')
        identity(q['id'])
        if type(q['options']) is not list or not 2 <= len(q['options']) <= 8:
            raise ValueError('Two to eight options required')
        for option in q['options']:
            if type(option) is not dict or set(option) != {'id', 'correct'} or type(option['correct']) is not bool:
                raise ValueError('Invalid option')
            identity(option['id'])
        correct = sum(o['correct'] for o in q['options'])
        if correct < 1 or q['mode'] == 'single' and correct != 1:
            raise ValueError('Invalid correct-answer set')
        selected = q['selected']
        if type(selected) is not list or not 1 <= len(selected) <= 8 or any(type(x) is not str or len(x) > 80 for x in selected) or len(set(selected)) != len(selected):
            raise ValueError('Nonempty distinct selected IDs required')
        if not set(selected) <= {o['id'] for o in q['options']} or q['mode'] == 'single' and len(selected) != 1:
            raise ValueError('Invalid selected-answer set')
    return case


def generate_cases():
    """Deterministic count-complete representatives, grouped by fifty questions.

    IDs/text are representatives, not enumeration of all possible input strings.
    Every admissible (option count, correct count, selected good, selected bad)
    tuple for multiple choice appears under each grading; single has both cases.
    """
    questions = []
    def question(n, good, selected_good, selected_bad, mode='multiple'):
        qid = 'q' + str(len(questions))
        options = [{'id': qid + '-o' + str(i), 'correct': i < good} for i in range(n)]
        q = {'id': qid, 'mode': mode, 'options': options,
             'selected': [o['id'] for o in options[:selected_good] + options[good:good + selected_bad]]}
        questions.append(q)
    for count in range(2, 9):
        for good in range(1, count + 1):
            for selected_good in range(good + 1):
                for selected_bad in range(count - good + 1):
                    if selected_good + selected_bad:
                        question(count, good, selected_good, selected_bad)
        question(count, 1, 1, 0, 'single')
        question(count, 1, 0, 1, 'single')
    cases = []
    for grading in ('all-or-nothing', 'partial'):
        for start in range(0, len(questions), 50):
            cases.append({'questions': json.loads(_json(questions[start:start + 50])), 'grading': grading, 'passing_percent': '60', 'show_correction': True})
    boundary = {'id': 'boundary', 'mode': 'multiple', 'options': [{'id': 'boundary-o' + str(i), 'correct': True} for i in range(7)], 'selected': ['boundary-o0']}
    for threshold in ('0', '100', '14.285714285714285714285714285', '14.285714285714285714285714286'):
        cases.append({'questions': [json.loads(_json(boundary))], 'grading': 'partial', 'passing_percent': threshold, 'show_correction': True})
    aggregate = []
    for i in range(7):
        q = json.loads(_json(boundary)); q['id'] = 'aggregate-' + str(i)
        q['options'] = [{'id': q['id'] + '-o' + str(j), 'correct': True} for j in range(7)]
        q['selected'] = [q['options'][0]['id']]; aggregate.append(q)
    cases.append({'questions': aggregate, 'grading': 'partial', 'passing_percent': '14.285714285714285714285714285', 'show_correction': True})
    permuted = json.loads(_json(cases[-1]))
    permuted['questions'].reverse()
    for q in permuted['questions']:
        q['options'].reverse(); q['selected'].reverse()
    cases.append(permuted)
    hidden = json.loads(_json(cases[0])); hidden['show_correction'] = False; cases.append(hidden)
    for case in cases:
        validate_case(case)
    if len(cases) > MAX_CASES:
        raise ValueError('Corpus exceeds oracle campaign bound')
    return cases


def observed_projection(result, case):
    """Project a model/Rust result onto fields actually observable over C# HTTP."""
    validate_case(case)
    for name in ('score_display', 'percent_display'):
        if type(result.get(name)) is not str or not re.fullmatch(r'[0-9]+\.[0-9]{2}', result[name]):
            raise ValueError('Invalid model display: ' + name)
    if type(result.get('passed')) is not bool or type(result.get('correct_count')) is not int:
        raise ValueError('Invalid model decision/count')
    points = result.get('points')
    if type(points) is not list or len(points) != len(case['questions']):
        raise ValueError('Model point list has wrong length')
    projected = []
    for point in points:
        if type(point.get('display')) is not str or not re.fullmatch(r'[0-9]+\.[0-9]{2}', point['display']) or type(point.get('is_correct')) is not bool:
            raise ValueError('Invalid model question presentation')
        projected.append({'display': point['display'], 'is_correct': point['is_correct']})
    return {k: result[k] for k in ('score_display', 'percent_display', 'passed', 'correct_count')} | {'points': projected if case['show_correction'] else None}


def _reference():
    release = REFERENCE / 'studio-release-manifest.json'
    runtime = REFERENCE / 'mvc_runtime.py'
    build_path = REFERENCE / BUILD_RELATIVE
    for path, expected in ((release, RELEASE_SHA), (runtime, RUNTIME_SHA), (build_path, BUILD_SHA)):
        if _sha(path.read_bytes()) != expected:
            raise ValueError('Delivered reference identity changed: ' + path.name)
    spec = importlib.util.spec_from_file_location('_mia_v2_readonly_runtime', runtime)
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    build = json.loads(build_path.read_bytes())
    helper.check_build(build)  # Read-only published and full SDK inventories.
    if helper.inventory(REFERENCE / 'qcm_studio') != build['source_files_sha256']:
        raise ValueError('Delivered C# source differs from its final build')
    return helper, build, {'release_sha256': RELEASE_SHA, 'runtime_sha256': RUNTIME_SHA, 'build_receipt_sha256': BUILD_SHA,
                         'build_receipt': str(build_path), 'source_files_sha256': build['source_files_sha256'],
                         'published_files_sha256': build['published_files_sha256'], 'toolchain': build['toolchain']}


class _Server:
    def __init__(self, build, folder):
        self.build, self.folder = build, folder
        self.process = None; self.lines = []; self.reader = None; self.url = None
    def start(self):
        env = dict(os.environ)
        for key in ('DOTNET_STARTUP_HOOKS', 'DOTNET_ADDITIONAL_DEPS', 'DOTNET_SHARED_STORE', 'MSBuildSDKsPath', 'ASPNETCORE_URLS',
                    'ASPNETCORE_HOSTINGSTARTUPASSEMBLIES', 'ASPNETCORE_ENVIRONMENT', 'DOTNET_ENVIRONMENT'):
            env.pop(key, None)
        for name in ('data', 'appdata', 'localappdata', 'dotnet-home', 'temp'):
            (self.folder / name).mkdir()
        env.update(MIA_QCM_DATA_DIR=str(self.folder / 'data'), DOTNET_ROOT=self.build['toolchain']['root'],
                   DOTNET_CLI_HOME=str(self.folder / 'dotnet-home'), DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1',
                   DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='false', ASPNETCORE_ENVIRONMENT='Production',
                   APPDATA=str(self.folder / 'appdata'), LOCALAPPDATA=str(self.folder / 'localappdata'),
                   TMP=str(self.folder / 'temp'), TEMP=str(self.folder / 'temp'))
        messages = queue.Queue()
        self.process = subprocess.Popen([self.build['toolchain']['path'], self.build['assembly']], cwd=self.build['published'], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        def read():
            with (self.folder / 'server.log').open('w', encoding='utf-8') as log:
                for line in self.process.stdout:
                    log.write(line); log.flush(); self.lines.append(line)
                    if len(self.lines) > 2000: self.lines.pop(0)
                    if re.fullmatch(r'READY [0-9]+\s*', line): messages.put(line.strip())
            messages.put(None)
        self.reader = threading.Thread(target=read, daemon=True); self.reader.start()
        line = messages.get(timeout=25)
        if line is None: raise RuntimeError('Reference server stopped before READY; inspect server.log')
        port = int(line.split()[1])
        if not 1 <= port <= 65535: raise ValueError('Invalid server port')
        self.url = 'http://127.0.0.1:' + str(port)
        return self
    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=5)
                except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=5)
            if self.reader: self.reader.join(timeout=2)
            if self.process.stdout: self.process.stdout.close()
        result = {'pid': self.process.pid if self.process else None, 'url': self.url,
                  'process_exited': self.process is None or self.process.poll() is not None,
                  'exit_code': self.process.returncode if self.process else None, 'log': str(self.folder / 'server.log')}
        _save(self.folder / 'stop.json', result)
        return result


def _display(value):
    if type(value) not in (int, Decimal): raise ValueError('C# numeric output is not a JSON number')
    value = Decimal(value)
    if value < 0 or value != value.quantize(Decimal('.01')): raise ValueError('C# exposed unexpected precision')
    return format(value, '.2f')


def _observe(result, case):
    if type(result) is not dict or type(result.get('passed')) is not bool or type(result.get('correctCount')) is not int or type(result.get('maxScore')) is not int or result['maxScore'] != len(case['questions']):
        raise ValueError('Unexpected C# result shape')
    details = result.get('details')
    if type(details) is not list: raise ValueError('C# details are not a list')
    points = None
    if case['show_correction']:
        if len(details) != len(case['questions']): raise ValueError('C# details omitted a question')
        points = []
        for q, detail in zip(case['questions'], details):
            if detail.get('questionId') != q['id'] or type(detail.get('maxPoints')) is not int or detail['maxPoints'] != 1 or type(detail.get('isCorrect')) is not bool:
                raise ValueError('Unexpected C# question identity or type')
            if detail.get('selectedOptionIds') != q['selected'] or detail.get('correctOptionIds') != [o['id'] for o in q['options'] if o['correct']]:
                raise ValueError('C# correction does not match the captured snapshot')
            points.append({'display': _display(detail['points']), 'is_correct': detail['isCorrect']})
    elif details:
        raise ValueError('C# exposed correction despite showCorrection=false')
    return {'score_display': _display(result['score']), 'percent_display': _display(result['percent']), 'passed': result['passed'],
            'correct_count': result['correctCount'], 'points': points}


def collect_cases(cases, output_dir):
    parent = Path(output_dir).resolve()
    if not parent.is_relative_to(BASE): raise ValueError('Oracle output must remain in the V0.3 workspace')
    folder = parent / ('csharp-oracle-' + uuid.uuid4().hex); folder.mkdir(parents=True, exist_ok=False)
    producer = Path(__file__).read_bytes()
    (folder / 'oracle-source.py').write_bytes(producer)
    report = {'status': 'REFUSED', 'results': [], 'probes': [], 'evidence_dir': str(folder), 'exact_internal_rationals_observed': False,
              'producer_files_sha256': {'csharp_oracle.py': _sha(producer)},
              'scope': 'Actual delivered C# MVC HTTP score presentations/decisions; no C# fraction introspection, no Rust state-migration claim.'}
    server = None; exchanges = []; started = time.monotonic()
    try:
        if type(cases) is not list or not 1 <= len(cases) <= MAX_CASES: raise ValueError('One to sixty-four cases required')
        for case in cases: validate_case(case)
        _save(folder / 'cases.json', cases)
        helper, build, reference = _reference(); report['reference'] = reference
        _save(folder / 'reference-build.json', build)
        report['status'] = 'INCONCLUSIVE'
        server = _Server(build, folder)
        server.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def call(method, path, payload=None, expected=200, html=False):
            if time.monotonic() - started > MAX_SECONDS: raise TimeoutError('Oracle campaign budget exceeded')
            raw_request = None if payload is None else _json(payload).encode('utf-8')
            request = urllib.request.Request(server.url + path, data=raw_request, method=method,
                      headers={'Content-Type': 'application/json', 'Origin': server.url})
            seq = len(exchanges); item = folder / ('http-' + str(seq).zfill(4)); item.mkdir()
            if raw_request is not None: (item / 'request.json').write_bytes(raw_request)
            try:
                with opener.open(request, timeout=10) as response:
                    status, headers, body = response.status, dict(response.headers), response.read(MAX_RESPONSE + 1)
            except urllib.error.HTTPError as exc:
                status, headers, body = exc.code, dict(exc.headers), exc.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE: raise ValueError('C# HTTP response exceeded bound')
            (item / ('response.html' if html else 'response.json')).write_bytes(body)
            exchange = {'method': method, 'path': path, 'status': status, 'expected_status': expected, 'headers': headers,
                        'request_sha256': _sha(raw_request) if raw_request else None, 'response_sha256': _sha(body), 'folder': str(item)}
            exchanges.append(exchange); _save(item / 'exchange.json', exchange)
            if status != expected: raise RuntimeError('Unexpected C# HTTP status ' + str(status) + ' for ' + path)
            return body if html else json.loads(body, parse_float=Decimal)
        home = call('GET', '/', html=True)
        if b'<html' not in home: raise ValueError('Actual MVC page not rendered')
        for index, case in enumerate(cases):
            config = {'grading': case['grading'], 'passingPercent': Decimal(case['passing_percent']), 'shuffleQuestions': False,
                      'shuffleOptions': False, 'showCorrection': case['show_correction']}
            quiz_body = {'title': 'Oracle scoring case ' + str(index), 'config': config,
                         'questions': [{'id': q['id'], 'text': 'Question ' + q['id'], 'mode': q['mode'], 'explanation': 'Oracle fixture ' + q['id'],
                                        'options': [{'id': o['id'], 'text': 'Option ' + o['id'], 'correct': o['correct']} for o in q['options']]} for q in case['questions']]}
            quiz = call('POST', '/api/quizzes', quiz_body, 201)
            creation = {'quizId': quiz['id'], 'quizRevision': quiz['revision'], 'requestId': str(uuid.uuid4())}
            attempt = call('POST', '/api/attempts', creation, 201); aid = attempt['id']
            if any('explanation' in q or any('correct' in o for o in q['options']) for q in attempt['questions']):
                raise ValueError('C# leaked a correction before completion')
            if index == 0:
                changed = json.loads(_json(quiz_body), parse_float=Decimal); changed['revision'] = quiz['revision']; changed['title'] += ' changed later'
                changed['config']['showCorrection'] = not case['show_correction']; changed['config']['passingPercent'] = 100 if case['passing_percent'] != '100' else 0
                updated = call('PUT', '/api/quizzes/' + quiz['id'], changed)
                if updated['revision'] != quiz['revision'] + 1 or updated['title'] != changed['title']:
                    raise ValueError('C# edit was not observed')
                report['probes'].append({'name': 'quiz_edited_after_attempt_start', 'revision_before': quiz['revision'], 'revision_after': updated['revision']})
            answers = [{'questionId': q['id'], 'optionIds': q['selected']} for q in case['questions']]
            saved = call('PUT', '/api/attempts/' + aid + '/answers', {'revision': attempt['revision'], 'answers': answers})
            done = call('POST', '/api/attempts/' + aid + '/complete', {'revision': saved['revision']})
            if done['quizRevision'] != quiz['revision'] or done['config'] != config: raise ValueError('C# attempt snapshot changed')
            observed = _observe(done['result'], case)
            report['results'].append({'index': index, 'case_sha256': case_sha256(case), 'status': 'OBSERVED',
                                      'observed': observed, 'quiz_id': quiz['id'], 'attempt_id': aid, 'result': done['result']})
            if index == 0:
                repeated = call('POST', '/api/attempts/' + aid + '/complete', {'revision': saved['revision']})
                if repeated != done: raise ValueError('C# completion replay changed the receipt')
                archived = call('POST', '/api/quizzes/' + quiz['id'] + '/archive', {'revision': updated['revision']})
                if archived['archived'] is not True: raise ValueError('C# archive was not observed')
                replay = call('POST', '/api/attempts', creation)
                if replay != done: raise ValueError('C# creation replay failed after edit/archive')
                report['probes'].append({'name': 'snapshot_and_complete_start_replays_after_edit_archive', 'status': 'PASS'})
        scores = call('GET', '/api/scores')
        entries = [entry for group in scores['groups'] for entry in group['entries']]
        if len(entries) != len(cases) or {x['attemptId'] for x in entries} != {x['attempt_id'] for x in report['results']}:
            raise ValueError('Unexpected C# score entries after replay')
        report['probes'].append({'name': 'one_score_entry_per_attempt', 'status': 'PASS', 'entries': len(entries)})
        helper.check_build(build)
        if helper.inventory(REFERENCE / 'qcm_studio') != build['source_files_sha256']: raise ValueError('C# source changed during collection')
        report['status'] = 'COLLECTED'
    except Exception as exc:
        report['reason'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if server is not None:
            report['stop'] = server.stop()
            if not report['stop']['process_exited']: report['status'] = 'INCONCLUSIVE'
        try:
            if Path(__file__).read_bytes() != producer:
                report.update(status='INCONCLUSIVE', reason='Oracle source changed during collection')
        except OSError as exc:
            report.update(status='INCONCLUSIVE', reason='Oracle source unavailable after collection: ' + type(exc).__name__ + ': ' + str(exc))
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['http_exchanges'] = len(exchanges)
        _save(folder / 'exchanges.json', exchanges); _save(folder / 'report.json', report)
    return report
