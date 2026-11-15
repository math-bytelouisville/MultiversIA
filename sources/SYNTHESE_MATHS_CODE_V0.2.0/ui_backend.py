"""UTF-8 process commands and immutable input/result archives for the workshop."""
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

BASE=Path(__file__).resolve().parent
PYTHON=str(Path(sys.executable).with_name('python.exe')) if Path(sys.executable).name.lower()=='pythonw.exe' else sys.executable

def now():return datetime.now(timezone.utc).isoformat()
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()
def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.pending')
    with temp.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)

def launch_site():
    """Explicitly launch the fixed local demo, never a supplied path or shell command."""
    script=BASE/'launch_site.py'
    if not script.is_file():raise FileNotFoundError('Le lanceur du site de test n’est pas encore disponible.')
    folder=BASE/'local/site-launches';folder.mkdir(parents=True,exist_ok=True)
    with (folder/('launch-'+uuid.uuid4().hex+'.log')).open('xb') as log:
        return subprocess.Popen([PYTHON,str(script)],cwd=BASE,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)

def perform(operation,parameters,output):
    if operation=='module_import':return importlib.import_module('module_import').import_csharp_module(parameters['source'],output_dir=Path(output))
    if operation=='module_run':return importlib.import_module('module_pipeline').run_module(parameters['module'],targets=parameters['targets'],preference=parameters['preference'],output_root=Path(output))
    if operation=='module_revise':return importlib.import_module('module_pipeline').revise_module(parameters['module'],parameters['unit_id'],parameters['spec'],parameters['intent'],output_root=Path(output))
    if operation=='run':
        return importlib.import_module('pipeline').run(parameters['spec'],targets=parameters['targets'],preference=parameters['preference'],prove=parameters['prove'],output_root=Path(output))
    if operation=='import':return importlib.import_module('migration').import_csharp(parameters['source'],output_dir=Path(output))
    raise ValueError('Commande inconnue.')

class Store:
    def __init__(self,root=None):self.root=Path(root or BASE/'local').resolve();self.lock=threading.Lock()
    def command(self,operation,**parameters):
        if operation not in {'run','import','module_import','module_run','module_revise'}:raise ValueError('Commande non autorisée.')
        if not self.lock.acquire(False):raise RuntimeError('Une commande est déjà en cours.')
        try:
            request_id='request-'+uuid.uuid4().hex;folder=self.root/'requests'/request_id;folder.mkdir(parents=True,exist_ok=False)
            request={'request_id':request_id,'operation':operation,'at':now(),'parameters':parameters}
            atomic_json(folder/'request.json',request)
            timeout=60 if operation=='module_revise' else 120 if operation in {'module_run','module_import','import'} else 360 if operation=='run' and parameters.get('prove') else 90
            try:
                child=subprocess.run([PYTHON,str(BASE/'ui_backend.py'),'--worker',operation,str(folder/'proofs')],
                    input=json.dumps(parameters,ensure_ascii=False,allow_nan=False),capture_output=True,encoding='utf-8',timeout=timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                envelope=json.loads(child.stdout)
                if child.returncode or not envelope.get('ok'):raise RuntimeError(envelope.get('error','Processus interrompu.'))
                result=envelope['result']
                if not isinstance(result,dict):raise ValueError('Réponse non structurée.')
            except Exception as exc:
                result={'status':'INCONCLUSIVE','reason':('Délai dépassé, résultat incertain. Aucun rejeu automatique.' if isinstance(exc,subprocess.TimeoutExpired) else str(exc)),
                    'limitations':['Aucun résultat complet accepté pour cette opération. Les preuves partielles sont conservées.'],'evidence_dir':str(folder/'proofs')}
            outcome={'request_id':request_id,'operation':operation,'request':request,'request_sha256':digest(request),'result':result,'completed_at':now(),'archive':str(folder)}
            outcome['sha256']=digest(outcome);atomic_json(folder/'outcome.json',outcome)
            return outcome
        finally:self.lock.release()
    def history(self):
        rows=[]
        for folder in sorted((self.root/'requests').glob('request-*')):
            try:
                request=json.loads((folder/'request.json').read_text(encoding='utf-8'))
                if not (folder/'outcome.json').exists():
                    rows.append({'request_id':folder.name,'operation':request.get('operation'),'error':'Opération interrompue ou toujours en cours ; aucun résultat final.','request':request,'archive':str(folder)});continue
                outcome=json.loads((folder/'outcome.json').read_text(encoding='utf-8'));actual={k:v for k,v in outcome.items() if k!='sha256'}
                if outcome.get('sha256')!=digest(actual) or outcome.get('request_sha256')!=digest(request) or outcome.get('request')!=request or outcome.get('request_id')!=folder.name:raise ValueError('Archive non vérifiée.')
                rows.append(outcome)
            except Exception as exc:rows.append({'request_id':folder.name,'error':str(exc),'archive':str(folder)})
        return sorted(rows,key=lambda row:row.get('request',{}).get('at',''),reverse=True)
    def preferences(self):
        try:
            value=json.loads((self.root/'view.json').read_text(encoding='utf-8'));return value if isinstance(value,dict) else {}
        except (OSError,ValueError):return {}
    def save_preferences(self,value):atomic_json(self.root/'view.json',value)

def main():
    for stream in (sys.stdin,sys.stdout):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    try:
        if len(sys.argv)!=4 or sys.argv[1]!='--worker':raise ValueError('Invocation invalide.')
        parameters=json.loads(sys.stdin.read());result=perform(sys.argv[2],parameters,sys.argv[3])
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False,allow_nan=False));return 0
    except Exception as exc:print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
