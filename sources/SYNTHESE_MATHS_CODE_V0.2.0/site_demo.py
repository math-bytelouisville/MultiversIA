"""Explicit local HTTP profile: preserve a full demo site while migrating its logic.

Only this declared host profile is recognized. Unknown files/adapters are refused.
This is not an arbitrary ASP.NET/site translator.
"""
from pathlib import Path
import copy
import hashlib
import json
import os
import queue
import subprocess
import threading
import uuid
import urllib.request
import urllib.error
from migration import save
from module_import import import_csharp_module,sha
from module_model import validate_module,revise_unit,SOURCE_FIELDS
import module_backends

BASE=Path(__file__).resolve().parent
TEMPLATES=BASE/'site_templates'
ASSETS=('index.html','style.css','app.js')
PROFILE={'schema_version':'mia-local-site-profile-1','profile':'shipping-http-get-1',
    'entry':'ShippingRules.Shipping','routes':['GET /','GET /style.css','GET /app.js','GET /api/quote','GET /api/meta'],
    'request':{'weight':'ASCII decimal 0..5000, 1..4 digits','express':['0','1'],'duplicate_or_extra_keys':'400'},
    'response':{'weight':'int','express':'bool','price_eur':'int'},'metadata_difference':'/api/meta language identifies C# or Rust',
    'state':'none','database':'none','authentication':'none','binding':'127.0.0.1 only','unknown_route':404,'wrong_method':405,
    'limits':'HTTP/1.1 GET only, 8192-byte headers, no request bodies, 2-second socket timeouts; local experiment, not production HTTP server'}

def _folder(parent,prefix):
    p=Path(parent)/(prefix+'-'+uuid.uuid4().hex);p.mkdir(parents=True,exist_ok=False);return p.resolve()

def create_reference(output=None):
    root=_folder(output or BASE/'local/sites','source')
    (root/'Host.cs').write_text((TEMPLATES/'Host.cs').read_text(encoding='utf-8').replace('__ENTRY__','ShippingRules.Shipping'),encoding='utf-8',newline='\n')
    (root/'Rules.cs').write_bytes((TEMPLATES/'Rules.cs').read_bytes())
    for asset in ASSETS:(root/asset).write_bytes((TEMPLATES/asset).read_bytes())
    save(root/'site-contract.json',PROFILE)
    return root

def import_reference(root,output=None):
    root=Path(root).resolve();evidence=_folder(output or BASE/'evidence/sites','import')
    inventory=[];report={'status':'REFUSED','reason':'NOT_COMPLETED','inventory':inventory,'model':None,'evidence_dir':str(evidence)}
    try:
        expected={'Host.cs','Rules.cs','site-contract.json',*ASSETS}
        if not root.is_dir():raise ValueError('Source site directory absent')
        files=list(root.rglob('*'))
        if any(p.is_symlink() or not p.resolve().is_relative_to(root) for p in files):raise ValueError('Site symlink/escaping path refused')
        actual={str(p.relative_to(root)) for p in files if p.is_file()}
        for name in sorted(actual):inventory.append({'path':name,'sha256':sha(root/name),'status':'INVENTORIED'})
        if actual!=expected:raise ValueError('Unknown or missing site file; no silent omission')
        if json.loads((root/'site-contract.json').read_text(encoding='utf-8'))!=PROFILE:raise ValueError('Unrecognized HTTP profile')
        host=(TEMPLATES/'Host.cs').read_text(encoding='utf-8').replace('__ENTRY__','ShippingRules.Shipping')
        if (root/'Host.cs').read_bytes()!=host.encode('utf-8'):raise ValueError('Unrecognized C# host adapter')
        for asset in ASSETS:
            if (root/asset).read_bytes()!=(TEMPLATES/asset).read_bytes():raise ValueError('Unrecognized demo frontend revision')
        # Archive every byte before translation. The origin will remain available.
        archive=evidence/'source';archive.mkdir()
        for name in sorted(expected):(archive/name).write_bytes((root/name).read_bytes())
        source=(root/'Rules.cs').read_bytes().decode('utf-8')
        imported=import_csharp_module(source,evidence);save(evidence/'logic-import.json',imported)
        if imported['status']!='IMPORTED':raise ValueError('Logic import refused: '+imported['reason'])
        module=imported['module'];entries=[u for u in module['units'] if u['qualified_name']==PROFILE['entry']]
        if len(entries)!=1 or entries[0]['parameters']!=[{'name':'weight','type':'int'},{'name':'express','type':'bool'}] or entries[0]['return_type']!='int':raise ValueError('HTTP entry signature mismatch')
        for item in inventory:item['status']='TRANSLATED_PURE_MODULE' if item['path']=='Rules.cs' else 'ADAPTED_KNOWN_HTTP_PROFILE' if item['path']=='Host.cs' else 'CONTRACT_PRESERVED' if item['path']=='site-contract.json' else 'COPIED_BYTES'
        model={'schema_version':'mia-demo-site-model-1','profile':copy.deepcopy(PROFILE),'module':module,'entry_unit_id':entries[0]['id'],
               'source_directory':str(root),'source_archive':str(archive),'source_files_sha256':{x['path']:x['sha256'] for x in inventory},
               'inventory':copy.deepcopy(inventory),'limitations':['Explicit shipping HTTP adapter; not general ASP.NET extraction.','Static frontend preserved byte-for-byte; not a mathematical proof of browser rendering.']}
        report.update(status='IMPORTED',reason='All six source files accounted for; pure logic imported and explicit HTTP profile recognized.',model=model)
        save(evidence/'site-model.json',model)
    except (ValueError,TypeError,KeyError,OSError) as exc:report['reason']=str(exc)
    save(evidence/'report.json',report);return report

