"""Offline Roslyn → restricted mathematics → deterministic Lean certificates.

Never execute supplied C#, supplied Lean, or an LLM. Historical translation
helpers are pinned, loaded as a module, and their main/model functions unused.
"""
from datetime import datetime, timezone
import base64
import ctypes
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import time
import types
import uuid

BASE=Path(__file__).resolve().parent
TOOLS=BASE.parent/'CSHARP_LEAN_CANDIDAT_001'
DRIVER=TOOLS/'MIA-F001165-csharpleanverifier-v023-natif-identite-qwen.py'
DRIVER_SHA='1756e74bc0acc7704daa264cee20b954f5978a82068080946dde015c749e017d'
EXTRACTOR=TOOLS/'MIA-F000860-extracteur-roslyn-v020-blocs-purs.ps1'
PWSH=TOOLS/'outils-tiers-powershell-7.6.9/pwsh.exe'
ROSLYN_MANIFEST=TOOLS/'MIA-F000877-manifest-runtime-powershell-roslyn-isole.json'
LEAN=TOOLS/'outils-tiers-lean-4.35.0/lean-4.35.0-windows/bin/lean.exe'
LEAN_MANIFEST=TOOLS/'MIA-F000847-manifest-toolchain-lean435-approuve.json'
EXAMPLE_SOURCE='public static class Example { public static int Compute(int a, int b) { return a >= b ? a : b; } }'
EXAMPLE_CONTRACT='public static class Contract { public static bool Spec(int a, int b, int result) { return result >= a && result >= b && (result == a || result == b); } }'
LIMITATIONS=[
    'Sous-ensemble C# pur Int32/bool : comparaisons, conditions, if/return. Pas d’appels, boucles, arithmétique ou état mutable.',
    'Certificat Lean sur la propriété traduite ; fidélité C#→Lean justifiée par règles, non prouvée mécaniquement.',
    'Le C# fourni n’est jamais exécuté. Ni vérification de solution Visual Studio complète, ni inférence IA.',
    'Une recherche bornée sans preuve ni contre-exemple donne INCONCLUSIF, jamais une réfutation.',
    'Le garde RAM est un contrôle d’admission, pas un plafond global du PC. Lean est borné à un thread et 512 Mio demandés.',
    'Les entrées et preuves restent dans le dossier local de l’analyse. Aucun appel réseau.'
]


def sha(data):return hashlib.sha256(data).hexdigest()
def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def helpers():
    raw=DRIVER.read_bytes()
    if sha(raw)!=DRIVER_SHA:raise ValueError('Pilote de traduction modifié ou non qualifié.')
    module=types.ModuleType('mia_pinned_translation');module.__file__=str(DRIVER)
    exec(compile(raw,str(DRIVER),'exec'),module.__dict__)
    return module


def resource_admission():
    if os.name!='nt':raise RuntimeError('Ce pont et ses dépendances sont qualifiés seulement sous Windows.')
    class Status(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
    s=Status();s.length=ctypes.sizeof(s)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):raise RuntimeError('Capteur RAM indisponible.')
    if s.load>=90 or s.available<1024**3:raise RuntimeError('RAM insuffisante pour une nouvelle vérification bornée.')
    return {'ram_percent':s.load,'available_bytes':s.available}


