"""Import the supported pure C# subset into a language-independent math spec."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
from legacy_bridge import load_bridge

BASE=Path(__file__).resolve().parent
LIMITATIONS=[
    'Import limité à une fonction C# pure : int32/bool, comparaisons et conditions.',
    'Un fichier, projet ou site hors de ce sous-ensemble reste non traduit ; aucun morceau refusé n’est supprimé silencieusement.',
    'Le modèle décrit la valeur retournée. Routes, bases de données, erreurs, effets, concurrence et interface web nécessitent des adaptateurs distincts.',
    'La fidélité Roslyn vers le modèle est justifiée par règles et tests ; aucun théorème général d’équivalence C#–Rust n’est revendiqué.'
]

def math_text(node):
    kind=node['kind']
    if kind=='param':return node['name']
    if kind=='constant':return str(node['value'])
    if kind=='not':return '(not '+math_text(node['operand'])+')'
    if kind=='binary':
        op={'eq':'==','ne':'!=','lt':'<','le':'<=','gt':'>','ge':'>=','and':'and','or':'or'}[node['op']]
        return '('+math_text(node['left'])+' '+op+' '+math_text(node['right'])+')'
    if kind=='ite':return '('+math_text(node['when_true'])+' if '+math_text(node['condition'])+' else '+math_text(node['when_false'])+')'
    raise ValueError('Expression importée hors grammaire.')

def save(path,value):
    raw=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temp.open('x',encoding='utf-8') as f:
        f.write(raw);f.flush()
        __import__('os').fsync(f.fileno())
    temp.replace(path)

def import_csharp(source,output_dir=None):
    directory=(Path(output_dir or BASE/'local/imports')/('IMPORT-'+uuid.uuid4().hex)).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    report={'schema_version':'mia-csharp-import-1','status':'INCONCLUSIVE','reason':'NOT_COMPLETED',
            'spec':None,'expression':None,'mathematics':'','evidence_dir':str(directory),
            'created_at':datetime.now(timezone.utc).isoformat(),'limitations':LIMITATIONS,
            'coverage':{'unit':'single_pure_function','site_migrated':False},'input_execution':False,'model_calls':0}
    try:
        if type(source) is not str or not source.strip() or len(source.encode('utf-8'))>16384:
            raise ValueError('Une fonction C# complète de16Kio maximum est requise.')
        path=directory/'source.cs';path.write_bytes(source.encode('utf-8'))
        report['source_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        bridge=load_bridge();helpers=bridge.helpers()
        if bridge.sha(bridge.EXTRACTOR.read_bytes())!=helpers.APPROVED_EXTRACTOR_SHA256:
            raise ValueError('Extracteur Roslyn non qualifié.')
        report['resources']=bridge.resource_admission()
        report['runtime']=helpers.validate_roslyn_runtime(bridge.ROSLYN_MANIFEST,bridge.PWSH)
        ir=bridge.extract(helpers,path,'',directory,20)
        save(directory/'roslyn-ir.json',ir)
        method=ir['method'];expression=method['body'];text=math_text(expression)
        spec={'schema_version':'mia-math-spec-1','name':method['name'],
              'parameters':method['parameters'],'return_type':method['return_type'],
              'precondition':'True','postcondition':'result == ('+text+')'}
        from math_core import validate_spec
        normalized=validate_spec(spec)
        # Verify the source and extraction components again before accepting.
        if path.read_bytes()!=source.encode('utf-8'):raise ValueError('Source modifiée pendant l’import.')
        if bridge.sha(bridge.EXTRACTOR.read_bytes())!=helpers.APPROVED_EXTRACTOR_SHA256:
            raise ValueError('Extracteur modifié pendant l’import.')
        report['runtime_after']=helpers.validate_roslyn_runtime(bridge.ROSLYN_MANIFEST,bridge.PWSH)
        load_bridge()
        report.update(status='IMPORTED',reason='Fonction pure extraite par Roslyn et propriété typée validée.',
                      spec=spec,expression=expression,mathematics='result = '+text,
                      normalized_spec_sha256=hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest())
        save(directory/'math-spec.json',spec)
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,subprocess.TimeoutExpired) as exc:
        report.update(status='REFUSED' if isinstance(exc,(ValueError,TypeError,KeyError)) else 'INCONCLUSIVE',reason=str(exc))
    finally:
        report['files_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.is_file() and p.name!='report.json'}
        save(directory/'report.json',report)
    return report
