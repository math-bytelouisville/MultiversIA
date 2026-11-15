"""Withdrawn Tk + real UTF-8 command transport; fixtures are explicitly labelled."""
from concurrent.futures import Future
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tkinter as tk
import unittest
from unittest import mock
import uuid
import types
import app
import ui_backend

RUN=ui_backend.BASE/'evidence'/('ui-tests-'+uuid.uuid4().hex)
RUN.mkdir(parents=True)
def hashes(path):return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}
def record(operation='run',spec=None,result=None):
    parameters={'spec':spec or app.example(),'targets':['python','csharp','rust'],'preference':'auto','prove':False,'import_origin':None,'spec_text_exact':app.pretty(spec or app.example())} if operation=='run' else {'source':'// éà📐\n'+app.SOURCE}
    result=result if result is not None else {'status':'GENERATED','reason':'Fixture explicite, pas une preuve de synthèse.','spec':spec or app.example(),'synthesis':{'status':'CANDIDATE','reason':'Fixture','candidate':{'kind':'param','type':'int','name':'a'}},'artifacts':[{'language':'python','source':'fixture Python'},{'language':'rust','source':'fixture Rust'}], 'verification':[{'language':'python','status':'TESTED_FINITE','compile_status':'NOT_APPLICABLE','execution_status':'EXECUTED','reason':'Fixture'},{'language':'rust','status':'NON_COMPILE','compile_status':'NON_COMPILE','execution_status':'NOT_EXECUTED','reason':'Compilateur absent dans fixture'}], 'proof':None,'events':[{'seq':1,'type':'FIXTURE','at':'2026-11-15','data':{'fixture':True}}],'limitations':['Fixture de présentation seulement.']}
    return {'request_id':'request-fixture','operation':operation,'request':{'parameters':parameters},'result':result,'archive':'archive-fixture'}

class BackendTests(unittest.TestCase):
    def setUp(self):self.store=ui_backend.Store(RUN/self._testMethodName)
    def test_unicode_exact_request_archived_and_command_is_not_shell(self):
        parameters={'spec':app.example(),'targets':['python'],'preference':'auto','prove':False,'import_origin':{'source':'// vérification 📐'},'spec_text_exact':'Propriété ≥ à contrôler'}
        output=mock.Mock(returncode=0,stdout=json.dumps({'ok':True,'result':{'status':'INCONCLUSIVE','reason':'Fixture'}}))
        with mock.patch('ui_backend.subprocess.run',return_value=output) as run:out=self.store.command('run',**parameters)
        self.assertEqual(out['request']['parameters'],parameters)
        self.assertEqual(json.loads(run.call_args.kwargs['input']),parameters);self.assertEqual(run.call_args.kwargs['encoding'],'utf-8');self.assertNotIn('shell',run.call_args.kwargs)
        before=hashes(self.store.root);rows=self.store.history();self.assertEqual(rows[0],out);self.assertEqual(before,hashes(self.store.root))
    def test_unique_archives_failure_no_retry(self):
        with mock.patch('ui_backend.subprocess.run',side_effect=subprocess.TimeoutExpired(['worker'],120)) as run:
            one=self.store.command('import',source='a');two=self.store.command('import',source='b')
        self.assertEqual(run.call_count,2);self.assertNotEqual(one['archive'],two['archive']);self.assertEqual(one['result']['status'],'INCONCLUSIVE');self.assertIn('incertain',one['result']['reason'])
    def test_corrupt_and_interrupted_archives_never_claim_verified(self):
        folder=self.store.root/'requests'/'request-incomplete';folder.mkdir(parents=True)
        ui_backend.atomic_json(folder/'request.json',{'operation':'run','at':'2026'})
        self.assertIn('interrompue',self.store.history()[0]['error'])
        ui_backend.atomic_json(folder/'outcome.json',{'request_id':folder.name,'sha256':'bad'})
        self.assertIn('non vérifiée',self.store.history()[0]['error'])
    def test_allowlist(self):
        with self.assertRaises(ValueError):self.store.command('execute',source='anything')
    def test_real_worker_import_refuses_empty_without_tools(self):
        result=self.store.command('import',source='')
        self.assertEqual(result['result']['status'],'REFUSED')
        self.assertIn('16Kio',result['result']['reason'])
        self.assertTrue(Path(result['result']['evidence_dir']).is_dir())
    def test_real_worker_utf8_roundtrip_with_fixture_pipeline(self):
        # Launch the actual worker main with a fake pipeline only; explicitly not synthesis evidence.
        code="import sys,types; import ui_backend; m=types.ModuleType('pipeline'); m.run=lambda spec,**kw:{'status':'FIXTURE','spec':spec}; sys.modules['pipeline']=m; sys.argv=['ui_backend.py','--worker','run',sys.argv[1]]; raise SystemExit(ui_backend.main())"
        spec=app.example();spec['name']='Évolution à contrôler 📐'
        result=subprocess.run([ui_backend.PYTHON,'-c',code,str(self.store.root/'proofs')],cwd=ui_backend.BASE,
            input=json.dumps({'spec':spec,'targets':['python'],'preference':'auto','prove':False},ensure_ascii=False),capture_output=True,encoding='utf-8',timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if __import__('os').name=='nt' else 0)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['result']['spec'],spec)

