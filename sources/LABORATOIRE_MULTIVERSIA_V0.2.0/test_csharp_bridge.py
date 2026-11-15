import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import csharp_bridge as bridge
import qualify_csharp

TEST_ROOT=(bridge.BASE/'evidence/csharp-unit-temporary').resolve()
assert TEST_ROOT.is_relative_to(bridge.BASE.resolve())
TEST_ROOT.mkdir(parents=True,exist_ok=True)


def param(name,typ='int'):return {'kind':'param','type':typ,'name':name}
def binary(op,a,b):return {'kind':'binary','type':'bool','op':op,'left':a,'right':b}


class BridgeTests(unittest.TestCase):
    def test_bad_input_is_archived_without_process(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder, mock.patch.object(bridge,'process') as process:
            result=bridge.analyze(None,bridge.EXAMPLE_CONTRACT,folder)
            self.assertEqual(result['verdict'],'INCONCLUSIF');process.assert_not_called()
            self.assertTrue((Path(result['evidence_dir'])/'report.json').is_file())
    def test_size_bound_before_toolchain(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder, mock.patch.object(bridge,'helpers') as helper:
            result=bridge.analyze('x'*17000,bridge.EXAMPLE_CONTRACT,folder)
            self.assertEqual(result['verdict'],'INCONCLUSIF');helper.assert_not_called()
    def test_unsupported_ir_never_executes(self):
        with self.assertRaises(ValueError):bridge.evaluate({'kind':'call','text':'malicious'}, {})
    def test_reflexive_contract_detected_as_nondiscriminating(self):
        program={'parameters':[{'name':'a','type':'int'}],'return_type':'int','body':param('a')}
        contract={'body':binary('eq',param('result'),param('result'))}
        result=bridge.contract_sensitivity(program,contract)
        self.assertEqual(result['candidate_results_rejected'],0)
    def test_specific_contract_rejects_mutants(self):
        program={'parameters':[{'name':'a','type':'int'}],'return_type':'int','body':param('a')}
        contract={'body':binary('eq',param('result'),param('a'))}
        self.assertGreater(bridge.contract_sensitivity(program,contract)['candidate_results_rejected'],0)
    def test_counterexample_nomination_not_verdict(self):
        program={'parameters':[{'name':'a','type':'int'}],'body':param('a')}
        contract={'body':binary('ne',param('result'),param('a'))}
        candidate=bridge.propose_counterexample(program,contract)
        self.assertIsInstance(candidate,list);self.assertEqual(len(candidate),1)
    def test_unapproved_driver_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            p=Path(folder)/'driver.py';p.write_text('raise AssertionError("must not execute")')
            with mock.patch.object(bridge,'DRIVER',p):
                with self.assertRaises(ValueError):bridge.helpers()
    def test_fake_lean_success_is_rejected(self):
        helper=bridge.helpers()
        self.assertFalse(helper.check_certificate_output(0,'PASS','')['accepted'])
        self.assertFalse(helper.check_certificate_output(0,'mia_certificate depends on axioms: [sorryAx]','')['accepted'])
    def test_full_result_reference_and_ir_whitelist(self):
        self.assertFalse(bridge.uses_result({'kind':'constant','value':True}))
        self.assertTrue(bridge.uses_result(binary('eq',param('result'),param('a'))))
        helper=bridge.helpers()
        with self.assertRaises(ValueError):helper.validate_method({'name':'X','return_type':'int','parameters':[],'body':{'kind':'call','type':'int'}})
    def test_qualification_preserves_previous_run_and_rejects_generic_failures(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            root=Path(folder)/'campaign'
            fake=lambda *args:{'verdict':'INCONCLUSIF','reason':'RAM indisponible','elapsed_seconds':0,'evidence_dir':folder}
            result=qualify_csharp.run(root,analyze=fake)
            self.assertFalse(result['all_pass']);self.assertTrue(result['sources_and_protocol_unchanged'])
            self.assertTrue(all(not r['pass'] for r in result['cases']))
            prior=(root/'results.json').read_bytes()
            with self.assertRaises(FileExistsError):qualify_csharp.run(root,analyze=fake)
            self.assertEqual((root/'results.json').read_bytes(),prior)
    def test_qualification_detects_protocol_change(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as folder:
            def changed(source,contract,root):
                (root/'protocol.json').write_text('{}',encoding='utf-8')
                return {'verdict':'PROUVÉ_CONDITIONNEL','reason':'CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION','elapsed_seconds':0,'evidence_dir':str(root)}
            result=qualify_csharp.run(Path(folder)/'campaign',analyze=changed)
            self.assertFalse(result['all_pass']);self.assertFalse(result['complete']);self.assertFalse(result['sources_and_protocol_unchanged'])


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BridgeTests))
    (bridge.BASE/'evidence').mkdir(exist_ok=True)
    bridge.save(bridge.BASE/'evidence/csharp-unit-results.json',{'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),'scope':'Contrats, refus et interprétation IR ; tests Lean réels dans campagne séparée.'})
    raise SystemExit(not result.wasSuccessful())
