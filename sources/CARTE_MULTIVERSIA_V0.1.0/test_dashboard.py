"""End-to-end local tests; all effectful missions stay under evidence/."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
import uuid
import backend
import app

RUN = backend.BASE/'evidence'/('checks-'+uuid.uuid4().hex[:12])
RUN.mkdir(parents=True)

def hashes(folder):
    return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.store=backend.Store(RUN/self._testMethodName,legacy=RUN/'absent-legacy')
        self.key=self.store.create('Mission test')
    def test_real_run_wrong_prediction_and_export(self):
        a=self.store.command(self.key,'run',a=True,b=False,prediction=False)
        self.assertEqual(a['state'],'TERMINÉE');self.assertFalse(a['detail']['prediction_correct'])
        self.assertIn('Cause non déterminée',backend.diagnosis(a))
        result=json.loads(Path(self.store.export(self.key)).read_text(encoding='utf-8'))
        self.assertEqual(result['snapshot']['effect_count'],1)
    def test_real_process_crash_resume_no_duplicate(self):
        a=self.store.command(self.key,'run',a=True,b=False,prediction=True,crash='after_effect')
        self.assertTrue(a['crashed']);state=self.store.scan()[0]['snapshot'];self.assertEqual(state['effect_count'],1)
        b=self.store.command(self.key,'resume',action_id=a['action_id'])
        self.assertEqual(b['state'],'TERMINÉE')
        self.store.command(self.key,'resume',action_id=a['action_id'])
        self.assertEqual(self.store.scan()[0]['snapshot']['effect_count'],1)
    def test_no_receipt_stays_uncertain(self):
        a=self.store.command(self.key,'run',a=True,b=True,prediction=False,crash='after_dispatch')
        b=self.store.command(self.key,'resume',action_id=a['action_id'])
        self.assertEqual(b['state'],'RÉSULTAT_À_VÉRIFIER');self.assertEqual(self.store.scan()[0]['snapshot']['effect_count'],0)
    def test_readonly_reader_does_not_touch_source(self):
        self.store.command(self.key,'run',a=False,b=True,prediction=True)
        folder=Path(self.store.source(self.key)['data']);before=hashes(folder)
        for _ in range(3):backend.read_snapshot(folder)
        self.assertEqual(hashes(folder),before)
        observer=backend.Store(RUN/'observer',legacy=folder)
        with self.assertRaises(ValueError):observer.command('historique','run',a=True,b=True,prediction=False)
        observer.scan();self.assertEqual(hashes(folder),before)
    def test_corrupt_journal_is_not_success_or_repaired(self):
        folder=Path(self.store.source(self.key)['data'])
        (folder/'journal-head.json').write_text('{"seq":999,"sha256":"bad"}',encoding='utf-8');before=hashes(folder)
        s=self.store.scan()[0];self.assertIsNone(s['snapshot']);self.assertIn('non vérifiée',s['error'])
        with self.assertRaises(RuntimeError):self.store.command(self.key,'run',a=True,b=True,prediction=False)
        self.assertEqual(hashes(folder),before)
    def test_missing_source_not_reinitialized(self):
        folder=Path(self.store.source(self.key)['data']);(folder/'journal.sqlite3').rename(folder/'journal.saved')
        self.assertIsNotNone(self.store.scan()[0]['error']);self.assertFalse((folder/'journal.sqlite3').exists())
    def test_permissions_preserved(self):
        policy=Path(self.store.source(self.key)['data'])/'policy.json'
        value=json.loads(policy.read_text());value['allow_effect']=False;policy.write_text(json.dumps(value))
        a=self.store.command(self.key,'run',a=True,b=False,prediction=True)
        self.assertEqual(a['state'],'REFUSÉE');self.assertEqual(self.store.scan()[0]['snapshot']['effect_count'],0)
    def test_separate_missions_and_path_refusal(self):
        other=self.store.create('Autre');self.store.command(self.key,'run',a=True,b=False,prediction=True)
        self.assertEqual(next(s for s in self.store.scan() if s['id']==other)['snapshot']['effect_count'],0)
        with self.assertRaises(ValueError):self.store.command('../escape','run',a=True,b=True,prediction=False)


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.store=backend.Store(RUN/self._testMethodName,legacy=RUN/'absent')
        self.key=self.store.create('Mission interface')
        self.a=self.store.command(self.key,'run',a=True,b=False,prediction=False)
        self.root=tk.Tk();self.root.withdraw();self.ui=app.MapApp(self.root,self.store,auto_poll=False)
        self.ui.ingest(self.store.scan());self.root.update()
    def tearDown(self):
        if not self.ui.closed:self.ui.finish_close()
    def test_zoom_anchor_and_refresh_preserve_view(self):
        self.ui.ox=300;self.ui.oy=120;self.ui.zoom=.8
        before=((150-self.ui.ox)/self.ui.zoom,(100-self.ui.oy)/self.ui.zoom)
        self.ui.zoom_at(1.25,150,100)
        self.assertEqual(before,((150-self.ui.ox)/self.ui.zoom,(100-self.ui.oy)/self.ui.zoom))
        self.ui.select_node('action:'+self.a['action_id']);view=(self.ui.zoom,self.ui.ox,self.ui.oy,self.ui.action_id)
        self.ui.ingest(self.store.scan());self.assertEqual(view,(self.ui.zoom,self.ui.ox,self.ui.oy,self.ui.action_id))
    def test_action_details_raw_and_uncertainty(self):
        self.ui.select_node('action:'+self.a['action_id'])
        self.assertIn('Cause non déterminée',self.ui.summary.get('1.0','end'))
        self.assertIn('OBSERVATION_RECORDED',self.ui.events.get('1.0','end'))
        self.assertEqual(json.loads(self.ui.raw.get('1.0','end'))['action_id'],self.a['action_id'])
    def test_proof_zoom_uses_real_events_and_retains_full_event(self):
        self.ui.select_node('action:'+self.a['action_id']);self.ui.focus_proofs()
        events=[e for e in self.ui.state()['events'] if e['action_id']==self.a['action_id']]
        self.assertEqual(len([key for key in self.ui.items if key.startswith('proof:')]),len(events))
        self.ui.select_node('proof:'+str(events[-1]['seq']))
        self.assertEqual(json.loads(self.ui.raw.get('1.0','end')),events[-1])
        self.ui.zoom=.5;self.ui.draw()
        self.assertFalse(any(key.startswith('proof:') for key in self.ui.items))
        self.assertEqual(json.loads(self.ui.raw.get('1.0','end')),events[-1])
    def test_stale_read_disables_commands_keeps_old_snapshot(self):
        self.ui.select_node('action:'+self.a['action_id']);sources=self.store.scan()
        sources[0].update(snapshot=None,error='Source inaccessible');self.ui.ingest(sources)
        self.assertFalse(self.ui.can_write());self.assertIn('périmées',self.ui.summary.get('1.0','end'))
        self.assertEqual(self.ui.state()['effect_count'],1)
    def test_restart_view_preferences(self):
        self.ui.select_node('action:'+self.a['action_id']);self.ui.zoom=1.4;self.ui.ox=222;self.ui.oy=-90;self.ui.save_view()
        self.ui.finish_close();self.root=tk.Tk();self.root.withdraw();self.ui=app.MapApp(self.root,self.store,auto_poll=False)
        self.ui.ingest(self.store.scan())
        self.assertEqual(self.ui.action_id,self.a['action_id']);self.assertEqual((self.ui.zoom,self.ui.ox,self.ui.oy),(1.4,222,-90))
    def test_architecture_full_source_available(self):
        self.ui.architecture();win=self.ui.arch_window
        def texts(widget):
            for child in widget.winfo_children():
                if isinstance(child,tk.Text):yield child.get('1.0','end')
                yield from texts(child)
        raw=(backend.BASE.parent/'ARCHITECTURE_CHANTIER_2026-11-15/architecture.txt').read_text(encoding='utf-8')
        self.assertTrue(any(raw in t for t in texts(win)));win.destroy()
    def test_async_command_and_scan(self):
        self.ui.dispatch(self.store.command,self.key,'run',a=True,b=True,prediction=False)
        self.ui.tick();end=time.monotonic()+12
        while time.monotonic()<end and (self.ui.command_future or len(self.ui.state().get('actions',[]))<2):
            self.root.update();time.sleep(.03)
        self.assertEqual(len(self.ui.state()['actions']),2)
        self.assertEqual(self.ui.action()['state'],'TERMINÉE')


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'data':str(RUN),'scope':'Simulation locale et interface Tk hors écran ; aucune IA ou validation scientifique.'}
    backend.atomic(backend.BASE/'evidence/test-results.json',report)
    raise SystemExit(not result.wasSuccessful())
