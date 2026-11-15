"""Process boundary and read-only projections for the laboratory desktop UI."""
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

BASE = Path(__file__).resolve().parent
PYTHON = str(Path(sys.executable).with_name('python.exe')) if Path(sys.executable).name.lower() == 'pythonw.exe' else sys.executable
WORLD_VALUES = ('xor', 'or', 'and', 'true', 'false')


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def perform(root, operation, parameters):
    """Only explicit allowlisted commands; source code is never executed."""
    if operation == 'analyze':
        bridge = importlib.import_module('csharp_bridge')
        return bridge.analyze(parameters['source'], parameters['contract'])
    lab = importlib.import_module('engine').Lab(Path(root))
    if operation == 'create':
        return {'mission_id': lab.create(**parameters)}
    if operation == 'step':
        return lab.step(parameters['mission_id'], count=1)
    if operation == 'resume':
        return lab.resume(parameters['mission_id'])
    if operation == 'export':
        return {'export_path': str(lab.export(parameters['mission_id']))}
    raise ValueError('Commande inconnue.')


class Store:
    def __init__(self, root=None):
        self.root = Path(root or BASE / 'local').resolve()
        self._lock = threading.Lock()

    def scan(self):
        lab = importlib.import_module('engine').Lab(self.root)
        missions = lab.missions()
        result = []
        for mission in missions:
            item = dict(mission)
            try:
                snapshot = lab.snapshot(item['mission_id'])
                if snapshot.get('integrity') != 'VERIFIED':
                    raise RuntimeError('Intégrité non vérifiée.')
                item.update(snapshot=snapshot, error=None)
            except Exception as exc:
                item.update(snapshot=None, error=str(exc))
            result.append(item)
        return result

    def command(self, operation, **parameters):
        if operation not in {'create','step','resume','export','analyze'}:
            raise ValueError('Commande non autorisée.')
        if not self._lock.acquire(False):
            raise RuntimeError('Une commande est déjà en cours.')
        try:
            # JSON passed over stdin, never shell interpolation. Process is isolated
            # from the UI, and timeout cannot be mistaken for non-execution.
            result = subprocess.run([PYTHON, str(BASE / 'ui_backend.py'), '--worker', str(self.root), operation],
                input=json.dumps(parameters,ensure_ascii=False), capture_output=True,
                encoding='utf-8', timeout=240 if operation=='analyze' else 45,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                payload = json.loads(result.stdout)
            except ValueError:
                raise RuntimeError('Résultat du processus illisible ou interrompu. Examiner puis reprendre ; aucun rejeu automatique.')
            if result.returncode or not payload.get('ok'):
                raise RuntimeError(payload.get('error','Échec du processus.'))
            return payload['result']
        except subprocess.TimeoutExpired:
            raise RuntimeError('Délai dépassé : résultat incertain. La campagne est arrêtée ; rapprocher les preuves avant de reprendre.')
        finally:
            self._lock.release()

    def preferences(self):
        try:
            value=json.loads((self.root/'ui-view.json').read_text(encoding='utf-8'))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError):
            return {}

    def save_preferences(self,value):
        atomic_json(self.root/'ui-view.json',value)

    def archive_analysis(self, source, contract, result):
        path=self.root/'analyses-csharp'/('analyse-'+uuid.uuid4().hex+'.json')
        atomic_json(path,{'at':datetime.now(timezone.utc).isoformat(),'source':source,'contract':contract,'result':result,
                          'scope':'Analyse du sous-ensemble déclaré ; aucune exécution native du C#.'})
        return str(path)


def main():
    if hasattr(sys.stdin,'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        if len(sys.argv)!=4 or sys.argv[1]!='--worker':
            raise ValueError('Invocation de processus invalide.')
        parameters=json.loads(sys.stdin.read())
        result=perform(sys.argv[2],sys.argv[3],parameters)
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
