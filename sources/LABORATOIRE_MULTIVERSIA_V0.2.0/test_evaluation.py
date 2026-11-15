"""Independent scorer fixtures plus bounded runner controls; no engine mocked as proof."""
from copy import deepcopy
from contextlib import contextmanager
import json
from pathlib import Path
import unittest
import uuid
import evaluation as ev


@contextmanager
def retained_test_folder():
    path=ev.BASE/'evidence'/('evaluation-fixture-'+uuid.uuid4().hex)
    path.mkdir(parents=True,exist_ok=False)
    yield path


def fixture_snapshot(case, n=12, rule='xor', status=ev.SUPPORTED):
    observations=[]
    for i in range(1,n+1):
        a,b=ev.POINTS[(i-1)%4]
        observed=ev.truth(ev.actual_rule(case,i),a,b)
        prediction=ev.truth('xor',a,b)
        observations.append({'observation_id':f'O-{i}','action_id':f'A-{i}',
                             'inputs':{'a':a,'b':b},'prediction':prediction,
                             'observed':observed,'purpose':'monitor','context_id':'fixture'})
    knowledge=[] if n<4 else [{'knowledge_id':'K-1','revision_id':'K-1-R1','rule':rule,'status':status,'created_after_observation_id':'O-4','evidence_ids':['O-1','O-2','O-3','O-4']}]
    return {'schema_version':'fixture-only','integrity':'VERIFIED','status':'BUDGET_EXHAUSTED' if n==case['max_actions'] else 'ACTIVE',
            'observations':observations,'events':[{'seq':i} for i in range(1,n+1)],
            'budget':{'used':n,'limit':case['max_actions']},'knowledge':knowledge,'diagnostics':[]}


