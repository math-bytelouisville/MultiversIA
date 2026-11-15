"""Real Roslyn import and pipeline evidence, with explicit failure boundaries."""
from pathlib import Path
from copy import deepcopy
import hashlib,json,unittest,uuid
from unittest import mock
import module_import as imp
import module_pipeline as pipeline
import module_backends as backends
from module_model import unit_spec
from migration import save

ROOT=Path(__file__).resolve().parent/'evidence'/('module-pipeline-tests-'+uuid.uuid4().hex)
ROOT.mkdir(parents=True)
SOURCE='// origine éè\r\npublic static class M { public static int Max(int a,int b) { return a>=b?a:b; } public static int Max3(int a,int b,int c) { return Max(Max(a,b),c); } }\r\n'

class ImportPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.imported=imp.import_csharp_module(SOURCE,ROOT)
        if cls.imported['status']!='IMPORTED':raise AssertionError(cls.imported['reason'])
        cls.model=cls.imported['module']
    def test_exact_import_inventory_and_source(self):
        r=self.imported;m=self.model
        self.assertEqual(m['provenance']['source_text'],SOURCE)
        self.assertEqual(m['provenance']['source_sha256'],hashlib.sha256(SOURCE.encode()).hexdigest())
        self.assertEqual(m['dependencies'],{'u0':[],'u1':['u0']})
        self.assertEqual(len(r['inventory']),3);self.assertFalse(r['input_execution'])
        self.assertEqual([x['kind'] for x in r['inventory']],['ClassDeclarationSyntax','MethodDeclarationSyntax','MethodDeclarationSyntax'])
        self.assertEqual((Path(r['evidence_dir'])/'source.cs').read_bytes(),SOURCE.encode())
    def test_real_three_target_module_and_saved_hashes(self):
        before=deepcopy(self.model);r=pipeline.run_module(self.model,output_root=ROOT)
        self.assertEqual(r['status'],'GENERATED',r['reason']);self.assertEqual(before,self.model)
        self.assertEqual([v['status'] for v in r['verification']],['TESTED_FINITE']*3)
        self.assertEqual(r['selected_language'],'python')
        for p,h in r['files_sha256'].items():self.assertEqual(imp.sha(Path(r['evidence_dir'])/p),h)
    def test_revision_breaks_caller_contract_and_preserves_origin(self):
        spec=unit_spec(self.model,'u0');spec['postcondition']='result == (a if a <= b else b)'
        r=pipeline.revise_module(self.model,'u0',spec,'Changer Max en Min pour vérifier le contrat appelant.',ROOT)
        self.assertEqual(r['status'],'REVISED',r.get('reason'));self.assertEqual(r['module']['parent_revision_id'],self.model['revision_id'])
        self.assertEqual(r['module']['provenance'],self.model['provenance'])
        checked=pipeline.run_module(r['module'],['python'],output_root=ROOT)
        self.assertEqual(checked['status'],'INCONCLUSIVE');self.assertEqual(checked['verification'][0]['status'],'COUNTEREXAMPLE')
        self.assertIsNone(checked['selected_language'])
    def test_refused_source_keeps_unknown_members(self):
        r=imp.import_csharp_module('public static class M { public static int X; public static int F(int x) { return x; } }',ROOT)
        self.assertEqual(r['status'],'REFUSED');self.assertGreaterEqual(len(r['inventory']),2)
    def test_external_call_and_recursion_refused(self):
        for source in ['public static class M { public static int F(int x) { return System.Math.Abs(x); } }','public static class M { public static int F(int x) { return F(x); } }']:
            r=imp.import_csharp_module(source,ROOT);self.assertEqual(r['status'],'REFUSED',r['reason'])
    def test_empty_source_never_loads_runtime(self):
        with mock.patch.object(imp,'load_bridge') as loader:
            r=imp.import_csharp_module('',ROOT);self.assertEqual(r['status'],'REFUSED');loader.assert_not_called()
    def test_internal_failure_is_inconclusive_and_archived(self):
        bridge=mock.MagicMock();bridge.process.return_value={'exit_code':0,'stdout':'{}','stderr':''}
        bridge.helpers.return_value.strict_json.return_value={'status':'REFUSED','reason':'INTERNAL_ERROR','source_sha256':hashlib.sha256(SOURCE.encode()).hexdigest()}
        bridge.resource_admission.return_value={};bridge.helpers.return_value.validate_roslyn_runtime.return_value={}
        with mock.patch.object(imp,'load_bridge',return_value=bridge):r=imp.import_csharp_module(SOURCE,ROOT)
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertTrue((Path(r['evidence_dir'])/'report.json').is_file())
    def test_missing_extractor_is_inconclusive_not_unarchived_exception(self):
        bridge=mock.MagicMock();bridge.resource_admission.return_value={};bridge.helpers.return_value.validate_roslyn_runtime.return_value={}
        with mock.patch.object(imp,'SCRIPT',ROOT/'missing.ps1'),mock.patch.object(imp,'load_bridge',return_value=bridge):r=imp.import_csharp_module(SOURCE,ROOT)
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertIsNone(r['module']);self.assertTrue((Path(r['evidence_dir'])/'report.json').is_file())
    def test_invalid_target_prevents_backend(self):
        with mock.patch.object(backends,'validate_module_generated') as validator:
            r=pipeline.run_module(self.model,['rust','rust'],output_root=ROOT);self.assertEqual(r['status'],'REFUSED');validator.assert_not_called()
    def test_altered_provenance_and_revision_refused(self):
        for key in ['provenance','revision_id']:
            m=deepcopy(self.model)
            if key=='provenance':m[key]['source_text']+='altered'
            else:m[key]='0'*64
            with mock.patch.object(backends,'validate_module_generated') as validator:
                self.assertEqual(pipeline.run_module(m,['python'],output_root=ROOT)['status'],'REFUSED');validator.assert_not_called()
    def test_unknown_checker_status_not_success(self):
        with mock.patch.object(backends,'validate_module_generated',return_value={'status':'SURPRISE'}):
            r=pipeline.run_module(self.model,['python'],output_root=ROOT)
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertIsNone(r['selected_language'])
    def test_auto_choice_excludes_unknown_status_when_other_language_verified(self):
        with mock.patch.object(backends,'validate_module_generated',side_effect=[{'status':'SURPRISE','execution_status':'EXECUTED'},{'status':'TESTED_FINITE','execution_status':'EXECUTED'}]):
            r=pipeline.run_module(self.model,['python','csharp'],output_root=ROOT)
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertEqual(r['selected_language'],'csharp')
    def test_uncompiled_sources_have_no_auto_recommendation(self):
        with mock.patch.object(backends,'validate_module_generated',return_value={'status':'NON_COMPILE'}):
            r=pipeline.run_module(self.model,['rust'],output_root=ROOT)
        self.assertEqual(r['status'],'GENERATED');self.assertIsNone(r['selected_language']);self.assertIn('Aucune cible',r['selection_reason'])
    def test_sources_changed_during_run_inconclusive(self):
        with mock.patch.object(pipeline,'identities',side_effect=[{'x':'a'},{'x':'b'}]):
            r=pipeline.run_module(self.model,['python'],output_root=ROOT)
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertFalse(r['sources_unchanged'])
    def test_separate_requests_never_overwrite_evidence(self):
        a=pipeline.run_module(self.model,['invalid'],output_root=ROOT);file=Path(a['evidence_dir'])/'report.json';old=file.read_bytes()
        b=pipeline.run_module(self.model,['invalid'],output_root=ROOT);self.assertNotEqual(a['evidence_dir'],b['evidence_dir']);self.assertEqual(file.read_bytes(),old)

if __name__=='__main__':
    r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ImportPipelineTests))
    save(ROOT/'results.json',{'tests':r.testsRun,'errors':len(r.errors),'failures':len(r.failures),'skipped':len(r.skipped),'scope':'Real import/Roslyn and Python C# Rust modules; negative cases and evidence; no website proof'})
    print('Evidence:',ROOT);raise SystemExit(not r.wasSuccessful())
