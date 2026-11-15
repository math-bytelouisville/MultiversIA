"""Withdrawn Tk fixtures and real UTF-8 IPC; no backend qualification implied."""
from concurrent.futures import Future
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
import types
import unittest
from unittest.mock import patch, Mock
import uuid

import app
import math_core
import module_model

RUN = app.BASE / 'evidence' / ('ui-tests-' + uuid.uuid4().hex)
RUN.mkdir(parents=True)


def fixture_module(label='one', source=None):
    source = app.SOURCE_EXAMPLES['rust'] if source is None else source
    params = [{'name':'a','type':'int'},{'name':'b','type':'int'}]
    body = math_core.parse_expression('a if a >= b else b', {'a':'int','b':'int'})
    units = [{'id':'u0','name':'Max2','qualified_name':'Fixture.Max2','parameters':params,'return_type':'int','body':body,'contract':None},
        {'id':'u1','name':'Wrapper','qualified_name':'Fixture.Wrapper','parameters':params,'return_type':'int','body':{'kind':'call','type':'int','target':'u0','arguments':[{'kind':'param','type':'int','name':'a'},{'kind':'param','type':'int','name':'b'}]},'contract':None}]
    return module_model.validate_module({'schema_version':'mia-module-1','semantic_profile':'pure-int32-bool-wrap-call-1','module_id':'fixture-'+label,'name':'Fixture',
        'parent_revision_id':None,'intent':'Explicit UI fixture','provenance':{'source_text':source,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'import_id':'fixture-'+label,'origin':{'language':'rust'},'limitations':['UI fixture, not Rust parse evidence']},'units':units})


def import_result(module=None):
    return {'status':'IMPORTED','reason':'UI fixture only','source_language':'rust','module':module or fixture_module(),'limitations':['Fixture UI seulement.']}


def archived(store, operation='import', parameters=None, result=None, context=None):
    if parameters is None: parameters={'source':app.SOURCE_EXAMPLES['rust'],'source_language':'rust'}
    result = result or import_result()
    output = subprocess.CompletedProcess([],0,json.dumps({'ok':True,'result':result},ensure_ascii=False),'')
    with patch('app.run_worker',return_value=output):
        return store.command(operation,parameters,context)


