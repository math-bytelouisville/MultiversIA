import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import code_backends
import legacy_bridge
import migration
import pipeline

BASE=Path(__file__).resolve().parent
ROOT=(BASE/'evidence/pipeline-unit-temporary').resolve()
assert ROOT.is_relative_to(BASE)
ROOT.mkdir(parents=True,exist_ok=True)
SPEC={'schema_version':'mia-math-spec-1','name':'Maximum','parameters':[{'name':'a','type':'int'},{'name':'b','type':'int'}],
      'return_type':'int','precondition':'True','postcondition':'result >= a and result >= b and (result == a or result == b)'}

class PipelineTests(unittest.TestCase):
    def test_real_three_target_pipeline_preserves_inputs_and_evidence(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            spec=copy.deepcopy(SPEC);r=pipeline.run(spec,output_root=folder)
            self.assertEqual(r['status'],'GENERATED');self.assertEqual(spec,SPEC)
            self.assertEqual({a['language'] for a in r['artifacts']},{'python','csharp','rust'})
            self.assertEqual(r['selected_language'],'python')
            bylang={a['language']:a for a in r['verification']}
            self.assertEqual(bylang['python']['status'],'TESTED_FINITE')
            self.assertIn(bylang['csharp']['status'],{'TESTED_FINITE','NON_COMPILE'})
            self.assertIn(bylang['rust']['status'],{'TESTED_FINITE','NON_COMPILE'})
            directory=Path(r['evidence_dir'])
            self.assertEqual(json.loads((directory/'request.json').read_text(encoding='utf-8'))['spec'],SPEC)
            self.assertTrue(any(p.endswith('report.json') for p in r['files_sha256']))
            for path,expected in r['files_sha256'].items():self.assertEqual(pipeline.hash_file(directory/path),expected)
    def test_invalid_target_archived_without_synthesis(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder, mock.patch('math_core.solve') as solve:
            r=pipeline.run(SPEC,['unsupported'],output_root=folder)
            self.assertEqual(r['status'],'REFUSED');solve.assert_not_called()
            self.assertTrue((Path(r['evidence_dir'])/'report.json').exists())
            self.assertEqual(json.loads((Path(r['evidence_dir'])/'request.json').read_text(encoding='utf-8')),
                             {'spec':SPEC,'targets':['unsupported'],'preference':'auto','prove':False})
    def test_invalid_proof_option_preserves_exact_request(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder, mock.patch('math_core.solve') as solve:
            r=pipeline.run(SPEC,prove='yes',output_root=folder)
            self.assertEqual(r['status'],'REFUSED');solve.assert_not_called()
            self.assertEqual(json.loads((Path(r['evidence_dir'])/'request.json').read_text(encoding='utf-8')),
                             {'spec':SPEC,'targets':None,'preference':'auto','prove':'yes'})
    def test_invalid_spec_no_backend_execution(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder, mock.patch.object(code_backends,'validate_generated') as check:
            r=pipeline.run(dict(SPEC,postcondition='__import__("os")'),output_root=folder)
            self.assertEqual(r['status'],'REFUSED');check.assert_not_called()
    def test_backend_counterexample_prevents_completed_generation_claim(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder, mock.patch.object(code_backends,'validate_generated',return_value={'status':'COUNTEREXAMPLE','execution_status':'EXECUTED','compile_status':'NOT_APPLICABLE'}):
            r=pipeline.run(SPEC,['python'],output_root=folder)
            self.assertEqual(r['status'],'INCONCLUSIVE')
    def test_refuted_proof_is_not_accepted(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder, mock.patch.object(code_backends,'prove',return_value={'status':'RÉFUTÉ_CONDITIONNEL','reason':'controlled fixture'}):
            r=pipeline.run(SPEC,['python'],prove=True,output_root=folder)
            self.assertEqual(r['status'],'INCONCLUSIVE');self.assertEqual(r['proof']['status'],'RÉFUTÉ_CONDITIONNEL')
    def test_auto_choice_never_prefers_failed_execution_over_valid_execution(self):
        selected,_=pipeline.select_language(['python','csharp'],'auto',[
            {'language':'python','status':'COUNTEREXAMPLE','execution_status':'EXECUTED'},
            {'language':'csharp','status':'TESTED_FINITE','execution_status':'EXECUTED','compile_status':'COMPILED'}])
        self.assertEqual(selected,'csharp')
    def test_new_run_preserves_previous_artifacts(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            r1=pipeline.run(SPEC,['python'],output_root=folder);p=Path(r1['evidence_dir'])/'report.json';before=p.read_bytes()
            r2=pipeline.run(SPEC,['python'],output_root=folder)
            self.assertNotEqual(r1['run_id'],r2['run_id']);self.assertEqual(p.read_bytes(),before)
    def test_import_refuses_missing_source_without_loading_tools(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder, mock.patch.object(migration,'load_bridge') as load:
            r=migration.import_csharp('',folder)
            self.assertEqual(r['status'],'REFUSED');load.assert_not_called();self.assertIsNone(r['expression'])
    def test_legacy_module_identity_checked_before_execution(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            path=Path(folder)/'wrong.py';path.write_text('raise AssertionError("must not execute")',encoding='utf-8')
            with mock.patch.object(legacy_bridge,'PATH',path):
                with self.assertRaises(ValueError):legacy_bridge.load_bridge()

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PipelineTests))
    migration.save(BASE/'evidence/pipeline-unit-results.json',{'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),
        'scope':'Pipeline réel sans Lean, contrôles négatifs et archives ; refus import avant outil. Import Roslyn et preuve réels qualifiés séparément.'})
    raise SystemExit(not result.wasSuccessful())
