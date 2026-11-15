"""Exact, curated QCM Studio profile. No C# parsing or arbitrary code execution.

All admissible regular-file bytes, even files outside the profile, are archived
before a profile mismatch is reported. Unsafe/oversized trees are refused without
claiming a complete snapshot. Receipts never turn preservation into equivalence.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

PROFILE_ID = 'qcm-studio-v0.2.0-20716b63'
SOURCE_VERSION = 'SYNTHESE_MATHS_CODE_V0.2.0/qcm_studio'
MAX_FILES, MAX_ENTRIES, MAX_DEPTH = 64, 96, 8
MAX_FILE_BYTES, MAX_TOTAL_BYTES = 2 * 1024 * 1024, 8 * 1024 * 1024
PROFILE_SHA256 = {
    'Program.cs': 'c5b53894e1a7b9cdb7b36f28c889982996cad8556764334272a39567a78c4fb9',
    'QcmDemo.csproj': '1dfff0e68753b755ad9f91a74b3244e46be0af9785d4be9b0d493dd9b3c9e17e',
    'Rules.cs': '8f26d7134e9dcf1f006923438d069c7fe30246b867732441ca801b0403af7fae',
    'Controllers/QuizController.cs': 'f4d54be857d5ba055e73fa9470a5053d74491578e8b2156e45066c1c1ed3ae62',
    'Controllers/StudioController.cs': '13354c248211e068ba53ec59da0caff60087b186f6e3630cc99c6d4072e3aad8',
    'Data/questions.json': '66af1188acdd6a79db5ddafe5ddcf0657766cf926a22a52c7be83ce36b15847c',
    'Models/QuizModels.cs': '35557d7cc4b623e7d1e4e79b9ef239edae84c730044a4ae0585b0d7bcae536c0',
    'Models/StudioModels.cs': 'dab73245605f119a3f7c707c1b08bd35b4b72b9e364bed5b3da032b16f34b742',
    'Services/QuizCatalog.cs': 'a4613a59d22294e2b74780c2caea3fc7b84506a561b64ac6352cbf9dc8cb1b0f',
    'Services/QuizService.cs': '957744f6d432c7ac3db97887fb77d9307d078750e3b82a08aa0bc6473e6f35fd',
    'Services/StudioExtensions.cs': '5b96e49de6c7cb6b5d513a8348ea926687b32bcc0df151832bd2bdd6b07c0d2f',
    'Services/StudioStore.cs': 'dba50f86269d45247c00bcb0705a9709383a7d9d48e41504c5767df86c7aa774',
    'wwwroot/app.js': '20716b63727047b970197b5de83379ed615269739e6c2f40aed0995b19683ee4',
    'wwwroot/style.css': 'c4432858fe1917ea42b4491a9c18de92cac1fb5fba3156d61c5ba9b8f9cd09c7',
    'Views/Quiz/Index.cshtml': '3804bfff884214547ca56182bbfe95542145d8ce52f8578835aadbfd5a5c4a5e',
    'Views/Studio/Index.cshtml': '757bd1039a29f42a23fa82d064410181fedeed44ec3c76e8c1369b2a337c2c89',
}
EXCLUDED = frozenset(('Rules.cs', 'Controllers/QuizController.cs', 'Models/QuizModels.cs',
    'Services/QuizCatalog.cs', 'Services/QuizService.cs', 'Views/Quiz/Index.cshtml', 'Data/questions.json'))
PROFILE_DIRS = frozenset(str(p).replace('\\', '/') for name in PROFILE_SHA256
    for p in Path(name).parents if str(p) != '.')
UNTRANSLATED = [
    'Routes HTTP, middleware et rendu MVC/Razor',
    'Persistance, verrouillage, sauvegardes et récupération du stockage',
    'Compte local, catalogue de jeux et transitions des tentatives',
    'Éditeur, carte, navigation, accessibilité et brouillons du navigateur',
    'Ordre aléatoire, horodatages, identifiants et classement des tentatives',
    'Données utilisateur et stockage navigateur : hors de cet import de sources',
]


class Refused(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _is_link(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _safe_path(path: Path, *, missing: bool = False) -> Path:
    """Check original spelling before resolve, including every existing ancestor."""
    if '..' in path.parts or any(':' in part for part in path.parts[1:]):
        raise Refused('Chemin relatif parent ou flux alternatif refusé.')
    path = Path(os.path.abspath(path))
    if str(path).startswith(('\\\\', '//')) or len(str(path)) > 2048:
        raise Refused('Chemin réseau ou trop long refusé.')
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise Refused('Chemin absent : ' + str(item))
        if _is_link(info):
            raise Refused('Lien ou point de réanalyse refusé : ' + str(item))
    return path


def _identity(info):
    # Python 3.12 Windows stat/fstat expose different historical meanings for
    # st_ctime (creation vs change); it is not a portable identity component.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_file(path: Path, root: Path) -> bytes:
    _safe_path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
        raise Refused('Fichier spécial, lien physique ou taille refusée : ' + path.name)
    if os.name == 'nt':
        # Disallow write/delete sharing while reading. Verify the opened handle's
        # final path, closing the directory-substitution gap of path-only checks.
        import ctypes
        from ctypes import wintypes
        import msvcrt
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        handle = k32.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == wintypes.HANDLE(-1).value:
            raise Refused('Ouverture exclusive en lecture impossible : ' + path.name)
        try:
            buffer = ctypes.create_unicode_buffer(4096)
            count = k32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
            final = buffer.value.removeprefix('\\\\?\\')
            if not count or count >= len(buffer) or os.path.normcase(final) != os.path.normcase(str(path)):
                raise Refused('Le chemin ouvert a changé : ' + path.name)
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            handle = None
        finally:
            if handle is not None:
                k32.CloseHandle(handle)
    else:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if _identity(before) != _identity(opened):
            raise Refused('Fichier remplacé pendant ouverture : ' + path.name)
        data = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(data) > MAX_FILE_BYTES or _identity(before) != _identity(after):
        raise Refused('Fichier modifié pendant lecture : ' + path.name)
    _safe_path(path)
    if _identity(before) != _identity(path.lstat()):
        raise Refused('Fichier remplacé après lecture : ' + path.name)
    if not path.is_relative_to(root):
        raise Refused('Fichier hors du répertoire source.')
    return data


def _scan(root: Path):
    _safe_path(root)
    if not root.is_dir():
        raise Refused('Le dossier source doit être qcm_studio.')
    files, directories, seen, entries, total = {}, [], set(), 0, 0

    def visit(folder: Path, depth: int):
        nonlocal entries, total
        if depth > MAX_DEPTH:
            raise Refused('Profondeur maximale dépassée.')
        _safe_path(folder)
        with os.scandir(folder) as scan:
            # Bound enumeration before materialising/sorting arbitrary trees.
            batch = []
            for entry in scan:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise Refused('Trop de fichiers ou dossiers.')
                batch.append(entry)
        for entry in sorted(batch, key=lambda e: e.name):
            rel = Path(entry.path).relative_to(root).as_posix()
            if any(c in entry.name for c in (':', '\\')) or entry.name.rstrip(' .') != entry.name:
                raise Refused('Nom de fichier ambigu refusé.')
            if rel.casefold() in seen:
                raise Refused('Collision de noms insensibles à la casse.')
            seen.add(rel.casefold())
            info = entry.stat(follow_symlinks=False)
            if _is_link(info):
                raise Refused('Lien ou point de réanalyse refusé : ' + rel)
            if stat.S_ISDIR(info.st_mode):
                directories.append(rel)
                visit(Path(entry.path), depth + 1)
            elif stat.S_ISREG(info.st_mode):
                if len(files) >= MAX_FILES:
                    raise Refused('Trop de fichiers.')
                data = _read_file(Path(entry.path), root)
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise Refused('Taille totale maximale dépassée.')
                files[rel] = data
            else:
                raise Refused('Objet non régulier refusé : ' + rel)
    visit(root, 0)
    _safe_path(root)
    return files, sorted(directories)


def _mapping():
    rows = [
        ('StudioStore.Points', 'Services/StudioStore.cs', 167, 174, 'expressions.point', ['Fraction', 'Calculate', 'Result']),
        ('StudioStore.Calculate', 'Services/StudioStore.cs', 175, 179, 'expressions.score, expressions.percent, correct_count', ['Points', 'Fraction', 'Result']),
        ('StudioStore.Result', 'Services/StudioStore.cs', 180, 189, 'expressions.passed, score_display, percent_display, points', ['Calculate', 'Points', 'Fraction.CompareTo', 'Fraction.OfDecimal', 'Fraction.Rounded']),
        ('Fraction', 'Services/StudioExtensions.cs', 29, 54, 'arithmétique rationnelle exacte et présentation', ['Points', 'Calculate', 'Result']),
    ]
    return [dict(symbol=s, path=p, start_line=a, end_line=b, source_sha256=PROFILE_SHA256[p], target=t,
        connections=c, kind='CURATED_MAPPING', limitation='Result : calcul observable ; projection HTTP/texte de correction non traduits.')
        for s, p, a, b, t, c in rows]


def create_import(source_dir, output_dir) -> dict:
    """Create a unique immutable receipt. Unsafe output paths raise ValueError.

    A REFUSED receipt may contain a complete byte snapshot of a mismatched but
    admissible tree. It NEVER grants provenance/model admission to such a tree.
    No user state, browser cache, builds or dependencies are captured implicitly.
    """
    out = _safe_path(Path(output_dir), missing=True)
    raw_source = Path(os.path.abspath(source_dir))
    if out == raw_source or out.is_relative_to(raw_source) or raw_source.is_relative_to(out):
        raise Refused('Source et dossier de sortie doivent être séparés, sans imbrication.')
    out.mkdir(parents=True, exist_ok=True)
    _safe_path(out)
    folder = out / ('import-' + uuid.uuid4().hex)
    folder.mkdir(exist_ok=False)
    receipt = dict(schema='mia-studio-profile-import-1', status='REFUSED', reason='Import non terminé.',
        profile_id=PROFILE_ID, source_dir=str(raw_source), source_inventory=[], source_directories=[],
        snapshot_dir=None, snapshot_complete=False, provenance=None, unsupported=[],
        coverage=dict(source_bytes='NOT_CAPTURED', profile='NOT_RECOGNIZED', mapping='NOT_ADMITTED',
            behavioral_equivalence='NOT_PROVED', translated_scope=[], untranslated=list(UNTRANSLATED)),
        limitations=['Profil connu et correspondance rédigée explicitement ; aucun analyseur C# général.',
            'La double lecture détecte les changements observés ; aucune garantie contre un adversaire contrôlant le système de fichiers.',
            'Les octets sources sont archivés ; les données utilisateur et les brouillons du navigateur ne sont pas importés.'],
        evidence_dir=str(folder))
    try:
        source = _safe_path(Path(source_dir))
        files, directories = _scan(source)
        receipt['source_directories'] = directories
        for rel, data in sorted(files.items()):
            role = 'EXCLUDED' if rel in EXCLUDED else 'ACTIVE' if rel in PROFILE_SHA256 else 'UNKNOWN'
            receipt['source_inventory'].append(dict(path=rel, sha256=_sha(data), size=len(data), role=role))
        # Capture memory buffers, not a second uncontrolled copy from paths.
        snapshot = folder / 'snapshot'
        snapshot.mkdir()
        for rel in directories:
            (snapshot / rel).mkdir(parents=True, exist_ok=True)
        for rel, data in files.items():
            target = snapshot / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(data)
            if target.read_bytes() != data:
                raise Refused('Vérification des octets archivés échouée : ' + rel)
        receipt['snapshot_dir'] = str(snapshot)
        again, dirs_again = _scan(source)
        if files != again or directories != dirs_again:
            raise Refused('La source a changé pendant cet import ; instantané non qualifié.')
        receipt['snapshot_complete'] = True
        receipt['coverage']['source_bytes'] = 'PRESERVED_EXACT'
        found = {row['path']: row['sha256'] for row in receipt['source_inventory']}
        differences = ([dict(kind='MISSING_FILE', path=p) for p in sorted(PROFILE_SHA256.keys() - found.keys())] +
            [dict(kind='UNKNOWN_FILE', path=p) for p in sorted(found.keys() - PROFILE_SHA256.keys())] +
            [dict(kind='MODIFIED_FILE', path=p, expected_sha256=PROFILE_SHA256[p], actual_sha256=found[p])
                for p in sorted(found.keys() & PROFILE_SHA256.keys()) if found[p] != PROFILE_SHA256[p]] +
            [dict(kind='UNKNOWN_DIRECTORY', path=p) for p in sorted(set(directories) - PROFILE_DIRS)])
        receipt['unsupported'] = differences
        if differences:
            raise Refused('Le dossier ne correspond pas exactement au profil connu ; aucune traduction admise.')
        stable_files = [{k: row[k] for k in ('path', 'sha256', 'role')} for row in receipt['source_inventory']]
        receipt['provenance'] = dict(profile_id=PROFILE_ID, source_version=SOURCE_VERSION,
            source_tree_sha256=_sha(_json(stable_files).encode('utf-8')), source_files=stable_files,
            mapping=_mapping(), mapping_kind='CURATED_KNOWN_PROFILE_NOT_GENERIC_PARSER')
        receipt['coverage'].update(profile='RECOGNIZED_EXACT', mapping='CURATED_SCORING_ONLY',
            translated_scope=['Points', 'Calculate', 'Result : calcul seulement', 'Fraction : arithmétique et arrondi'])
        receipt.update(status='IMPORTED', reason='16 fichiers reconnus et conservés, dont 7 exclus du projet compilé.')
    except (OSError, ValueError) as exc:
        receipt['reason'] = str(exc)
    receipt_path = folder / 'receipt.json'
    with receipt_path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return receipt
