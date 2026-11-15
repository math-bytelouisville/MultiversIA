"""Bounded orchestration; immutable run identities and explicit verification scope."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid
from migration import save

BASE=Path(__file__).resolve().parent
LANGUAGES=('python','csharp','rust')
PUBLIC_SPEC_FIELDS=('schema_version','name','parameters','return_type','precondition','postcondition')
LIMITATIONS=[
    'Premier jalon : fonctions pures int32/bool, comparaisons et conditions. Aucun site complet migré.',
    'Synthèse symbolique bornée ; aucun modèle de langage chargé. Échec de recherche ne prouve pas impossibilité.',
    'Tests finis, compilation et preuve conditionnelle sont trois contrôles différents.',
    'La preuve Lean porte sur la traduction du candidat et son contrat. Elle ne prouve pas les compilateurs ni l’équivalence générale entre langages.'
]

def hash_file(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def select_language(targets,preference,verification):
    if preference!='auto':return preference,'Cible choisie explicitement par l’utilisateur ; ses limites restent indiquées.'
    by_language={r.get('language'):r for r in verification}
    # A transparent preference for a runnable verified path, not a performance claim.
    ranked=[]
    for language in targets:
        result=by_language.get(language,{})
        execution=str(result.get('execution_status','')).upper()
        compilation=str(result.get('compile_status','')).upper()
        usable=result.get('status') not in {'FAIL','FAILED','REFUSED','ERROR','COUNTEREXAMPLE','INCONCLUSIVE'}
        ran=usable and execution in {'PASS','PASSED','EXECUTED','TESTED','VERIFIED'}
        compiled=usable and compilation in {'PASS','PASSED','COMPILED','VERIFIED'}
        ranked.append(((2 if ran else 1 if compiled else 0),-LANGUAGES.index(language),language))
    chosen=max(ranked)[2]
    return chosen,'Choix selon contrôles disponibles : exécution testée, puis compilation ; égalité départagée Python, C#, Rust. Pas de garantie de meilleur langage pour un projet.'

def run(spec,targets=None,preference='auto',prove=False,output_root=None):
    started=time.monotonic();directory=(Path(output_root or BASE/'local/runs')/('RUN-'+uuid.uuid4().hex)).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    result={'schema_version':'mia-math-code-run-1','run_id':directory.name,'status':'INCONCLUSIVE','reason':'NOT_COMPLETED',
            'spec':None,'synthesis':None,'selected_language':None,'selection_reason':'','artifacts':[],'verification':[],
            'proof':None,'events':[],'evidence_dir':str(directory),'limitations':LIMITATIONS,'model_calls':0}
    sources={name:hash_file(BASE/name) for name in ('pipeline.py','migration.py','legacy_bridge.py','math_core.py','code_backends.py') if (BASE/name).is_file()}
    result['sources_sha256_at_start']=sources
    def event(kind,data):
        result['events'].append({'seq':len(result['events'])+1,'type':kind,'at':datetime.now(timezone.utc).isoformat(),'data':data})
        save(directory/'events.json',result['events'])
    try:
        # Preserve the caller's exact JSON request, including invalid options and
        # an omitted target list, before normalization or refusal.
        save(directory/'request.json',{'spec':spec,'targets':targets,'preference':preference,'prove':prove})
        event('REQUEST_SAVED',{'targets':targets,'proof_requested':prove})
        if targets is None:targets=list(LANGUAGES)
        if type(targets) is not list or not targets or any(type(x) is not str or x not in LANGUAGES for x in targets) or len(set(targets))!=len(targets):
            raise ValueError('Cibles uniques requises parmi python, csharp, rust.')
        if preference!='auto' and preference not in targets:raise ValueError('La préférence doit être auto ou une cible demandée.')
        if type(prove) is not bool:raise ValueError('Option de preuve booléenne requise.')
        from math_core import validate_spec,solve
        import code_backends
        normalized=validate_spec(spec);result['spec']=normalized;save(directory/'spec.json',normalized)
        event('SPEC_VALIDATED',{'parameters':normalized['parameters'],'return_type':normalized['return_type']})
        synthesis=solve({k:normalized[k] for k in PUBLIC_SPEC_FIELDS});result['synthesis']=synthesis;save(directory/'synthesis.json',synthesis)
        event('SYNTHESIS_FINISHED',{'status':synthesis['status'],'reason':synthesis.get('reason','')})
        if synthesis['status']!='CANDIDATE':
            result.update(status=synthesis['status'],reason=synthesis.get('reason','Aucun candidat.'));return result
        candidate=synthesis['candidate']
        for language in targets:
            folder=directory/language;folder.mkdir()
            artifact=code_backends.emit(candidate,normalized,language)
            path=folder/('generated'+artifact['extension']);path.write_text(artifact['source'],encoding='utf-8',newline='\n')
            artifact['path']=str(path);artifact['source_sha256']=hash_file(path)
            result['artifacts'].append(artifact);save(folder/'artifact.json',artifact)
            event('CODE_GENERATED',{'language':language,'source_sha256':artifact['source_sha256']})
            checked=code_backends.validate_generated(artifact,candidate,normalized,folder)
            checked=dict(checked,language=language);result['verification'].append(checked);save(folder/'verification.json',checked)
            event('BACKEND_CHECKED',{'language':language,'status':checked.get('status'),'compile_status':checked.get('compile_status'),'execution_status':checked.get('execution_status')})
            if hash_file(path)!=artifact['source_sha256']:raise ValueError('Code généré modifié pendant les contrôles.')
        result['selected_language'],result['selection_reason']=select_language(targets,preference,result['verification'])
        if prove:
            proof_dir=directory/'proof';proof_dir.mkdir()
            event('PROOF_STARTED',{'scope':'Certificat conditionnel sur traduction, pas équivalence générale des langages.'})
            result['proof']=code_backends.prove(candidate,normalized,proof_dir);save(proof_dir/'proof-summary.json',result['proof'])
            event('PROOF_FINISHED',{'status':result['proof'].get('status'),'reason':result['proof'].get('reason')})
        failed=[r for r in result['verification'] if r.get('status') in {'FAIL','FAILED','REFUSED','ERROR','COUNTEREXAMPLE'}]
        proof_status=str((result['proof'] or {}).get('status','')).upper()
        if 'RÉFUT' in proof_status or 'REFUT' in proof_status:failed.append({'status':proof_status})
        result.update(status='INCONCLUSIVE' if failed else 'GENERATED',
                      reason='Un contrôle a échoué ; consulter les preuves.' if failed else 'Sources générées ; chaque langage conserve son statut de test/compilation, et la preuve son propre statut.')
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,ImportError) as exc:
        result.update(status='REFUSED' if isinstance(exc,(ValueError,TypeError,KeyError)) else 'INCONCLUSIVE',reason=str(exc))
        event('STOPPED',{'reason':str(exc)})
    finally:
        after={name:hash_file(BASE/name) for name in sources if (BASE/name).is_file()}
        result['sources_sha256_at_end']=after;result['sources_unchanged']=after==sources
        if after!=sources:result.update(status='INCONCLUSIVE',reason='Sources modifiées pendant le run ; résultat non qualifié.')
        result['elapsed_seconds']=time.monotonic()-started
        event('RUN_FINISHED',{'status':result['status'],'reason':result['reason']})
        result['files_sha256']={str(p.relative_to(directory)):hash_file(p) for p in directory.rglob('*') if p.is_file() and p!=directory/'report.json'}
        save(directory/'report.json',result)
    return result
