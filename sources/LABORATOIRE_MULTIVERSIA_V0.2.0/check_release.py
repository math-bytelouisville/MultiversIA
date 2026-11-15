"""Check current artifacts against two independent recorded reviews per module.

This verifies local evidence consistency; it does not authenticate reviewers or
protect against an actor who rewrites both sources and all review evidence.
"""
import hashlib
import json
from pathlib import Path

BASE=Path(__file__).resolve().parent
MODULES={
    'MOTEUR':(['engine.py','test_engine.py','ENGINE_NOTES.txt'],['EVALUATION','INTERFACE']),
    'EVALUATION':(['evaluation.py','test_evaluation.py','EVALUATION_PROTOCOL.txt'],['MOTEUR','INTERFACE']),
    'INTERFACE':(['app.py','ui_backend.py','test_ui.py','GUIDE.txt'],['MOTEUR','EVALUATION']),
    'CSHARP':(['csharp_bridge.py','test_csharp_bridge.py','qualify_csharp.py'],['MOTEUR','EVALUATION'])
}

def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def check():
    problems=[];reviews=[];files={}
    for module,(sources,reviewers) in MODULES.items():
        current={name:sha(BASE/name) for name in sources};files.update(current)
        for reviewer in reviewers:
            name=f'REVUE_{reviewer}_{module}.json'
            try:
                report=read(BASE/name)
                valid=(report.get('verdict')=='PASS' and report.get('reviewer','').upper()==reviewer
                       and all(report.get('files_sha256',{}).get(k)==v for k,v in current.items()))
                if not valid:problems.append(f'{name} : avis absent, périmé ou incomplet')
                reviews.append({'path':name,'sha256':sha(BASE/name),'valid_for_current_sources':valid})
            except (OSError,ValueError) as exc:problems.append(f'{name} : {exc}')
    for name in ('engine','ui','csharp-unit'):
        path=BASE/f'evidence/{name}-test-results.json' if name!='csharp-unit' else BASE/'evidence/csharp-unit-results.json'
        try:
            result=read(path)
            if not result.get('tests') or result.get('errors',1)!=0 or result.get('failures',1)!=0:
                problems.append(f'{path.name} : tests incomplets ou en échec')
        except (OSError,ValueError) as exc:problems.append(f'{path.name} : {exc}')
    try:
        pointer=read(BASE/'evidence/csharp-qualification/latest.json')
        q=read(Path(pointer['results']))
        if (not q.get('complete') or not q.get('all_pass') or len(q.get('cases',[]))!=4
            or not q.get('sources_and_protocol_unchanged')
            or any(q.get('sources_sha256_at_start',{}).get(name)!=files[name] for name in ('csharp_bridge.py','qualify_csharp.py'))):
            problems.append('Qualification C# absente, échouée ou périmée')
        if q.get('sources_sha256_at_end')!=q.get('sources_sha256_at_start'):
            problems.append('Sources de qualification différentes entre début et fin')
        expected={
            'maximum':('PROUVÉ_CONDITIONNEL','CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION'),
            'minimum_wrong_max':('RÉFUTÉ_CONDITIONNEL','CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION'),
            'arithmetic_refused':('INCONCLUSIF','C# hors périmètre : UNSUPPORTED_SYNTAX'),
            'reflexive_contract':('INCONCLUSIF','CONTRAT_NON_DISCRIMINANT_SUR_ECHANTILLON')}
        rows=q.get('cases',[])
        if {r.get('name') for r in rows}!=set(expected) or any(
            r.get('pass') is not True or (r.get('actual'),r.get('reason'))!=expected.get(r.get('name'))
            or r.get('actual')!=r.get('expected') or r.get('reason')!=r.get('expected_reason') for r in rows):
            problems.append('Détails des cas C# incompatibles avec le succès déclaré')
        protocol=Path(pointer['results']).parent/'protocol.json'
        if sha(protocol)!=q.get('protocol_sha256_at_start') or sha(protocol)!=q.get('protocol_sha256_at_end'):
            problems.append('Protocole de qualification C# modifié après campagne')
    except (OSError,ValueError,KeyError,TypeError) as exc:problems.append(f'Qualification C# : {exc}')
    try:
        manifest=read(BASE/'evidence/integration.json')
        if manifest.get('verdict')!='PASS':problems.append('Intégration non validée')
        for name,expected in manifest.get('files_sha256',{}).items():
            if sha(BASE/name)!=expected:problems.append(f'Intégration périmée : {name}')
        required={'engine.py','evaluation.py','app.py','ui_backend.py','csharp_bridge.py'}
        if not required.issubset(manifest.get('files_sha256',{})):problems.append('Intégration sans toutes les empreintes exécutables requises')
    except (OSError,ValueError,KeyError,TypeError) as exc:problems.append(f'Intégration : {exc}')
    return {'schema_version':'mia-release-check-1','status':'PASS' if not problems else 'NOT_READY',
            'files_sha256':files,'reviews':reviews,'problems':problems,
            'scope':'Cohérence des preuves locales, deux revues par module, qualification C# et intégration déclarée ; pas authentification externe.'}

if __name__=='__main__':
    result=check();(BASE/'evidence').mkdir(exist_ok=True)
    (BASE/'evidence/release-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
    raise SystemExit(result['status']!='PASS')