class FixtureLab:
    """Fixtures test the evaluator runner, not the real planner implementation."""
    calls=[]
    fail_strategy=None
    def __init__(self,path):
        # Registration must exist before even constructing the experimental arm.
        registration=Path(path).parent.parent/'protocol.json'
        if not registration.is_file():raise AssertionError('Arm created before preregistration')
    def create(self,title,**kwargs):
        self.case={k:kwargs[k] for k in ('world','change_to','change_after','max_actions')}
        self.strategy=kwargs['strategy'];self.n=0
        self.calls.append(('create',self.strategy))
        if self.strategy==self.fail_strategy:raise RuntimeError('Deliberate fixture failure')
        return 'fixture-mission'
    def snapshot(self,_):return fixture_snapshot(self.case,self.n,rule=self.case['world'])
    def step(self,_,count=1):
        self.calls.append(('step',count));self.n+=1;return self.snapshot(_)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.case={'case_id':'fixture','world':'xor','change_to':'xor','change_after':8,'max_actions':12}
    def test_oracle_is_independent_and_truth_tables_complete(self):
        self.assertEqual(len(set(ev.TABLES.values())),16)
        for a,b in ev.POINTS:
            self.assertEqual(ev.truth('xor',a,b),a!=b)
            self.assertEqual(ev.truth('and',a,b),a and b)
            self.assertEqual(ev.truth('or',a,b),a or b)
    def test_archived_prediction_not_reported_flag_controls_accuracy(self):
        snap=fixture_snapshot(self.case)
        snap['observations'][0]['prediction']=not snap['observations'][0]['observed']
        snap['observations'][0]['prediction_correct']=True
        metrics=ev.score(snap,self.case)
        self.assertEqual(metrics['prediction_accuracy'],11/12)
        self.assertEqual(metrics['wrong_prediction_ids'],['O-1'])
    def test_input_snapshot_not_mutated(self):
        snap=fixture_snapshot(self.case);before=deepcopy(snap)
        ev.score(snap,self.case);self.assertEqual(snap,before)
    def test_wrong_revision_is_retained_and_common_domain_scored(self):
        metrics=ev.score(fixture_snapshot(self.case,rule='or'),self.case)
        self.assertEqual(metrics['incorrect_revisions_at_creation'],['K-1-R1'])
        self.assertEqual(metrics['common_horizon_rule_accuracy'],.75)
    def test_revised_unconfirmed_knowledge_is_inconclusive(self):
        metrics=ev.score(fixture_snapshot(self.case,status='EN_RÉVISION'),self.case)
        self.assertIsNone(metrics['final_supported_rule'])
        self.assertIsNone(metrics['common_horizon_rule_accuracy'])
    def test_early_stop_does_not_claim_common_horizon(self):
        case=dict(self.case,change_to='or')
        metrics=ev.score(fixture_snapshot(case,n=6),case)
        self.assertEqual(metrics['final_rule_accuracy_at_last_observed_effect'],1.0)
        self.assertIsNone(metrics['common_horizon_rule_accuracy'])
        self.assertFalse(metrics['horizon_reached'])
    def test_exact_change_boundary_and_diagnostic_latency(self):
        case=dict(self.case,change_to='or');snap=fixture_snapshot(case)
        snap['diagnostics']=[{'trigger_observation_id':'O-12','status':'CLOSED'}]
        metrics=ev.score(snap,case)
        self.assertEqual(metrics['post_change_prediction_accuracy'],.75)
        self.assertEqual(metrics['diagnostic_latency_effects'],4)
        self.assertEqual(metrics['incorrect_revisions_at_creation'],[])
    def test_stable_false_alarm_and_open_diagnostic_visible(self):
        snap=fixture_snapshot(self.case)
        snap['diagnostics']=[{'trigger_observation_id':'O-9','status':'OPEN'}]
        metrics=ev.score(snap,self.case)
        self.assertEqual(metrics['false_alarms_stable'],1)
        self.assertEqual(metrics['inconclusive_diagnostics'],1)
    def test_invalid_oracle_observation_and_reference_rejected(self):
        snap=fixture_snapshot(self.case);snap['observations'][1]['observed']=False
        with self.assertRaisesRegex(ValueError,'oracle'):ev.score(snap,self.case)
        snap=fixture_snapshot(self.case);snap['knowledge'][0]['created_after_observation_id']='invented'
        with self.assertRaisesRegex(ValueError,'anchored'):ev.score(snap,self.case)
    def test_common_prefix_and_unique_observation_ids_required(self):
        snap=fixture_snapshot(self.case);snap['observations'][0]['observation_id']='O-2'
        with self.assertRaisesRegex(ValueError,'duplicate'):ev.score(snap,self.case)
        snap=fixture_snapshot(self.case);snap['observations'][0],snap['observations'][1]=snap['observations'][1],snap['observations'][0]
        with self.assertRaisesRegex(ValueError,'common sequence'):ev.score(snap,self.case)
    def test_runner_preregisters_and_preserves_failure_denominator(self):
        spec=ev.protocol();spec['cases']=[self.case]
        with retained_test_folder() as tmp:
            FixtureLab.calls=[];FixtureLab.fail_strategy='none'
            try:folder,report=ev.run_campaign(tmp,spec,FixtureLab)
            finally:FixtureLab.fail_strategy=None
            self.assertEqual(report['qualification'],'INCONCLUSIVE')
            self.assertEqual(len(report['records']),3)
            none=report['summary']['by_strategy']['none']
            self.assertEqual(none['assigned_arms'],1)
            self.assertEqual(none['failed_or_missing_arms'],1)
            self.assertEqual(none['inconclusive_horizon_arms'],1)
            self.assertIsNone(none['mean_common_horizon_accuracy_resolved_only'])
            self.assertTrue((folder/'protocol.json').is_file())
            self.assertEqual(len(list(folder.glob('arm-*/result.json'))),3)
    def test_runner_repeated_campaigns_are_separate(self):
        spec=ev.protocol();spec['cases']=[self.case]
        with retained_test_folder() as tmp:
            first,a=ev.run_campaign(tmp,spec,FixtureLab)
            before=(first/'report.json').read_bytes()
            second,b=ev.run_campaign(tmp,spec,FixtureLab)
            self.assertNotEqual(first,second);self.assertEqual((first/'report.json').read_bytes(),before)
            self.assertEqual(a['qualification'],'COMPLETED');self.assertEqual(b['qualification'],'COMPLETED')
    def test_protocol_rejects_missing_control_arm_and_unbounded_budget(self):
        spec=ev.protocol();spec['strategies']=['targeted']
        with self.assertRaises(ValueError):ev.validate_protocol(spec)
        spec=ev.protocol();spec['cases'][0]['max_actions']=100000
        with self.assertRaises(ValueError):ev.validate_protocol(spec)


if __name__=='__main__':
    unittest.main(verbosity=2)