class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.store=ui_backend.Store(RUN/self._testMethodName);self.root=tk.Tk();self.root.withdraw();self.ui=app.Workshop(self.root,self.store,auto_poll=False)
    def tearDown(self):
        if not self.ui.closed:self.ui.finish_close()
    def test_properties_are_not_implementation_templates(self):
        spec=self.ui.draft();self.assertIn('result >= a',spec['postcondition']);self.assertNotIn('candidate',spec)
        self.ui.new_draft(app.example('minimum'));self.assertIn('result <= a',self.ui.draft()['postcondition'])
    def test_zoom_anchor_and_render_preserve_view(self):
        self.ui.zoom=.7;self.ui.ox=210;self.ui.oy=80;point=((100-self.ui.ox)/self.ui.zoom,(120-self.ui.oy)/self.ui.zoom)
        self.ui.zoom_at(1.25,100,120);self.assertEqual(point,((100-self.ui.ox)/self.ui.zoom,(120-self.ui.oy)/self.ui.zoom))
        before=(self.ui.zoom,self.ui.ox,self.ui.oy);self.ui.apply_record(record());self.assertEqual(before,(self.ui.zoom,self.ui.ox,self.ui.oy))
    def test_results_locked_to_exact_inputs_and_edit_clears_visible_proof(self):
        result=record();self.ui.apply_record(result)
        self.assertEqual(str(self.ui.spec_text['state']),'disabled');self.assertEqual(json.loads(self.ui.raw.get('1.0','end')),result)
        self.assertIn('fixture Python',self.ui.code.get('1.0','end'))
        self.ui.edit();self.assertEqual(str(self.ui.spec_text['state']),'normal');self.assertIsNone(self.ui.record)
        self.assertNotIn('fixture Python',self.ui.code.get('1.0','end'))
    def test_noncompiled_rust_is_visible_and_proof_distinct(self):
        self.ui.apply_record(record());self.ui.selected='rust';self.ui.render()
        self.assertIn('NON_COMPILE',self.ui.summary.get('1.0','end'));self.assertIn('NOT_EXECUTED',self.ui.summary.get('1.0','end'))
        self.ui.selected='proof';self.ui.render();self.assertIn('Non demandée',self.ui.summary.get('1.0','end'))
    def test_invalid_json_or_no_target_does_not_dispatch(self):
        with mock.patch.object(self.ui,'dispatch') as dispatch:
            self.ui.put(self.ui.spec_text,'bad',editable=True);self.ui.run();dispatch.assert_not_called()
            self.ui.new_draft(app.example())
            for value in self.ui.targets.values():value.set(False)
            self.ui.run();dispatch.assert_not_called()
    def test_run_sends_exact_text_and_origin_and_explicit_proof_option(self):
        self.ui.origin={'source':'// Origine éà📐'};self.ui.prove.set(True)
        with mock.patch.object(self.ui,'dispatch') as dispatch:self.ui.run()
        self.assertEqual(dispatch.call_args.args,('run',));self.assertTrue(dispatch.call_args.kwargs['prove']);self.assertEqual(dispatch.call_args.kwargs['import_origin'],self.ui.origin)
        self.assertEqual(dispatch.call_args.kwargs['spec_text_exact'],self.ui.spec_text.get('1.0','end-1c'))
    def test_import_does_not_replace_draft_until_explicit_load(self):
        before=self.ui.spec_text.get('1.0','end-1c');self.ui.open_import()
        result=record('import',result={'status':'IMPORTED','reason':'Fixture','spec':app.example('minimum'),'mathematics':'fixture maths','limitations':[],'evidence_dir':'fixture'})
        self.ui.apply_record(result);self.assertEqual(self.ui.spec_text.get('1.0','end-1c'),before)
        self.assertEqual(self.ui.import_fields['source'].get('1.0','end-1c'),result['request']['parameters']['source'])
        self.assertEqual(str(self.ui.import_fields['source']['state']),'disabled')
        self.ui.import_window.destroy();self.ui.open_import();self.assertIn('éà📐',self.ui.import_fields['source'].get('1.0','end'))
        self.ui.load_import();self.assertEqual(self.ui.draft()['name'],'minimum');self.assertEqual(self.ui.origin['source'],result['request']['parameters']['source'])
        self.ui.import_window.destroy()
    def test_refused_null_synthesis_and_completed_status(self):
        result=record(result={'status':'REFUSED','reason':'Propriété refusée','synthesis':None,'spec':None,'limitations':[]})
        self.ui.job=Future();self.ui.job.set_result(result);self.ui.operation='run';self.ui.tick()
        self.assertIn('terminé : REFUSED',self.ui.message.get());self.assertNotIn('en cours',self.ui.message.get())
    def test_busy_locks_editor_no_fabricated_stage_and_no_restart(self):
        self.ui.job=Future();self.ui.operation='run';self.ui.render()
        self.assertEqual(str(self.ui.spec_text['state']),'disabled');self.assertIn('ne sont pas encore observées',self.ui.summary.get('1.0','end'))
        self.ui.job=None;self.ui.operation=None;self.ui.finish_close()
        self.root=tk.Tk();self.root.withdraw();self.ui=app.Workshop(self.root,self.store,auto_poll=False);self.assertIsNone(self.ui.job)
    def test_draft_and_import_source_restore_without_restarting_or_claiming_result(self):
        draft='{"brouillon incomplet": "à préserver 📐"}'
        self.ui.put(self.ui.spec_text,draft,editable=True);self.ui.origin={'archive':'origine conservée'}
        self.ui.open_import();self.ui.put(self.ui.import_fields['source'],'source C# à préserver 📐',editable=True)
        self.ui.finish_close();self.root=tk.Tk();self.root.withdraw();self.ui=app.Workshop(self.root,self.store,auto_poll=False)
        self.assertEqual(self.ui.spec_text.get('1.0','end-1c'),draft);self.assertEqual(self.ui.origin['archive'],'origine conservée')
        self.assertIsNone(self.ui.record);self.assertIsNone(self.ui.job)
        self.ui.open_import();self.assertEqual(self.ui.import_fields['source'].get('1.0','end-1c'),'source C# à préserver 📐');self.ui.import_window.destroy()
    def test_history_corruption_invalidates_selected_visible_result(self):
        self.ui.apply_record(record());self.ui.scan=Future();self.ui.scan.set_result([{'request_id':'request-fixture','error':'Archive altérée'}]);self.ui.tick()
        self.assertIn('NON VÉRIFIÉE',self.ui.summary.get('1.0','end'));self.assertNotIn('fixture Python',self.ui.code.get('1.0','end'))
    def test_closing_import_before_submit_retains_exact_unsent_source(self):
        for source in ('// Brouillon non envoyé éà📐\n'+app.SOURCE,''):
            self.ui.open_import();self.ui.put(self.ui.import_fields['source'],source,editable=True)
            self.ui.show_import();self.assertEqual(self.ui.import_fields['source'].get('1.0','end-1c'),source)
            self.ui.close_import()
            self.ui.open_import();self.assertEqual(self.ui.import_fields['source'].get('1.0','end-1c'),source);self.ui.close_import()
            self.ui.finish_close();self.root=tk.Tk();self.root.withdraw();self.ui=app.Workshop(self.root,self.store,auto_poll=False)
            self.ui.open_import();self.assertEqual(self.ui.import_fields['source'].get('1.0','end-1c'),source)
            self.assertIsNone(self.ui.job);self.assertIsNone(self.ui.import_record);self.ui.close_import()
    def test_old_incomplete_scan_cannot_replace_completed_command(self):
        result=record();self.ui.job=Future();self.ui.job.set_result(result);self.ui.operation='run'
        self.ui.scan=Future();self.ui.scan.set_result([{'request_id':result['request_id'],'error':'Opération toujours en cours'}]);self.ui.scan_generation=self.ui.generation
        self.ui.tick();self.assertEqual(self.ui.record,result);self.assertIn('fixture Python',self.ui.code.get('1.0','end'))
        self.ui.apply_history([result]);self.assertEqual(self.ui.record,result)

