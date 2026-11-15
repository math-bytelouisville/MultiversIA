"""Compile and exercise generated scoring only, using the qualified local SDK."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

BASE = Path(__file__).resolve().parent
PREVIOUS = BASE.parent / 'SYNTHESE_MATHS_CODE_V0.2.0'
RUST_ROOT = PREVIOUS / 'toolchains/rust/install'
MANIFEST = PREVIOUS / 'toolchains/rust/manifest.json'
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def load(path, maximum=2_000_000):
    with Path(path).open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Document trop volumineux.')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Clé JSON répétée : ' + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nombre JSON non fini.')))


def owned(path):
    path = Path(path).absolute()
    if not path.resolve().is_relative_to(BASE):
        raise ValueError('Sortie hors du chantier V0.3.')
    for part in [path, *path.parents]:
        if part == BASE.parent:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Lien de dossier refusé.')
    return path.resolve()


def toolchain():
    m = load(MANIFEST)
    if (m.get('schema_version') != 'mia-rust-toolchain-1' or m.get('status') != 'QUALIFIED_LOCAL'
            or m.get('qualification', {}).get('status') != 'COMPILED_AND_EXECUTED'):
        raise ValueError('Compilateur Rust local non qualifié.')
    root = RUST_ROOT.resolve()
    if Path(m['sysroot']).resolve() != root or Path(m['rustc_path']).resolve() != root / 'bin/rustc.exe':
        raise ValueError('Chemin du compilateur différent du profil.')
    linker = root / 'lib/rustlib/x86_64-pc-windows-gnu/bin/self-contained/x86_64-w64-mingw32-gcc.exe'
    args = ['-C', 'linker=' + str(linker), '-C', 'link-self-contained=yes']
    paths = [str(linker.parent), str(root / 'bin')]
    if m['extra_arguments'] != args or m['path_entries'] != paths:
        raise ValueError('Arguments du compilateur hors profil.')
    expected = m.get('files_sha256')
    if type(expected) is not dict or not 1 <= len(expected) <= 4096:
        raise ValueError('Inventaire du compilateur invalide.')
    actual = {}
    for path in root.rglob('*'):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Lien dans le compilateur.')
        if path.is_file():
            actual[path.relative_to(root).as_posix()] = sha(path)
    normalized = {}
    for relative, digest in expected.items():
        if (type(relative) is not str or type(digest) is not str
                or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('Empreinte du compilateur invalide.')
        p = Path(relative)
        if p.is_absolute() or '..' in p.parts or ':' in relative:
            raise ValueError('Chemin du compilateur invalide.')
        key = p.as_posix()
        if key in normalized:
            raise ValueError('Chemin du compilateur répété.')
        normalized[key] = digest
    if actual != normalized or actual.get('bin/rustc.exe') != m['rustc_sha256']:
        raise ValueError('Fichier du compilateur modifié, ajouté ou absent.')
    return {'path': str(root / 'bin/rustc.exe'), 'extra_arguments': args,
            'path_entries': paths, 'manifest_sha256': sha(MANIFEST), 'file_count': len(actual)}


def environment(tool):
    env = dict(os.environ)
    for key in ('RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS',
                'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'):
        env.pop(key, None)
    env['PATH'] = os.pathsep.join(tool['path_entries'] + [env.get('PATH', '')])
    return env


def compile_model(model, output_dir):
    import scoring_model
    import scoring_rust
    normalized = scoring_model.validate_model(model)
    folder = owned(output_dir) / ('rust-' + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    report = {'status': 'INCONCLUSIVE', 'folder': str(folder), 'scope': 'Generated rational scoring kernel; no HTTP or persistence'}
    try:
        save(folder / 'model.json', normalized)
        source = scoring_rust.emit_rust(normalized)
        (folder / 'main.rs').write_text(source, encoding='utf-8', newline='\n')
        inputs_before = {name: sha(folder / name) for name in ('model.json', 'main.rs')}
        tool = toolchain()
        command = [tool['path'], '--edition=2021', '-O', str(folder / 'main.rs'), '-o', str(folder / 'scoring.exe'), *tool['extra_arguments']]
        process = subprocess.run(command, cwd=folder, env=environment(tool), capture_output=True,
                                 encoding='utf-8', errors='replace', timeout=90, creationflags=FLAGS)
        save(folder / 'compilation.json', {'command': command, 'exit_code': process.returncode,
                                          'stdout': process.stdout, 'stderr': process.stderr})
        if process.returncode != 0 or not (folder / 'scoring.exe').is_file():
            raise RuntimeError('Compilation refusée ; voir compilation.json.')
        if any(sha(folder / name) != digest for name, digest in inputs_before.items()):
            raise RuntimeError('Entrée modifiée pendant la compilation ; artefact non qualifié.')
        if toolchain() != tool:
            raise RuntimeError('Compilateur modifié pendant la compilation.')
        report.update(status='COMPILED', toolchain=tool, input_files_sha256=inputs_before,
                      files_sha256={name: sha(folder / name) for name in ('model.json', 'main.rs', 'scoring.exe')})
    except Exception as exc:
        report['reason'] = type(exc).__name__ + ': ' + str(exc)
    save(folder / 'build.json', report)
    return report


def run_cases(build, cases, output_dir):
    import scoring_model
    import scoring_rust
    if type(cases) is not list or not 1 <= len(cases) <= 4096:
        raise ValueError('La campagne attend de 1 à 4096 cas.')
    cases = [scoring_model.validate_case(case) for case in cases]
    folder = owned(build['folder'])
    if build.get('status') != 'COMPILED' or load(folder / 'build.json') != build:
        raise ValueError('Reçu de compilation absent ou différent.')
    if set(build['files_sha256']) != {'model.json', 'main.rs', 'scoring.exe'}:
        raise ValueError('Inventaire de compilation invalide.')
    if any(sha(folder / name) != digest for name, digest in build['files_sha256'].items()):
        raise ValueError('Artefact généré modifié depuis la compilation.')
    model = scoring_model.validate_model(load(folder / 'model.json'))
    if (folder / 'main.rs').read_bytes() != scoring_rust.emit_rust(model).encode('utf-8'):
        raise ValueError('Le code Rust ne correspond plus au modèle.')
    tool = toolchain()
    if tool != build['toolchain']:
        raise ValueError('Compilateur différent depuis la compilation.')
    text = '\n'.join(scoring_rust.encode_case(case) for case in cases) + '\n'
    if len(text.encode('utf-8')) > 8 * 1024 * 1024:
        raise ValueError('Entrée de campagne trop volumineuse.')
    evidence = owned(output_dir) / ('execution-' + uuid.uuid4().hex)
    evidence.mkdir(parents=True, exist_ok=False)
    save(evidence / 'cases.json', cases)
    (evidence / 'input.txt').write_text(text, encoding='utf-8', newline='\n')
    report = {'status': 'INCONCLUSIVE', 'build': str(folder / 'build.json'), 'evidence_dir': str(evidence), 'count': len(cases)}
    try:
        process = subprocess.run([str(folder / 'scoring.exe')], input=text, cwd=evidence,
                                 env=environment(tool), capture_output=True, encoding='utf-8',
                                 errors='strict', timeout=60, creationflags=FLAGS)
        (evidence / 'stdout.txt').write_text(process.stdout, encoding='utf-8')
        (evidence / 'stderr.txt').write_text(process.stderr, encoding='utf-8')
        report['exit_code'] = process.returncode
        lines = process.stdout.splitlines()
        if process.returncode != 0 or len(lines) != len(cases):
            raise RuntimeError('Exécution incomplète ou refusée ; consulter les sorties conservées.')
        values = [scoring_rust.parse_result(line) for line in lines]
        if any(sha(folder / name) != digest for name, digest in build['files_sha256'].items()):
            raise RuntimeError('Artefact modifié pendant son exécution.')
        report.update(status='EXECUTED', results=values)
    except Exception as exc:
        report['reason'] = type(exc).__name__ + ': ' + str(exc)
    save(evidence / 'result.json', report)
    return report
