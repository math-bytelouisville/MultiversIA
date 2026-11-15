"""Build the explicit qcm-mvc-1 Rust adapter, not an ASP.NET translator.

The coordinator validates the full MVC source profile and archives its rendered
HTML. This builder checks the pure module, fixed signatures and bounded inputs,
then preserves supplied bytes and compiles an explicitly authored HTTP adapter.
It never starts a server or downloads a compiler.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import uuid

import module_backends
from module_model import validate_module

BASE = Path(__file__).resolve().parent
HOST = BASE / 'qcm_templates/host.rs'
SIGNATURES = {
    'QcmDemo.QuizRules.IsCorrect': ([('answer', 'int'), ('correct', 'int')], 'bool'),
    'QcmDemo.QuizRules.Score': ([('a', 'bool'), ('b', 'bool'), ('c', 'bool')], 'int'),
    'QcmDemo.QuizRules.Passed': ([('score', 'int')], 'bool'),
}
LIMITATIONS = [
    'Known qcm-mvc-1 adapter written explicitly; not an automatic ASP.NET/Razor translation.',
    'MVC HTML and CSS/JS/data bytes are preserved; this builder does not prove their MVC origin.',
    'COMPILED is not an HTTP execution result, a browser test or a universal module proof.',
    'Local sequential HTTP demo: 127.0.0.1, one request per connection, 8192-byte ASCII headers, 2-second I/O timeouts; no TLS, sessions or database.',
    'Full source inventory, profile admission, process lifecycle and cross-language qualification belong to qcm_demo.',
]


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def _bytes(value, label, maximum):
    if type(value) is not bytes or not value or len(value) > maximum:
        raise ValueError(label + ': nonempty bytes within the declared bound required')
    value.decode('utf-8')
    return value


def _data(data, raw):
    _bytes(raw, 'questions.json', 65536)
    if type(data) is not dict or set(data) != {'title', 'questions'}:
        raise ValueError('Question data requires exactly title and questions')
    # Compare canonical typed JSON, not Python equality (where True == 1).
    if json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True, allow_nan=False) != json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False):
        raise ValueError('Exact data bytes do not represent the supplied data object')
    if type(data['title']) is not str or not 1 <= len(data['title']) <= 512:
        raise ValueError('Invalid quiz title')
    questions = data['questions']
    if type(questions) is not list or len(questions) != 3:
        raise ValueError('This profile requires exactly three questions')
    for i, question in enumerate(questions):
        if type(question) is not dict or set(question) != {'id', 'text', 'options', 'correct'}:
            raise ValueError('Question fields outside the known profile')
        if question['id'] != 'q' + str(i + 1) or type(question['text']) is not str or not 1 <= len(question['text']) <= 4096:
            raise ValueError('Invalid question identity or text')
        if type(question['options']) is not list or len(question['options']) != 4 or any(type(x) is not str or not 1 <= len(x) <= 1024 for x in question['options']):
            raise ValueError('Exactly four bounded text options required')
        if type(question['correct']) is not int or not 0 <= question['correct'] <= 3:
            raise ValueError('Correct-answer index must be an integer from zero to three')
    return {'title': data['title'], 'questions': [{k: q[k] for k in ('id', 'text', 'options')} for q in questions]}


def build_qcm_rust(module, rendered_html, data, assets, output=None, *, data_bytes):
    """Return a structured build receipt. Explicit execution is a separate action.

    assets must contain only style.css and app.js bytes. data_bytes is mandatory
    so formatting and all original data bytes survive the parsed representation.
    The caller owns full source/profile admission; arbitrary paths are not read.
    """
    folder = (Path(output) if output is not None else BASE / 'runtime/qcm') / ('rust-' + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    folder = folder.resolve()
    report = {'status': 'REFUSED', 'reason': 'Build not completed', 'language': 'rust', 'profile': 'qcm-mvc-1',
              'folder': str(folder), 'compile_status': 'NOT_COMPILED', 'execution_status': 'NOT_EXECUTED',
              'formal_scope': 'NOT_PROVED_MODULE', 'limitations': list(LIMITATIONS)}
    try:
        normalized = validate_module(module)
        units = {unit['qualified_name']: unit for unit in normalized['units']}
        if len(normalized['units']) != 3 or set(units) != set(SIGNATURES):
            raise ValueError('Exact IsCorrect, Score and Passed units required')
        for name, (parameters, result) in SIGNATURES.items():
            unit = units[name]
            if [(p['name'], p['type']) for p in unit['parameters']] != parameters or unit['return_type'] != result:
                raise ValueError('QCM entry signature mismatch: ' + name)
        _bytes(rendered_html, 'rendered MVC HTML', 1_048_576)
        if type(assets) is not dict or set(assets) != {'style.css', 'app.js'}:
            raise ValueError('Exactly style.css and app.js assets required')
        for name, raw in assets.items():
            _bytes(raw, name, 1_048_576)
        public = _data(data, data_bytes)
        artifact = module_backends.emit_module(normalized, 'rust')
        template = HOST.read_bytes()
        source = template.decode('utf-8')
        for name, token in [('IsCorrect', '__IS_CORRECT__'), ('Score', '__SCORE__'), ('Passed', '__PASSED__')]:
            unit = units['QcmDemo.QuizRules.' + name]
            source = source.replace(token, artifact['unit_mapping'][unit['id']]['target_symbol'])
        source = source.replace('__EXPECTED__', ','.join(str(q['correct']) for q in data['questions']))
        if any(token in source for token in ('__IS_CORRECT__', '__SCORE__', '__PASSED__', '__EXPECTED__')):
            raise ValueError('Unresolved host template token')
        (folder / 'index.html').write_bytes(rendered_html)
        for name, raw in assets.items():
            (folder / name).write_bytes(raw)
        (folder / 'questions.json').write_bytes(data_bytes)
        (folder / 'public-questions.json').write_text(json.dumps(public, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8', newline='\n')
        (folder / 'rules.rs').write_text(artifact['source'], encoding='utf-8', newline='\n')
        (folder / 'host.rs').write_text(source, encoding='utf-8', newline='\n')
        _save(folder / 'module.json', normalized)
        _save(folder / 'artifact.json', artifact)
        names = ('index.html', 'style.css', 'app.js', 'questions.json', 'public-questions.json', 'rules.rs', 'host.rs', 'module.json', 'artifact.json')
        before = {name: _sha((folder / name).read_bytes()) for name in names}
        report.update(module_revision=normalized['revision_id'], source_files_sha256=before, sources_sha256=before,
                      host_template_sha256=_sha(template), unit_mapping=artifact['unit_mapping'])
        toolchain = module_backends._toolchain('rust')
        if toolchain is None:
            report.update(status='NON_COMPILE', compile_status='NON_COMPILE', reason='No qualified or existing Rust compiler available')
            return report
        exe = folder / 'server.exe'
        command = [str(toolchain['path']), '--edition=2021', *toolchain['extra_args'], str(folder / 'host.rs'), '-o', str(exe)]
        report['command'] = command
        report['toolchain'] = dict(toolchain, path=str(toolchain['path']))
        compilation = module_backends._process(command, folder, 45, toolchain=toolchain)
        _save(folder / 'compilation.json', compilation)
        if any(_sha((folder / name).read_bytes()) != digest for name, digest in before.items()) or HOST.read_bytes() != template:
            report.update(status='FAILED', reason='Build source or host template changed during compilation')
            return report
        if module_backends._toolchain('rust') != toolchain:
            report.update(status='FAILED', reason='Rust toolchain changed during compilation')
            return report
        if compilation['exit_code'] or not exe.is_file():
            report.update(status='FAILED', compile_status='FAILED', reason='Rust compilation failed; see compilation.json')
            return report
        report.update(status='COMPILED', compile_status='COMPILED', executable=str(exe), executable_sha256=_sha(exe.read_bytes()),
                      reason='Known QCM HTTP adapter compiled with the generated pure module; server not started')
    except subprocess.TimeoutExpired:
        report.update(status='INCONCLUSIVE', compile_status='TIMEOUT', reason='Rust compilation exceeded 45 seconds; no server started')
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError) as exc:
        report['reason'] = str(exc)
    finally:
        _save(folder / 'build.json', report)
    return report