def build_site(site_model,language,output=None,original=False):
    if language not in ('csharp','rust') or original and language!='csharp':raise ValueError('Unsupported site target')
    folder=_folder(output or BASE/'local/sites','built-'+language)
    module=validate_module(site_model['module'])
    if site_model.get('schema_version')!='mia-demo-site-model-1' or site_model.get('profile')!=PROFILE:raise ValueError('Unrecognized site model profile')
    archive=Path(site_model['source_archive']);hashes=site_model['source_files_sha256']
    if set(hashes)!={'Host.cs','Rules.cs','site-contract.json',*ASSETS}:raise ValueError('Incomplete site inventory')
    for name,expected in hashes.items():
        if sha(archive/name)!=expected:raise ValueError('Archived source changed: '+name)
    if hashlib.sha256(module['provenance']['source_text'].encode('utf-8')).hexdigest()!=hashes['Rules.cs']:raise ValueError('Module origin differs from original site')
    for asset in ASSETS:(folder/asset).write_bytes((archive/asset).read_bytes())
    artifact=module_backends.emit_module(module,language);entry=artifact['unit_mapping'][site_model['entry_unit_id']]['target_symbol']
    if language=='csharp':
        rules='Rules.cs';host='Host.cs'
        if original:
            if module['parent_revision_id'] is not None:raise ValueError('Original site cannot represent an intentional revision')
            (folder/rules).write_bytes((archive/rules).read_bytes());(folder/host).write_bytes((archive/host).read_bytes())
        else:
            (folder/rules).write_text(artifact['source'],encoding='utf-8',newline='\n')
            (folder/host).write_text((TEMPLATES/host).read_text(encoding='utf-8').replace('__ENTRY__','MiaModule.'+entry),encoding='utf-8',newline='\n')
    else:
        rules='rules.rs';host='host.rs';(folder/rules).write_text(artifact['source'],encoding='utf-8',newline='\n')
        (folder/host).write_text((TEMPLATES/host).read_text(encoding='utf-8').replace('__ENTRY__',entry),encoding='utf-8',newline='\n')
    save(folder/'site-model.json',site_model)
    toolchain=module_backends._toolchain(language)
    if toolchain is None:raise RuntimeError('Compiler unavailable: '+language)
    exe=folder/'server.exe';tool=toolchain['path']
    command=([tool,'/nologo','/noconfig','/target:exe','/utf8output','/out:'+str(exe),folder/rules,folder/host] if language=='csharp'
             else [tool,'--edition=2021',*toolchain['extra_args'],folder/host,'-o',exe])
    before={name:sha(folder/name) for name in (rules,host,*ASSETS,'site-model.json')}
    compilation=module_backends._process(command,folder,45,toolchain=toolchain);save(folder/'compilation.json',compilation)
    if compilation['exit_code'] or not exe.is_file():raise RuntimeError('Site compilation failed: '+compilation['stderr']+' '+compilation['stdout'])
    if any(sha(folder/name)!=h for name,h in before.items()):raise RuntimeError('Site sources changed during compilation')
    if module_backends._toolchain(language)!=toolchain:raise RuntimeError('Toolchain changed during compilation')
    report={'status':'COMPILED','language':language,'original':original,'folder':str(folder),'executable':str(exe),'executable_sha256':sha(exe),
            'module_revision':module['revision_id'],'site_model':site_model,'source_files_sha256':before,'toolchain':dict(toolchain,path=str(tool)),
            'scope':'Complete local shipping demo with explicit HTTP adapter; execution/browser checked separately.'}
    save(folder/'build.json',report);return report