def process(command,timeout,cwd):
    env=dict(os.environ)
    for name in ('LEAN_PATH','LEAN_SRC_PATH','LEAN_SYSROOT','DOTNET_STARTUP_HOOKS','DOTNET_ADDITIONAL_DEPS','DOTNET_SHARED_STORE','DOTNET_ROOT','DOTNET_ROOT_X64'):
        env.pop(name,None)
    env['PSModulePath']=str(PWSH.parent/'Modules')
    p=subprocess.run(command,cwd=cwd,env=env,capture_output=True,encoding='utf-8',timeout=timeout,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    return {'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}


def extract(module,path,method,directory,timeout):
    command=("$ErrorActionPreference='Stop'; $env:PSModulePath=$PSHOME+'\\Modules'; & ([scriptblock]::Create([IO.File]::ReadAllText("
             +module.ps_quote(EXTRACTOR)+'))) -SourcePath '+module.ps_quote(path)+' -MethodName '+module.ps_quote(method))
    encoded=base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    result=process([str(PWSH),'-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',encoded],timeout,directory)
    save(directory/(path.stem+'-roslyn-output.json'),result)
    if result['exit_code'] or result['stderr'].strip():raise ValueError('Analyse Roslyn arrêtée ; voir preuve locale.')
    ir=module.strict_json(result['stdout'])
    if ir.get('source_sha256')!=sha(path.read_bytes()):raise ValueError('Empreinte de source Roslyn différente.')
    if ir.get('status')!='TRANSLATED':raise ValueError('C# hors périmètre : '+str(ir.get('reason')))
    module.validate_method(ir['method'])
    return ir


def evaluate(node,env):
    """Trusted restricted IR interpreter, only used to nominate counterexamples."""
    kind=node['kind']
    if kind=='constant':return node['value']
    if kind=='param':return env[node['name']]
    if kind=='not':return not evaluate(node['operand'],env)
    if kind=='ite':return evaluate(node['when_true'] if evaluate(node['condition'],env) else node['when_false'],env)
    if kind=='binary':
        left=evaluate(node['left'],env);right=evaluate(node['right'],env)
        op=node['op']
        if op=='eq':return left==right
        if op=='ne':return left!=right
        if op=='lt':return left<right
        if op=='le':return left<=right
        if op=='gt':return left>right
        if op=='ge':return left>=right
        if op=='and':return left and right
        if op=='or':return left or right
    raise ValueError('IR non admis.')


def propose_counterexample(program,contract):
    values=[(-2147483648,-1,0,1,2147483647) if p['type']=='int' else (False,True) for p in program['parameters']]
    for combination in itertools.islice(itertools.product(*values),256):
        env={p['name']:value for p,value in zip(program['parameters'],combination)}
        env['result']=evaluate(program['body'],env)
        if evaluate(contract['body'],env) is False:return list(combination)
    return None


def uses_result(node):
    if isinstance(node,dict):return (node.get('kind')=='param' and node.get('name')=='result') or any(uses_result(v) for v in node.values())
    if isinstance(node,list):return any(uses_result(v) for v in node)
    return False


def contract_sensitivity(program,contract):
    """A small explicit mutant check; not a proof of specification adequacy."""
    inputs=[(-1,0,1) if p['type']=='int' else (False,True) for p in program['parameters']]
    outputs=(-2147483648,-1,0,1,2147483647) if program['return_type']=='int' else (False,True)
    checked=rejected=0
    for values in itertools.islice(itertools.product(*inputs),32):
        env={p['name']:v for p,v in zip(program['parameters'],values)}
        for result in outputs:
            env['result']=result;checked+=1
            rejected+=evaluate(contract['body'],env) is False
    return {'candidate_results_checked':checked,'candidate_results_rejected':rejected,
            'scope':'Échantillon de résultats mutants ; ne prouve pas la pertinence métier du contrat.'}


def analyze(source,contract,output_root=None):
    started=time.monotonic();deadline=started+180
    directory=(Path(output_root or BASE/'local/csharp')/('CS-'+uuid.uuid4().hex)).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    report={'schema_version':'mia-csharp-bridge-1','verdict':'INCONCLUSIF','reason':'NOT_COMPLETED','mathematics':'',
            'attempts':[],'limitations':LIMITATIONS,'evidence_dir':str(directory),'network_calls':0,'model_calls':0,
            'input_execution':False,'formal_translation_equivalence_proved':False,'created_at':datetime.now(timezone.utc).isoformat()}
    def time_left(maximum):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Budget total de vérification atteint.')
        return min(maximum,remaining)
    try:
        for value in (source,contract):
            if type(value)!=str or not value.strip() or len(value.encode('utf-8'))>16384:
                raise ValueError('Source et contrat complets requis, 16 Kio maximum chacun.')
        source_path=directory/'source.cs';contract_path=directory/'contract.cs'
        source_path.write_text(source,encoding='utf-8');contract_path.write_text(contract,encoding='utf-8')
        report['input_sha256']={'source':sha(source_path.read_bytes()),'contract':sha(contract_path.read_bytes())}
        module=helpers()
        if sha(EXTRACTOR.read_bytes())!=module.APPROVED_EXTRACTOR_SHA256:raise ValueError('Extracteur non qualifié.')
        report['resources']=resource_admission()
        report['roslyn_runtime']=module.validate_roslyn_runtime(ROSLYN_MANIFEST,PWSH)
        ir=extract(module,source_path,'',directory,time_left(20))
        spec=extract(module,contract_path,'Spec',directory,time_left(20))
        save(directory/'program-ir.json',ir);save(directory/'contract-ir.json',spec)
        translation=module.prepare_translation(ir,spec)
        report['mathematics']=translation['definitions']+'\n-- Propriété à vérifier\n'+translation['goal']
        report['translation_justification']=translation['justification']
        save(directory/'translation.json',translation)
        if not uses_result(spec['method']['body']):
            report['reason']='CONTRAT_INSUFFISANT_SANS_RESULTAT';return report
        report['contract_sensitivity']=contract_sensitivity(ir['method'],spec['method'])
        if report['contract_sensitivity']['candidate_results_rejected']==0:
            report['reason']='CONTRAT_NON_DISCRIMINANT_SUR_ECHANTILLON';return report
        # A finite search nominates a candidate, never assigns the verdict.
        counterexample=propose_counterexample(ir['method'],spec['method'])
        proposals=([{'kind':'counterexample','strategy':'decide','values':counterexample}] if counterexample is not None else [])
        proposals += [{'kind':'proof','strategy':s,'values':[]} for s in ('grind','omega','split_omega','simp')]
        report['toolchain']=module.validate_toolchain(LEAN_MANIFEST,LEAN)
        time_left(1)
        component_paths=[DRIVER,EXTRACTOR,PWSH,ROSLYN_MANIFEST,LEAN,LEAN_MANIFEST]
        components={str(p):module.file_sha(p) for p in component_paths}
        report['components_sha256']=components
        report['reason']='RECHERCHE_BORNEE_SANS_CERTIFICAT'
        for index,proposal in enumerate(proposals,1):
            resource_admission()
            certificate=module.certificate_text(translation,proposal)
            proof_path=directory/f'certificate-{index}.lean';proof_path.write_text(certificate,encoding='utf-8')
            attempt={'proposal':proposal,'certificate':proof_path.name,'accepted':False}
            report['attempts'].append(attempt)
            try:
                output=process([str(LEAN),'-j','1','-M','512',str(proof_path)],time_left(10),directory)
                verdict=module.check_certificate_output(output['exit_code'],output['stdout'],output['stderr'])
                save(directory/f'lean-output-{index}.json',output);attempt.update(verdict)
            except subprocess.TimeoutExpired:
                attempt.update(reason='LEAN_TIMEOUT',accepted=False)
            if attempt['accepted']:
                report['toolchain_after']=module.validate_toolchain(LEAN_MANIFEST,LEAN)
                report['roslyn_runtime_after']=module.validate_roslyn_runtime(ROSLYN_MANIFEST,PWSH)
                if any(module.file_sha(Path(p))!=h for p,h in components.items()):raise ValueError('Composant modifié pendant la vérification.')
                if source_path.read_text(encoding='utf-8')!=source or contract_path.read_text(encoding='utf-8')!=contract:raise ValueError('Source modifiée pendant la vérification.')
                if proof_path.read_text(encoding='utf-8')!=certificate:raise ValueError('Certificat modifié pendant la vérification.')
                time_left(1)
                report['verdict']='RÉFUTÉ_CONDITIONNEL' if proposal['kind']=='counterexample' else 'PROUVÉ_CONDITIONNEL'
                report['reason']='CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION'
                report['certificate']=str(proof_path)
                if proposal['kind']=='counterexample':report['counterexample']=proposal['values']
                break
    except (OSError,ValueError,RuntimeError,TimeoutError,KeyError,TypeError,subprocess.TimeoutExpired) as exc:
        report['verdict']='INCONCLUSIF';report['reason']=str(exc)
    finally:
        report['elapsed_seconds']=time.monotonic()-started
        report['files_sha256']={p.name:sha(p.read_bytes()) for p in directory.iterdir() if p.is_file() and p.name!='report.json'}
        save(directory/'report.json',report)
    return report
