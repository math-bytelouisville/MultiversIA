"""Closed typed expressions -> source, bounded real execution and scoped proof.

No pasted source is executed: artifacts are regenerated from validated IR first.
Finite differential tests, compilation and a conditional Lean proof are separate.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

BASE = Path(__file__).resolve().parent
LOW, HIGH = -2147483648, 2147483647
LANGUAGES = {'python': '.py', 'csharp': '.cs', 'rust': '.rs'}
MAX_CASES = 512
SCOPE = 'Expressions pures Int32/bool, comparaisons et conditions. Traduction par règles ; équivalence formelle des langages/compilateurs non démontrée.'
LIMITATIONS = [
    'Tests différentiels finis : aucun succès ne prouve tous les entiers Int32.',
    'Seul le code régénéré de la grammaire fermée peut être exécuté ; aucune exécution de source collé.',
    'Délais et tailles bornés par application ; pas de sandbox OS ni plafond global du PC.',
    'Absence d’un compilateur reste NON_COMPILE ; aucun téléchargement automatique.',
]


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


def _save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _folder(output_dir, prefix):
    parent = Path(output_dir)
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / (prefix + '-' + uuid.uuid4().hex)
    path.mkdir(exist_ok=False)
    return path.resolve()


def _validate_ast(node, symbols):
    """Independent type/shape admission, with bounded size before rendering."""
    count = 0
    def visit(value, depth=0):
        nonlocal count
        count += 1
        if count > 512 or depth > 32 or type(value) is not dict:
            raise ValueError('AST trop grand, trop profond ou mal formé.')
        kind, typ = value.get('kind'), value.get('type')
        if typ not in ('int', 'bool') or type(typ) is not str:
            raise ValueError('Type AST inconnu.')
        fields = {
            'param': {'kind', 'type', 'name'}, 'constant': {'kind', 'type', 'value'},
            'binary': {'kind', 'type', 'op', 'left', 'right'},
            'not': {'kind', 'type', 'operand'},
            'ite': {'kind', 'type', 'condition', 'when_true', 'when_false'},
        }
        if type(kind) is not str or kind not in fields or set(value) != fields[kind]:
            raise ValueError('Nœud ou champs AST non admis.')
        if kind == 'constant':
            literal = value['value']
            if typ == 'int' and (type(literal) is not int or not LOW <= literal <= HIGH):
                raise ValueError('Constante hors Int32.')
            if typ == 'bool' and type(literal) is not bool:
                raise ValueError('Constante booléenne invalide.')
        elif kind == 'param':
            if type(value['name']) is not str or symbols.get(value['name']) != typ:
                raise ValueError('Paramètre AST inconnu ou de type incorrect.')
        elif kind == 'not':
            if typ != 'bool' or visit(value['operand'], depth + 1) != 'bool':
                raise ValueError('Négation non booléenne.')
        elif kind == 'ite':
            if (visit(value['condition'], depth + 1) != 'bool'
                    or visit(value['when_true'], depth + 1) != typ
                    or visit(value['when_false'], depth + 1) != typ):
                raise ValueError('Condition ou branches incompatibles.')
        else:
            left, right = visit(value['left'], depth + 1), visit(value['right'], depth + 1)
            op = value['op']
            if typ != 'bool' or left != right or type(op) is not str:
                raise ValueError('Types binaires incompatibles.')
            if not (op in ('eq', 'ne') or op in ('lt', 'le', 'gt', 'ge') and left == 'int'
                    or op in ('and', 'or') and left == 'bool'):
                raise ValueError('Opérateur binaire non admis.')
        return typ
    return visit(node)


def _prepare(candidate, spec):
    import math_core
    normalized = math_core.validate_spec(spec)
    symbols = {p['name']: p['type'] for p in normalized['parameters']}
    if _validate_ast(candidate, symbols) != normalized['return_type']:
        raise ValueError('Le candidat ne possède pas le type de retour déclaré.')
    if _validate_ast(normalized['pre_ast'], symbols) != 'bool':
        raise ValueError('Précondition non booléenne.')
    if _validate_ast(normalized['post_ast'], dict(symbols, result=normalized['return_type'])) != 'bool':
        raise ValueError('Postcondition non booléenne.')
    return normalized


def _render(node, mapping, language):
    kind = node['kind']
    if kind == 'param':
        return mapping[node['name']]
    if kind == 'constant':
        if node['type'] == 'bool':
            return ('True' if node['value'] else 'False') if language == 'python' else ('true' if node['value'] else 'false')
        value = node['value']
        if language == 'rust':
            return 'i32::MIN' if value == LOW else '(' + str(value) + 'i32)'
        return '(' + str(value) + ')'
    if kind == 'not':
        return '(' + ('not ' if language == 'python' else '!') + _render(node['operand'], mapping, language) + ')'
    if kind == 'ite':
        c, yes, no = (_render(node[k], mapping, language) for k in ('condition', 'when_true', 'when_false'))
        if language == 'python':
            return '(' + yes + ' if ' + c + ' else ' + no + ')'
        if language == 'csharp':
            return '(' + c + ' ? ' + yes + ' : ' + no + ')'
        return '(if ' + c + ' { ' + yes + ' } else { ' + no + ' })'
    operator = {'eq': '==', 'ne': '!=', 'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>='}
    operator.update({'and': 'and', 'or': 'or'} if language == 'python' else {'and': '&&', 'or': '||'})
    return '(' + _render(node['left'], mapping, language) + ' ' + operator[node['op']] + ' ' + _render(node['right'], mapping, language) + ')'


def _parameters(spec, language, include_result=False):
    params = [(f'p{i}', p['type']) for i, p in enumerate(spec['parameters'])]
    if include_result:
        params.append(('result', spec['return_type']))
    if language == 'csharp':
        return ', '.join(typ + ' ' + name for name, typ in params)
    types = {'int': 'i32', 'bool': 'bool'} if language == 'rust' else {'int': 'int', 'bool': 'bool'}
    return ', '.join(name + ': ' + types[typ] for name, typ in params)


def emit(candidate, spec, language):
    if language not in LANGUAGES:
        raise ValueError('Langage cible inconnu.')
    normalized = _prepare(candidate, spec)
    mapping = {p['name']: f'p{i}' for i, p in enumerate(normalized['parameters'])}
    expression = _render(candidate, mapping, language)
    parameters, ret = _parameters(normalized, language), normalized['return_type']
    if language == 'python':
        source = f'def compute({parameters}) -> {ret}:\n    return {expression}\n'
        entry = 'compute'
    elif language == 'csharp':
        source = f'public static class MiaProgram\n{{\n    public static {ret} Compute({parameters})\n    {{\n        return {expression};\n    }}\n}}\n'
        entry = 'MiaProgram.Compute'
    else:
        rust_ret = 'i32' if ret == 'int' else 'bool'
        source = f'pub fn compute({parameters}) -> {rust_ret} {{\n    {expression}\n}}\n'
        entry = 'compute'
    return {'language': language, 'source': source, 'extension': LANGUAGES[language],
            'entrypoint': entry, 'parameter_mapping': mapping, 'semantic_scope': SCOPE,
            'source_sha256': _sha(source)}


def _oracle(node, env):
    """Separate from math_core.evaluate and from all code emitters."""
    kind = node['kind']
    if kind == 'constant':
        return node['value']
    if kind == 'param':
        return env[node['name']]
    if kind == 'not':
        return not _oracle(node['operand'], env)
    if kind == 'ite':
        branch = 'when_true' if _oracle(node['condition'], env) else 'when_false'
        return _oracle(node[branch], env)
    a, b = _oracle(node['left'], env), _oracle(node['right'], env)
    op = node['op']
    if op == 'eq': return a == b
    if op == 'ne': return a != b
    if op == 'lt': return a < b
    if op == 'le': return a <= b
    if op == 'gt': return a > b
    if op == 'ge': return a >= b
    if op == 'and': return bool(a and b)
    if op == 'or': return bool(a or b)
    raise ValueError('Opération oracle inconnue.')


def _constants(node):
    if type(node) is not dict:
        return set()
    values = {node['value']} if node.get('kind') == 'constant' and node.get('type') == 'int' else set()
    for value in node.values():
        if isinstance(value, dict):
            values.update(_constants(value))
    return values


def _cases(spec, candidate):
    values = {LOW, HIGH, -2, -1, 0, 1, 2}
    for value in sorted(_constants(candidate) | _constants(spec['pre_ast']) | _constants(spec['post_ast'])):
        values.update(n for n in (value - 1, value, value + 1) if LOW <= n <= HIGH)
    ordered = sorted(values)
    # All boundary/constant values retained in metadata, at most 33 used per axis.
    if len(ordered) > 33:
        ordered = [ordered[i * (len(ordered) - 1) // 32] for i in range(33)]
    axes = [(False, True) if p['type'] == 'bool' else tuple(ordered) for p in spec['parameters']]
    total = math.prod(map(len, axes))
    if total <= MAX_CASES:
        samples = list(itertools.product(*axes))
    else:
        samples = []
        base = tuple(False if p['type'] == 'bool' else 0 for p in spec['parameters'])
        for axis, axis_values in enumerate(axes):
            for value in axis_values:
                row = list(base); row[axis] = value
                if tuple(row) not in samples: samples.append(tuple(row))
        for index in range(MAX_CASES):
            encoded = index * (total - 1) // (MAX_CASES - 1)
            row = []
            for axis_values in reversed(axes):
                encoded, digit = divmod(encoded, len(axis_values))
                row.append(axis_values[digit])
            row = tuple(reversed(row))
            if row not in samples: samples.append(row)
            if len(samples) == MAX_CASES: break
    records = []
    for inputs in samples:
        env = dict(zip((p['name'] for p in spec['parameters']), inputs))
        expected = _oracle(candidate, env)
        records.append({'inputs': list(inputs), 'expected': expected,
                        'precondition': _oracle(spec['pre_ast'], env),
                        'postcondition': _oracle(spec['post_ast'], dict(env, result=expected))})
    return records


def _find_tool(language):
    if language == 'python':
        current = Path(sys.executable)
        return current.with_name('python.exe') if current.name.lower() == 'pythonw.exe' else current
    if language == 'rust':
        found = shutil.which('rustc')
        candidates = [Path(found)] if found else []
        candidates.append(Path(os.environ.get('USERPROFILE', str(Path.home()))) / '.cargo/bin/rustc.exe')
    else:
        found = shutil.which('csc')
        candidates = [Path(found)] if found else []
        windows = Path(os.environ.get('WINDIR', 'C:/Windows'))
        candidates += [windows / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe',
                       windows / 'Microsoft.NET/Framework/v4.0.30319/csc.exe']
    return next((p.resolve() for p in candidates if p.is_file()), None)


def _process(command, folder, timeout, stdin=None):
    env = dict(os.environ)
    for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'DOTNET_STARTUP_HOOKS',
                 'DOTNET_ADDITIONAL_DEPS', 'DOTNET_SHARED_STORE', 'RUSTC_WRAPPER'):
        env.pop(name, None)
    result = subprocess.run([str(x) for x in command], input=stdin, cwd=folder, env=env,
                            capture_output=True, encoding='utf-8', errors='replace', timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    return {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}


def _literal(value, language):
    return _render({'kind': 'constant', 'type': 'bool' if type(value) is bool else 'int', 'value': value}, {}, language)


def _harness(artifact, spec, cases):
    language = artifact['language']
    if language == 'python':
        return artifact['source'] + '\nimport json, sys\ninputs = json.loads(sys.stdin.read())\nfor values in inputs:\n    print(json.dumps(compute(*values)))\n'
    calls = []
    for case in cases:
        args = ', '.join(_literal(v, language) for v in case['inputs'])
        if language == 'csharp':
            expression = f'MiaProgram.Compute({args})'
            text = f'({expression} ? "true" : "false")' if spec['return_type'] == 'bool' else f'{expression}.ToString(System.Globalization.CultureInfo.InvariantCulture)'
            calls.append('        System.Console.WriteLine(' + text + ');')
        else:
            calls.append('    println!("{}", compute(' + args + '));')
    if language == 'csharp':
        return 'public static class MiaValidation\n{\n    public static void Main()\n    {\n' + '\n'.join(calls) + '\n    }\n}\n'
    return artifact['source'] + '\nfn main() {\n' + '\n'.join(calls) + '\n}\n'


def validate_generated(artifact, candidate, spec, output_dir):
    folder = _folder(output_dir, 'validation')
    phase = 'admission'
    runtime = None
    report = {'status': 'INCONCLUSIVE', 'reason': 'NOT_COMPLETED',
              'tests': {'planned': 0, 'executed': 0, 'contract_checked': 0},
              'compile_status': 'NON_COMPILE', 'execution_status': 'NOT_EXECUTED',
              'limitations': list(LIMITATIONS), 'evidence_files': [], 'evidence_dir': str(folder)}
    try:
        if type(artifact) is not dict or artifact.get('language') not in LANGUAGES:
            raise ValueError('Artefact cible invalide.')
        normalized = _prepare(candidate, spec)
        expected_artifact = emit(candidate, normalized, artifact['language'])
        if any(artifact.get(k) != v for k, v in expected_artifact.items()):
            raise ValueError('Artefact différent de la régénération exacte : aucune exécution.')
        language = artifact['language']
        report['language'] = language
        report['compile_status'] = 'NOT_APPLICABLE' if language == 'python' else 'NON_COMPILE'
        report['source_sha256'] = artifact['source_sha256']
        _save(folder / 'artifact.json', expected_artifact)
        _save(folder / 'spec.json', normalized)
        _save(folder / 'candidate.json', candidate)
        cases = _cases(normalized, candidate)
        _save(folder / 'cases.json', cases)
        report['tests']['planned'] = len(cases)
        active = sum(c['precondition'] is True for c in cases)
        if not active:
            report.update(status='INCONCLUSIVE', reason='Aucun témoin de précondition dans l’échantillon ; aucune vacuité proclamée prouvée.')
            return report
        tool = _find_tool(language)
        if tool is None:
            report.update(status='NON_COMPILE', reason='Compilateur local indisponible ; source conservé, aucune exécution cible.')
            return report
        tool_hash = _sha(tool.read_bytes())
        report['tool'] = {'path': str(tool), 'sha256': tool_hash}
        # Legacy csc and Windows process cwd can fail beyond MAX_PATH. Keep a
        # unique short execution directory in this new project's workspace.
        # Both copies and the runtime are retained; neither is a pasted input.
        runtime = _folder(BASE / 'runtime', 'R')
        report['runtime_dir'] = str(runtime)
        source_archive = folder / ('program' + artifact['extension'])
        source_path = runtime / source_archive.name
        source_archive.write_text(artifact['source'], encoding='utf-8', newline='\n')
        source_path.write_text(artifact['source'], encoding='utf-8', newline='\n')
        harness = _harness(expected_artifact, normalized, cases)
        harness_archive = folder / ('harness' + artifact['extension'])
        harness_path = runtime / harness_archive.name
        harness_archive.write_text(harness, encoding='utf-8', newline='\n')
        harness_path.write_text(harness, encoding='utf-8', newline='\n')
        executable = runtime / ('run.exe' if os.name == 'nt' else 'run')
        if language != 'python':
            phase = 'compilation'
            if language == 'csharp':
                command = [tool, '/nologo', '/noconfig', '/target:exe', '/utf8output', '/out:' + str(executable), source_path, harness_path]
            else:
                command = [tool, '--edition=2021', '-C', 'opt-level=0', '-o', executable, harness_path]
            compilation = _process(command, runtime, 20)
            _save(folder / 'compilation.json', compilation)
            if compilation['exit_code'] or not executable.is_file():
                report.update(status='FAILED', reason='Compilation cible refusée ; voir sortie conservée.', compile_status='FAILED')
                return report
            report['compile_status'] = 'COMPILED'
            report['executable_sha256'] = _sha(executable.read_bytes())
            shutil.copy2(executable, folder / executable.name)
        if (source_path.read_text(encoding='utf-8') != expected_artifact['source']
                or source_archive.read_text(encoding='utf-8') != expected_artifact['source']
                or harness_path.read_text(encoding='utf-8') != harness
                or harness_archive.read_text(encoding='utf-8') != harness):
            raise ValueError('Source ou harness modifié avant exécution.')
        if _sha(tool.read_bytes()) != tool_hash:
            raise ValueError('Outil modifié avant exécution.')
        if language != 'python' and (_sha(executable.read_bytes()) != report['executable_sha256']
                                     or _sha((folder / executable.name).read_bytes()) != report['executable_sha256']):
            raise ValueError('Exécutable modifié avant exécution.')
        command = [tool, '-I', '-S', harness_path] if language == 'python' else [executable]
        phase = 'execution'
        output = _process(command, runtime, 5, _json([c['inputs'] for c in cases]) if language == 'python' else None)
        _save(folder / 'execution.json', output)
        if output['exit_code'] or output['stderr'].strip():
            report.update(status='FAILED', reason='Exécution cible en échec ; voir sortie conservée.', execution_status='FAILED')
            return report
        actual = [json.loads(line) for line in output['stdout'].splitlines()]
        report['tests']['executed'] = len(actual)
        report['execution_status'] = 'EXECUTED'
        if len(actual) != len(cases):
            report.update(status='FAILED', reason='Nombre de résultats différent du nombre de cas.')
            return report
        mismatches, counterexamples = [], []
        for index, (case, value) in enumerate(zip(cases, actual)):
            case['actual'] = value
            if type(value) is not type(case['expected']) or value != case['expected']:
                mismatches.append(index)
            if case['precondition']:
                report['tests']['contract_checked'] += 1
                env = dict(zip((p['name'] for p in normalized['parameters']), case['inputs']))
                case['actual_postcondition'] = _oracle(normalized['post_ast'], dict(env, result=value))
                if case['actual_postcondition'] is not True:
                    counterexamples.append(index)
        _save(folder / 'cases.json', cases)
        report['tests'].update(differential_mismatch_indices=mismatches, counterexample_indices=counterexamples)
        if mismatches:
            report.update(status='FAILED', reason='Code exécuté différent de l’oracle AST indépendant sur des cas conservés.')
        elif counterexamples:
            report.update(status='COUNTEREXAMPLE', reason='Postcondition violée sur des entrées exécutées ; contre-exemples conservés.')
        else:
            report.update(status='TESTED_FINITE', reason='Exécution réelle conforme à l’AST et au contrat sur les seuls cas conservés.')
        if (_sha(tool.read_bytes()) != tool_hash or source_path.read_text(encoding='utf-8') != expected_artifact['source']
                or source_archive.read_text(encoding='utf-8') != expected_artifact['source']
                or harness_path.read_text(encoding='utf-8') != harness
                or harness_archive.read_text(encoding='utf-8') != harness
                or language != 'python' and (_sha(executable.read_bytes()) != report['executable_sha256']
                                            or _sha((folder / executable.name).read_bytes()) != report['executable_sha256'])):
            report.update(status='INCONCLUSIVE', reason='Source/harness/outil modifié pendant la validation.')
    except subprocess.TimeoutExpired as exc:
        report.update(status='INCONCLUSIVE', reason='Délai borné dépassé ; aucun succès accepté.')
        report['compile_status' if phase == 'compilation' else 'execution_status'] = 'TIMEOUT'
        _save(folder / 'timeout.json', {'phase': phase, 'timeout': exc.timeout, 'stdout': str(exc.stdout), 'stderr': str(exc.stderr)})
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        report.update(status='REFUSED', reason=str(exc))
    except (OSError, RuntimeError) as exc:
        report.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc))
    finally:
        report['evidence_files'] = [str(p) for p in sorted(folder.iterdir()) if p.is_file()]
        report['files_sha256'] = {p.name: _sha(p.read_bytes()) for p in folder.iterdir() if p.is_file() and p.name != 'report.json'}
        if runtime is not None:
            report['runtime_files_sha256'] = {p.name: _sha(p.read_bytes()) for p in runtime.iterdir() if p.is_file()}
            report['evidence_files'] += [str(p) for p in sorted(runtime.iterdir()) if p.is_file()]
        _save(folder / 'report.json', report)
    return report


def prove(candidate, spec, output_dir):
    folder = _folder(output_dir, 'proof')
    report = {'status': 'INCONCLUSIVE', 'reason': 'NOT_COMPLETED', 'evidence_dir': str(folder),
              'language_equivalence_proved': False,
              'scope': 'Certificat sur la traduction du programme C# généré et précondition ⇒ postcondition. Pas preuve formelle des backends Python/Rust ou compilateurs.'}
    try:
        normalized = _prepare(candidate, spec)
        artifact = emit(candidate, normalized, 'csharp')
        _save(folder / 'spec.json', normalized)
        _save(folder / 'candidate.json', candidate)
        if not any(c['precondition'] is True for c in _cases(normalized, candidate)):
            report['reason'] = 'Aucun témoin de précondition dans l’échantillon ; preuve non lancée.'
            return report
        mapping = dict(artifact['parameter_mapping'], result='result')
        pre = _render(normalized['pre_ast'], mapping, 'csharp')
        post = _render(normalized['post_ast'], mapping, 'csharp')
        contract = 'public static class Contract\n{\n    public static bool Spec(' + _parameters(normalized, 'csharp', True) + ')\n    {\n        return ((!(' + pre + ')) || (' + post + '));\n    }\n}\n'
        (folder / 'generated-program.cs').write_text(artifact['source'], encoding='utf-8', newline='\n')
        (folder / 'generated-contract.cs').write_text(contract, encoding='utf-8', newline='\n')
        report['input_sha256'] = {'source': _sha(artifact['source']), 'contract': _sha(contract)}
        from legacy_bridge import load_bridge
        bridge = load_bridge()
        runtime = _folder(BASE / 'runtime', 'P')
        report['runtime_dir'] = str(runtime)
        result = bridge.analyze(artifact['source'], contract, output_root=runtime)
        report.update(status=result['verdict'], reason=result['reason'], bridge_result=result)
        if result['verdict'] not in ('PROUVÉ_CONDITIONNEL', 'RÉFUTÉ_CONDITIONNEL', 'INCONCLUSIF'):
            report.update(status='INCONCLUSIVE', reason='Verdict du pont non reconnu.')
        if ((folder / 'generated-program.cs').read_text(encoding='utf-8') != artifact['source']
                or (folder / 'generated-contract.cs').read_text(encoding='utf-8') != contract):
            report.update(status='INCONCLUSIVE', reason='Entrée de preuve modifiée pendant la vérification.')
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, RecursionError) as exc:
        report.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc))
    finally:
        report['files_sha256'] = {p.name: _sha(p.read_bytes()) for p in folder.iterdir() if p.is_file() and p.name != 'report.json'}
        _save(folder / 'report.json', report)
    return report
