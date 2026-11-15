"""Pre-registered finite benchmark. Oracle is external to the planner's inputs."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import uuid

BASE = Path(__file__).resolve().parent
STRATEGIES = ('targeted', 'exhaustive', 'none')
POINTS = ((False, False), (False, True), (True, False), (True, True))
# Independent evaluator oracle. Do not import engine.predict: a shared bug could
# otherwise make a bad engine appear correct in the benchmark.
TABLES = {
    'false':'0000', 'true':'1111', 'and':'0001', 'or':'0111',
    'xor':'0110', 'xnor':'1001', 'nand':'1110', 'nor':'1000',
    'a':'0011', 'b':'0101', 'not_a':'1100', 'not_b':'1010',
    'a_implies_b':'1101', 'b_implies_a':'1011',
    'a_and_not_b':'0010', 'not_a_and_b':'0100',
}
SUPPORTED = 'ÉTAYÉE_DANS_UN_PÉRIMÈTRE'
KNOWLEDGE_STATES = {SUPPORTED, 'EN_RÉVISION', 'REMPLACÉE'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def save_new(path, value):
    """Exclusive creation prevents accidental replacement of previous evidence."""
    with Path(path).open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def truth(rule, a, b):
    if rule not in TABLES or type(a) is not bool or type(b) is not bool:
        raise ValueError('Unknown rule or non-Boolean oracle inputs')
    return TABLES[rule][2 * int(a) + int(b)] == '1'


def protocol():
    cases = [
        {'case_id':'stable_xor', 'world':'xor', 'change_to':'xor'},
        {'case_id':'stable_or', 'world':'or', 'change_to':'or'},
        {'case_id':'stable_and', 'world':'and', 'change_to':'and'},
        {'case_id':'xor_to_or', 'world':'xor', 'change_to':'or'},
        {'case_id':'and_to_or', 'world':'and', 'change_to':'or'},
        {'case_id':'or_to_xor', 'world':'or', 'change_to':'xor'},
    ]
    return {
        'schema_version':'mia-evaluation-1',
        'protocol_id':'MIA-EVAL-DET-001',
        'strategies':list(STRATEGIES),
        'cases':[dict(case, change_after=8, max_actions=24) for case in cases],
        'common_initial_inputs':[[int(a),int(b)] for a,b in POINTS],
        'max_step_calls_extra':4,
        'scope':'Six deterministic Boolean worlds; descriptive functional benchmark, not a general scientific validation.',
        'primary_measure':'Final supported rule accuracy on the common four-point domain at the full budget horizon; null if horizon not reached.',
        'secondary_measures':['reserved_actions','observations','prediction_accuracy','post_change_prediction_accuracy','diagnostic_latency_effects','false_alarms_stable','incorrect_revisions_at_creation','inconclusive_diagnostics'],
        'no_gain_assumption':True,
        'hidden_answer_policy':'Oracle answers are used only after an arm finishes and are never passed to Lab.step or to planner inputs.',
    }


def validate_protocol(spec):
    if spec.get('schema_version') != 'mia-evaluation-1':
        raise ValueError('Unknown protocol schema')
    if spec.get('strategies') != list(STRATEGIES):
        raise ValueError('All three declared strategies must be retained in fixed order')
    if spec.get('common_initial_inputs') != [[0,0],[0,1],[1,0],[1,1]]:
        raise ValueError('Common initial inputs differ from the declared control')
    cases=spec.get('cases', [])
    if not 1 <= len(cases) <= 32:
        raise ValueError('Case count must be bounded')
    ids=set()
    for case in cases:
        if not isinstance(case.get('case_id'),str) or not case['case_id'] or case['case_id'] in ids:
            raise ValueError('Missing or duplicate case id')
        ids.add(case['case_id'])
        if case.get('world') not in TABLES or case.get('change_to') not in TABLES:
            raise ValueError('Unknown Boolean world')
        if type(case.get('max_actions')) is not int or not 12 <= case['max_actions'] <= 64:
            raise ValueError('Budget must be in [12,64]')
        if type(case.get('change_after')) is not int or not 4 <= case['change_after'] < case['max_actions']:
            raise ValueError('Switch must follow the four common observations and precede horizon')
    if spec.get('max_step_calls_extra') != 4:
        raise ValueError('Bounded step-call allowance must be explicit')


def actual_rule(case, effect_number):
    return case['world'] if effect_number <= case['change_after'] else case['change_to']


def score(snapshot, case):
    """Pure scoring; never mutates a snapshot or feeds answers back to the Lab."""
    if snapshot.get('integrity') != 'VERIFIED':
        raise ValueError('An unverified snapshot cannot be scored')
    observations=snapshot['observations']
    budget=snapshot['budget']
    used=budget['used']
    if type(used) is not int or not len(observations) <= used <= case['max_actions'] or budget['limit'] != case['max_actions']:
        raise ValueError('Observation/reservation budget mismatch')
    ids={}; prefix=[]; wrong=[]; post=[]
    for index, item in enumerate(observations,1):
        if not isinstance(item.get('observation_id'),str) or item['observation_id'] in ids:
            raise ValueError('Missing or duplicate observation identity')
        ids[item['observation_id']]=index
        a,b=item['inputs']['a'],item['inputs']['b']
        if any(type(x) is not bool for x in (a,b,item['prediction'],item['observed'])):
            raise ValueError('Boolean observations and archived predictions required')
        if item['observed'] != truth(actual_rule(case,index),a,b):
            raise ValueError('Observed world differs from the independent deterministic oracle')
        correct=item['prediction']==item['observed']
        if not correct:wrong.append(item['observation_id'])
        if index<=4:prefix.append([int(a),int(b),item['observed']])
        if index>case['change_after']:post.append(correct)
    if len(observations)>=4 and [row[:2] for row in prefix] != [[0,0],[0,1],[1,0],[1,1]]:
        raise ValueError('Initial observations are not the pre-registered common sequence')
    knowledge=snapshot['knowledge']
    supported=[]; incorrect=[]
    for revision in knowledge:
        if revision.get('status') not in KNOWLEDGE_STATES or revision.get('rule') not in TABLES:
            raise ValueError('Unknown knowledge status or rule')
        anchor=revision.get('created_after_observation_id')
        if anchor not in ids:
            raise ValueError('Knowledge creation is not anchored to an observation')
        expected=actual_rule(case,ids[anchor])
        if TABLES[revision['rule']] != TABLES[expected]:
            incorrect.append(revision['revision_id'])
        if revision['status']==SUPPORTED:supported.append(revision)
    if len(supported)>1:
        raise ValueError('Several current supported rules are ambiguous for this single-domain benchmark')
    rule=supported[0]['rule'] if supported else None
    last_expected=actual_rule(case,max(1,len(observations)))
    final_accuracy=(sum(truth(rule,a,b)==truth(last_expected,a,b) for a,b in POINTS)/4) if rule else None
    reached=len(observations)==case['max_actions']
    diagnostics=snapshot['diagnostics']
    triggers=[]
    for diagnostic in diagnostics:
        trigger=diagnostic.get('trigger_observation_id')
        if trigger not in ids:
            raise ValueError('Diagnostic trigger is not a verified observation')
        if diagnostic.get('status') not in {'OPEN','CLOSED','INCONCLUSIVE'}:
            raise ValueError('Unknown diagnostic status')
        triggers.append(ids[trigger])
    changed=case['world']!=case['change_to']
    after_triggers=[i for i in triggers if i>case['change_after']]
    return {
        'reserved_actions':used, 'observations':len(observations),
        'prediction_accuracy':((len(observations)-len(wrong))/len(observations)) if observations else None,
        'post_change_prediction_accuracy':sum(post)/len(post) if changed and post else None,
        'wrong_prediction_ids':wrong, 'initial_observation_prefix':prefix,
        'diagnostics':len(diagnostics),
        'false_alarms_stable':len(triggers) if not changed else None,
        'diagnostic_latency_effects':min(after_triggers)-case['change_after'] if changed and after_triggers else None,
        'inconclusive_diagnostics':sum(d['status'] in {'OPEN','INCONCLUSIVE'} for d in diagnostics),
        'incorrect_revisions_at_creation':incorrect,
        'final_supported_rule':rule,
        'final_rule_accuracy_at_last_observed_effect':final_accuracy,
        'horizon_reached':reached,
        'common_horizon_rule_accuracy':final_accuracy if reached else None,
        'terminal_status':snapshot['status'],
    }


def summarize(records, spec):
    expected=len(spec['cases'])
    result={}
    for strategy in spec['strategies']:
        arms=[r for r in records if r['strategy']==strategy]
        valid=[r['metrics'] for r in arms if r.get('metrics') is not None]
        resolved=[m['common_horizon_rule_accuracy'] for m in valid if m['common_horizon_rule_accuracy'] is not None]
        result[strategy]={
            'assigned_arms':expected,'recorded_arms':len(arms),
            'failed_or_missing_arms':expected-len(valid),
            'common_horizon_scored_arms':len(resolved),
            'inconclusive_horizon_arms':expected-len(resolved),
            'perfect_common_horizon_arms':sum(x==1.0 for x in resolved),
            'reserved_actions_known':sum(m['reserved_actions'] for m in valid),
            'mean_common_horizon_accuracy_resolved_only':sum(resolved)/len(resolved) if resolved else None,
            'denominator_note':'The resolved-only mean excludes inconclusive arms; all assigned/failure counts remain explicit.',
        }
    paired=[]
    for case in spec['cases']:
        arms={r['strategy']:r for r in records if r['case_id']==case['case_id']}
        row={'case_id':case['case_id']}
        for baseline in ('exhaustive','none'):
            a=arms.get('targeted',{}).get('metrics') or {}
            b=arms.get(baseline,{}).get('metrics') or {}
            x,y=a.get('common_horizon_rule_accuracy'),b.get('common_horizon_rule_accuracy')
            row['targeted_minus_'+baseline]=x-y if x is not None and y is not None else None
        paired.append(row)
    return {'by_strategy':result,'paired_common_horizon_differences':paired,'claim':'Descriptive results on a fixed finite benchmark only. No generalized superiority or significance claim.'}


def run_campaign(output_root=None, spec=None, lab_factory=None):
    spec=deepcopy(spec if spec is not None else protocol())
    validate_protocol(spec)
    if lab_factory is None:
        from engine import Lab
        lab_factory=Lab
    parent=Path(output_root or BASE/'evidence')
    parent.mkdir(parents=True,exist_ok=True)
    folder=parent/('campaign-'+uuid.uuid4().hex)
    folder.mkdir(exist_ok=False)
    sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [BASE/'evaluation.py',BASE/'engine.py',BASE/'EVALUATION_PROTOCOL.txt'] if p.is_file()}
    registration={'registered_at':timestamp(),'protocol':spec,'protocol_sha256':fingerprint(spec),'source_sha256':sources}
    save_new(folder/'protocol.json',registration)
    registration_bytes=(folder/'protocol.json').read_bytes()
    records=[]
    for case in spec['cases']:
        for strategy in spec['strategies']:
            arm_name=case['case_id']+'--'+strategy
            # Case identifiers are data, never paths. Folder uses a bounded index.
            arm_folder=folder/('arm-'+str(len(records)+1).zfill(2))
            arm_folder.mkdir()
            record={'case_id':case['case_id'],'strategy':strategy,'assigned_budget':case['max_actions'],'metrics':None,'error':None,'snapshot_file':None}
            start=time.perf_counter();snapshot=None
            try:
                lab=lab_factory(arm_folder/'mission')
                mission=lab.create(arm_name,world=case['world'],change_to=case['change_to'],change_after=case['change_after'],max_actions=case['max_actions'],strategy=strategy)
                record['mission_id']=mission
                snapshot=lab.snapshot(mission)
                for _ in range(case['max_actions']+spec['max_step_calls_extra']):
                    if snapshot['budget']['used']>=case['max_actions']:
                        break
                    before=(snapshot['budget']['used'],len(snapshot['events']))
                    snapshot=lab.step(mission,count=1)
                    after=(snapshot['budget']['used'],len(snapshot['events']))
                    if after==before:
                        record['stopped_without_progress']=True
                        break
                record['metrics']=score(snapshot,case)
            except Exception as exc:
                record['error']=type(exc).__name__+': '+str(exc)
            finally:
                record['elapsed_seconds']=time.perf_counter()-start
                if snapshot is not None:
                    save_new(arm_folder/'snapshot.json',snapshot)
                    record['snapshot_file']=str((arm_folder/'snapshot.json').relative_to(folder))
                save_new(arm_folder/'result.json',record)
                records.append(record)
    if (folder/'protocol.json').read_bytes()!=registration_bytes:
        raise RuntimeError('Pre-registered protocol changed during the campaign; evidence retained, campaign invalid')
    source_end={name:hashlib.sha256((BASE/name).read_bytes()).hexdigest() for name in sources if (BASE/name).is_file()}
    source_unchanged=source_end==sources
    initial_equal={}
    for case in spec['cases']:
        trio=[r for r in records if r['case_id']==case['case_id']]
        prefixes=[r['metrics']['initial_observation_prefix'] for r in trio if r.get('metrics')]
        initial_equal[case['case_id']]=len(prefixes)==3 and all(len(p)==4 and p==prefixes[0] for p in prefixes)
    report={
        'schema_version':'mia-evaluation-report-1','completed_at':timestamp(),
        'protocol_sha256':fingerprint(spec),'protocol_unchanged':True,
        'sources_unchanged':source_unchanged,'source_sha256_at_end':source_end,
        'initial_observations_equal':initial_equal,'records':records,
        'summary':summarize(records,spec),
        'qualification':'COMPLETED' if source_unchanged and all(r['metrics'] is not None for r in records) and all(initial_equal.values()) else 'INCONCLUSIVE',
        'scope':spec['scope'],
    }
    save_new(folder/'report.json',report)
    return folder,report


def main():
    parser=argparse.ArgumentParser(description='Finite local MultiversIA evaluation; no model inference.')
    parser.add_argument('--output',type=Path,default=BASE/'evidence')
    args=parser.parse_args()
    folder,report=run_campaign(args.output)
    print(json.dumps({'folder':str(folder),'qualification':report['qualification'],'summary':report['summary']},ensure_ascii=False,indent=2))
    return 0 if report['qualification']=='COMPLETED' else 2


if __name__=='__main__':
    raise SystemExit(main())
