"""Semantic module import using Roslyn; never executes pasted C#."""
from pathlib import Path
import hashlib
import json
import uuid
from legacy_bridge import load_bridge
from migration import save

BASE=Path(__file__).resolve().parent
SCRIPT=BASE/'module_import.ps1'
LIMITATIONS=[
    'Une classe de 1 à 8 méthodes pures Int32/bool ; appels statiques internes sans récursion.',
    'Aucun effet, objet général, arithmétique, route HTTP ou base de données extrait par cet importateur.',
    'Les noms source sont conservés ; une table de symboles cibles ne prouve pas compatibilité des API externes.',
    'Roslyn vers le modèle est une traduction par règles testées, pas un théorème général d’équivalence.'
]
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def import_csharp_module(source,output_dir=None):
    folder=(Path(output_dir or BASE/'local/module-imports')/('MI-'+uuid.uuid4().hex)).resolve()
    folder.mkdir(parents=True,exist_ok=False)
    result={'schema_version':'mia-module-import-report-1','status':'INCONCLUSIVE','reason':'NOT_COMPLETED','module':None,
            'inventory':[],'coverage':{'accepted':False,'whole_site_migrated':False},'evidence_dir':str(folder),
            'input_execution':False,'model_calls':0,'limitations':LIMITATIONS}
    script_before=None
    try:
        save(folder/'request.json',{'source':source})
        if type(source) is not str or not source.strip() or len(source.encode('utf-8'))>65536:
            raise ValueError('Source C# non vide, au plus 64 Kio, requise.')
        path=folder/'source.cs';path.write_bytes(source.encode('utf-8'));source_hash=sha(path)
        bridge=load_bridge();helpers=bridge.helpers();result['resources']=bridge.resource_admission()
        result['runtime']=helpers.validate_roslyn_runtime(bridge.ROSLYN_MANIFEST,bridge.PWSH)
        script_before=sha(SCRIPT);result['extractor_sha256']=script_before
        # A short working directory avoids Windows legacy cwd limitations.
        work=BASE/'runtime'/('I-'+uuid.uuid4().hex);work.mkdir(parents=True,exist_ok=False)
        response=bridge.process([str(bridge.PWSH),'-NoLogo','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-SourcePath',str(path)],30,work)
        save(folder/'roslyn-process.json',response)
        if response['exit_code'] or response['stderr'].strip():raise RuntimeError('Analyse Roslyn interrompue ; sortie conservée.')
        ir=helpers.strict_json(response['stdout']);save(folder/'roslyn-ir.json',ir)
        result['inventory']=ir.get('inventory',[])
        if ir.get('source_sha256')!=source_hash:raise ValueError('Empreinte source Roslyn différente.')
        if ir.get('status')!='TRANSLATED':
            result['diagnostics']=ir.get('diagnostics',[])
            if ir.get('reason') not in {'UNSUPPORTED_SYNTAX','COMPILATION_ERROR'}:
                raise RuntimeError('Import Roslyn inconclusif : '+str(ir.get('reason')))
            raise ValueError('Module C# refusé : '+str(ir.get('reason')))
        from module_model import validate_module
        model={'schema_version':'mia-module-1','semantic_profile':'pure-int32-bool-call-1','module_id':'MOD-'+uuid.uuid4().hex,
            'name':ir['class_name'],'parent_revision_id':None,'intent':'Import initial, comportement source conservé dans le modèle.',
            'provenance':{'source_sha256':source_hash,'source_text':source,'import_id':folder.name,
                'origin':{'language':'csharp','class_name':ir['class_name'],'extractor_sha256':script_before},
                'evidence_dir':str(folder),'limitations':LIMITATIONS},'units':ir['units']}
        normalized=validate_module(model)
        if sha(path)!=source_hash or sha(SCRIPT)!=script_before:raise ValueError('Source ou extracteur modifié pendant import.')
        result['runtime_after']=helpers.validate_roslyn_runtime(bridge.ROSLYN_MANIFEST,bridge.PWSH);load_bridge()
        result.update(status='IMPORTED',reason='Toutes les méthodes du module sont admises et leurs appels sont résolus.',module=normalized,
                      coverage={'accepted':True,'units':len(normalized['units']),'inventory_items':len(result['inventory']),'whole_site_migrated':False})
        save(folder/'module.json',normalized)
    except (ValueError,TypeError,KeyError,RecursionError) as exc:
        result.update(status='REFUSED',reason=str(exc))
    except Exception as exc:
        result.update(status='INCONCLUSIVE',reason=type(exc).__name__+': '+str(exc))
    finally:
        try:script_unchanged=script_before is None or sha(SCRIPT)==script_before
        except OSError:script_unchanged=False
        if not script_unchanged:
            result.update(status='INCONCLUSIVE',reason='Extracteur modifié pendant opération.',module=None)
        result['files_sha256']={p.name:sha(p) for p in folder.iterdir() if p.is_file() and p.name!='report.json'}
        save(folder/'report.json',result)
    return result
