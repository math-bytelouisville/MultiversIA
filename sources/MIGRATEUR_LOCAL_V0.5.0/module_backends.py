"""Emit real calls between pure units, then check the whole executed module.

Only regenerated, closed typed expressions are executed. The separate oracle
walks calls with fresh parameter environments; no pasted C# is run.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import code_backends as legacy
import math_core

BASE = Path(__file__).resolve().parent
LANGUAGES = {'python': '.py', 'csharp': '.cs', 'rust': '.rs'}
LOW, HIGH = -2147483648, 2147483647
MAX_CASES_PER_UNIT = 512
MAX_ORACLE_STEPS = 2_000_000
RUST_MANIFEST = BASE / 'toolchains/rust/manifest.json'
SCOPE = 'Module pur Int32/bool avec appels directs internes acycliques ; noms source reliés aux symboles cibles. Pas de site ni de compatibilité ABI garantie.'
LIMITATIONS = [
    'Tests finis de chaque unité et de ses appels ; aucune preuve universelle du module.',
    'Oracle d’appels distinct de l’émetteur et de module_model.evaluate_unit ; validation des types et parser de contrats partagés.',
    'Contrats et préconditions des appels contrôlés sur les entrées racines admissibles échantillonnées ; pas de preuve universelle de composition.',
    'Symboles cibles canoniques : table de correspondance conservée, aucune compatibilité binaire ou HTTP annoncée.',
    'Limites applicatives et délais ; aucune isolation OS revendiquée.',
    'Aucun téléchargement ni changement du PATH global ; chemins Rust qualifiés limités au processus enfant.',
]


def _sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


def _save(path, value):
    legacy._save(Path(path), value)


def _validated(module):
    import module_model
    return module_model.validate_module(module)


def _render(node, parameter_map, symbols, language):
    kind = node['kind']
    if kind == 'param':
        return parameter_map[node['name']]
    if kind == 'constant':
        return legacy._literal(node['value'], language)
    if kind == 'call':
        return symbols[node['target']] + '(' + ', '.join(
            _render(arg, parameter_map, symbols, language) for arg in node['arguments']) + ')'
    if kind == 'neg32':
        return legacy._wrap_expression('neg32', _render(node['operand'], parameter_map, symbols, language), None, language)
    if kind == 'not':
        return '(' + ('not ' if language == 'python' else '!') + _render(node['operand'], parameter_map, symbols, language) + ')'
    if kind == 'ite':
        condition, yes, no = (_render(node[key], parameter_map, symbols, language)
                              for key in ('condition', 'when_true', 'when_false'))
        if language == 'python': return '(' + yes + ' if ' + condition + ' else ' + no + ')'
        if language == 'csharp': return '(' + condition + ' ? ' + yes + ' : ' + no + ')'
        return '(if ' + condition + ' { ' + yes + ' } else { ' + no + ' })'
    if kind == 'binary':
        if node['op'] in ('add32','sub32','mul32'):
            return legacy._wrap_expression(node['op'], _render(node['left'], parameter_map, symbols, language),
                                           _render(node['right'], parameter_map, symbols, language), language)
        ops = {'eq': '==', 'ne': '!=', 'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>='}
        ops.update({'and': 'and', 'or': 'or'} if language == 'python' else {'and': '&&', 'or': '||'})
        return '(' + _render(node['left'], parameter_map, symbols, language) + ' ' + ops[node['op']] + ' ' + _render(node['right'], parameter_map, symbols, language) + ')'
    raise ValueError('Nœud non admis dans le module.')


def emit_module(module, language):
    if type(language) is not str or language not in LANGUAGES:
        raise ValueError('Langage cible inconnu.')
    normalized = _validated(module)
    units = {unit['id']: unit for unit in normalized['units']}
    symbols = {uid: ('F' if language == 'csharp' else 'f') + uid[1:] for uid in units}
    mapping, definitions = {}, []
    for uid in normalized['topological_order']:
        unit = units[uid]
        params = {p['name']: 'p' + str(i) for i, p in enumerate(unit['parameters'])}
        mapping[uid] = {'source_name': unit['name'], 'qualified_name': unit['qualified_name'],
                        'target_symbol': symbols[uid], 'parameters': [
                            {'source_name': p['name'], 'target_name': params[p['name']], 'type': p['type']}
                            for p in unit['parameters']]}
        body = _render(unit['body'], params, symbols, language)
        if language == 'python':
            parameters = ', '.join(params[p['name']] + ': ' + p['type'] for p in unit['parameters'])
            definitions.append('def ' + symbols[uid] + '(' + parameters + ') -> ' + unit['return_type'] + ':\n    return ' + body + '\n')
        elif language == 'csharp':
            parameters = ', '.join(p['type'] + ' ' + params[p['name']] for p in unit['parameters'])
            definitions.append('    public static ' + unit['return_type'] + ' ' + symbols[uid] + '(' + parameters + ')\n    {\n        return ' + body + ';\n    }\n')
        else:
            types = {'int': 'i32', 'bool': 'bool'}
            parameters = ', '.join(params[p['name']] + ': ' + types[p['type']] for p in unit['parameters'])
            definitions.append('pub fn ' + symbols[uid] + '(' + parameters + ') -> ' + types[unit['return_type']] + ' {\n    ' + body + '\n}\n')
    source = '\n'.join(definitions)
    if language == 'csharp': source = 'public static class MiaModule\n{\n' + source + '}\n'
    return {'language': language, 'source': source, 'extension': LANGUAGES[language],
            'source_sha256': _sha(source), 'unit_mapping': mapping, 'semantic_scope': SCOPE,
            'module_id': normalized['module_id'], 'revision_id': normalized['revision_id'],
            'emitted_class': 'MiaModule' if language == 'csharp' else None}


class _OracleBudget(RuntimeError):
    pass


class _Oracle:
    """Independent interpreter, preserving argument evaluation and call scope."""
    def __init__(self, units, specs=None):
        self.units = units
        self.specs = specs or {}
        self.steps = 0
        self.calls = {uid: 0 for uid in units}
        self.deadline = time.monotonic() + 15
        self.stack = []
        self.track_dependencies = False
        self.violations = []

    def evaluate(self, node, env, depth=0):
        self.steps += 1
        if self.steps > MAX_ORACLE_STEPS or depth > 64 or time.monotonic() > self.deadline:
            raise _OracleBudget('Budget d’évaluation de l’oracle atteint ; résultat non qualifié.')
        kind = node['kind']
        if kind == 'constant': return node['value']
        if kind == 'param': return env[node['name']]
        if kind == 'neg32':
            bits = (-self.evaluate(node['operand'], env, depth + 1)) & 0xffffffff
            return bits - 0x100000000 if bits & 0x80000000 else bits
        if kind == 'call':
            values = [self.evaluate(arg, env, depth + 1) for arg in node['arguments']]
            return self.call(node['target'], values, depth + 1)
        if kind == 'not': return not self.evaluate(node['operand'], env, depth + 1)
        if kind == 'ite':
            chosen = 'when_true' if self.evaluate(node['condition'], env, depth + 1) else 'when_false'
            return self.evaluate(node[chosen], env, depth + 1)
        left = self.evaluate(node['left'], env, depth + 1)
        op = node['op']
        if op == 'and': return bool(left and self.evaluate(node['right'], env, depth + 1))
        if op == 'or': return bool(left or self.evaluate(node['right'], env, depth + 1))
        right = self.evaluate(node['right'], env, depth + 1)
        if op in ('add32','sub32','mul32'):
            bits = (left+right if op=='add32' else left-right if op=='sub32' else left*right) & 0xffffffff
            return bits - 0x100000000 if bits & 0x80000000 else bits
        if op == 'eq': return left == right
        if op == 'ne': return left != right
        if op == 'lt': return left < right
        if op == 'le': return left <= right
        if op == 'gt': return left > right
        if op == 'ge': return left >= right
        raise ValueError('Opérateur oracle inconnu.')

    def call(self, uid, values, depth=0):
        unit = self.units[uid]
        if len(values) != len(unit['parameters']): raise ValueError('Arity oracle invalide.')
        for value, param in zip(values, unit['parameters']):
            if param['type'] == 'bool' and type(value) is not bool: raise ValueError('Argument booléen invalide.')
            if param['type'] == 'int' and (type(value) is not int or not LOW <= value <= HIGH): raise ValueError('Argument Int32 invalide.')
        self.calls[uid] += 1
        env = dict(zip((p['name'] for p in unit['parameters']), values))
        if self.stack and self.track_dependencies and uid in self.specs:
            if self.evaluate(self.specs[uid]['pre_ast'], env, depth + 1) is not True:
                self.violations.append({'kind': 'CALL_PRECONDITION_VIOLATION', 'caller': self.stack[-1],
                                        'callee': uid, 'arguments': list(values), 'call_stack': self.stack + [uid],
                                        'rule': self.specs[uid]['precondition'], 'observed_precondition': False})
        self.stack.append(uid)
        try:
            return self.evaluate(unit['body'], env, depth + 1)
        finally:
            self.stack.pop()


def _constants(node):
    if type(node) is not dict: return set()
    found = {node['value']} if node.get('kind') == 'constant' and node.get('type') == 'int' else set()
    for value in node.values():
        if type(value) is dict: found.update(_constants(value))
        elif type(value) is list:
            for item in value: found.update(_constants(item))
    return found


def _sample_inputs(unit, constants):
    return legacy._sample_tuples(unit['parameters'], constants, MAX_CASES_PER_UNIT)


def _records(normalized):
    import module_model
    units = {u['id']: u for u in normalized['units']}
    specs = {uid: math_core.validate_spec(module_model.unit_spec(normalized, uid)) for uid in units}
    constants = set()
    for uid, unit in units.items():
        constants.update(_constants(unit['body']) | _constants(specs[uid]['pre_ast']) | _constants(specs[uid]['post_ast']))
    oracle = _Oracle(units, specs)
    records = []
    for uid in normalized['topological_order']:
        unit, spec = units[uid], specs[uid]
        for inputs in _sample_inputs(unit, constants):
            env = dict(zip((p['name'] for p in unit['parameters']), inputs))
            precondition = oracle.evaluate(spec['pre_ast'], env)
            oracle.violations = []
            oracle.track_dependencies = precondition is True
            expected = oracle.call(uid, inputs)
            records.append({'unit_id': uid, 'inputs': list(inputs), 'expected': expected,
                            'precondition': precondition,
                            'expected_postcondition': oracle.evaluate(spec['post_ast'], dict(env, result=expected)),
                            'call_precondition_violations': deepcopy(oracle.violations)})
    return records, specs, oracle


def _toolchain(language):
    if language == 'rust' and RUST_MANIFEST.is_file():
        raw = RUST_MANIFEST.read_bytes()
        if len(raw) > 1_000_000: raise ValueError('Registre Rust trop grand.')
        manifest = json.loads(raw)
        if (type(manifest) is not dict or manifest.get('schema_version') != 'mia-rust-toolchain-1'
                or manifest.get('status') != 'QUALIFIED_LOCAL' or type(manifest.get('qualification')) is not dict
                or manifest['qualification'].get('status') != 'COMPILED_AND_EXECUTED'):
            raise ValueError('Registre Rust non qualifié ; aucune activation.')
        root = (BASE / 'toolchains/rust').resolve()
        sysroot = Path(manifest['sysroot']).resolve()
        if not sysroot.is_relative_to(root) or not sysroot.is_dir(): raise ValueError('Sysroot Rust hors du chantier qualifié.')
        tool = Path(manifest['rustc_path']).resolve()
        if tool != sysroot / 'bin/rustc.exe': raise ValueError('Compilateur Rust inattendu.')
        linker = sysroot / 'lib/rustlib/x86_64-pc-windows-gnu/bin/self-contained/x86_64-w64-mingw32-gcc.exe'
        arguments = ['-C', 'linker=' + str(linker), '-C', 'link-self-contained=yes']
        paths = [str(linker.parent), str(sysroot / 'bin')]
        if manifest.get('extra_arguments') != arguments or manifest.get('path_entries') != paths:
            raise ValueError('Arguments ou chemins Rust différents du profil GNU admis.')
        inventory = manifest.get('files_sha256')
        if type(inventory) is not dict or not inventory or len(inventory) > 4096: raise ValueError('Inventaire Rust absent ou invalide.')
        files = {}
        for relative, digest in inventory.items():
            if type(relative) is not str or Path(relative).is_absolute() or type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError('Entrée d’inventaire Rust invalide.')
            path = (sysroot / relative).resolve()
            if not path.is_relative_to(sysroot) or str(path) in files: raise ValueError('Chemin Rust hors sysroot ou dupliqué.')
            if not path.is_file() or _sha(path.read_bytes()) != digest: raise ValueError('Fichier Rust absent ou empreinte différente : ' + relative)
            files[str(path)] = digest
        actual_files = {str(p.resolve()) for p in sysroot.rglob('*') if p.is_file()}
        if actual_files != set(files): raise ValueError('Inventaire Rust incomplet ou fichier ajouté.')
        if str(tool) not in files or files[str(tool)] != manifest.get('rustc_sha256') or str(linker.resolve()) not in files:
            raise ValueError('Compilateur ou linker non couvert par le registre Rust.')
        files[str(RUST_MANIFEST.resolve())] = _sha(raw)
        return {'path': tool, 'extra_args': arguments, 'path_entries': paths, 'files_sha256': files,
                'manifest_path': str(RUST_MANIFEST.resolve()), 'manifest_sha256': _sha(raw),
                'source': 'qualified_project_local_rust_gnu'}
    tool = legacy._find_tool(language)
    if tool is None: return None
    return {'path': tool, 'extra_args': [], 'path_entries': [], 'files_sha256': {str(tool): _sha(tool.read_bytes())},
            'source': 'existing_local_tool'}


def _process(command, folder, timeout, stdin=None, toolchain=None):
    if not toolchain or not toolchain.get('path_entries'):
        return legacy._process(command, folder, timeout, stdin)
    env = dict(os.environ)
    for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'DOTNET_STARTUP_HOOKS',
                 'DOTNET_ADDITIONAL_DEPS', 'DOTNET_SHARED_STORE', 'RUSTC_WRAPPER'):
        env.pop(name, None)
    env['PATH'] = os.pathsep.join(toolchain['path_entries'] + [env.get('PATH', '')])
    result = subprocess.run([str(x) for x in command], input=stdin, cwd=folder, env=env,
                            capture_output=True, encoding='utf-8', errors='replace', timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    return {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}


def _harness(artifact, units, records):
    language = artifact['language']
    mapping = artifact['unit_mapping']
    if language == 'python':
        entries = ', '.join(repr(uid) + ': ' + data['target_symbol'] for uid, data in sorted(mapping.items()))
        return artifact['source'] + '\nimport json, sys\n_functions = {' + entries + '}\nfor unit_id, values in json.loads(sys.stdin.read()):\n    print(json.dumps([unit_id, _functions[unit_id](*values)]))\n'
    calls = []
    for record in records:
        uid = record['unit_id']
        arguments = ', '.join(legacy._literal(v, language) for v in record['inputs'])
        symbol = mapping[uid]['target_symbol']
        if language == 'csharp':
            expression = 'MiaModule.' + symbol + '(' + arguments + ')'
            value = '(' + expression + ' ? "true" : "false")' if units[uid]['return_type'] == 'bool' else expression + '.ToString(System.Globalization.CultureInfo.InvariantCulture)'
            calls.append('        System.Console.WriteLine("[\\"' + uid + '\\"," + ' + value + ' + "]");')
        else:
            calls.append('    println!("[\\"' + uid + '\\",{}]", ' + symbol + '(' + arguments + '));')
    if language == 'csharp':
        return 'public static class MiaValidation\n{\n    public static void Main()\n    {\n' + '\n'.join(calls) + '\n    }\n}\n'
    return artifact['source'] + '\nfn main() {\n' + '\n'.join(calls) + '\n}\n'


def _snapshot(folder):
    return {p.name: _sha(p.read_bytes()) for p in folder.iterdir() if p.is_file() and p.name != 'report.json'}


def validate_module_generated(artifact, module, output_dir):
    folder = legacy._folder(output_dir, 'module-validation')
    phase, runtime = 'admission', None
    report = {'status': 'INCONCLUSIVE', 'reason': 'NOT_COMPLETED', 'units': [],
              'compile_status': 'NON_COMPILE', 'execution_status': 'NOT_EXECUTED',
              'tests': {'planned': 0, 'executed': 0, 'contract_checked': 0},
              'evidence_dir': str(folder), 'limitations': list(LIMITATIONS),
              'formal_scope': 'NOT_PROVED_MODULE', 'input_source_executed': False}
    try:
        normalized = _validated(module)
        if type(artifact) is not dict or type(artifact.get('language')) is not str or artifact['language'] not in LANGUAGES:
            raise ValueError('Artefact module invalide.')
        expected = emit_module(normalized, artifact['language'])
        if any(artifact.get(k) != v for k, v in expected.items()):
            raise ValueError('Artefact différent de la régénération exacte ; aucune exécution.')
        language = artifact['language']
        report.update(language=language, module_id=normalized['module_id'], revision_id=normalized['revision_id'],
                      source_sha256=expected['source_sha256'], compile_status='NOT_APPLICABLE' if language == 'python' else 'NON_COMPILE')
        _save(folder / 'module.json', normalized); _save(folder / 'artifact.json', expected)
        records, specs, oracle = _records(normalized)
        _save(folder / 'cases.json', records)
        units = {u['id']: u for u in normalized['units']}
        rows = {}
        for uid in normalized['topological_order']:
            planned = [r for r in records if r['unit_id'] == uid]
            rows[uid] = {'unit_id': uid, 'source_name': units[uid]['name'], 'status': 'NOT_EXECUTED',
                         'tests': {'planned': len(planned), 'executed': 0, 'contract_checked': 0,
                                   'precondition_witnesses': sum(r['precondition'] is True for r in planned),
                                   'differential_mismatch_indices': [], 'counterexample_indices': [],
                                   'call_precondition_violation_indices': []}}
            report['units'].append(rows[uid])
        report['tests']['planned'] = len(records)
        report['oracle'] = {'steps': oracle.steps, 'calls_by_unit': dict(oracle.calls), 'method': 'independent_direct_call_interpreter',
                            'call_trace_scope': 'Traces du modèle indépendant sur entrées racines admissibles ; runtime non instrumenté.'}
        toolchain = _toolchain(language)
        if toolchain is None:
            report.update(status='NON_COMPILE', reason='Compilateur local indisponible ; module conservé, aucune exécution cible.')
            return report
        tool = toolchain['path']
        report['toolchain'] = {**toolchain, 'path': str(tool)}
        runtime = legacy._folder(BASE / 'runtime', 'M')
        report['runtime_dir'] = str(runtime)
        program = runtime / ('program' + artifact['extension']); harness = runtime / ('harness' + artifact['extension'])
        program_text, harness_text = expected['source'], _harness(expected, units, records)
        for path, value in ((program, program_text), (harness, harness_text)):
            path.write_text(value, encoding='utf-8', newline='\n')
            (folder / path.name).write_text(value, encoding='utf-8', newline='\n')
        runtime_sources = {str(program): _sha(program_text), str(harness): _sha(harness_text),
                           str(folder / program.name): _sha(program_text), str(folder / harness.name): _sha(harness_text)}
        executable = runtime / ('run.exe' if os.name == 'nt' else 'run')
        if language != 'python':
            phase = 'compilation'
            if language == 'csharp':
                command = [tool, '/nologo', '/noconfig', '/target:exe', '/utf8output', '/out:' + str(executable), program, harness]
            else:
                command = [tool, '--edition=2021', '-C', 'opt-level=0', *toolchain['extra_args'], '-o', executable, harness]
            report['compilation_command'] = [str(x) for x in command]
            compilation = _process(command, runtime, 30, toolchain=toolchain)
            _save(folder / 'compilation.json', compilation)
            if compilation['exit_code'] or not executable.is_file():
                report.update(status='FAILED', reason='Compilation du module refusée ; sortie conservée.', compile_status='FAILED')
                return report
            report['compile_status'] = 'COMPILED'
            report['executable_sha256'] = _sha(executable.read_bytes())
            shutil.copy2(executable, folder / executable.name)
            runtime_sources.update({str(executable): report['executable_sha256'], str(folder / executable.name): report['executable_sha256']})
        watched = dict(toolchain['files_sha256'], **runtime_sources)
        if any(_sha(Path(path).read_bytes()) != digest for path, digest in watched.items()):
            raise ValueError('Entrées ou outil modifiés avant exécution.')
        phase = 'execution'
        command = [tool, '-I', '-S', harness] if language == 'python' else [executable]
        stdin = json.dumps([[r['unit_id'], r['inputs']] for r in records], allow_nan=False) if language == 'python' else None
        output = _process(command, runtime, 10, stdin, toolchain=toolchain)
        _save(folder / 'execution.json', output)
        if output['exit_code'] or output['stderr'].strip():
            report.update(status='FAILED', reason='Exécution du module en échec ; sortie conservée.', execution_status='FAILED')
            return report
        actual = [json.loads(line) for line in output['stdout'].splitlines()]
        report['tests']['executed'] = len(actual); report['execution_status'] = 'EXECUTED'
        if len(actual) != len(records):
            report.update(status='FAILED', reason='Nombre de résultats différent du plan par unité.'); return report
        # Compilation/execution/tool inventory time is not oracle work. Give
        # postcondition checking its own bounded window, keeping the shared
        # instruction counter so the cumulative evaluation budget still holds.
        oracle.deadline = time.monotonic() + 15
        report['oracle']['time_budget'] = '15s for initial records, 15s for actual postconditions; shared step cap'
        for index, (case, observed) in enumerate(zip(records, actual)):
            uid = case['unit_id']; row = rows[uid]
            if (type(observed) is not list or len(observed) != 2 or observed[0] != uid
                    or type(observed[1]) is not (bool if units[uid]['return_type'] == 'bool' else int)
                    or type(observed[1]) is int and not LOW <= observed[1] <= HIGH):
                report.update(status='FAILED', reason='Résultat mal formé, mauvais type ou attribution d’unité invalide.'); return report
            value = observed[1]; case['actual'] = value; row['tests']['executed'] += 1
            if value != case['expected']: row['tests']['differential_mismatch_indices'].append(index)
            if case['call_precondition_violations']: row['tests']['call_precondition_violation_indices'].append(index)
            if case['precondition']:
                row['tests']['contract_checked'] += 1; report['tests']['contract_checked'] += 1
                env = dict(zip((p['name'] for p in units[uid]['parameters']), case['inputs']))
                case['actual_postcondition'] = oracle.evaluate(specs[uid]['post_ast'], dict(env, result=value))
                if case['actual_postcondition'] is not True: row['tests']['counterexample_indices'].append(index)
        _save(folder / 'cases.json', records)
        for row in rows.values():
            tests = row['tests']
            row['status'] = ('FAILED' if tests['differential_mismatch_indices'] else
                             'COUNTEREXAMPLE' if tests['counterexample_indices'] or tests['call_precondition_violation_indices'] else
                             'INCONCLUSIVE' if not tests['precondition_witnesses'] else 'TESTED_FINITE')
        statuses = {row['status'] for row in rows.values()}
        status = next((s for s in ('FAILED', 'COUNTEREXAMPLE', 'INCONCLUSIVE') if s in statuses), 'TESTED_FINITE')
        report.update(status=status, reason={
            'FAILED': 'Code exécuté différent de l’oracle indépendant ; unités et cas conservés.',
            'COUNTEREXAMPLE': 'Contrat de résultat ou précondition d’un appel violé ; cas et règles conservés séparément.',
            'INCONCLUSIVE': 'Au moins une précondition ne possède aucun témoin dans les cas de son unité.',
            'TESTED_FINITE': 'Module complet exécuté ; chaque unité conforme à l’oracle et au contrat sur les seuls cas conservés.'}[status])
        report['oracle']['steps'] = oracle.steps
        if any(_sha(Path(path).read_bytes()) != digest for path, digest in watched.items()):
            report.update(status='INCONCLUSIVE', reason='Entrée, binaire ou outil modifié pendant les contrôles.')
    except subprocess.TimeoutExpired as exc:
        report.update(status='INCONCLUSIVE', reason='Délai borné dépassé ; aucun succès accepté.')
        report['compile_status' if phase == 'compilation' else 'execution_status'] = 'TIMEOUT'
        _save(folder / 'timeout.json', {'phase': phase, 'timeout': exc.timeout, 'stdout': str(exc.stdout), 'stderr': str(exc.stderr)})
    except _OracleBudget as exc:
        report.update(status='INCONCLUSIVE', reason=str(exc))
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        report.update(status='REFUSED', reason=str(exc))
    except (OSError, RuntimeError, ImportError) as exc:
        report.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc))
    finally:
        report['files_sha256'] = _snapshot(folder)
        if runtime is not None: report['runtime_files_sha256'] = _snapshot(runtime)
        _save(folder / 'report.json', report)
    return report
