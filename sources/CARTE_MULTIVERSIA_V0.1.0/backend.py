"""Read verified V1 journals without writing; command isolated simulated missions."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
import engine_core as core

BASE = Path(__file__).resolve().parent
ENGINE_HASH = '36f58fab127bb40ca448e7affe96e5c4fa663dd6a05e02f7b6bf5409d06ddb3e'
PYTHON = str(Path(sys.executable).with_name('python.exe')) if Path(sys.executable).name.lower() == 'pythonw.exe' else sys.executable


@contextmanager
def readonly_database(path, create=False):
    if create:
        raise RuntimeError('Création interdite au lecteur.')
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.3)
    db.execute('PRAGMA query_only=ON')
    try:
        yield db
    finally:
        db.close()


# This module is a read-only projection. The separate command process imports its
# own pristine engine_core; no global monkey-patch crosses a process boundary.
core.database = readonly_database


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read_snapshot(path):
    """No mkdir, no lock file, no RW DB connection, no repair on failure."""
    path = Path(path)
    last = None
    for _ in range(3):
        try:
            before = (path / 'journal-head.json').read_bytes()
            result = core.Engine(path).status()
            if before != (path / 'journal-head.json').read_bytes():
                raise RuntimeError('Journal en cours de modification.')
            result['observed_at'] = now()
            return result
        except (OSError, ValueError, RuntimeError, core.IntegrityError, sqlite3.Error, KeyError, TypeError) as exc:
            last = exc
    raise RuntimeError('Lecture non vérifiée : ' + str(last))


def observation_label(action):
    if action['state'] == 'TERMINÉE':
        return 'Reçu vérifié (simulateur)'
    return 'Aucune observation finale vérifiée'


def diagnosis(action):
    """An observation comparison, explicitly not a causal diagnosis engine."""
    if action['state'] != 'TERMINÉE':
        return 'Non comparable : résultat final manquant.'
    if action['detail']['prediction_correct']:
        return 'Prédiction conforme. Cela ne prouve pas une règle générale.'
    return 'Écart observé. Cause non déterminée ; diagnostic causal automatique non raccordé.'


class Store:
    def __init__(self, root=None, legacy=None):
        self.root = Path(root or BASE / 'local').resolve()
        self.legacy = Path(legacy or BASE.parent / 'CYCLE_DURABLE_V0.1.0/data').resolve()
        self.mutation = threading.Lock()

    def check_engine(self):
        if hashlib.sha256((BASE / 'engine_core.py').read_bytes()).hexdigest() != ENGINE_HASH:
            raise RuntimeError('Le moteur local ne correspond plus à la version qualifiée.')

    def sources(self):
        items = []
        if self.legacy.exists():
            items.append({'id':'historique', 'title':'Cycle durable existant', 'readonly':True, 'data':str(self.legacy)})
        for p in sorted((self.root / 'missions').glob('M-*')):
            if not re.fullmatch(r'M-[a-f0-9]{32}', p.name):
                continue
            try:
                meta = json.loads((p / 'mission.json').read_text(encoding='utf-8'))
                if meta['id'] != p.name or not isinstance(meta['title'], str):
                    raise ValueError('Métadonnées invalides')
                title = meta['title'][:80]
            except (OSError, ValueError, KeyError, TypeError):
                title = p.name + ' · métadonnées à vérifier'
            items.append({'id':p.name,'title':title,'readonly':False,'data':str(p / 'data')})
        return items

    def source(self, key, write=False):
        result = next((s for s in self.sources() if s['id'] == key), None)
        if result is None:
            raise ValueError('Mission inconnue.')
        if write and result['readonly']:
            raise ValueError('Cette mission est en lecture seule.')
        if write:
            expected = (self.root / 'missions' / key / 'data').resolve()
            if not expected.is_relative_to((self.root / 'missions').resolve()):
                raise ValueError('Chemin de mission hors du dossier local.')
        return result

    def scan(self):
        self.check_engine()
        result = []
        for source in self.sources():
            try:
                result.append(dict(source, snapshot=read_snapshot(source['data']), error=None))
            except Exception as exc:
                result.append(dict(source, snapshot=None, error=str(exc)))
        return result

    def invoke(self, data, args):
        self.check_engine()
        result = subprocess.run([PYTHON, str(BASE / 'engine_core.py'), '--data', str(data), *args],
                                capture_output=True, encoding='utf-8', timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode == 86:
            return {'crashed':True}
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError('Réponse du moteur illisible. Ne pas rejouer automatiquement.')
        if result.returncode or not payload.get('ok'):
            raise RuntimeError(payload.get('message', result.stderr[:500]))
        return payload['result']

    def create(self, title):
        if not self.mutation.acquire(False):
            raise RuntimeError('Une commande est déjà en cours.')
        try:
            key = 'M-' + uuid.uuid4().hex
            folder = self.root / 'missions' / key
            folder.mkdir(parents=True, exist_ok=False)
            # Failure remains visible as a mission requiring inspection, never
            # automatically reinitialized by a subsequent launch.
            atomic(folder / 'mission.json', {'id':key,'title':(title.strip() or 'Mission lampe')[:80], 'created_at':now()})
            self.invoke(folder / 'data', ['init'])
            return key
        finally:
            self.mutation.release()

    def command(self, key, operation, **values):
        if not self.mutation.acquire(False):
            raise RuntimeError('Une commande est déjà en cours.')
        try:
            source = self.source(key, write=True)
            state = read_snapshot(source['data'])
            if operation == 'run':
                for field in ('a','b','prediction'):
                    if type(values.get(field)) is not bool:
                        raise ValueError('Entrée booléenne requise : ' + field)
                operation_key = 'UI-' + uuid.uuid4().hex
                args = ['run', '--a', str(int(values['a'])), '--b', str(int(values['b'])), '--prediction', str(int(values['prediction'])), '--operation-key', operation_key]
                crash = values.get('crash')
                if crash not in {None,'before_intent','after_intent','after_dispatch','after_effect'}:
                    raise ValueError('Panne inconnue.')
                if crash:
                    args += ['--crash', crash]
            elif operation == 'resume':
                action_id = values.get('action_id')
                if not any(a['action_id'] == action_id for a in state['actions']):
                    raise ValueError('Action inconnue dans cette mission.')
                args = ['resume', action_id]
            else:
                raise ValueError('Commande non autorisée.')
            result = self.invoke(source['data'], args)
            if result.get('crashed') and operation == 'run':
                latest = read_snapshot(source['data'])
                matched = next((a for a in latest['actions'] if a['request']['operation_key'] == operation_key), None)
                if matched:
                    result['action_id'] = matched['action_id']
            return result
        except subprocess.TimeoutExpired:
            raise RuntimeError('Délai dépassé : résultat à vérifier. Actualiser puis rapprocher le reçu ; aucun rejeu automatique.')
        finally:
            self.mutation.release()

    def export(self, key):
        source = self.source(key)
        state = read_snapshot(source['data'])
        path = self.root / 'exports' / ('preuve-' + uuid.uuid4().hex + '.json')
        atomic(path, {'exported_at':now(),'source':source,'snapshot':state,'scope':'Simulation locale ; export vérifié à cet instant, aucune preuve du monde réel.'})
        return str(path)

    def preferences(self):
        try:
            result = json.loads((self.root / 'view.json').read_text(encoding='utf-8'))
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_preferences(self, value):
        atomic(self.root / 'view.json', value)
