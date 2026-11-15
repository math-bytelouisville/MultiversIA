"""Independent reviewer checks; not part of the preregistered benchmark."""
import json
from pathlib import Path
import unittest
import uuid
import engine
from evaluation import TABLES, POINTS, SUPPORTED, truth

BASE=Path(__file__).resolve().parent
RUN=BASE/'evidence'/('evaluation-engine-review-'+uuid.uuid4().hex)
RUN.mkdir(parents=True,exist_ok=False)


class ReviewerChecks(unittest.TestCase):
    def test_all_sixteen_stable_rules_real_journal(self):
        lab=engine.Lab(RUN/'stable-missions')
        for rule in TABLES:
            with self.subTest(rule=rule):
                key=lab.create('Independent stable '+rule,world=rule,change_to=rule,max_actions=5)
                state=lab.step(key,5)
                self.assertEqual(state['knowledge'][-1]['rule'],rule)
                self.assertEqual(state['knowledge'][-1]['status'],SUPPORTED)
                self.assertFalse(state['diagnostics'])
                for observation in state['observations']:
                    self.assertEqual(observation['observed'],truth(rule,**observation['inputs']))

    def test_all_catalogue_transitions_pure_planner(self):
        checked=0
        for old in TABLES:
            for new in TABLES:
                for strategy in ('targeted','exhaustive','none'):
                    with self.subTest(old=old,new=new,strategy=strategy):
                        observations=[]
                        for index in range(24):
                            action=engine.plan(observations,strategy)
                            actual=truth(old if index<8 else new,**action['inputs'])
                            observations.append(dict(action,observation_id='O-'+str(index+1),observed=actual))
                        state=engine.science(observations,strategy)
                        supported=[k for k in state['knowledge'] if k['status']==SUPPORTED]
                        if strategy!='none' or old==new:
                            self.assertEqual([k['rule'] for k in supported],[new])
                        else:
                            self.assertFalse(supported)
                        self.assertEqual([tuple(o['inputs'][k] for k in ('a','b')) for o in observations[:4]],list(POINTS))
                        checked+=1
        self.assertEqual(checked,768)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReviewerChecks))
    (RUN/'result.json').write_text(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'scope':'16 real stable journals and 768 pure-planner cases; deterministic catalogue only; supplementary, not preregistered benchmark.'},indent=2),encoding='utf-8')
    print(str(RUN))
    raise SystemExit(not result.wasSuccessful())
