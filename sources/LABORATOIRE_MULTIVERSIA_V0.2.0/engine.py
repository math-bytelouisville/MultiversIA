"""Finite symbolic laboratory. No model inference; no external effects.

Planner input is public observations only. Private world parameters are read
only by the simulated receiver. This API boundary is not an OS sandbox.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

SCHEMA = 'mia-lab-2'
GENESIS = '0' * 64
INPUTS = ((False,False),(False,True),(True,False),(True,True))
WORLDS = {
    'false':(0,0,0,0),'and':(0,0,0,1),'a_and_not_b':(0,0,1,0),'a':(0,0,1,1),
    'not_a_and_b':(0,1,0,0),'b':(0,1,0,1),'xor':(0,1,1,0),'or':(0,1,1,1),
    'nor':(1,0,0,0),'xnor':(1,0,0,1),'not_b':(1,0,1,0),'b_implies_a':(1,0,1,1),
    'not_a':(1,1,0,0),'a_implies_b':(1,1,0,1),'nand':(1,1,1,0),'true':(1,1,1,1),
}
SCOPE = 'Simulation booléenne déterministe, catalogue fini de 16 fonctions ; séparation API, pas secret protégé ni diagnostic physique.'
EVENT_TYPES = {'MISSION_CREATED','PREDICTION_COMMITTED','DISPATCH_RECORDED','RESULT_UNCERTAIN',
               'OBSERVATION_RECORDED','ACTION_REFUSED','SCIENCE_UPDATED','BUDGET_EXHAUSTED'}

class IntegrityError(RuntimeError): pass
class Refused(RuntimeError): pass

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value): return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
def strict_json(text):
    def pairs(values):
        result={}
        for k,v in values:
            if k in result:raise IntegrityError('Clé dupliquée : '+k)
            result[k]=v
        return result
    def constant(value):raise IntegrityError('Nombre JSON non fini : '+value)
    return json.loads(text,object_pairs_hook=pairs,parse_constant=constant)
def predict(rule,a,b):
    if rule not in WORLDS or type(a) is not bool or type(b) is not bool:
        raise ValueError('Règle ou entrée booléenne invalide.')
    return bool(WORLDS[rule][int(a)*2+int(b)])

@contextmanager
def database(path,write=False,create=False):
    path=Path(path).resolve()
    if not create and not path.is_file():raise IntegrityError('Base absente : '+path.name)
    connection=sqlite3.connect(path.as_uri()+('?mode=rwc' if create else '?mode=rw' if write else '?mode=ro'),uri=True,timeout=1)
    try:
        if write or create:connection.execute('PRAGMA synchronous=FULL')
        else:connection.execute('PRAGMA query_only=ON')
        yield connection
    finally:connection.close()

@contextmanager
def exclusive(folder):
    # Used only by mutations. Reads never create a lock or a directory.
    with (folder/'controller.lock').open('a+b') as stream:
        stream.seek(0,2)
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:raise Refused('Une mutation utilise déjà cette mission.') from exc
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

def compatible(observations):
    return [rule for rule in sorted(WORLDS) if all(predict(rule,o['inputs']['a'],o['inputs']['b'])==o['observed'] for o in observations)]

def science(observations,strategy):
    """Replay learning solely from ordered, public observations.

    Replaying this pure function makes revision and invalidation atomic in the
    view. The stored SCIENCE_UPDATED events must match its output exactly.
    """
    knowledge=[];diagnostics=[];candidates=sorted(WORLDS);current=None;active=None
    phase='EXPLORATION';last_rule=None;monitor_index=0
    for index,o in enumerate(observations):
        if index<4:
            candidates=compatible(observations[:index+1])
            if index==3 and len(candidates)==1:
                last_rule=candidates[0]
                current={'knowledge_id':'K-001','revision_id':'K-001-R1','rule':last_rule,
                         'status':'ÉTAYÉE_DANS_UN_PÉRIMÈTRE','evidence_ids':[x['observation_id'] for x in observations[:4]],
                         'created_after_observation_id':o['observation_id'],'scope':'Les quatre entrées, sous hypothèse de stabilité du contexte et du capteur.',
                         'supersedes':None}
                knowledge.append(current);phase='MONITOR'
            continue
        if active is None:
            monitor_index+=1
            if last_rule is not None and o['observed']!=predict(last_rule,o['inputs']['a'],o['inputs']['b']):
                current['status']='EN_RÉVISION'
                active={'diagnostic_id':'D-'+str(len(diagnostics)+1).zfill(3),'trigger_observation_id':o['observation_id'],
                        'status':'OPEN','hypotheses':compatible([o]),'observation_ids':[o['observation_id']],
                        'stage':'repeat','old_revision':current['revision_id'],'conclusion':'Écart observé ; origine physique non déterminée.',
                        'reference_count':0,'validation_count':0}
                diagnostics.append(active);phase='DIAGNOSTIC';candidates=active['hypotheses']
                if strategy=='none':
                    active.update(status='INCONCLUSIVE',conclusion='Stratégie sans revalidation : ancienne règle non confirmée.')
                    active=None;phase='MONITOR'
            continue
        active['observation_ids'].append(o['observation_id'])
        group=[x for x in observations[:index+1] if x['observation_id'] in active['observation_ids']]
        candidates=compatible(group);active['hypotheses']=candidates
        if not candidates:
            active.update(status='INCONCLUSIVE',conclusion='Observations incompatibles avec une règle stable du catalogue.')
            active=None;phase='INCONCLUSIVE';last_rule=None;continue
        if o['purpose']=='validation':
            active['validation_count']+=1
            last_rule=candidates[0]
            old=current['revision_id'];current['status']='REMPLACÉE'
            revision='K-001-R'+str(len(knowledge)+1)
            current={'knowledge_id':'K-001','revision_id':revision,'rule':last_rule,'status':'ÉTAYÉE_DANS_UN_PÉRIMÈTRE',
                     'evidence_ids':list(active['observation_ids']),'created_after_observation_id':o['observation_id'],
                     'scope':'Catalogue fini ; observations et validation prospective dans le contexte observé. Cause physique non localisée.',
                     'supersedes':old}
            knowledge.append(current);active.update(status='CLOSED',stage='closed',conclusion='Règle compatible étayée après validation prospective ; cause physique indéterminée.',new_revision=revision)
            active=None;phase='MONITOR';monitor_index=0
        elif active['stage']=='repeat':
            active['stage']='references';phase='DIAGNOSTIC'
        else:
            active['reference_count']+=1
            if len(candidates)==1 and (strategy=='targeted' or active['reference_count']>=4):
                active['stage']='validate';phase='VALIDATION'
    return {'knowledge':knowledge,'diagnostics':diagnostics,'candidates':candidates,'phase':phase,
            'last_rule':last_rule,'monitor_index':monitor_index}

def plan(observations,strategy):
    """The planner has no receiver/world argument and no filesystem access."""
    s=science(observations,strategy);reason='';rule=s['last_rule'];purpose='monitor'
    if len(observations)<4:
        a,b=INPUTS[len(observations)];purpose='exploration';reason='Référence initiale commune aux trois stratégies.'
    elif s['phase']=='INCONCLUSIVE':
        raise Refused('Diagnostic inconclusif : aucune nouvelle action automatique.')
    elif s['diagnostics'] and s['diagnostics'][-1]['status']=='OPEN':
        d=s['diagnostics'][-1];group=[o for o in observations if o['observation_id'] in d['observation_ids']]
        if d['stage']=='repeat':
            a,b=group[0]['inputs']['a'],group[0]['inputs']['b'];purpose='diagnostic';reason='Reproduire l’écart avec une nouvelle identité d’action.'
        elif d['stage']=='validate':
            a,b=INPUTS[(INPUTS.index((group[-1]['inputs']['a'],group[-1]['inputs']['b']))+1)%4]
            purpose='validation';reason='Validation prospective distincte des observations de construction.'
        elif strategy=='exhaustive':
            a,b=INPUTS[d['reference_count']%4];purpose='diagnostic';reason='Rejouer les quatre références dans un ordre fixé.'
        else:
            candidates=s['candidates']
            def score(pair):
                ones=sum(predict(h,*pair) for h in candidates)
                return ones*(len(candidates)-ones)
            a,b=max(INPUTS,key=score);purpose='diagnostic';reason='Séparer le plus de paires d’hypothèses compatibles ; coût uniforme.'
        rule=s['candidates'][0] if len(s['candidates'])==1 else None
    else:
        a,b=INPUTS[s['monitor_index']%4];reason='Surveillance prospective cyclique des quatre références.'
    candidates=s['candidates'] or sorted(WORLDS)
    prediction=predict(rule,a,b) if rule else sum(predict(h,a,b) for h in candidates)>len(candidates)/2
    return {'inputs':{'a':a,'b':b},'prediction':prediction,'purpose':purpose,'hypothesis':rule,
            'reason':reason,'candidate_rules':candidates,'context_id':'lamp-public-context-v1'}

class Lab:
    def __init__(self,root):self.root=Path(root).resolve()
    def folder(self,mission_id):
        if not isinstance(mission_id,str) or not re.fullmatch(r'M-[a-f0-9]{32}',mission_id):raise Refused('Identifiant de mission invalide.')
        path=(self.root/mission_id).resolve()
        if not path.is_relative_to(self.root) or path.is_symlink():raise Refused('Chemin de mission invalide.')
        if not path.is_dir():raise Refused('Mission absente.')
        return path
    def _append(self,folder,entries):
        with database(folder/'journal.sqlite3',write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            head=strict_json(db.execute('SELECT value FROM meta WHERE key="head"').fetchone()[0])
            for kind,data in entries:
                event={'schema':SCHEMA,'seq':head['seq']+1,'at':now(),'type':kind,'data':data,'previous_sha256':head['sha256']}
                hashed=digest(event);db.execute('INSERT INTO events VALUES(?,?,?)',(event['seq'],canonical(event),hashed))
                head={'seq':event['seq'],'sha256':hashed}
            db.execute('UPDATE meta SET value=? WHERE key="head"',(canonical(head),));db.commit()
    def create(self,title,world='xor',change_to='or',change_after=4,max_actions=24,strategy='targeted'):
        if not isinstance(title,str) or not 1<=len(title.strip())<=160:raise Refused('Titre requis, 160 caractères maximum.')
        if world not in WORLDS or change_to not in WORLDS:raise Refused('Monde hors catalogue.')
        if type(change_after) is not int or not 0<=change_after<=128:raise Refused('Seuil de changement invalide.')
        if type(max_actions) is not int or not 4<=max_actions<=128:raise Refused('Budget entre 4 et 128 actions.')
        if strategy not in {'targeted','exhaustive','none'}:raise Refused('Stratégie inconnue.')
        self.root.mkdir(parents=True,exist_ok=True);key='M-'+uuid.uuid4().hex;folder=self.root/key;folder.mkdir()
        with exclusive(folder):
            with database(folder/'journal.sqlite3',create=True) as db:
                db.executescript('CREATE TABLE events(seq INTEGER PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL);CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);')
                db.execute('INSERT INTO meta VALUES(?,?)',('head',canonical({'seq':0,'sha256':GENESIS})));db.commit()
            with database(folder/'receiver.sqlite3',create=True) as db:
                db.executescript('CREATE TABLE config(payload TEXT NOT NULL,sha256 TEXT NOT NULL);CREATE TABLE receipts(action_id TEXT PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL);')
                private={'world':world,'change_to':change_to,'change_after':change_after}
                db.execute('INSERT INTO config VALUES(?,?)',(canonical(private),digest(private)));db.commit()
            policy={'allow_effect':True,'max_actions':max_actions,'scope':'local-boolean-simulation'}
            (folder/'policy.json').write_text(canonical(policy),encoding='utf-8')
            self._append(folder,[('MISSION_CREATED',{'mission_id':key,'title':title.strip(),'max_actions':max_actions,'strategy':strategy,'scope':SCOPE})])
        return key
    def _events(self,folder):
        with database(folder/'journal.sqlite3') as db:
            db.execute('BEGIN')
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise IntegrityError('Journal SQLite invalide.')
            rows=db.execute('SELECT seq,payload,sha256 FROM events ORDER BY seq').fetchall()
            heads=db.execute('SELECT value FROM meta WHERE key="head"').fetchall()
            if len(heads)!=1:raise IntegrityError('Tête du journal absente.')
            head=strict_json(heads[0][0])
        previous=GENESIS;events=[]
        for expected,(seq,raw,hashed) in enumerate(rows,1):
            e=strict_json(raw)
            if set(e)!={'schema','seq','at','type','data','previous_sha256'} or e['schema']!=SCHEMA or e['type'] not in EVENT_TYPES:
                raise IntegrityError('Événement hors schéma.')
            if seq!=expected or e['seq']!=seq or e['previous_sha256']!=previous or digest(e)!=hashed or canonical(e)!=raw:raise IntegrityError('Chaîne de preuve incohérente.')
            events.append(dict(e,sha256=hashed));previous=hashed
        if head!={'seq':len(events),'sha256':previous} or not events:raise IntegrityError('Tête incohérente ou journal vide.')
        return events
    def _receipts(self,folder):
        with database(folder/'receiver.sqlite3') as db:
            db.execute('BEGIN')
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise IntegrityError('Registre des reçus invalide.')
            configs=db.execute('SELECT payload,sha256 FROM config').fetchall()
            if len(configs)!=1:raise IntegrityError('Configuration privée absente.')
            raw,h=configs[0];config=strict_json(raw)
            if digest(config)!=h or set(config)!={'world','change_to','change_after'} or config['world'] not in WORLDS or config['change_to'] not in WORLDS or type(config['change_after']) is not int or not 0<=config['change_after']<=128:raise IntegrityError('Configuration privée invalide.')
            rows=db.execute('SELECT action_id,payload,sha256 FROM receipts ORDER BY rowid').fetchall()
        result={}
        for expected,(key,raw,hashed) in enumerate(rows,1):
            receipt=strict_json(raw)
            if set(receipt)!={'action_id','request_sha256','inputs','observed','effect_index'} or digest(receipt)!=hashed or canonical(receipt)!=raw or receipt['action_id']!=key or receipt['effect_index']!=expected or type(receipt['observed']) is not bool:raise IntegrityError('Reçu invalide.')
            result[key]=receipt
        return result
    def _project(self,events,receipts):
        first=events[0]
        if first['type']!='MISSION_CREATED':raise IntegrityError('Création de mission absente.')
        config=first['data']
        if (set(config)!={'mission_id','title','max_actions','strategy','scope'} or config['strategy'] not in {'targeted','exhaustive','none'}
            or type(config['max_actions']) is not int or not 4<=config['max_actions']<=128 or not isinstance(config['title'],str) or not 1<=len(config['title'])<=160):raise IntegrityError('Contrat mission invalide.')
        actions={};observations=[];pending=None;status='ACTIVE';stored_science=None
        for e in events[1:]:
            kind,d=e['type'],e['data']
            if kind=='PREDICTION_COMMITTED':
                if pending or status!='ACTIVE' or d['action_id'] in actions:raise IntegrityError('Action superposée ou dupliquée.')
                expected=plan(observations,config['strategy'])
                if set(d)!=set(expected)|{'action_id','operation_key'} or canonical({k:d[k] for k in expected})!=canonical(expected):raise IntegrityError('Plan ou prédiction non conforme aux observations antérieures.')
                if not isinstance(d['action_id'],str) or not re.fullmatch('A-[a-f0-9]{32}',d['action_id']) or not isinstance(d['operation_key'],str) or not re.fullmatch('OP-[a-f0-9]{32}',d['operation_key']):raise IntegrityError('Identité action invalide.')
                if any(a['operation_key']==d['operation_key'] for a in actions.values()):raise IntegrityError('Identité opération dupliquée.')
                pending=dict(d,state='PREPARED');actions[d['action_id']]=pending
            elif kind=='DISPATCH_RECORDED':
                if not pending or pending['state']!='PREPARED' or d!={'action_id':pending['action_id'],'request_sha256':digest({k:v for k,v in pending.items() if k!='state'})}:raise IntegrityError('Envoi sans intention correspondante.')
                pending['state']='DISPATCHED'
            elif kind=='RESULT_UNCERTAIN':
                if not pending or pending['state'] not in {'DISPATCHED','RESULT_UNCERTAIN'} or d['action_id']!=pending['action_id']:raise IntegrityError('Incertitude sans envoi.')
                pending['state']='RESULT_UNCERTAIN';status='RESULT_UNCERTAIN'
            elif kind=='OBSERVATION_RECORDED':
                if not pending or pending['state'] not in {'DISPATCHED','RESULT_UNCERTAIN'}:raise IntegrityError('Observation sans action envoyée.')
                receipt=receipts.get(pending['action_id'])
                expected=self._observation(pending,receipt) if receipt else None
                if canonical(d)!=canonical(expected):raise IntegrityError('Observation non soutenue par son reçu.')
                observations.append(d);pending['state']='COMPLETED';pending=None;status='ACTIVE'
            elif kind=='SCIENCE_UPDATED':
                expected=science(observations,config['strategy'])
                if canonical(d)!=canonical(expected):raise IntegrityError('Révision de connaissance non soutenue par les observations.')
                stored_science=d
            elif kind=='ACTION_REFUSED':
                if not pending or pending['state'] not in {'PREPARED','DISPATCHED'} or d['action_id']!=pending['action_id'] or d.get('before_effect') is not True:raise IntegrityError('Refus sans contrôle avant effet.')
                pending['state']='REFUSED';pending=None;status='REFUSED'
            elif kind=='BUDGET_EXHAUSTED':
                if pending or len(actions)<config['max_actions'] or d!={'used':len(actions),'limit':config['max_actions']}:raise IntegrityError('Arrêt budget injustifié.')
                status='BUDGET_EXHAUSTED'
            else:raise IntegrityError('Événement à cette position non autorisé.')
        s=science(observations,config['strategy'])
        if observations and stored_science!=s:raise IntegrityError('Révision atomique manquante après observation.')
        for key,receipt in receipts.items():
            if key not in actions:raise IntegrityError('Reçu sans action connue.')
            action=actions[key];request={k:v for k,v in action.items() if k!='state'}
            if action['state'] not in {'DISPATCHED','RESULT_UNCERTAIN','COMPLETED'} or receipt['request_sha256']!=digest(request) or receipt['inputs']!=action['inputs']:raise IntegrityError('Effet non lié à une action autorisée.')
        if len(actions)>config['max_actions']:raise IntegrityError('Budget global dépassé.')
        if s['phase']=='INCONCLUSIVE' and status=='ACTIVE':status='INCONCLUSIVE'
        diagnostics=deepcopy(s['diagnostics'])
        if status=='BUDGET_EXHAUSTED':
            for d in diagnostics:
                if d['status']=='OPEN':d.update(status='INCONCLUSIVE',conclusion='Budget global épuisé avant conclusion.')
        public={'schema_version':SCHEMA,'mission_id':config['mission_id'],'title':config['title'],'status':status,
                'phase':s['phase'],'budget':{'used':len(actions),'limit':config['max_actions']},'events':events,
                'observations':observations,'hypotheses':[{'hypothesis_id':'H-'+r,'rule':r,'status':'COMPATIBLE'} for r in s['candidates']],
                'knowledge':s['knowledge'],'diagnostics':diagnostics,'pending_action':pending,
                'summary':f"{len(observations)} observation(s), {len(s['knowledge'])} révision(s), phase {s['phase']}. {status}.",
                'integrity':'VERIFIED','scope':SCOPE,'strategy':config['strategy'],'effect_count':len(receipts),'actions':list(actions.values())}
        return public
    def snapshot(self,mission_id):
        folder=self.folder(mission_id);last=None
        for _ in range(3):
            try:
                events=self._events(folder);receipts=self._receipts(folder)
                if events[-1]['sha256']!=self._events(folder)[-1]['sha256']:continue
                result=self._project(events,receipts)
                if result['mission_id']!=mission_id:raise IntegrityError('Mission non liée à son dossier.')
                return result
            except (sqlite3.Error,ValueError,KeyError,TypeError,IntegrityError) as exc:last=exc
        raise IntegrityError('Lecture non vérifiée : '+str(last or 'mission modifiée pendant lecture'))
    def missions(self):
        result=[]
        if not self.root.exists():return result
        for folder in sorted(self.root.glob('M-*')):
            try:
                s=self.snapshot(folder.name);result.append({'mission_id':folder.name,'title':s['title'],'status':s['status']})
            except Exception as exc:result.append({'mission_id':folder.name,'title':folder.name,'status':'UNVERIFIED','error':str(exc)})
        return result
    def _gate(self,folder,s):
        try:
            p=strict_json((folder/'policy.json').read_text(encoding='utf-8'))
            if set(p)!={'allow_effect','max_actions','scope'} or p['allow_effect'] is not True or type(p['max_actions']) is not int or not 1<=p['max_actions']<=128 or p['scope']!='local-boolean-simulation':return 'Politique invalide ou permission refusée.'
            if s['budget']['used']>min(p['max_actions'],s['budget']['limit']):return 'Budget de politique atteint.'
            if sum(f.stat().st_size for f in folder.iterdir() if f.is_file())>30_000_000:return 'Stockage de mission supérieur à 30 Mo.'
            if shutil.disk_usage(folder).free<64_000_000:return 'Espace disque disponible insuffisant.'
            return None
        except (OSError,ValueError,TypeError):return 'Politique absente ou illisible.'
    @staticmethod
    def _crash(name,point):
        if point==name:os._exit(86)
    @staticmethod
    def _observation(action,receipt):
        return {'observation_id':'OBS-'+action['action_id'],'action_id':action['action_id'],'inputs':action['inputs'],
                'prediction':action['prediction'],'observed':receipt['observed'],'prediction_correct':action['prediction']==receipt['observed'],
                'purpose':action['purpose'],'context_id':action['context_id'],'receipt_sha256':digest(receipt),'quality':'ADMISSIBLE_SIMULATION'}
    def _effect(self,folder,action):
        request={k:v for k,v in action.items() if k!='state'}
        with database(folder/'receiver.sqlite3',write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT payload FROM receipts WHERE action_id=?',(action['action_id'],)).fetchone()
            if old:return strict_json(old[0])
            raw,hashed=db.execute('SELECT payload,sha256 FROM config').fetchone();private=strict_json(raw)
            if digest(private)!=hashed:raise IntegrityError('Configuration privée modifiée.')
            effect_index=db.execute('SELECT count(*) FROM receipts').fetchone()[0]+1
            rule=private['world'] if effect_index<=private['change_after'] else private['change_to']
            receipt={'action_id':action['action_id'],'request_sha256':digest(request),'inputs':action['inputs'],
                     'observed':predict(rule,action['inputs']['a'],action['inputs']['b']),'effect_index':effect_index}
            db.execute('INSERT INTO receipts VALUES(?,?,?)',(action['action_id'],canonical(receipt),digest(receipt)));db.commit()
            return receipt
    def _archive(self,folder,s,action,receipt):
        observation=self._observation(action,receipt)
        updated=science(s['observations']+[observation],s['strategy'])
        self._append(folder,[('OBSERVATION_RECORDED',observation),('SCIENCE_UPDATED',updated)])
    def _resume_locked(self,folder,mission_id,crash=None):
        s=self.snapshot(mission_id);action=s['pending_action']
        if not action:return s
        if action['state'] in {'DISPATCHED','RESULT_UNCERTAIN'}:
            receipt=self._receipts(folder).get(action['action_id'])
            if receipt:self._archive(folder,s,action,receipt)
            elif action['state']!='RESULT_UNCERTAIN':self._append(folder,[('RESULT_UNCERTAIN',{'action_id':action['action_id'],'reason':'Aucun reçu : effet inconnu, aucune répétition automatique.'})])
            return self.snapshot(mission_id)
        reason=self._gate(folder,s)
        if reason:
            self._append(folder,[('ACTION_REFUSED',{'action_id':action['action_id'],'reason':reason,'before_effect':True})]);return self.snapshot(mission_id)
        self._crash('after_prediction',crash)
        request={k:v for k,v in action.items() if k!='state'}
        self._append(folder,[('DISPATCH_RECORDED',{'action_id':action['action_id'],'request_sha256':digest(request)})])
        self._crash('after_dispatch',crash)
        # A policy may have changed while the intent/enqueue was being written.
        # Renew admission at the local simulated effect boundary. This does not
        # provide atomic revocation against arbitrary external policy editors.
        reason=self._gate(folder,self.snapshot(mission_id))
        if reason:
            self._append(folder,[('ACTION_REFUSED',{'action_id':action['action_id'],'reason':reason,'before_effect':True})]);return self.snapshot(mission_id)
        receipt=self._effect(folder,action);self._crash('after_effect',crash)
        self._archive(folder,s,action,receipt);self._crash('after_observation',crash)
        return self.snapshot(mission_id)
    def resume(self,mission_id):
        folder=self.folder(mission_id)
        with exclusive(folder):return self._resume_locked(folder,mission_id)
    def step(self,mission_id,count=1,crash=None):
        if type(count) is not int or not 1<=count<=24:raise Refused('Une commande traite 1 à 24 expériences maximum.')
        if crash not in {None,'after_prediction','after_dispatch','after_effect','after_observation'}:raise Refused('Point de panne inconnu.')
        folder=self.folder(mission_id)
        with exclusive(folder):
            s=self.snapshot(mission_id)
            if s['pending_action']:
                # Reconcile only. The same command cannot silently resume and
                # perform an extra experiment after the user requested a step.
                return self._resume_locked(folder,mission_id,crash)
            for _ in range(count):
                if s['status']!='ACTIVE':break
                if s['budget']['used']>=s['budget']['limit']:
                    self._append(folder,[('BUDGET_EXHAUSTED',s['budget'])]);break
                proposal=plan(s['observations'],s['strategy']);proposal.update(action_id='A-'+uuid.uuid4().hex,operation_key='OP-'+uuid.uuid4().hex)
                self._append(folder,[('PREDICTION_COMMITTED',proposal)])
                s=self._resume_locked(folder,mission_id,crash)
                if s['pending_action']:break
            s=self.snapshot(mission_id)
            if s['status']=='ACTIVE' and s['pending_action'] is None and s['budget']['used']>=s['budget']['limit']:
                self._append(folder,[('BUDGET_EXHAUSTED',s['budget'])]);s=self.snapshot(mission_id)
            return s
    def export(self,mission_id):
        s=self.snapshot(mission_id);out=self.root/'exports';out.mkdir(exist_ok=True)
        path=out/(mission_id+'-'+uuid.uuid4().hex+'.json')
        with path.open('x',encoding='utf-8') as stream:stream.write(canonical({'exported_at':now(),'snapshot':s})+'\n')
        return str(path)

def main():
    if hasattr(__import__('sys').stdout,'reconfigure'):__import__('sys').stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent/'local')
    c=p.add_subparsers(dest='command',required=True);c.add_parser('list');create=c.add_parser('create')
    create.add_argument('--title',default='Laboratoire symbolique');create.add_argument('--world',default='xor');create.add_argument('--change-to',default='or');create.add_argument('--change-after',type=int,default=4);create.add_argument('--budget',type=int,default=24);create.add_argument('--strategy',default='targeted')
    for name in ('step','snapshot','resume','export'):
        sub=c.add_parser(name);sub.add_argument('mission_id')
        if name=='step':sub.add_argument('--count',type=int,default=1);sub.add_argument('--crash',default=None)
    args=p.parse_args();lab=Lab(args.root)
    try:
        if args.command=='create':result=lab.create(args.title,args.world,args.change_to,args.change_after,args.budget,args.strategy)
        elif args.command=='list':result=lab.missions()
        elif args.command=='step':result=lab.step(args.mission_id,args.count,args.crash)
        else:result=getattr(lab,args.command)(args.mission_id)
        print(canonical({'ok':True,'result':result}));return 0
    except Exception as exc:print(canonical({'ok':False,'error':type(exc).__name__,'message':str(exc)}));return 2

if __name__=='__main__':raise SystemExit(main())
