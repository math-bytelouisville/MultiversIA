"""Archived module reconstruction and intentional mathematical revisions."""
from pathlib import Path
from datetime import datetime,timezone
import uuid
import time
from migration import save
from pipeline import select_language,LANGUAGES
from module_import import sha

BASE=Path(__file__).resolve().parent
SOURCES=('module_model.py','module_backends.py','module_import.py','module_import.ps1','module_pipeline.py','math_core.py','code_backends.py','legacy_bridge.py')
LIMITATIONS=['Module pur borné, pas de site entier reconnu par cet importateur.',
             'Les tests finis ne prouvent pas tous les Int32, les compilateurs ou les API externes.',
             'Une modification intentionnelle peut rompre le contrat des appelants ; leurs contre-exemples restent visibles.',
             'Aucune preuve formelle du module complet dans ce parcours.']
def identities():return {f:sha(BASE/f) for f in SOURCES if (BASE/f).is_file()}

def run_module(module,targets=None,preference='auto',output_root=None):
    folder=(Path(output_root or BASE/'local/module-runs')/('MR-'+uuid.uuid4().hex)).resolve();folder.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();before=identities()
    r={'run_id':folder.name,'status':'INCONCLUSIVE','reason':'NOT_COMPLETED','module':None,'artifacts':[],'verification':[],
       'selected_language':None,'selection_reason':'','events':[],'evidence_dir':str(folder),'limitations':LIMITATIONS,
       'formal_scope':'NOT_PROVED_MODULE','model_calls':0,'sources_sha256_at_start':before}
    def event(kind,data):
        r['events'].append({'seq':len(r['events'])+1,'type':kind,'at':datetime.now(timezone.utc).isoformat(),'data':data})
        save(folder/'events.json',r['events'])
    try:
        save(folder/'request.json',{'module':module,'targets':targets,'preference':preference});event('REQUEST_SAVED',{})
        if targets is None:targets=list(LANGUAGES)
        if type(targets) is not list or not targets or any(type(x) is not str or x not in LANGUAGES for x in targets) or len(set(targets))!=len(targets):raise ValueError('Cibles uniques Python/C#/Rust requises.')
        if preference!='auto' and preference not in targets:raise ValueError('Préférence absente des cibles.')
        from module_model import validate_module
        from module_backends import emit_module,validate_module_generated
        r['module']=normalized=validate_module(module);save(folder/'module.json',normalized)
        event('MODEL_VALIDATED',{'revision_id':normalized['revision_id'],'dependencies':normalized['dependencies']})
        for language in targets:
            child=folder/language;child.mkdir();artifact=emit_module(normalized,language)
            path=child/('module'+artifact['extension']);path.write_bytes(artifact['source'].encode('utf-8'))
            if sha(path)!=artifact['source_sha256']:raise ValueError('Source émise incohérente.')
            artifact=dict(artifact,path=str(path));r['artifacts'].append(artifact);save(child/'artifact.json',artifact)
            checked=dict(validate_module_generated(artifact,normalized,child),language=language)
            r['verification'].append(checked);save(child/'verification.json',checked)
            event('MODULE_CHECKED',{'language':language,'status':checked.get('status')})
            if sha(path)!=artifact['source_sha256']:raise ValueError('Module source modifié pendant contrôle.')
        qualified=[v for v in r['verification'] if v.get('status')=='TESTED_FINITE']
        if preference=='auto':
            if qualified:
                r['selected_language'],r['selection_reason']=select_language([v['language'] for v in qualified],preference,qualified)
            else:
                r['selected_language']=None
                r['selection_reason']='Aucune cible vérifiée avec succès ; les sources restent disponibles sans recommandation automatique.'
        else:
            r['selected_language'],r['selection_reason']=select_language(targets,preference,r['verification'])
        failed=any(v.get('status') not in {'TESTED_FINITE','NON_COMPILE'} for v in r['verification'])
        r.update(status='INCONCLUSIVE' if failed else 'GENERATED',reason='Des contrôles du module échouent ou restent inconclusifs.' if failed else 'Modules générés ; contrôles réels séparés par langage.')
    except (ValueError,TypeError,KeyError) as exc:r.update(status='REFUSED',reason=str(exc))
    except Exception as exc:r.update(status='INCONCLUSIVE',reason=type(exc).__name__+': '+str(exc))
    finally:
        after=identities();r['sources_sha256_at_end']=after;r['sources_unchanged']=after==before
        if before!=after:r.update(status='INCONCLUSIVE',reason='Sources de l’atelier modifiées pendant le run.')
        r['elapsed_seconds']=time.monotonic()-started;event('RUN_FINISHED',{'status':r['status'],'reason':r['reason']})
        r['files_sha256']={str(p.relative_to(folder)):sha(p) for p in folder.rglob('*') if p.is_file() and p!=folder/'report.json'}
        save(folder/'report.json',r)
    return r

def revise_module(module,unit_id,spec,intent,output_root=None):
    folder=(Path(output_root or BASE/'local/revisions')/('RV-'+uuid.uuid4().hex)).resolve();folder.mkdir(parents=True,exist_ok=False)
    before=identities();r={'status':'INCONCLUSIVE','reason':'NOT_COMPLETED','module':None,'synthesis':None,'change':{},'evidence_dir':str(folder)}
    try:
        save(folder/'request.json',{'module':module,'unit_id':unit_id,'spec':spec,'intent':intent})
        from module_model import revise_unit
        r.update(revise_unit(module,unit_id,spec,intent))
        if r.get('module'):save(folder/'new-module.json',r['module'])
        save(folder/'change.json',r.get('change'))
    except (ValueError,TypeError,KeyError) as exc:r.update(status='REFUSED',reason=str(exc))
    except Exception as exc:r.update(status='INCONCLUSIVE',reason=type(exc).__name__+': '+str(exc))
    finally:
        after=identities();r['sources_sha256_at_start']=before;r['sources_sha256_at_end']=after
        if before!=after:r.update(status='INCONCLUSIVE',reason='Sources modifiées pendant révision.',module=None)
        r['files_sha256']={p.name:sha(p) for p in folder.iterdir() if p.is_file() and p.name!='report.json'};save(folder/'report.json',r)
    return r