class SiteProcess:
    def __init__(self,build):self.build=build;self.process=None;self.url=None
    def start(self):
        b=self.build;folder=Path(b['folder'])
        if sha(b['executable'])!=b['executable_sha256'] or any(sha(folder/p)!=h for p,h in b['source_files_sha256'].items()):raise ValueError('Built site changed before launch')
        env=dict(os.environ);entries=b['toolchain'].get('path_entries',[])
        # Backends name this property path_prefix; retain their confined admission.
        entries=b['toolchain'].get('path_prefix',entries)
        if entries:env['PATH']=os.pathsep.join(entries)+os.pathsep+env.get('PATH','')
        self.process=subprocess.Popen([b['executable'],'0',str(folder)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        messages=queue.Queue()
        threading.Thread(target=lambda:messages.put(self.process.stdout.readline()),daemon=True).start()
        try:
            line=messages.get(timeout=10).strip()
            if not line.startswith('READY '):raise RuntimeError('Server did not announce readiness')
            port=int(line.split()[1]);self.url='http://127.0.0.1:'+str(port)
            with urllib.request.urlopen(self.url+'/api/meta',timeout=3) as response:
                if response.status!=200:raise RuntimeError('Readiness HTTP failed')
            return self
        except Exception:self.stop();raise
    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
            for stream in (self.process.stdout,self.process.stderr):
                if stream:stream.close()
    def __enter__(self):return self.start()
    def __exit__(self,*args):self.stop()

def changed_tariff(site_model):
    previous=validate_module(site_model['module']);spec={'schema_version':'mia-math-spec-1','name':'BasePrice changed','parameters':[{'name':'weight','type':'int'}],
        'return_type':'int','precondition':'True','postcondition':'result == (7 if weight <= 1000 else 14)'}
    row=revise_unit(previous,'u0',spec,'Changer volontairement les tarifs de base de 5/10 à 7/14 euros.')
    if row['status']!='REVISED':raise RuntimeError(row['reason'])
    broken=row['module']
    # Explicit second change: accept the new behavior of dependent functions.
    coherent={k:copy.deepcopy(broken[k]) for k in SOURCE_FIELDS};coherent['parent_revision_id']=broken['revision_id'];coherent['intent']='Propager explicitement le nouveau tarif aux contrats des appelants, après constat des anciennes obligations rompues.'
    affected=row['change']['affected_dependents']
    for unit in coherent['units']:
        if unit['id'] in affected:unit['contract']=None
    coherent=validate_module(coherent);model=copy.deepcopy(site_model);model['module']=coherent
    return {'broken_module':broken,'site_model':model,'change':row['change'],'contract_rebase':{'from':broken['revision_id'],'to':coherent['revision_id'],'units':affected,'intent':coherent['intent']}}

def prepare_pair(output=None):
    root=_folder(output or BASE/'local/sites','pair')
    reference=create_reference(root);imported=import_reference(reference,root)
    if imported['status']!='IMPORTED':raise RuntimeError(imported['reason'])
    # Keep compiler paths short enough for the legacy C# compiler.
    builds=BASE/'runtime/sites';builds.mkdir(parents=True,exist_ok=True)
    original=build_site(imported['model'],'csharp',builds,original=True);rust=build_site(imported['model'],'rust',builds)
    result={'status':'BUILT_NOT_HTTP_QUALIFIED','root':str(root),'import':imported,'csharp':original,'rust':rust}
    save(root/'pair.json',result);return result