def module_fixture():
    p=lambda name:{'kind':'param','type':'int','name':name}
    params=lambda names:[{'name':name,'type':'int'} for name in names]
    maxbody={'kind':'ite','type':'int','condition':{'kind':'binary','type':'bool','op':'ge','left':p('a'),'right':p('b')},'when_true':p('a'),'when_false':p('b')}
    call=lambda args:{'kind':'call','type':'int','target':'u0','arguments':args}
    units=[{'id':'u0','name':'Max2','qualified_name':'ExampleModule.Max2','parameters':params('ab'),'return_type':'int','body':maxbody,'contract':{'precondition':'True','postcondition':app.example()['postcondition']}},
           {'id':'u1','name':'Max3','qualified_name':'ExampleModule.Max3','parameters':params('abc'),'return_type':'int','body':call([call([p('a'),p('b')]),p('c')]),'contract':{'precondition':'True','postcondition':'result >= a and result >= b and result >= c and (result == a or result == b or result == c)'}}]
    return {'schema_version':'mia-module-1','semantic_profile':'pure-int32-bool-call-1','module_id':'fixture-module','name':'Fixture module','parent_revision_id':None,'revision_id':'0'*64,'intent':'Fixture initial import','provenance':{'source_text':app.MODULE_SOURCE,'source_sha256':hashlib.sha256(app.MODULE_SOURCE.encode('utf-8')).hexdigest(),'import_id':'fixture-import'},'units':units,'dependencies':{'u0':[],'u1':['u0']},'topological_order':['u0','u1'],'coverage':{'formal_scope':'NOT_PROVED_MODULE'}}

