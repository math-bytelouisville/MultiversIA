"""Predefined offline real-component qualification; not a learning benchmark."""
import hashlib
from pathlib import Path
import uuid
import csharp_bridge as bridge

cases=[
 ('maximum',bridge.EXAMPLE_SOURCE,bridge.EXAMPLE_CONTRACT,'PROUVÉ_CONDITIONNEL'),
 ('minimum_wrong_max',bridge.EXAMPLE_SOURCE.replace('a >= b ? a : b','a <= b ? a : b'),bridge.EXAMPLE_CONTRACT,'RÉFUTÉ_CONDITIONNEL'),
 ('arithmetic_refused',bridge.EXAMPLE_SOURCE.replace('a >= b ? a : b','a + b'),bridge.EXAMPLE_CONTRACT,'INCONCLUSIF'),
 ('reflexive_contract',bridge.EXAMPLE_SOURCE,'public static class Contract { public static bool Spec(int a, int b, int result) { return result == result; } }','INCONCLUSIF')
]
REASONS={
    'maximum':'CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION',
    'minimum_wrong_max':'CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION',
    'arithmetic_refused':'C# hors périmètre : UNSUPPORTED_SYNTAX',
    'reflexive_contract':'CONTRAT_NON_DISCRIMINANT_SUR_ECHANTILLON'
}

def hashes():
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(bridge.__file__),Path(__file__))}

def run(root=None,analyze=bridge.analyze):
    root=Path(root or bridge.BASE/'evidence/csharp-qualification'/('campaign-'+uuid.uuid4().hex)).resolve()
    # Never reuse or overwrite any previous campaign, including a failed one.
    root.mkdir(exist_ok=False,parents=True)
    sources=hashes()
    bridge.save(root/'protocol.json',{'cases':[{'name':n,'source':s,'contract':c,'expected':e,'expected_reason':REASONS[n]} for n,s,c,e in cases],
        'sources_sha256_at_start':sources,'network':False,'model':False,'scope':'Quatre cas prédéfinis ; pas couverture générale du langage.'})
    protocol_sha=bridge.sha((root/'protocol.json').read_bytes());results=[]
    for name,source,contract,expected in cases:
        try:
            r=analyze(source,contract,root)
            row={'name':name,'expected':expected,'expected_reason':REASONS[name],
                 'actual':r['verdict'],'reason':r['reason'],'seconds':r['elapsed_seconds'],'evidence_dir':r['evidence_dir'],
                 'pass':r['verdict']==expected and r['reason']==REASONS[name]}
        except Exception as exc:
            row={'name':name,'expected':expected,'expected_reason':REASONS[name],'actual':'RUNNER_ERROR','reason':str(exc),'pass':False}
        results.append(row);print(row,flush=True)
        after=hashes();unchanged=(sources==after and protocol_sha==bridge.sha((root/'protocol.json').read_bytes()))
        report={'cases':results,'complete':len(results)==len(cases),'all_pass':len(results)==len(cases) and unchanged and all(x['pass'] for x in results),
                'source_sha256':sources['csharp_bridge.py'],'sources_sha256_at_start':sources,'sources_sha256_at_end':after,
                'protocol_sha256_at_start':protocol_sha,'protocol_sha256_at_end':bridge.sha((root/'protocol.json').read_bytes()),
                'sources_and_protocol_unchanged':unchanged,'run_dir':str(root)}
        bridge.save(root/'results.json',report)
        if not unchanged:break
    return report

if __name__=='__main__':
    result=run()
    bridge.save(bridge.BASE/'evidence/csharp-qualification/latest.json',{'results':str(Path(result['run_dir'])/'results.json')})
    raise SystemExit(not result['all_pass'])
