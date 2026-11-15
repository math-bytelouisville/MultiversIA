"""Finite, separately attributed checks of model, generated code and admitted source."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import time
import uuid

import code_backends as legacy
import module_backends as foundation
import module_model
import math_core

BASE = Path(__file__).resolve().parent
PREVIOUS = BASE.parent / 'SYNTHESE_MATHS_CODE_V0.2.0'
LOW, HIGH = -2147483648, 2147483647
LANGUAGES = {'python': '.py', 'csharp': '.cs', 'rust': '.rs'}
MAX_OUTPUT = 4 * 1024 * 1024
MAX_REQUEST = 16 * 1024 * 1024
LIMITATIONS = [
    'Tests finis, au plus 512 entrées par fonction ; aucune preuve universelle des Int32.',
    'Oracle d’appels distinct de l’émetteur ; validation des types et contrats issue des fondations communes.',
    'Sources Rust et C# exécutées seulement après réanalyse complète du profil fermé ; aucun programme général admis.',
    'Accord au modèle, respect des contrats et accord à la source sont des résultats distincts.',
    'Délais et répertoires contrôlés par application ; aucune isolation système contre un binaire hostile.',
    'Compilateurs déjà présents, aucun téléchargement ni changement du PATH global.',
]


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _text_sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _save(path, value):
    legacy._save(Path(path), value)


def _owned(path):
    path = Path(path).absolute()
    if not path.resolve().is_relative_to(BASE):
        raise ValueError('Verification output must stay in V0.5')
    for part in (path, *path.parents):
        if part == BASE.parent: break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Linked output folder refused')
    return path.resolve()


def _load(path, maximum=2_000_000):
    with Path(path).open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum: raise ValueError('JSON input bound')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def _foundation_hashes():
    receipt = _load(BASE / 'FOUNDATION_V050.json')
    if (type(receipt) is not dict or receipt.get('schema') != 'mia-local-foundations-1'
            or receipt.get('version') != '0.5.0'):
        raise ValueError('Invalid V0.5 foundation manifest')
    expected = receipt['files_sha256']
    names = {'module_model.py', 'math_core.py', 'module_backends.py', 'code_backends.py', 'legacy_bridge.py', 'module_import.ps1'}
    if type(expected) is not dict or set(expected) != names:
        raise ValueError('Foundation inventory does not match the shared contract')
    actual = {name: _sha(BASE / name) for name in names}
    if actual != expected: raise ValueError('A V0.5 foundation changed after its content freeze')
    return actual


def _toolchain(language):
    if language != 'rust':
        tool = legacy._find_tool(language)
        if tool is None: return None
        return {'path': str(tool), 'extra_arguments': [], 'path_entries': [],
                'files_sha256': {str(tool): _sha(tool)}, 'source': 'existing_local_tool'}
    manifest_path = PREVIOUS / 'toolchains/rust/manifest.json'
    if not manifest_path.is_file(): return None
    manifest = _load(manifest_path)
    if (type(manifest) is not dict or manifest.get('schema_version') != 'mia-rust-toolchain-1'
            or manifest.get('status') != 'QUALIFIED_LOCAL' or type(manifest.get('qualification')) is not dict
            or manifest['qualification'].get('status') != 'COMPILED_AND_EXECUTED'):
        raise ValueError('Rust toolchain is not qualified')
    root = (PREVIOUS / 'toolchains/rust/install').resolve()
    tool = root / 'bin/rustc.exe'
    if Path(manifest['sysroot']).resolve() != root or Path(manifest['rustc_path']).resolve() != tool:
        raise ValueError('Unexpected Rust compiler path')
    linker = root / 'lib/rustlib/x86_64-pc-windows-gnu/bin/self-contained/x86_64-w64-mingw32-gcc.exe'
    arguments = ['-C', 'linker=' + str(linker), '-C', 'link-self-contained=yes']
    paths = [str(linker.parent), str(root / 'bin')]
    if manifest['extra_arguments'] != arguments or manifest['path_entries'] != paths:
        raise ValueError('Unexpected Rust flags or child paths')
    inventory = manifest.get('files_sha256')
    if type(inventory) is not dict or not 1 <= len(inventory) <= 4096:
        raise ValueError('Invalid Rust inventory')
    expected = {}
    for relative, digest in inventory.items():
        if (type(relative) is not str or type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest)
                or Path(relative).is_absolute() or '..' in Path(relative).parts or ':' in relative):
            raise ValueError('Invalid Rust inventory entry')
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or str(path) in expected:
            raise ValueError('Invalid or duplicate Rust inventory path')
        expected[str(path)] = digest
    actual = {}
    for path in root.rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Linked toolchain entry refused')
        if path.is_file(): actual[str(path.resolve())] = _sha(path)
    if actual != expected or actual.get(str(tool)) != manifest.get('rustc_sha256') or str(linker) not in actual:
        raise ValueError('Rust toolchain inventory mismatch')
    actual[str(manifest_path.resolve())] = _sha(manifest_path)
    return {'path': str(tool), 'extra_arguments': arguments, 'path_entries': paths,
            'files_sha256': actual, 'source': 'qualified_local_rust_gnu'}


def _unchanged(files):
    return all(Path(path).is_file() and _sha(path) == digest for path, digest in files.items())


def _archive_request(folder, module, artifacts, source_import):
    request = {'module': module, 'artifacts': artifacts, 'source_import': source_import}
    ancestors = set(); visits = 0; text_bytes = 0
    def visit(value, depth=0):
        nonlocal visits, text_bytes
        visits += 1
        if visits > 300_000 or depth > 100: raise ValueError('Request structural bound')
        if type(value) is str:
            text_bytes += len(value.encode('utf-8'))
            if text_bytes > MAX_REQUEST: raise ValueError('Request text bound')
        elif type(value) in (dict, list):
            if id(value) in ancestors: raise ValueError('Request JSON cycle')
            ancestors.add(id(value))
            if type(value) is dict:
                for key, child in value.items():
                    if type(key) is not str: raise ValueError('Request non-string key')
                    visit(key, depth + 1); visit(child, depth + 1)
            else:
                for child in value: visit(child, depth + 1)
            ancestors.remove(id(value))
        elif type(value) is int:
            if value.bit_length() > 128: raise ValueError('Request integer bound')
        elif value is not None and type(value) not in (float, bool):
            raise ValueError('Request non-JSON value')
    try:
        visit(request)
        raw = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_REQUEST: raise ValueError('Request byte bound')
        path = folder / 'request.json'; path.write_bytes(raw)
        return {'request_preserved': True, 'request_file': str(path), 'request_sha256': _sha(path)}
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        return {'request_preserved': False, 'request_preservation_reason': type(exc).__name__ + ': ' + str(exc)}


def _process(command, folder, phase, timeout, toolchain, stdin=None):
    """Own one process; record termination even after a bounded timeout."""
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'DOTNET_STARTUP_HOOKS', 'DOTNET_ADDITIONAL_DEPS',
                'DOTNET_SHARED_STORE', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS'):
        env.pop(key, None)
    env['PATH'] = os.pathsep.join(toolchain['path_entries'] + [env.get('PATH', '')])
    temp = folder / 'temp'; temp.mkdir(exist_ok=True)
    env.update(TMP=str(temp), TEMP=str(temp))
    stdin_path = folder / (phase + '-stdin.txt')
    stdin_path.write_text(stdin or '', encoding='utf-8', newline='\n')
    stdout_path, stderr_path = folder / (phase + '-stdout.txt'), folder / (phase + '-stderr.txt')
    result = {'command': [str(x) for x in command], 'timeout_seconds': timeout, 'process_exited': False,
              'timed_out': False, 'stdout_file': str(stdout_path), 'stderr_file': str(stderr_path)}
    process = None
    try:
        with stdin_path.open('rb') as source, stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
            process = subprocess.Popen([str(x) for x in command], cwd=folder, env=env,
                                       stdin=source, stdout=stdout, stderr=stderr, **options)
            result['pid'] = process.pid
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result['timed_out'] = True
                if os.name == 'nt':
                    taskkill = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/taskkill.exe'
                    killed = subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                                            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                    result['tree_stop_exit_code'] = killed.returncode
                    result['tree_stop_stdout'] = killed.stdout.decode('utf-8', errors='replace')
                    result['tree_stop_stderr'] = killed.stderr.decode('utf-8', errors='replace')
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                    result['tree_stop_exit_code'] = 0
                if process.poll() is None: process.kill()
                process.wait(timeout=5)
            result['exit_code'] = process.returncode
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            result['process_exited'] = process.poll() is not None
        _save(folder / (phase + '.json'), result)
    if stdout_path.stat().st_size > MAX_OUTPUT or stderr_path.stat().st_size > MAX_OUTPUT:
        raise ValueError('Process output exceeded the admitted bound')
    result['stdout'] = stdout_path.read_text(encoding='utf-8', errors='strict')
    result['stderr'] = stderr_path.read_text(encoding='utf-8', errors='strict')
    return result


def _source_baseline(normalized, source_import, output_dir=None):
    if source_import is None: return None
    if type(source_import) is not dict or source_import.get('status') != 'IMPORTED':
        raise ValueError('Source import is not admitted')
    baseline = module_model.validate_module(source_import['module'])
    if normalized['provenance'] != baseline['provenance'] or normalized['module_id'] != baseline['module_id']:
        raise ValueError('Current model has different source provenance')
    signature = lambda m: [{k: unit[k] for k in ('id', 'name', 'qualified_name', 'parameters', 'return_type')} for unit in m['units']]
    if signature(normalized) != signature(baseline): raise ValueError('Current source identities or signatures differ')
    language = baseline['provenance'].get('origin', {}).get('language')
    if language == 'csharp':
        import original_csharp
        admission = original_csharp.admit(normalized, source_import, output_dir)
        if type(admission) is not dict or admission.get('status') not in ('ADMITTED', 'REFUSED', 'INCONCLUSIVE'):
            raise RuntimeError('Unexpected original C# admission receipt')
        if admission['status'] == 'ADMITTED' and (admission.get('language') != 'csharp'
                or admission.get('baseline') != baseline or admission.get('current_model') != normalized):
            raise RuntimeError('Original C# admission identity differs from the requested models')
        return admission
    if language != 'rust':
        return {'language': language, 'baseline': baseline, 'status': 'NOT_TESTED'}
    import rust_import
    source = baseline['provenance']['source_text']
    reparsed = rust_import.import_rust(source)
    if reparsed.get('status') != 'IMPORTED' or module_model.validate_module(reparsed['module']) != baseline:
        raise ValueError('Rust baseline differs from complete source reparse')
    return {'language': 'rust', 'baseline': baseline, 'source': source, 'inventory': reparsed['inventory'], 'status': 'ADMITTED'}


def _source_harness(admission, records):
    identifiers = set(admission['inventory']['identifiers'])
    def fresh(prefix):
        name = prefix
        while name in identifiers: name += '_'
        identifiers.add(name)
        return name
    namespace, wrapper = fresh('__mia_source'), fresh('__mia_observe')
    names = {u['id']: u['name'] for u in admission['baseline']['units']}
    if any(not re.fullmatch('[A-Za-z_][A-Za-z_0-9]*', name) for name in names.values()):
        raise ValueError('Unexpected admitted Rust function name')
    calls = []
    for record in records:
        arguments = ', '.join(legacy._literal(value, 'rust') for value in record['inputs'])
        calls.append('        ::std::println!("[\\"' + record['unit_id'] + '\\",{}]", ' + names[record['unit_id']] + '(' + arguments + '));')
    # Source bytes are also archived alone. A separating newline terminates a
    # trailing line comment, without altering the admitted source text itself.
    return ('mod ' + namespace + ' {\n' + admission['source'] + '\n'
            '    pub fn ' + wrapper + '() {\n' + '\n'.join(calls) + '\n    }\n}\n'
            'fn main() { ' + namespace + '::' + wrapper + '(); }\n')


def _parse_output(text, records, units):
    lines = text.splitlines()
    if len(lines) != len(records): raise ValueError('Output count differs from shared case count')
    values = []
    for line, record in zip(lines, records):
        item = json.loads(line)
        typ = bool if units[record['unit_id']]['return_type'] == 'bool' else int
        if (type(item) is not list or len(item) != 2 or item[0] != record['unit_id'] or type(item[1]) is not typ
                or typ is int and not LOW <= item[1] <= HIGH):
            raise ValueError('Output identity, type or range mismatch')
        values.append(item[1])
    return values


def _execute(language, source, harness, records, units, folder, toolchain, separate_csharp=False):
    folder.mkdir()
    # The framework csc still uses legacy Windows path limits for its temporary
    # files. Keep process paths short independently of nested evidence folders.
    runtime_parent = _owned(BASE / 'runtime'); runtime_parent.mkdir(exist_ok=True)
    runtime = runtime_parent / ('v-' + uuid.uuid4().hex[:12]); runtime.mkdir()
    report = {'status': 'INCONCLUSIVE', 'compile_status': 'NOT_APPLICABLE' if language == 'python' else 'NON_COMPILE',
              'execution_status': 'NOT_EXECUTED', 'evidence_dir': str(folder), 'runtime_dir': str(runtime), 'language': language}
    try:
        program, runner = runtime / ('program' + LANGUAGES[language]), runtime / ('harness' + LANGUAGES[language])
        program.write_bytes(source.encode('utf-8')); runner.write_bytes(harness.encode('utf-8'))
        watched = {str(program): _sha(program), str(runner): _sha(runner)}
        for path in (program, runner):
            archive = folder / path.name; shutil.copy2(path, archive)
            watched[str(archive)] = _sha(path)
        if toolchain is None:
            report.update(status='NON_COMPILE', reason='Existing local tool unavailable'); return report
        report['toolchain'] = toolchain
        executable = runtime / ('run.exe' if os.name == 'nt' else 'run')
        if language != 'python':
            if language == 'csharp':
                command = [toolchain['path'], '/nologo', '/noconfig', '/target:exe', '/utf8output', '/out:' + str(executable), program, runner] if separate_csharp else []
            else:
                command = [toolchain['path'], '--edition=2021', '-A', 'warnings', '-C', 'opt-level=0', *toolchain['extra_arguments'], '-o', executable, runner]
            compilation = _process(command, runtime, 'compilation', 45, toolchain)
            report['compilation'] = {key: value for key, value in compilation.items() if key not in ('stdout', 'stderr')}
            if compilation['timed_out'] or compilation['exit_code'] != 0 or not executable.is_file():
                report['compile_status'] = 'TIMEOUT' if compilation['timed_out'] else 'FAILED'
                report['reason'] = 'Compilation did not complete successfully'; return report
            report['compile_status'] = 'COMPILED'; watched[str(executable)] = _sha(executable)
            archive_executable = folder / executable.name; shutil.copy2(executable, archive_executable)
            watched[str(archive_executable)] = _sha(executable)
        if not _unchanged(watched): raise ValueError('Source or executable changed before execution')
        command = [toolchain['path'], '-I', '-S', runner] if language == 'python' else [executable]
        stdin = json.dumps([[r['unit_id'], r['inputs']] for r in records], allow_nan=False) if language == 'python' else None
        execution = _process(command, runtime, 'execution', 10, toolchain, stdin)
        report['execution'] = {key: value for key, value in execution.items() if key not in ('stdout', 'stderr')}
        if execution['timed_out'] or execution['exit_code'] != 0 or execution['stderr'].strip():
            report['execution_status'] = 'TIMEOUT' if execution['timed_out'] else 'FAILED'
            report['reason'] = 'Execution did not complete successfully'; return report
        report['values'] = _parse_output(execution['stdout'], records, units)
        if not _unchanged(watched): raise ValueError('Source or executable changed during execution')
        report.update(status='EXECUTED', execution_status='EXECUTED', files_sha256=watched)
    except Exception as exc:
        report.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc))
    finally:
        # Never replace the admitted archived source or executable after their
        # checks. Copy only logs/receipts which were produced in the short cwd.
        for path in runtime.iterdir():
            if path.is_file() and not (folder / path.name).exists():
                shutil.copy2(path, folder / path.name)
        report['runtime_files_sha256'] = {p.name: _sha(p) for p in runtime.iterdir() if p.is_file()}
        report['saved_files_sha256'] = {p.name: _sha(p) for p in folder.iterdir() if p.is_file() and p.name != 'report.json'}
        _save(folder / 'report.json', report)
    return report


def _check_generated(execution, records, normalized, specs, oracle):
    report = deepcopy(execution); report.update(model_agreement='NOT_TESTED', units=[], counterexamples=[])
    if execution['status'] != 'EXECUTED':
        if execution['status'] not in ('NON_COMPILE', 'REFUSED', 'INCONCLUSIVE'):
            report.update(status='INCONCLUSIVE', reason='Unexpected execution status; no success inferred')
        return report
    rows = {}
    units = {u['id']: u for u in normalized['units']}
    for uid in normalized['topological_order']:
        planned = [r for r in records if r['unit_id'] == uid]
        rows[uid] = {'unit_id': uid, 'status': 'NOT_EXECUTED', 'tests': {'planned': len(planned), 'executed': 0,
            'contract_checked': 0, 'precondition_witnesses': sum(r['precondition'] is True for r in planned),
            'differential_mismatch_indices': [], 'counterexample_indices': [], 'call_precondition_violation_indices': []}}
    oracle.deadline = time.monotonic() + 15
    for index, (case, actual) in enumerate(zip(records, execution['values'])):
        uid = case['unit_id']; tests = rows[uid]['tests']; tests['executed'] += 1
        witness = {'case_index': index, 'unit_id': uid, 'inputs': case['inputs'], 'expected': case['expected'], 'actual': actual}
        if actual != case['expected']:
            tests['differential_mismatch_indices'].append(index)
            report['counterexamples'].append(dict(witness, kind='MODEL_MISMATCH'))
        if case['precondition']:
            tests['contract_checked'] += 1
            env = dict(zip((p['name'] for p in units[uid]['parameters']), case['inputs'])); env['result'] = actual
            if oracle.evaluate(specs[uid]['post_ast'], env) is not True:
                tests['counterexample_indices'].append(index)
                report['counterexamples'].append(dict(witness, kind='POSTCONDITION_VIOLATION', rule=specs[uid]['postcondition']))
            if case['call_precondition_violations']:
                tests['call_precondition_violation_indices'].append(index)
                report['counterexamples'].append(dict(witness, kind='CALL_PRECONDITION_VIOLATION', violations=case['call_precondition_violations']))
    for row in rows.values():
        tests = row['tests']
        row['status'] = ('DIFFERENT' if tests['differential_mismatch_indices'] else 'COUNTEREXAMPLE'
                         if tests['counterexample_indices'] or tests['call_precondition_violation_indices'] else 'INCONCLUSIVE'
                         if not tests['precondition_witnesses'] else 'TESTED_FINITE')
    report['units'] = list(rows.values())
    statuses = {row['status'] for row in rows.values()}
    report['status'] = next((s for s in ('DIFFERENT', 'COUNTEREXAMPLE', 'INCONCLUSIVE') if s in statuses), 'TESTED_FINITE')
    report['model_agreement'] = 'DIFFERENT' if 'DIFFERENT' in statuses else 'PASS'
    return report


def _records(normalized, baseline=None):
    if baseline is None or baseline == normalized:
        return foundation._records(normalized)
    # A revised threshold must not remove the original boundary from the
    # representative input plan. Both models contribute constants; contracts
    # and expected values still belong only to the requested current model.
    units = {u['id']: u for u in normalized['units']}
    specs = {uid: math_core.validate_spec(module_model.unit_spec(normalized, uid)) for uid in units}
    constants = set()
    for model in (normalized, baseline):
        for unit in model['units']:
            spec = math_core.validate_spec(module_model.unit_spec(model, unit['id']))
            for node in (unit['body'], spec['pre_ast'], spec['post_ast']):
                constants.update(foundation._constants(node))
    oracle = foundation._Oracle(units, specs)
    records = []
    for uid in normalized['topological_order']:
        unit, spec = units[uid], specs[uid]
        for inputs in foundation._sample_inputs(unit, constants):
            env = dict(zip((p['name'] for p in unit['parameters']), inputs))
            precondition = oracle.evaluate(spec['pre_ast'], env)
            oracle.violations = []; oracle.track_dependencies = precondition is True
            expected = oracle.call(uid, inputs)
            records.append({'unit_id': uid, 'inputs': list(inputs), 'expected': expected, 'precondition': precondition,
                            'expected_postcondition': oracle.evaluate(spec['post_ast'], dict(env, result=expected)),
                            'call_precondition_violations': deepcopy(oracle.violations)})
    return records, specs, oracle


def _check_source(execution, records, baseline, units):
    """Validate original-source observations independently of the source adapter."""
    source = dict(execution, agreement='NOT_TESTED', baseline_model_agreement='NOT_TESTED',
                  differences=[], baseline_differences=[])
    if source.get('status') != 'EXECUTED':
        if source.get('status') not in ('NON_COMPILE', 'REFUSED', 'INCONCLUSIVE'):
            source.update(status='INCONCLUSIVE', reason='Unexpected original-source execution status')
        return source
    values = source.get('values')
    if (source.get('compile_status') != 'COMPILED' or source.get('execution_status') != 'EXECUTED'
            or type(values) is not list or len(values) != len(records)):
        source.update(status='INCONCLUSIVE', reason='Original-source execution receipt is incomplete')
        return source
    for case, value in zip(records, values):
        typ = bool if units[case['unit_id']]['return_type'] == 'bool' else int
        if type(value) is not typ or typ is int and not LOW <= value <= HIGH:
            source.update(status='INCONCLUSIVE', reason='Original-source output type or range mismatch')
            return source
    baseline_oracle = foundation._Oracle({u['id']: u for u in baseline['units']})
    for index, (case, actual) in enumerate(zip(records, values)):
        baseline_value = baseline_oracle.call(case['unit_id'], case['inputs'])
        witness = {'case_index': index, 'unit_id': case['unit_id'], 'inputs': case['inputs'], 'actual_source': actual}
        if actual != baseline_value: source['baseline_differences'].append(dict(witness, expected_baseline=baseline_value))
        if actual != case['expected']: source['differences'].append(dict(witness, expected_current_model=case['expected']))
    source['baseline_model_agreement'] = 'DIFFERENT' if source['baseline_differences'] else 'PASS'
    source['agreement'] = 'DIFFERENT' if source['differences'] else 'PASS'
    source['status'] = 'DIFFERENT' if source['differences'] or source['baseline_differences'] else 'TESTED_FINITE'
    return source


def verify(module, artifacts, output_dir, source_import=None):
    parent = _owned(output_dir); parent.mkdir(parents=True, exist_ok=True)
    folder = parent / ('verification-' + uuid.uuid4().hex); folder.mkdir()
    report = {'status': 'REFUSED', 'reason': 'Admission incomplete', 'generated_checks': [],
              'source_check': {'status': 'NOT_TESTED', 'agreement': 'NOT_TESTED', 'baseline_model_agreement': 'NOT_TESTED'},
              'evidence_dir': str(folder), 'limitations': LIMITATIONS, 'formal_scope': 'NOT_PROVED'}
    code_hashes = None
    try:
        code_names = ['verifier.py', 'emitters.py', 'common.py', 'rust_import.py', 'csharp_import.py',
                      'original_csharp.py', 'original_csharp_compile.ps1', 'original_csharp_run.ps1']
        code_hashes = {str(BASE / name): _sha(BASE / name) for name in code_names}
        report['implementation_files_sha256'] = code_hashes
        report.update(_archive_request(folder, module, artifacts, source_import))
        if not report['request_preserved']: raise ValueError('Request cannot be preserved within admitted JSON bounds')
        foundations = _foundation_hashes(); report['foundation_files_sha256'] = foundations
        normalized = module_model.validate_module(module)
        if type(artifacts) is not list or not 1 <= len(artifacts) <= 3: raise ValueError('One to three generated targets required')
        from emitters import emit_module
        languages = []
        for artifact in artifacts:
            if type(artifact) is not dict or artifact.get('language') not in LANGUAGES: raise ValueError('Invalid generated target')
            language = artifact['language']
            if language in languages: raise ValueError('Repeated generated target')
            if artifact != emit_module(normalized, language): raise ValueError('Artifact differs from exact regeneration')
            languages.append(language)
        admission = _source_baseline(normalized, source_import, folder / 'source-admission')
        if admission:
            report['source_admission'] = admission
            if admission['status'] not in ('ADMITTED', 'NOT_TESTED'):
                report['source_check'].update(language=admission.get('language'), status=admission['status'],
                    reason=admission.get('reason', 'Original source admission failed'),
                    compile_status='NOT_COMPILED', execution_status='NOT_EXECUTED',
                    evidence_dir=admission.get('evidence_dir'))
                report.update(status=admission['status'], reason='Original source admission failed before any generated target ran.')
                return report
        _save(folder / 'module.json', normalized); _save(folder / 'artifacts.json', artifacts)
        if source_import is not None: _save(folder / 'source-import.json', source_import)
        records, specs, oracle = _records(normalized, admission['baseline'] if admission else None)
        _save(folder / 'cases.json', records)
        report['tests'] = {'planned': len(records), 'max_cases_per_unit': 512}
        report['oracle'] = {'method': 'independent_direct_call_interpreter', 'initial_steps': oracle.steps,
                            'call_trace_scope': 'Model call traces, only from admissible root inputs; runtime calls not instrumented.',
                            'input_scope': 'Shared finite cases; constants and neighbours from both current and source baseline models.'}
        units = {u['id']: u for u in normalized['units']}
        needed = set(languages) | ({'rust'} if admission and admission['language'] == 'rust' else set())
        toolchains = {language: _toolchain(language) for language in needed}
        if not _unchanged(code_hashes) or _foundation_hashes() != foundations:
            report.update(status='INCONCLUSIVE', reason='Implementation changed during admission; no process launched')
            return report
        report['status'] = 'INCONCLUSIVE'
        for artifact in artifacts:
            language = artifact['language']
            harness = foundation._harness(artifact, units, records)
            execution = _execute(language, artifact['source'], harness, records, units, folder / ('generated-' + language), toolchains[language], language == 'csharp')
            check = _check_generated(execution, records, normalized, specs, oracle)
            report['generated_checks'].append(check)
            _save(Path(check['evidence_dir']) / 'checked.json', check)
        if admission and admission['language'] in ('rust', 'csharp'):
            if not _unchanged(code_hashes):
                raise RuntimeError('Implementation changed before original-source execution')
            if admission['language'] == 'rust':
                execution = _execute('rust', admission['source'], _source_harness(admission, records), records, units,
                                     folder / 'original-rust', toolchains['rust'])
            else:
                import original_csharp
                execution = original_csharp.execute(admission, records, folder / 'original-csharp')
            source = _check_source(execution, records, admission['baseline'], units)
            source['language'] = admission['language']
            report['source_check'] = source
            _save(Path(source['evidence_dir']) / 'checked.json', source)
        elif admission:
            report['source_check'].update(language=admission['language'], reason='Original source execution not implemented for this language in this milestone')
        statuses = {c['status'] for c in report['generated_checks']}
        status = next((s for s in ('REFUSED', 'DIFFERENT', 'COUNTEREXAMPLE', 'NON_COMPILE', 'INCONCLUSIVE') if s in statuses), 'TESTED_FINITE')
        if admission and admission['language'] in ('rust', 'csharp'):
            source = report['source_check']
            if source['baseline_model_agreement'] == 'DIFFERENT': status = 'DIFFERENT'
            elif source['status'] not in ('TESTED_FINITE', 'DIFFERENT'): status = 'INCONCLUSIVE'
        report.update(status=status, reason='Generated targets, contracts and original-source comparisons are reported separately.')
        if (_foundation_hashes() != foundations or not _unchanged(code_hashes)
                or any(tool and not _unchanged(tool['files_sha256']) for tool in toolchains.values())):
            report.update(status='INCONCLUSIVE', reason='Implementation or toolchain changed during verification')
    except foundation._OracleBudget as exc:
        report.update(status='INCONCLUSIVE', reason=str(exc))
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        report.update(status='REFUSED', reason=type(exc).__name__ + ': ' + str(exc))
    except Exception as exc:
        report.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc))
    finally:
        if code_hashes is not None:
            try:
                if not _unchanged(code_hashes):
                    report.update(status='INCONCLUSIVE', reason='Implementation changed during verification')
            except OSError as exc:
                report.update(status='INCONCLUSIVE', reason='Implementation cannot be checked at completion: ' + str(exc))
        _save(folder / 'report.json', report)
    return report