def module_record(operation='module_import',module=None,status=None,**extra):
    module=deepcopy(module or module_fixture());parameters={'source':app.MODULE_SOURCE} if operation=='module_import' else {'module':module,'targets':['python','csharp','rust'],'preference':'auto'}
    result={'status':status or ('IMPORTED' if operation=='module_import' else 'GENERATED'),'reason':'Fixture explicite UI, aucune qualification du module.','module':module,'inventory':[{'name':u['qualified_name']} for u in module['units']],'coverage':{'formal_scope':'NOT_PROVED_MODULE'},'artifacts':[],'verification':[],'events':[],'limitations':['Fixture UI'],'formal_scope':'NOT_PROVED_MODULE'}
    result.update(extra)
    return {'request_id':'fixture-'+operation,'operation':operation,'request':{'parameters':parameters},'result':result,'archive':'fixture-archive'}

class ModuleBackendTests(unittest.TestCase):
    def test_site_launch_fixed_path_no_shell_and_hidden(self):
        with mock.patch.object(ui_backend,'BASE',RUN/'launch-fixture'),mock.patch('ui_backend.subprocess.Popen') as process:
            ui_backend.BASE.mkdir();(ui_backend.BASE/'launch_site.py').write_text('# explicit fixture',encoding='utf-8')
            ui_backend.launch_site()
            self.assertEqual(process.call_args.args[0],[ui_backend.PYTHON,str(ui_backend.BASE/'launch_site.py')])
            self.assertEqual(process.call_args.kwargs['creationflags'],subprocess.CREATE_NO_WINDOW)
            self.assertNotIn('shell',process.call_args.kwargs)
            self.assertEqual(len(list((ui_backend.BASE/'local/site-launches').glob('*.log'))),1)
    def test_module_worker_routes_exact_apis(self):
        imports=types.SimpleNamespace(import_csharp_module=mock.Mock(return_value={'status':'IMPORTED'}))
        pipeline=types.SimpleNamespace(run_module=mock.Mock(return_value={'status':'GENERATED'}),revise_module=mock.Mock(return_value={'status':'REVISED'}))
        with mock.patch('ui_backend.importlib.import_module',side_effect=lambda name:imports if name=='module_import' else pipeline):
            out=RUN/'routes';m=module_fixture();spec=app.example()
            ui_backend.perform('module_import',{'source':'// Évolution 📐'},out)
            imports.import_csharp_module.assert_called_once_with('// Évolution 📐',output_dir=out)
            ui_backend.perform('module_run',{'module':m,'targets':['rust'],'preference':'rust'},out)
            pipeline.run_module.assert_called_once_with(m,targets=['rust'],preference='rust',output_root=out)
            ui_backend.perform('module_revise',{'module':m,'unit_id':'u0','spec':spec,'intent':'Changement volontaire'},out)
            pipeline.revise_module.assert_called_once_with(m,'u0',spec,'Changement volontaire',output_root=out)
    def test_module_timeouts_and_exact_unicode_intent(self):
        store=ui_backend.Store(RUN/'module-timeouts');response=mock.Mock(returncode=0,stdout='{"ok":true,"result":{"status":"INCONCLUSIVE"}}')
        for operation,seconds in [('module_import',120),('module_run',120),('module_revise',60)]:
            with mock.patch('ui_backend.subprocess.run',return_value=response) as process:
                out=store.command(operation,intent='Révision explicite 📐')
                self.assertEqual(process.call_args.kwargs['timeout'],seconds)
                self.assertEqual(out['request']['parameters']['intent'],'Révision explicite 📐')

class ModuleInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.store=ui_backend.Store(RUN/self._testMethodName);self.root=tk.Tk();self.root.withdraw();self.ui=app.ModuleWorkshop(self.root,self.store,auto_poll=False)
    def tearDown(self):
        if not self.ui.closed:self.ui.finish_close()
    def load(self,module=None):
        row=module_record(module=module);self.ui.apply_record(row);self.ui.load_import();return row
    def test_default_module_mode_no_fake_project_or_automatic_command(self):
        self.assertEqual(self.ui.mode,'module');self.assertIsNone(self.ui.module);self.assertIsNone(self.ui.job)
        self.assertIn('profil HTTP fixe',self.ui.summary.get('1.0','end'));self.assertTrue(self.ui.run_button.instate(['disabled']))
        self.assertIn('Max3',self.ui.pending_source)
    def test_site_launch_explicit_and_single_live_process(self):
        process=mock.Mock();process.poll.return_value=None
        with mock.patch('ui_backend.launch_site',return_value=process) as launch:
            self.assertIsNone(self.ui.site_process);launch.assert_not_called()
            self.ui.open_site();self.ui.open_site();launch.assert_called_once_with()
            self.assertIn('déjà actif',self.ui.message.get())
            process.poll.return_value=0;self.ui.open_site();self.assertEqual(launch.call_count,2)
    def test_module_run_history_uses_revision_specific_drafts(self):
        first=self.load();self.ui.put(self.ui.spec_text,'{"original draft":true}',editable=True)
        other=module_fixture();other.update(module_id='other',revision_id='2'*64)
        self.ui.apply_record(module_record('module_run',module=other));self.assertNotIn('original draft',self.ui.spec_text.get('1.0','end'))
        self.ui.apply_record(module_record('module_run',module=first['result']['module']));self.assertIn('original draft',self.ui.spec_text.get('1.0','end'))
    def test_long_source_name_has_compatible_property_name(self):
        m=module_fixture();m['units'][0]['name']='N'*100;self.load(m)
        self.assertEqual(self.ui.draft()['name'],'u0');self.assertEqual(self.ui.module['units'][0]['name'],'N'*100)
    def test_refused_history_labels_exact_other_module_context(self):
        self.load();other=module_fixture();other.update(name='Other archived module',module_id='other',revision_id='7'*64)
        row=module_record('module_revise',module=other,status='REFUSED');row['result']['module']=None;self.ui.apply_record(row)
        summary=self.ui.summary.get('1.0','end');self.assertIn('Other archived module',summary);self.assertIn('différent de cette entrée historique',summary)
        self.assertEqual(self.ui.module['module_id'],'fixture-module');self.assertEqual(json.loads(self.ui.raw.get('1.0','end'))['operation']['request']['parameters']['module'],other)
    def test_import_inventory_compact_summary_complete_raw(self):
        row=module_record(inventory=[{'text':'long source '*300,'kind':'ClassDeclarationSyntax'}]);self.ui.apply_record(row);self.ui.open_import()
        self.assertNotIn('long source',self.ui.import_fields['summary'].get('1.0','end'));self.assertIn('long source',self.ui.import_fields['raw'].get('1.0','end'))
    def test_corrupt_selected_archive_blocks_module_commands(self):
        row=self.load();self.ui.apply_history([dict(row,error='Archive changed')])
        self.assertTrue(self.ui.run_button.instate(['disabled']));self.assertTrue(self.ui.revise_button.instate(['disabled']))
        with mock.patch.object(self.ui,'dispatch') as dispatch:self.ui.run();self.ui.revise();self.ui.load_import();dispatch.assert_not_called()
        self.assertIn('NON VÉRIFIÉE',self.ui.summary.get('1.0','end'))
    def test_corruption_in_inactive_mode_invalidates_cached_results(self):
        row=self.load();self.ui.switch_mode('function');self.ui.apply_history([dict(row,error='Archive changed while inactive')]);self.ui.switch_mode('module')
        self.assertTrue(self.ui.run_button.instate(['disabled']));self.assertTrue(self.ui.revise_button.instate(['disabled']));self.assertIn('NON VÉRIFIÉE',self.ui.summary.get('1.0','end'))
    def test_revised_history_does_not_leak_or_erase_drafts(self):
        self.load();self.ui.select_unit('u1');self.ui.put(self.ui.spec_text,'{"foreign draft":true}',editable=True)
        other=module_fixture();other.update(module_id='other',revision_id='6'*64,parent_revision_id='5'*64)
        row=module_record('module_revise',module=other,status='REVISED');row['request']['parameters'].update(module=dict(other,revision_id='5'*64),unit_id='u0')
        self.ui.apply_record(row);self.ui.select_unit('u1');self.assertNotIn('foreign draft',self.ui.spec_text.get('1.0','end'))
        self.ui.select_unit('u0');self.ui.put(self.ui.spec_text,'{"current revision draft":true}',editable=True);self.ui.apply_record(row)
        self.assertIn('current revision draft',self.ui.spec_text.get('1.0','end'))
    def test_target_summary_counts_large_counterexample_lists(self):
        self.load();indices=list(range(512));row=module_record('module_run',status='INCONCLUSIVE',verification=[{'language':'rust','status':'COUNTEREXAMPLE','units':[{'unit_id':'u1','status':'COUNTEREXAMPLE','tests':{'planned':512,'executed':512,'contract_checked':512,'counterexample_indices':indices,'call_precondition_violation_indices':indices}}]}])
        self.ui.apply_record(row);self.ui.selected='rust';self.ui.render();summary=self.ui.summary.get('1.0','end')
        self.assertIn('contre-exemples 512',summary);self.assertNotIn('0, 1, 2, 3',summary);self.assertEqual(json.loads(self.ui.raw.get('1.0','end'))['operation']['result']['verification'][0]['units'][0]['tests']['counterexample_indices'],indices)
    def test_import_explicit_loading_unit_graph_and_calls(self):
        before=self.ui.module;row=module_record();self.ui.apply_record(row);self.assertIs(self.ui.module,before)
        self.ui.load_import();self.assertEqual(self.ui.unit_id,'u0');self.ui.select_unit('u1')
        self.assertIn('ExampleModule.Max2',self.ui.summary.get('1.0','end'))
        self.assertIn('node:u0',[tag for item in self.ui.canvas.find_all() for tag in self.ui.canvas.gettags(item)])
        self.assertEqual(self.ui.draft()['parameters'],row['result']['module']['units'][1]['parameters'])
    def test_unit_drafts_survive_switch_and_block_unapplied_generation(self):
        self.load();spec=self.ui.draft();spec['postcondition']=spec['postcondition'].replace('>=','<=')
        self.ui.put(self.ui.spec_text,app.pretty(spec),editable=True);self.ui.intent.set('Choisir le minimum')
        self.ui.select_unit('u1');self.ui.select_unit('u0');self.assertEqual(self.ui.draft(),spec);self.assertEqual(self.ui.intent.get(),'Choisir le minimum')
        with mock.patch.object(self.ui,'dispatch') as dispatch:self.ui.run();dispatch.assert_not_called()
        self.assertIn('brouillon',self.ui.message.get())
    def test_revision_requires_intent_and_preserves_input(self):
        self.load();before=deepcopy(self.ui.module)
        with mock.patch.object(self.ui,'dispatch') as dispatch:
            self.ui.revise();dispatch.assert_not_called();self.ui.intent.set('Remplacer volontairement Max2 par Min2');self.ui.revise()
            self.assertEqual(dispatch.call_args.args,('module_revise',));self.assertEqual(dispatch.call_args.kwargs['module'],before)
        self.assertEqual(self.ui.module,before)
    def test_revised_module_shows_parent_affected_and_contract_counterexample(self):
        self.load();old=deepcopy(self.ui.module);new=deepcopy(old);new.update(revision_id='1'*64,parent_revision_id=old['revision_id'],intent='Min2 volontaire')
        new['units'][0]['contract']['postcondition']=new['units'][0]['contract']['postcondition'].replace('>=','<=')
        result=module_record('module_revise',module=new,status='REVISED',change={'affected_dependents':['u1'],'equivalence_claimed':False})
        result['request']['parameters'].update(module=old,unit_id='u0',spec=self.ui.draft(),intent='Min2 volontaire')
        self.ui.apply_record(result);self.assertEqual(self.ui.module['parent_revision_id'],old['revision_id']);self.assertIn('u1',self.ui.summary.get('1.0','end'))
        run=module_record('module_run',module=new,status='INCONCLUSIVE',verification=[{'language':'python','status':'COUNTEREXAMPLE','compile_status':'NOT_APPLICABLE','execution_status':'EXECUTED','units':[{'unit_id':'u1','counterexamples':[1]}]}])
        self.ui.apply_record(run);self.assertIn('COUNTEREXAMPLE',self.ui.summary.get('1.0','end'));self.assertIn('non prouvé',self.ui.summary.get('1.0','end'))
        self.assertEqual(json.loads(self.ui.raw.get('1.0','end'))['operation'],run)
    def test_refused_revision_keeps_current_module_and_draft(self):
        self.load();before=deepcopy(self.ui.module);self.ui.intent.set('Essai refusé');self.ui.put(self.ui.spec_text,'{"draft":"kept"}',editable=True)
        row=module_record('module_revise',status='REFUSED');row['result']['module']=None
        self.ui.apply_record(row);self.assertEqual(self.ui.module,before);self.assertEqual(self.ui.spec_text.get('1.0','end-1c'),'{"draft":"kept"}')
    def test_modes_keep_separate_drafts_sources_and_no_auto_run(self):
        self.load();self.ui.put(self.ui.spec_text,'{"module draft":"à conserver"}',editable=True);self.ui.pending_source='// module draft'
        self.ui.switch_mode('function');self.assertIsNone(self.ui.job);self.ui.put(self.ui.spec_text,'{"function draft":"à conserver"}',editable=True);self.ui.pending_source='// function draft'
        self.ui.switch_mode('module');self.assertIn('module draft',self.ui.spec_text.get('1.0','end'));self.assertEqual(self.ui.pending_source,'// module draft')
        self.ui.switch_mode('function');self.assertIn('function draft',self.ui.spec_text.get('1.0','end'));self.assertEqual(self.ui.pending_source,'// function draft')
    def test_loading_other_module_keeps_previous_unsent_drafts_by_revision(self):
        first=self.load();self.ui.put(self.ui.spec_text,'{"unsent":"original module"}',editable=True)
        other=module_fixture();other.update(module_id='other',revision_id='2'*64);self.load(other)
        self.ui.apply_record(first);self.ui.load_import();self.assertEqual(self.ui.spec_text.get('1.0','end-1c'),'{"unsent":"original module"}')
    def test_module_import_source_close_and_refresh_preserve_unicode(self):
        self.ui.open_import();text='// Module non envoyé éà📐\n'+app.MODULE_SOURCE;self.ui.put(self.ui.import_fields['source'],text,editable=True)
        self.ui.show_import();self.assertEqual(self.ui.import_fields['source'].get('1.0','end-1c'),text);self.ui.close_import();self.ui.open_import()
        self.assertEqual(self.ui.import_fields['source'].get('1.0','end-1c'),text);self.ui.close_import()
    def test_module_preferences_restore_drafts_without_command_fixture_validator(self):
        self.load();module=deepcopy(self.ui.module);self.ui.put(self.ui.spec_text,'{"draft":"preserved"}',editable=True);self.ui.intent.set('Intention préservée');self.ui.finish_close()
        with mock.patch('module_model.validate_module',return_value=module):
            self.root=tk.Tk();self.root.withdraw();self.ui=app.ModuleWorkshop(self.root,self.store,auto_poll=False)
        self.assertIsNone(self.ui.job);self.assertEqual(self.ui.module,module);self.assertIn('preserved',self.ui.spec_text.get('1.0','end'));self.assertEqual(self.ui.intent.get(),'Intention préservée')

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__));result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'run':str(RUN),'scope':'Tk retiré; modes fonction/module; transport worker UTF8 et modules fixtures; lanceur site simulé, aucun navigateur ni serveur lancé; aucune synthèse ou preuve démontrée par fixtures.'}
    ui_backend.atomic_json(RUN/'results.json',report)
    print(json.dumps(report,ensure_ascii=False))
    raise SystemExit(not result.wasSuccessful())
