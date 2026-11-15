"""Desktop component tests: Tk withdrawn, real subprocess and simulated missions."""
from concurrent.futures import Future
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest import mock
import uuid
import app
import ui_backend
import engine

RUN=ui_backend.BASE/'evidence'/('ui-tests-'+uuid.uuid4().hex[:12])
RUN.mkdir(parents=True,exist_ok=False)

def hashes(path):
    return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}


class BackendTests(unittest.TestCase):
    def setUp(self):self.store=ui_backend.Store(RUN/self._testMethodName)
    def test_subprocess_create_step_scan_export(self):
        key=self.store.command('create',title='Test UI',world='xor',change_to='or',change_after=4,max_actions=16,strategy='targeted')['mission_id']
        result=self.store.command('step',mission_id=key)
        self.assertEqual(len(result['observations']),1)
        before=hashes(self.store.root);rows=self.store.scan();self.assertEqual(hashes(self.store.root),before)
        self.assertEqual(rows[0]['snapshot']['integrity'],'VERIFIED')
        exported=self.store.command('export',mission_id=key)
        self.assertTrue(Path(exported['export_path']).is_file())
    def test_subprocess_unicode_title_roundtrip(self):
        title='Hypothèse à vérifier — évolution 📐'
        key=self.store.command('create',title=title,world='xor',change_to='xor',change_after=4,max_actions=8,strategy='targeted')['mission_id']
        self.assertEqual(self.store.scan()[0]['snapshot']['title'],title)
        result=self.store.command('step',mission_id=key)
        self.assertEqual(result['title'],title)
        exported=self.store.command('export',mission_id=key)
        self.assertEqual(json.loads(Path(exported['export_path']).read_text(encoding='utf-8'))['snapshot']['title'],title)
    def test_commands_allowlist_and_no_shell(self):
        with self.assertRaises(ValueError):self.store.command('execute_code',source='anything')
        payload=mock.Mock(returncode=0,stdout='{"ok":true,"result":{"verdict":"INCONCLUSIF"}}')
        with mock.patch('ui_backend.subprocess.run',return_value=payload) as run:
            source='// Précondition à vérifier 📐\na;$(x)';contract='// Spécification française : résultat ≥ entrée'
            self.store.command('analyze',source=source,contract=contract)
            self.assertNotIn('shell',run.call_args.kwargs)
            self.assertEqual(run.call_args.kwargs['timeout'],240)
            self.assertEqual(run.call_args.kwargs['encoding'],'utf-8')
            self.assertEqual(json.loads(run.call_args.kwargs['input']),{'source':source,'contract':contract})
    def test_timeout_is_uncertain_not_retry(self):
        with mock.patch('ui_backend.subprocess.run',side_effect=subprocess.TimeoutExpired(['worker'],45)) as run:
            with self.assertRaisesRegex(RuntimeError,'incertain'):self.store.command('step',mission_id='test')
            self.assertEqual(run.call_count,1)
    def test_unknown_corrupt_mission_stays_visible(self):
        key=self.store.command('create',title='Read test',world='xor',change_to='xor',change_after=4,max_actions=8,strategy='targeted')['mission_id']
        lab=engine.Lab(self.store.root);snapshot=lab.snapshot(key)
        self.assertEqual(snapshot['mission_id'],key)
        with mock.patch.object(engine.Lab,'snapshot',side_effect=RuntimeError('corruption')):
            rows=self.store.scan();self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['snapshot']);self.assertIn('corruption',rows[0]['error'])


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.store=ui_backend.Store(RUN/self._testMethodName)
        self.lab=engine.Lab(self.store.root);self.key=self.lab.create('Interface test',world='xor',change_to='or',change_after=4,max_actions=24,strategy='targeted')
        self.lab.step(self.key,4)
        self.root=tk.Tk();self.root.withdraw();self.ui=app.LabApp(self.root,self.store,auto_poll=False);self.ui.ingest(self.store.scan())
    def tearDown(self):
        if not self.ui.closed:self.ui.finish_close()
    def spin(self,predicate,timeout=12):
        end=time.monotonic()+timeout
        while time.monotonic()<end and not predicate():self.root.update();time.sleep(.02)
        self.assertTrue(predicate())
    def test_zoom_anchor_and_refresh_keep_view(self):
        self.ui.zoom=.8;self.ui.ox=300;self.ui.oy=100
        world=((120-self.ui.ox)/self.ui.zoom,(80-self.ui.oy)/self.ui.zoom)
        self.ui.zoom_at(1.25,120,80)
        self.assertEqual(world,((120-self.ui.ox)/self.ui.zoom,(80-self.ui.oy)/self.ui.zoom))
        self.ui.select('knowledge');position=(self.ui.zoom,self.ui.ox,self.ui.oy,self.ui.selected)
        self.ui.ingest(self.store.scan());self.assertEqual(position,(self.ui.zoom,self.ui.ox,self.ui.oy,self.ui.selected))
    def test_real_objects_not_fabricated_and_full_json(self):
        self.ui.select('knowledge');self.assertEqual(json.loads(self.ui.raw.get('1.0','end')),self.lab.snapshot(self.key)['knowledge'])
        keys=[k for k in self.ui.objects if k.startswith('entry:knowledge:')];self.assertTrue(keys)
        value=self.ui.objects[keys[0]];self.ui.select(keys[0]);self.assertEqual(json.loads(self.ui.raw.get('1.0','end')),value)
        self.assertIn('PREDICTION_COMMITTED',self.ui.journal.get('1.0','end'))
    def test_selected_objects_have_readable_summary_and_complete_raw_proof(self):
        self.lab.step(self.key,4);self.ui.ingest(self.store.scan())
        def select_first(category):
            self.ui.select(category);key=next(iter(self.ui.objects));value=self.ui.objects[key];self.ui.select(key)
            self.assertEqual(json.loads(self.ui.raw.get('1.0','end')),value)
            summary=self.ui.summary.get('1.0','end')
            self.assertFalse(summary.lstrip().startswith('{'))
            return value,summary
        diagnostic,summary=select_first('diagnostics')
        self.assertIn('Diagnostic en cours',summary);self.assertIn('Tests de référence : 0',summary)
        self.assertIn('Prédiction : 0',summary);self.assertIn('Résultat observé : 1',summary)
        self.assertNotIn(diagnostic['trigger_observation_id'],summary)
        observation,summary=select_first('observations')
        self.assertIn('SURPRISE',summary);self.assertNotIn(observation['receipt_sha256'],summary)
        knowledge,summary=select_first('knowledge')
        self.assertIn('Remise en question',summary);self.assertIn('quatre entrées',summary)
        event,summary=select_first('events')
        self.assertIn(event['at'],summary);self.assertNotIn(event['sha256'],summary)
        self.lab.step(self.key,10);self.ui.ingest(self.store.scan())
        diagnostic,summary=select_first('diagnostics')
        self.assertIn('Diagnostic terminé',summary);self.assertIn('Validations prospectives : 1',summary)
        knowledge,summary=select_first('knowledge')
        self.assertIn('validation prospective',summary);self.assertEqual(knowledge['rule'],'or')
        self.assertIn(diagnostic['trigger_observation_id'],self.ui.journal.get('1.0','end'))
    def test_stale_reader_preserves_view_and_disables_mutations(self):
        before=self.ui.state();rows=self.store.scan();rows[0].update(snapshot=None,error='Erreur de source');self.ui.ingest(rows)
        self.assertEqual(self.ui.state(),before);self.assertFalse(self.ui.can_step());self.assertIn('PÉRIMÉES',self.ui.summary.get('1.0','end'))
    def test_older_scan_never_rolls_back_newer_snapshot(self):
        old=self.store.scan();new=self.lab.step(self.key);self.ui.cache[self.key]=new;self.ui.ingest(old)
        self.assertEqual(len(self.ui.state()['observations']),5)
        self.assertFalse(self.ui.verified());self.assertFalse(self.ui.can_step())
        self.assertIn('NON VÉRIFIÉES',self.ui.summary.get('1.0','end'))
        self.ui.ingest(self.store.scan());self.assertTrue(self.ui.verified())
    def test_selected_proof_stays_visible_outside_current_page(self):
        self.ui.select('events');key=list(self.ui.objects)[-1];value=self.ui.objects[key]
        self.ui.select(key);self.lab.step(self.key,4);self.ui.ingest(self.store.scan())
        self.assertNotIn(key,self.ui.objects)
        self.assertEqual(json.loads(self.ui.raw.get('1.0','end')),value)
    def test_scan_started_before_create_keeps_new_mission_selected(self):
        old=self.store.scan();created=self.lab.create('Nouvelle mission')
        self.ui.scan_future=Future();self.ui.job=Future();self.ui.job.set_result({'mission_id':created});self.ui.job_operation='create'
        self.ui.tick();self.assertEqual(self.ui.mission_id,created)
        self.assertEqual(self.ui.message.get(),'Mission créée ; ses preuves sont conservées.')
        self.ui.ingest(old);self.assertEqual(self.ui.mission_id,created);self.assertFalse(self.ui.verified())
        self.ui.ingest(self.store.scan());self.assertEqual(self.ui.mission_id,created);self.assertTrue(self.ui.verified())
        self.assertIsNone(self.ui.created_mission)
    def test_bounded_sequence_then_pause_has_no_extra_action(self):
        self.ui.count.set('3');self.ui.start_campaign();self.ui.pause();self.ui.tick()
        self.spin(lambda:self.ui.job is None and len(self.ui.state()['observations'])==5)
        self.assertEqual(self.lab.snapshot(self.key)['budget']['used'],5);self.assertEqual(self.ui.remaining,0)
    def test_real_two_step_sequence(self):
        self.ui.count.set('2');self.ui.start_campaign();self.ui.tick()
        self.spin(lambda:self.ui.job is None and self.ui.remaining==0 and len(self.ui.state()['observations'])==6)
        self.assertEqual(self.lab.snapshot(self.key)['budget']['used'],6)
    def test_restored_view_does_not_auto_restart_campaign(self):
        self.ui.section='knowledge';self.ui.selected='knowledge';self.ui.zoom=1.4;self.ui.ox=222;self.ui.oy=-90;self.ui.remaining=6
        self.ui.finish_close();self.root=tk.Tk();self.root.withdraw();self.ui=app.LabApp(self.root,self.store,auto_poll=False);self.ui.ingest(self.store.scan())
        self.assertEqual((self.ui.zoom,self.ui.ox,self.ui.oy),(1.4,222,-90));self.assertEqual(self.ui.remaining,0)
    def test_invalid_sequence_count_no_command(self):
        for value in ('0','25','text','-4'):
            self.ui.count.set(value);self.ui.start_campaign();self.assertIsNone(self.ui.job)
    def test_csharp_example_and_result_visible(self):
        self.ui.open_csharp();fields=self.ui.csharp_fields
        self.assertIn('public static bool Spec',fields['contract'].get('1.0','end'))
        self.ui.analysis_payload=(app.SOURCE_EXAMPLE,app.CONTRACT_EXAMPLE)
        result={'verdict':'INCONCLUSIF','reason':'Fixture explicite','mathematics':'f(a,b)=max(a,b)','limitations':['Test de présentation uniquement']}
        self.ui.analysis_done(result)
        self.assertIn('Fixture explicite',fields['summary'].get('1.0','end'));self.assertIn('f(a,b)',fields['maths'].get('1.0','end'))
        files=list((self.store.root/'analyses-csharp').glob('*.json'));self.assertEqual(len(files),1)
        self.assertEqual(json.loads(files[0].read_text(encoding='utf-8'))['result'],result)
        self.ui.csharp_window.destroy()
    def test_csharp_reopen_keeps_exact_inputs_and_new_edit_clears_result(self):
        self.ui.open_csharp();fields=self.ui.csharp_fields
        fields['source'].delete('1.0','end');fields['source'].insert('1.0','source fixture exacte')
        with mock.patch.object(self.ui,'dispatch',return_value=True):self.ui.analyze_csharp()
        self.ui.job_operation='analyze'
        self.assertEqual(str(fields['source']['state']),'disabled')
        self.ui.csharp_window.destroy();self.ui.open_csharp();fields=self.ui.csharp_fields
        self.assertEqual(fields['source'].get('1.0','end-1c'),'source fixture exacte')
        self.assertEqual(str(fields['source']['state']),'disabled')
        self.ui.job_operation=None
        self.ui.analysis_done({'verdict':'INCONCLUSIF','reason':'Fixture exacte','mathematics':'fixture maths'})
        self.ui.csharp_window.destroy();self.ui.open_csharp();fields=self.ui.csharp_fields
        self.assertIn('Fixture exacte',fields['summary'].get('1.0','end'))
        self.assertEqual(fields['source'].get('1.0','end-1c'),'source fixture exacte')
        self.ui.edit_csharp();self.assertEqual(str(fields['source']['state']),'normal')
        self.assertIsNone(self.ui.analysis_result)
        self.assertNotIn('fixture maths',fields['maths'].get('1.0','end'))
        self.ui.csharp_window.destroy()
    def test_csharp_completion_updates_main_status_even_without_window(self):
        self.ui.open_csharp();self.ui.analysis_payload=(app.SOURCE_EXAMPLE,app.CONTRACT_EXAMPLE)
        self.ui.csharp_window.destroy()
        for verdict in ('PROUVÉ_CONDITIONNEL','INCONCLUSIF'):
            self.ui.message.set('Commande en cours ; la carte reste consultable.')
            self.ui.analysis_done({'verdict':verdict,'reason':'Fixture de présentation, aucun certificat réel.'})
            self.assertEqual(self.ui.message.get(),'Analyse C# terminée : '+verdict+'.')
            self.assertNotIn('en cours',self.ui.message.get())


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'run':str(RUN),
            'scope':'Lectures/mutations locales réelles en simulation ; Tk hors écran ; présentation C# par fixture, pas preuve Lean.'}
    ui_backend.atomic_json(ui_backend.BASE/'evidence/ui-test-results.json',report)
    raise SystemExit(not result.wasSuccessful())
