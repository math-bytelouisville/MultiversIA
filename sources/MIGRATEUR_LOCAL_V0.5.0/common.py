"""Local bounded archives and the separately qualified V0.5 foundations."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import uuid

BASE = Path(__file__).resolve().parent


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def load_json(path, maximum=4 * 1024 * 1024):
    with Path(path).open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('Document trop volumineux.')
    def object_pairs(items):
        value = {}
        for k, v in items:
            if k in value:
                raise ValueError('Clé JSON répétée : ' + k)
            value[k] = v
        return value
    return json.loads(raw, object_pairs_hook=object_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nombre JSON non fini.')))


def owned(path):
    p = Path(path).absolute()
    if not p.resolve().is_relative_to(BASE):
        raise ValueError('Les nouvelles écritures doivent rester dans V0.5.')
    for parent in [p, *p.parents]:
        if parent == BASE.parent:
            break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Lien de répertoire refusé.')
    return p.resolve()


def save(path, value):
    p = owned(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    temporary = p.with_name(p.name + '.' + uuid.uuid4().hex + '.pending')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(p)


def campaign(output_root=None, kind='run'):
    parent = owned(output_root or BASE / 'runs')
    folder = parent / (kind + '-' + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def foundations():
    manifest = load_json(BASE / 'FOUNDATION_V050.json')
    # V0.5 evolves its own copies. BASELINE_V2 remains historical evidence only.
    # This content pin is not a signature or a claim that qualification passed.
    if (type(manifest) is not dict or manifest.get('schema') != 'mia-local-foundations-1'
            or manifest.get('version') != '0.5.0'):
        raise ValueError('Manifeste des fondations V0.5 invalide.')
    expected = manifest['files_sha256']
    if type(expected) is not dict or set(expected) != {'module_model.py', 'math_core.py', 'module_backends.py', 'code_backends.py', 'legacy_bridge.py', 'module_import.ps1'}:
        raise ValueError('Inventaire des fondations incomplet.')
    if any(sha(BASE / name) != digest for name, digest in expected.items()):
        raise ValueError('Une fondation V0.5 a changé ; nouveau gel et qualification requis.')
    return dict(expected)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def archive_request(folder, request, maximum=16 * 1024 * 1024):
    """Preserve JSON-compatible input before semantic validation, within a stated bound."""
    # Count the expanded JSON tree, including repeated aliases, before dumps.
    # Otherwise a small Python DAG could expand exponentially during encoding.
    active = set()
    nodes = estimated = 0
    def visit(value, depth=0):
        nonlocal nodes, estimated
        nodes += 1
        if nodes > 500000 or depth > 96:
            raise ValueError('Demande hors borne structurelle d’archive.')
        estimated += 8 + 2 * depth
        if type(value) is str:
            if len(value) > maximum:
                raise ValueError('Texte dépassant la borne d’archive.')
            estimated += len(value.encode('utf-8')) * 6
        elif value is None or type(value) in (bool, float):
            estimated += 32
        elif type(value) is int:
            if value.bit_length() > 4096:
                raise ValueError('Entier dépassant la borne d’archive.')
            estimated += len(str(value))
        elif type(value) in (dict, list):
            if id(value) in active:
                raise ValueError('Cycle dans la demande ; JSON impossible.')
            active.add(id(value))
            try:
                if type(value) is dict:
                    for key, item in value.items():
                        if type(key) is not str:
                            raise ValueError('Clé de demande non textuelle.')
                        visit(key, depth + 1)
                        visit(item, depth + 1)
                else:
                    for item in value:
                        visit(item, depth + 1)
            finally:
                active.remove(id(value))
        else:
            raise ValueError('Type de demande non représentable en JSON.')
        if estimated > maximum:
            raise ValueError('Demande dépassant la borne conservatrice d’archive de16Mio.')
    visit(request)
    try:
        raw = json.dumps(request, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError('Demande non archivable en JSON UTF8 fini : ' + str(exc)) from exc
    if len(raw) > maximum:
        raise ValueError('Demande dépassant la borne d’archive de16Mio ; entrée non copiée.')
    save(Path(folder) / 'request.json', request)
    return {'preserved': True, 'file': str(Path(folder) / 'request.json'),
            'sha256': sha(Path(folder) / 'request.json')}
