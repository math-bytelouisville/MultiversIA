"""Tests of actual local persistence and finite symbolic learning."""
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch
import engine

BASE=Path(__file__).resolve().parent
RUN=BASE/'evidence'/('engine-tests-'+uuid.uuid4().hex[:12])

def hashes(folder):
    return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.lab=engine.Lab(RUN/self._testMethodName)
    def create(self,**kwargs):return self.lab.create('Test',**kwargs)
    def crash(self,key,point):
        result=subprocess.run([sys.executable,str(BASE/'engine.py'),'--root',str(self.lab.root),'step',key,'--crash',point],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,86)
    def test_initial_acquisition_all_four_inputs(self):
        for rule in engine.WORLDS:
            key=self.create(world=rule,change_to=rule);s=self.lab.step(key,4)
            self.assertEqual(s['knowledge'][-1]['rule'],rule)
            self.assertEqual([tuple(o['inputs'][k] for k in ('a','b')) for o in s['observations']],list(engine.INPUTS))
    def test_changed_rule_retested_and_revised(self):
        key=self.create(change_after=8);s=self.lab.step(key,24)
        self.assertEqual([k['rule'] for k in s['knowledge']],['xor','or'])
        self.assertEqual(s['knowledge'][0]['status'],'REMPLACÉE')
        d=s['diagnostics'][0];self.assertEqual(d['status'],'CLOSED')
        group=[o for o in s['observations'] if o['observation_id'] in d['observation_ids']]
        self.assertEqual(group[0]['inputs'],group[1]['inputs']);self.assertNotEqual(group[0]['action_id'],group[1]['action_id'])
        self.assertEqual(group[-1]['purpose'],'validation')
        self.assertEqual(s['knowledge'][-1]['created_after_observation_id'],group[-1]['observation_id'])
    def test_stable_world_no_false_diagnostic(self):
        key=self.create(world='and',change_to='and');s=self.lab.step(key,24)
        self.assertEqual(s['diagnostics'],[]);self.assertEqual(len(s['knowledge']),1)
    def test_none_retains_doubt_not_false_confirmation(self):
        key=self.create(change_after=8,strategy='none');s=self.lab.step(key,24)
        self.assertEqual(len(s['knowledge']),1);self.assertEqual(s['knowledge'][0]['status'],'EN_RÉVISION')
        self.assertTrue(all(d['status']=='INCONCLUSIVE' for d in s['diagnostics']))
    def test_strategies_same_initial_evidence(self):
        observations=[]
        for strategy in ('targeted','exhaustive','none'):
            key=self.create(change_after=8,strategy=strategy);s=self.lab.step(key,4)
            observations.append([(o['inputs'],o['prediction'],o['observed']) for o in s['observations']])
        self.assertEqual(observations[0],observations[1]);self.assertEqual(observations[1],observations[2])
    def test_exhaustive_has_four_references_and_validation(self):
        key=self.create(change_after=8,strategy='exhaustive');s=self.lab.step(key,24);d=s['diagnostics'][0]
        self.assertEqual(d['reference_count'],4);self.assertEqual(d['validation_count'],1);self.assertEqual(d['status'],'CLOSED')
    def test_budget_never_refilled_on_reopen(self):
        key=self.create(max_actions=5);s=self.lab.step(key,24);self.assertEqual(s['budget'],{'used':5,'limit':5})
        self.assertEqual(s['status'],'BUDGET_EXHAUSTED');before=hashes(self.lab.root)
        other=engine.Lab(self.lab.root);other.step(key,24);other.resume(key)
        self.assertEqual(s['effect_count'],5);self.assertEqual(hashes(self.lab.root),before)
    def test_crash_before_dispatch_resumes_once(self):
        key=self.create();self.crash(key,'after_prediction');s=self.lab.snapshot(key)
        self.assertEqual(s['effect_count'],0);self.assertEqual(s['pending_action']['state'],'PREPARED')
        s=self.lab.resume(key);self.assertEqual(s['effect_count'],1);self.assertEqual(len(s['observations']),1)
    def test_crash_after_dispatch_no_receipt_never_reexecutes(self):
        key=self.create();self.crash(key,'after_dispatch');s=self.lab.resume(key)
        self.assertEqual(s['status'],'RESULT_UNCERTAIN');self.assertEqual(s['effect_count'],0)
        for _ in range(3):s=self.lab.step(key,24)
        self.assertEqual(s['budget']['used'],1);self.assertEqual(s['effect_count'],0)
    def test_crash_after_effect_reconciles_without_duplicate(self):
        key=self.create();self.crash(key,'after_effect');s=self.lab.snapshot(key)
        self.assertEqual(s['effect_count'],1);self.assertEqual(len(s['observations']),0)
        s=self.lab.resume(key);self.assertEqual(s['effect_count'],1);self.assertEqual(len(s['observations']),1)
        self.lab.resume(key);self.assertEqual(self.lab.snapshot(key)['effect_count'],1)
    def test_crash_after_observation_science_is_atomic(self):
        key=self.create();self.lab.step(key,3);self.crash(key,'after_observation');s=self.lab.snapshot(key)
        self.assertEqual(len(s['observations']),4);self.assertEqual(len(s['knowledge']),1)
        self.lab.resume(key);self.assertEqual(self.lab.snapshot(key)['effect_count'],4)
    def test_readonly_snapshot_no_writes_or_repair(self):
        key=self.create();self.lab.step(key,4);before=hashes(self.lab.root)
        self.lab.snapshot(key);self.lab.missions();self.assertEqual(hashes(self.lab.root),before)
    def test_corrupt_event_refused_without_changes(self):
        key=self.create();self.lab.step(key,1);folder=self.lab.folder(key)
        with sqlite3.connect(folder/'journal.sqlite3') as db:db.execute("UPDATE events SET sha256='bad' WHERE seq=2")
        before=hashes(folder)
        with self.assertRaises(engine.IntegrityError):self.lab.snapshot(key)
        with self.assertRaises(engine.IntegrityError):self.lab.step(key)
        self.assertEqual(hashes(folder),before)
    def test_tail_truncation_detected_by_transactional_head(self):
        key=self.create();self.lab.step(key,1);folder=self.lab.folder(key)
        with sqlite3.connect(folder/'journal.sqlite3') as db:db.execute('DELETE FROM events WHERE seq=(SELECT max(seq) FROM events)')
        with self.assertRaises(engine.IntegrityError):self.lab.snapshot(key)
    def test_missing_receiver_not_recreated(self):
        key=self.create();folder=self.lab.folder(key);(folder/'receiver.sqlite3').rename(folder/'receiver.backup')
        with self.assertRaises(engine.IntegrityError):self.lab.snapshot(key)
        self.assertFalse((folder/'receiver.sqlite3').exists())
    def test_revoked_permission_before_effect_refused(self):
        key=self.create();self.crash(key,'after_prediction');folder=self.lab.folder(key)
        p=engine.strict_json((folder/'policy.json').read_text());p['allow_effect']=False;(folder/'policy.json').write_text(engine.canonical(p))
        s=self.lab.resume(key);self.assertEqual(s['status'],'REFUSED');self.assertEqual(s['effect_count'],0)
    def test_reconcile_allowed_after_revocation_without_effect(self):
        key=self.create();self.crash(key,'after_effect');folder=self.lab.folder(key)
        p=engine.strict_json((folder/'policy.json').read_text());p['allow_effect']=False;(folder/'policy.json').write_text(engine.canonical(p))
        s=self.lab.resume(key);self.assertEqual(s['effect_count'],1);self.assertEqual(len(s['observations']),1)
    def test_permission_revoked_after_dispatch_before_effect(self):
        key=self.create();folder=self.lab.folder(key);append=self.lab._append
        def revoke_on_dispatch(folder,entries):
            append(folder,entries)
            if entries[0][0]=='DISPATCH_RECORDED':
                p=engine.strict_json((folder/'policy.json').read_text());p['allow_effect']=False
                (folder/'policy.json').write_text(engine.canonical(p))
        with patch.object(self.lab,'_append',side_effect=revoke_on_dispatch):s=self.lab.step(key)
        self.assertEqual(s['status'],'REFUSED');self.assertEqual(s['effect_count'],0);self.assertEqual(s['observations'],[])
    def test_duplicate_json_keys_and_nonfinite_refused(self):
        for text in ('{"a":1,"a":2}','{"a":NaN}'):
            with self.assertRaises(engine.IntegrityError):engine.strict_json(text)
    def test_no_private_world_parameters_in_public_snapshot(self):
        key=self.create(world='and',change_to='nor',change_after=8);s=self.lab.snapshot(key)
        raw=engine.canonical(s)
        for name in ('"world"','"change_to"','"change_after"','"effect_index"'):self.assertNotIn(name,raw)
        import inspect
        self.assertEqual(list(inspect.signature(engine.plan).parameters),['observations','strategy'])
    def test_prediction_precedes_observation_and_matches(self):
        key=self.create();s=self.lab.step(key,5)
        for o in s['observations']:
            p=next(e for e in s['events'] if e['type']=='PREDICTION_COMMITTED' and e['data']['action_id']==o['action_id'])
            r=next(e for e in s['events'] if e['type']=='OBSERVATION_RECORDED' and e['data']['action_id']==o['action_id'])
            self.assertLess(p['seq'],r['seq']);self.assertIs(p['data']['prediction'],o['prediction'])
    def test_path_scope_and_type_limits(self):
        for key in ('../outside','M-../x','M-x'):
            with self.assertRaises(engine.Refused):self.lab.snapshot(key)
        for budget in (True,0,129):
            with self.assertRaises(engine.Refused):self.create(max_actions=budget)
        key=self.create()
        for count in (True,0,25):
            with self.assertRaises(engine.Refused):self.lab.step(key,count)
    def test_lock_blocks_parallel_mutation(self):
        key=self.create();folder=self.lab.folder(key)
        with engine.exclusive(folder):
            result=subprocess.run([sys.executable,str(BASE/'engine.py'),'--root',str(self.lab.root),'step',key],capture_output=True,timeout=15)
            self.assertEqual(result.returncode,2)
        self.assertEqual(self.lab.snapshot(key)['effect_count'],0)
    def test_export_is_complete_public_snapshot(self):
        key=self.create();s=self.lab.step(key,4);path=Path(self.lab.export(key));out=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(out['snapshot'],s)
    def test_unknown_event_type_rejected_even_rehashed(self):
        key=self.create();folder=self.lab.folder(key)
        with sqlite3.connect(folder/'journal.sqlite3') as db:
            raw=db.execute('SELECT payload FROM events WHERE seq=1').fetchone()[0];e=json.loads(raw);e['type']='UNKNOWN';h=engine.digest(e)
            db.execute('UPDATE events SET payload=?,sha256=?',(engine.canonical(e),h));db.execute('UPDATE meta SET value=? WHERE key="head"',(engine.canonical({'seq':1,'sha256':h}),))
        with self.assertRaises(engine.IntegrityError):self.lab.snapshot(key)

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(EngineTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),'data':str(RUN),'engine_sha256':hashlib.sha256((BASE/'engine.py').read_bytes()).hexdigest(),'scope':'Moteur fini local ; interruptions de processus réelles ; pas panne électrique ni preuve scientifique.'}
    (BASE/'evidence').mkdir(exist_ok=True)
    (BASE/'evidence/engine-test-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    raise SystemExit(not result.wasSuccessful())