class StoreTests(unittest.TestCase):
    def setUp(self): self.store=app.Store(RUN/self._testMethodName)

    def test_utf8_exact_context_archive_and_no_shell(self):
        params={'source':'// hypothèse é漢字 📐\r\n','source_language':'rust'}
        with patch('app.run_worker',return_value=subprocess.CompletedProcess([],0,'{"ok":true,"result":{"status":"REFUSED"}}','')) as process:
            row=self.store.command('import',params,{'source_basis':params})
        self.assertEqual(row['request']['parameters'],params)
        self.assertEqual(row['request']['context']['source_basis'],params)
        self.assertEqual(json.loads(process.call_args.kwargs['input']),params)
        self.assertEqual(process.call_args.kwargs['encoding'],'utf-8')
        self.assertNotIn('shell',process.call_args.kwargs)

    def test_timeout_archived_uncertain_without_retry(self):
        with patch('app.run_worker',side_effect=subprocess.TimeoutExpired(['owned-worker'],180,output=b'partial')) as process:
            row=self.store.command('import',{'source':'a','source_language':'rust'})
        self.assertEqual(process.call_count,1)
        self.assertTrue(row['result']['uncertain'])
        self.assertEqual((Path(row['archive'])/'stdout.txt').read_bytes(),b'partial')

    def test_corruption_and_partial_receipts_refused(self):
        row=archived(self.store)
        (Path(row['archive'])/'result.json').write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):self.store.read(row['archive'])
        self.assertIn('non vérifié',self.store.history()[0]['error'])
        with self.assertRaises(ValueError):self.store.command('arbitrary',{})

    def test_json_guards_and_write_once_requests(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.assertRaises(ValueError):app.parse(text)
        first=archived(self.store);second=archived(self.store)
        self.assertNotEqual(first['archive'],second['archive'])
        self.assertEqual(len(self.store.history()),2)

    def test_owned_pid_tree_stop_uses_pid_never_global_name(self):
        process=Mock(pid=12345);process.poll.return_value=None
        with patch('app.subprocess.run',return_value=types.SimpleNamespace(returncode=0)) as run:
            app.stop_owned_process(process)
        self.assertEqual(run.call_args.args[0],['taskkill','/PID','12345','/T','/F'])
        process.wait.assert_called_once()

    def test_worker_timeout_stops_owned_tree_then_closes_pipes(self):
        process=Mock(pid=1122,returncode=1)
        process.communicate.side_effect=[subprocess.TimeoutExpired(['worker'],1),('partiel','err')]
        with patch('app.subprocess.Popen',return_value=process),patch('app.stop_owned_process') as stop:
            with self.assertRaises(subprocess.TimeoutExpired):app.run_worker(['worker'],input='{}',timeout=1,encoding='utf-8')
        stop.assert_called_once_with(process)
        for stream in (process.stdin,process.stdout,process.stderr):stream.close.assert_called_once()

    def test_tree_refusal_kills_and_waits_owned_parent_but_stays_uncertain(self):
        process=Mock(pid=45678,returncode=1);process.poll.return_value=None
        with patch('app.subprocess.run',return_value=types.SimpleNamespace(returncode=1)):
            with self.assertRaises(app.ProcessTreeUncertain) as raised:app.stop_owned_process(process)
        process.kill.assert_called_once();process.wait.assert_called_once()
        self.assertIn('descendants non confirmé',str(raised.exception))
        process.communicate.side_effect=subprocess.TimeoutExpired(['worker'],1,output=b'partial')
        with patch('app.subprocess.Popen',return_value=process),patch('app.stop_owned_process',side_effect=raised.exception):
            with self.assertRaises(app.ProcessTreeUncertain) as final:app.run_worker(['worker'],input='{}',timeout=1)
        with patch('app.run_worker',side_effect=final.exception):
            row=self.store.command('import',{'source':'a','source_language':'rust'})
        self.assertEqual(row['result']['status'],'INCONCLUSIVE');self.assertTrue(row['result']['uncertain'])
        self.assertEqual((Path(row['archive'])/'stdout.txt').read_bytes(),b'partial')

    def test_real_worker_utf8_with_fixture_backend(self):
        code="import sys,types;import app;m=types.ModuleType('migrator');m.import_source=lambda **kw:{'status':'FIXTURE','source':kw['source'],'language':kw['source_language']};sys.modules['migrator']=m;raise SystemExit(app.worker_main('import',sys.argv[1]))"
        params={'source':'// Équation ≥ 漢字 📐\r\n','source_language':'rust'}
        process=subprocess.run([app.PYTHON,'-c',code,str(self.store.root/'proof')],input=json.dumps(params,ensure_ascii=False),capture_output=True,encoding='utf-8',timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertEqual(json.loads(process.stdout)['result']['source'],params['source'])


class UITests(unittest.TestCase):
    def setUp(self):
        self.store=app.Store(RUN/self._testMethodName)
        self.root=tk.Tk();self.root.withdraw()
        self.ui=app.Workshop(self.root,self.store,auto_poll=False)

    def tearDown(self):
        if not self.ui.closed:self.ui.finish_close()

    def load(self,module=None):
        row=archived(self.store,result=import_result(module))
        self.ui.apply_record(row,completed=True)
        return row

    def context(self):return {'source_import':deepcopy(self.ui.source_import),'source_basis':deepcopy(self.ui.source_basis)}

    def edit_property(self,post='result <= a and result <= b'):
        spec=json.loads(self.ui.get(self.ui.spec_text));spec['postcondition']=post
        self.ui.put(self.ui.spec_text,app.pretty(spec));self.ui.capture()
        return spec

    def test_startup_has_no_implicit_backend(self):
        self.assertIsNone(self.ui.future)
        self.assertEqual(self.store.history(),[])
        self.assertEqual(str(self.ui.run_button['state']),'disabled')
        self.assertIn('V0.5.0',self.root.title())

    def test_source_drafts_survive_language_switch_and_reopen(self):
        text='// brouillon é漢字\n'
        self.ui.put(self.ui.source_text,text)
        self.ui.source_language.set('csharp');self.ui.change_language()
        self.ui.put(self.ui.source_text,'// C# autre')
        self.ui.close()
        root=tk.Tk();root.withdraw();ui=app.Workshop(root,self.store,auto_poll=False)
        try:
            self.assertEqual(ui.get(ui.source_text),'// C# autre')
            ui.source_language.set('rust');ui.change_language()
            self.assertEqual(ui.get(ui.source_text),text)
            self.assertIsNone(ui.future)
        finally:ui.finish_close()

    def test_zoom_anchor_and_result_refresh_preserve_camera(self):
        self.ui.zoom=.7;self.ui.ox=80;self.ui.oy=40
        before=((130-self.ui.ox)/self.ui.zoom,(90-self.ui.oy)/self.ui.zoom)
        self.ui.zoom_at(1.25,130,90)
        self.assertAlmostEqual(before[0],(130-self.ui.ox)/self.ui.zoom)
        self.assertAlmostEqual(before[1],(90-self.ui.oy)/self.ui.zoom)
        camera=(self.ui.zoom,self.ui.ox,self.ui.oy);self.load()
        self.assertEqual(camera,(self.ui.zoom,self.ui.ox,self.ui.oy))

    def test_source_or_property_pending_blocks_generation(self):
        self.load();self.assertEqual(str(self.ui.run_button['state']),'normal')
        self.ui.put(self.ui.source_text,'changed');self.ui.update_controls()
        self.assertEqual(str(self.ui.run_button['state']),'disabled')
        self.ui.put(self.ui.source_text,app.SOURCE_EXAMPLES['rust']);self.edit_property();self.ui.update_controls()
        self.assertEqual(str(self.ui.run_button['state']),'disabled')
        with patch.object(self.ui,'start') as start:self.ui.translate()
        start.assert_not_called()

    def test_revision_requires_explicit_intent(self):
        self.load();self.edit_property()
        with patch.object(self.ui,'start') as start:self.ui.revise()
        start.assert_not_called()
        self.assertIn('intention',self.ui.status.get())

    def test_revision_history_restores_exact_origin_not_other_module(self):
        first=self.load();module=deepcopy(self.ui.module);context=self.context();spec=app.unit_spec(module['units'][0])
        revision=archived(self.store,'revise',{'module':module,'unit_id':'u0','spec':spec,'intent':'fixture'},
            {'status':'REFUSED','reason':'fixture refusal','module':None},context)
        second=fixture_module('two','// autre source')
        self.load(second)
        self.ui.apply_record(revision)
        self.assertEqual(self.ui.module['module_id'],module['module_id'])
        self.assertEqual(self.ui.source_basis,context['source_basis'])
        self.assertEqual(self.ui.source_import,context['source_import'])

    def test_unsent_properties_survive_module_history_and_reselection(self):
        first=self.load();draft=self.edit_property();key=self.ui.key()
        second=fixture_module('two');self.load(second)
        self.assertNotEqual(json.loads(self.ui.get(self.ui.spec_text)),draft)
        self.ui.apply_record(first)
        self.assertEqual(json.loads(self.ui.get(self.ui.spec_text)),draft)
        self.assertEqual(json.loads(self.ui.property_drafts[key]),draft)

    def test_repaired_model_not_adopted_implicitly_and_adoption_survives_reopen(self):
        self.load();original=deepcopy(self.ui.module)
        repaired=deepcopy(original);repaired['parent_revision_id']=original['revision_id'];repaired['intent']='fixture repair';repaired.pop('revision_id');repaired=module_model.validate_module(repaired)
        params={'module':original,'targets':['csharp'],'source_import':self.ui.source_import,'auto_correct':True,'max_repairs':2}
        row=archived(self.store,'translate',params,{'status':'TESTED_FINITE','reason':'fixture','module':repaired,'repair':{'status':'REPAIRED_FINITE','promoted':True},'verification':{'source_check':{'status':'TESTED_FINITE','agreement':'DIFFERENT'},'generated_checks':[{'language':'csharp','status':'TESTED_FINITE'}]}},self.context())
        self.ui.apply_record(row,completed=True)
        self.assertEqual(self.ui.module['revision_id'],original['revision_id'])
        self.assertIn('DIFFERENT',self.ui.get(self.ui.result_text))
        self.ui.adopt_repair();self.assertEqual(self.ui.module['revision_id'],repaired['revision_id'])
        self.assertEqual(json.loads(self.ui.get(self.ui.raw_text))['active_module']['revision_id'],repaired['revision_id'])
        self.ui.close()
        root=tk.Tk();root.withdraw();ui=app.Workshop(root,self.store,auto_poll=False)
        try:
            self.assertEqual(ui.module['revision_id'],repaired['revision_id']);self.assertIsNone(ui.future)
            self.assertEqual(json.loads(ui.get(ui.raw_text))['active_module']['revision_id'],repaired['revision_id'])
        finally:ui.finish_close()

    def test_uncertain_result_and_corrupt_history_clear_old_claim(self):
        row=self.load();(Path(row['archive'])/'result.json').write_text('{}',encoding='utf-8')
        self.ui.refresh_history()
        self.assertIsNone(self.ui.module);self.assertIsNone(self.ui.record)
        self.assertIn('non vérifié',self.ui.status.get())
        self.assertEqual(str(self.ui.run_button['state']),'disabled')

    def test_completion_updates_status_and_unlocks_editors(self):
        row=archived(self.store)
        future=Future();future.set_result(row)
        self.ui.future=future;self.ui.update_controls()
        self.assertEqual(str(self.ui.source_text['state']),'disabled')
        self.ui.tick(schedule=False)
        self.assertIn('terminée',self.ui.status.get())
        self.assertEqual(str(self.ui.source_text['state']),'normal')

    def test_long_function_name_uses_id_in_spec_and_equations_keep_calls(self):
        unit=fixture_module()['units'][0];unit['name']='x'*100
        self.assertEqual(app.unit_spec(unit)['name'],'u0')
        module=fixture_module()
        self.assertIn('Max2',app.equation(module['units'][1]['body'],{'u0':'Max2'}))

    def test_arithmetic_equations_preserve_explicit_wrap_semantics(self):
        a={'kind':'param','type':'int','name':'a'};b={'kind':'param','type':'int','name':'b'}
        for op in ('add32','sub32','mul32'):
            node={'kind':'binary','type':'int','op':op,'left':a,'right':b}
            self.assertEqual(app.equation(node),op+'(a, b)')
        self.assertEqual(app.equation({'kind':'neg32','type':'int','operand':a}),'neg32(a)')
        self.load()
        math_text=self.ui.get(self.ui.math_text)
        self.assertIn('modulo 2^32',math_text)
        self.assertIn('add32(2147483647, 1) = -2147483648',math_text)

    def test_legacy_module_does_not_offer_unsupported_arithmetic(self):
        module=fixture_module();module['semantic_profile']='pure-int32-bool-call-1'
        self.ui.load_module(module)
        math_text=self.ui.get(self.ui.math_text)
        self.assertIn('ancien profil',math_text)
        self.assertIn('sans arithmétique',math_text)
        self.assertIn('Réimportez la source',math_text)
        for operation in ('add32(', 'sub32(', 'mul32(', 'neg32('):
            self.assertNotIn(operation,math_text)

    def test_default_sources_make_overflow_and_composition_explicit(self):
        self.assertIn('a.wrapping_add(b)',app.SOURCE_EXAMPLES['rust'])
        self.assertIn('add_pair(a, 2147483647)',app.SOURCE_EXAMPLES['rust'])
        self.assertIn('unchecked(a + b)',app.SOURCE_EXAMPLES['csharp'])
        self.assertIn('AddPair(a, 2147483647)',app.SOURCE_EXAMPLES['csharp'])
        self.assertIn('-2147483648',app.SOURCE_EXAMPLES['csharp'])

    def test_results_separate_execution_model_contract_and_source(self):
        self.load();module=deepcopy(self.ui.module)
        result={'status':'COUNTEREXAMPLE','module':module,'candidate_module':module,'reason':'fixture',
            'repair':{'status':'UNRESOLVED','promoted':False,'attempts':[{'status':'REVISED'}]},
            'verification':{'status':'COUNTEREXAMPLE','source_check':{'status':'EXECUTED','agreement':'DIFFERENT'},
                'generated_checks':[{'language':'csharp','status':'COUNTEREXAMPLE','compile_status':'COMPILED','execution_status':'EXECUTED','model_agreement':'PASS',
                    'units':[{'unit_id':'u0','status':'COUNTEREXAMPLE','tests':{'contract_checked':49,'counterexample_indices':list(range(512))}}],
                    'counterexamples':[{'case_index':i,'inputs':[i,0]} for i in range(512)]}]}}
        row=archived(self.store,'translate',{'module':module,'targets':['csharp'],'source_import':self.ui.source_import,'auto_correct':True,'max_repairs':2},result,self.context())
        self.ui.apply_record(row)
        text=self.ui.get(self.ui.result_text)
        self.assertIn('Accord au modèle : PASS',text);self.assertIn('Contrat u0 : COUNTEREXAMPLE',text)
        self.assertIn('DIFFERENT',text);self.assertIn('512 contre-exemples',text)
        self.assertIn('non promu',text);self.assertLess(len(text),1800)

    def test_new_target_and_unpromoted_revision_never_inherit_old_pass(self):
        self.load();module=deepcopy(self.ui.module)
        result={'status':'TESTED_FINITE','module':module,'verified_revision_id':module['revision_id'],'repair':{'promoted':False},
            'verification':{'status':'TESTED_FINITE','source_check':{'status':'EXECUTED','agreement':'PASS'},'generated_checks':[{'language':'csharp','status':'TESTED_FINITE'}]}}
        row=archived(self.store,'translate',{'module':module,'targets':['csharp'],'source_import':self.ui.source_import,'auto_correct':True,'max_repairs':2},result,self.context())
        self.ui.apply_record(row)
        label=lambda:next(n[-1] for n in self.ui.nodes() if n[0]=='checks')
        self.assertEqual(label(),'TESTED_FINITE')
        self.ui.target.set('rust');self.ui.render()
        self.assertIn('pas été vérifié',label())
        self.assertIn('csharp',self.ui.get(self.ui.result_text))
        self.ui.target.set('csharp');self.ui.module=deepcopy(module);self.ui.module['revision_id']='different-revision'
        self.assertIn('pas été vérifié',label())

    def test_source_language_and_real_execution_are_independent_of_target_success(self):
        self.load();module=deepcopy(self.ui.module)
        params={'module':module,'targets':['rust'],'source_import':self.ui.source_import,'auto_correct':False,'max_repairs':0}
        result={'status':'TESTED_FINITE','module':module,'verified_revision_id':module['revision_id'],'repair':{'promoted':False},
            'verification':{'status':'TESTED_FINITE','source_check':{'language':'csharp','status':'TESTED_FINITE','compile_status':'COMPILED','execution_status':'EXECUTED','baseline_model_agreement':'PASS','agreement':'DIFFERENT'},
                'generated_checks':[{'language':'rust','status':'TESTED_FINITE','model_agreement':'PASS'}]}}
        row=archived(self.store,'translate',params,result,self.context());self.ui.apply_record(row)
        text=self.ui.get(self.ui.result_text)
        self.assertIn('Source originale · C# : TESTED_FINITE',text)
        self.assertIn('Compilation : COMPILED · exécution : EXECUTED',text)
        self.assertIn('modèle importé : PASS',text);self.assertIn('modèle courant : DIFFERENT',text)
        result['verification']['source_check']={'language':'csharp','status':'NOT_TESTED'}
        self.ui.apply_record(archived(self.store,'translate',params,result,self.context()))
        text=self.ui.get(self.ui.result_text)
        self.assertIn('Source originale · C# : NOT_TESTED',text)
        self.assertIn('exécution : NOT_TESTED',text)
        self.assertIn('modèle importé : Non établi',text)


if __name__=='__main__':
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(StoreTests),unittest.defaultTestLoader.loadTestsFromTestCase(UITests)])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    (RUN/'results.json').write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors),'scope':'Withdrawn Tk and labelled fixtures, plus real UTF8 worker with fake backend; no real migration qualification.'},indent=2),encoding='utf-8')
    print(RUN)
    raise SystemExit(not result.wasSuccessful())
