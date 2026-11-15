"""Audit existing evidence and replay two exact certificates, no C# execution."""
import json
from pathlib import Path
import uuid
import csharp_bridge as bridge

BASE=Path(__file__).resolve().parent
ROOT=BASE/'evidence/csharp-qualification'
OUT=BASE/'evidence'/('evaluation-csharp-review-'+uuid.uuid4().hex)
OUT.mkdir(exist_ok=False)
helper=bridge.helpers()
protocol=json.loads((ROOT/'protocol.json').read_text(encoding='utf-8'))
results=json.loads((ROOT/'results.json').read_text(encoding='utf-8'))
assert results['source_sha256']==bridge.sha((BASE/'csharp_bridge.py').read_bytes())
assert results['complete'] and results['all_pass'] and len(results['cases'])==len(protocol['cases'])==4
qualification=helper.validate_toolchain(bridge.LEAN_MANIFEST,bridge.LEAN)
checked=[]
for specification,row in zip(protocol['cases'],results['cases']):
    folder=Path(row['evidence_dir'])
    report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    assert row['name']==specification['name']
    assert row['actual']==report['verdict']==specification['expected']
    assert report['network_calls']==report['model_calls']==0 and report['input_execution'] is False
    for name,digest in report['files_sha256'].items():
        assert bridge.sha((folder/name).read_bytes())==digest,(row['name'],name)
    assert (folder/'source.cs').read_text(encoding='utf-8')==specification['source']
    assert (folder/'contract.cs').read_text(encoding='utf-8')==specification['contract']
    detail={'name':row['name'],'bundle_hashes_match':True,'verdict':report['verdict']}
    if report['verdict']!='INCONCLUSIF':
        for component,digest in report['components_sha256'].items():
            assert bridge.sha(Path(component).read_bytes())==digest,component
        ir=json.loads((folder/'program-ir.json').read_text(encoding='utf-8'))
        spec=json.loads((folder/'contract-ir.json').read_text(encoding='utf-8'))
        assert ir['source_sha256']==report['input_sha256']['source']
        assert spec['source_sha256']==report['input_sha256']['contract']
        translation=helper.prepare_translation(ir,spec)
        assert translation==json.loads((folder/'translation.json').read_text(encoding='utf-8'))
        accepted=[a for a in report['attempts'] if a.get('accepted')]
        assert len(accepted)==1
        attempt=accepted[0]
        text=helper.certificate_text(translation,attempt['proposal'])
        certificate=Path(report['certificate'])
        assert certificate.read_text(encoding='utf-8')==text
        bridge.resource_admission()
        output=bridge.process([str(bridge.LEAN),'-j','1','-M','512',str(certificate)],10,OUT)
        verdict=helper.check_certificate_output(output['exit_code'],output['stdout'],output['stderr'])
        bridge.save(OUT/(row['name']+'-replay.json'),output)
        assert verdict['accepted'],verdict
        detail.update(certificate_reconstructed=True,kernel_replay=verdict)
    checked.append(detail)
bridge.save(OUT/'result.json',{'verdict':'PASS','scope':'Four existing evidence bundles audited; two exact proof/refutation certificates reconstructed and replayed with pinned Lean; no complete Roslyn campaign repeated.','toolchain':qualification,'cases':checked})
print(str(OUT))
