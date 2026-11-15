"""Independent, persisted HTTP scenarios against a real MVC build."""
from copy import deepcopy
from pathlib import Path
import json,urllib.request,urllib.error,uuid,traceback
import mvc_runtime as runtime

def fixture(title='Quiz témoin',grading='partial',show=True):
    return {'title':title,'config':{'grading':grading,'passingPercent':70,'shuffleQuestions':False,'shuffleOptions':False,'showCorrection':show},'questions':[
        {'id':'q-one','text':'Deux plus trois ?','mode':'single','explanation':'2 + 3 = 5.','options':[{'id':'one-a','text':'5','correct':True},{'id':'one-b','text':'6','correct':False}]},
        {'id':'q-many','text':'Quels nombres sont pairs ?','mode':'multiple','explanation':'2 et 4 sont pairs.','options':[{'id':'many-a','text':'2','correct':True},{'id':'many-b','text':'4','correct':True},{'id':'many-c','text':'5','correct':False}]}]}

def qualify(build=None):
    root=runtime.BASE/'evidence'/('studio-http-'+uuid.uuid4().hex);root.mkdir(parents=True)
    data=root/'data';checks=[];requests=[];server=None
    r={'status':'INCONCLUSIVE','evidence_dir':str(root),'checks':checks,'scope':'Actual local ASP.NET Core MVC HTTP behavior and persistent state; browser and general migration are separate'}
    def check(name,condition):
        checks.append({'name':name,'pass':bool(condition)})
        if not condition:raise AssertionError(name)
    def call(method,path,payload=None,expected=200,headers=None):
        h={'Content-Type':'application/json','Origin':server.url};h.update(headers or {})
        req=urllib.request.Request(server.url+path,data=None if payload is None else json.dumps(payload).encode(),headers=h,method=method)
        try:
            with urllib.request.urlopen(req,timeout=10) as response:status=response.status;raw=response.read();mime=response.headers.get('Content-Type','')
        except urllib.error.HTTPError as exc:status=exc.code;raw=exc.read();mime=exc.headers.get('Content-Type','')
        value=json.loads(raw) if raw and 'json' in mime else raw.decode('utf-8','replace')
        requests.append({'method':method,'path':path,'request':payload,'status':status,'response':value})
        check(method+' '+path+' status '+str(expected),status==expected)
        return value
    try:
        build=build or runtime.load_or_build();r['build']=str(Path(build['folder'])/'build.json')
        server=runtime.StudioProcess(build,data).start()
        home=call('GET','/');check('Real MVC view rendered',isinstance(home,str) and '<html' in home and 'studio' in home.lower())
        state=call('GET','/api/studio');account=state['account'];check('Only QCM enabled',all(g['id']=='qcm' for g in state['games'] if g['enabled']))
        changed=call('PUT','/api/account',{'displayName':'Joueur témoin'});check('Single identity stable',changed['id']==account['id'])
        quiz=call('POST','/api/quizzes',fixture(),201)
        qid=quiz['id'];check('Quiz creation revision',quiz['revision']==1 and len(quiz['questions'])==2)
        invalid=fixture();invalid['questions'][0]['options'][1]['correct']=True
        call('POST','/api/quizzes',invalid,400)
        invalid=fixture();invalid['questions'][1]['options'][1]['id']='many-a';call('POST','/api/quizzes',invalid,400)
        call('POST','/api/quizzes',fixture(),403,{'Origin':'https://untrusted.invalid'})
        key=str(uuid.uuid4());request={'quizId':qid,'quizRevision':1,'requestId':key}
        attempt=call('POST','/api/attempts',request,201);aid=attempt['id']
        replay=call('POST','/api/attempts',request,200);check('Start replay has same id',replay['id']==aid)
        call('POST','/api/attempts',dict(request,quizRevision=999),409)
        check('No answer key before completion',all('correct' not in o for q in attempt['questions'] for o in q['options']) and all('explanation' not in q for q in attempt['questions']))
        call('POST','/api/attempts/'+aid+'/complete',{'revision':attempt['revision']},400)
        path='/api/attempts/'+aid+'/answers'
        call('PUT',path,{'revision':attempt['revision'],'answers':[{'questionId':'q-one','optionIds':['one-a','one-b']}]},400)
        call('PUT',path,{'revision':attempt['revision'],'answers':[{'questionId':'q-many','optionIds':['many-a','many-a']}]},400)
        answers=[{'questionId':'q-one','optionIds':['one-a']},{'questionId':'q-many','optionIds':['many-a']}]
        call('PUT',path,{'revision':attempt['revision'],'answers':answers,'score':999999},400)
        saved=call('PUT',path,{'revision':attempt['revision'],'answers':answers})
        check('Progress persisted, client score not authoritative','result' not in saved or saved['result'] is None)
        call('PUT',path,{'revision':attempt['revision'],'answers':answers},409)
        edited=fixture('Quiz témoin modifié');edited['revision']=1;edited['questions'][0]['options'][0]['correct']=False;edited['questions'][0]['options'][1]['correct']=True
        newer=call('PUT','/api/quizzes/'+qid,edited);check('New quiz revision',newer['revision']==2)
        call('PUT','/api/quizzes/'+qid,edited,409)
        done=call('POST','/api/attempts/'+aid+'/complete',{'revision':saved['revision']})
        result=done['result'];check('Snapshot plus partial independent oracle',result['score']==1.5 and result['maxScore']==2 and result['percent']==75 and result['passed'] is True)
        repeated=call('POST','/api/attempts/'+aid+'/complete',{'revision':saved['revision']});check('Completion replay is identical',repeated==done)
        call('PUT',path,{'revision':done['revision'],'answers':answers},409)
        second=call('POST','/api/attempts',{'quizId':qid,'quizRevision':2,'requestId':str(uuid.uuid4())},201)
        sid=second['id'];second=call('PUT','/api/attempts/'+sid+'/answers',{'revision':second['revision'],'answers':answers})
        second=call('POST','/api/attempts/'+sid+'/complete',{'revision':second['revision']})
        check('New revision uses new answer key',second['result']['score']==.5 and second['result']['percent']==25)
        allquiz=call('POST','/api/quizzes',fixture('Tout ou rien','all-or-nothing',False),201)
        a=call('POST','/api/attempts',{'quizId':allquiz['id'],'quizRevision':allquiz['revision'],'requestId':str(uuid.uuid4())},201)
        a=call('PUT','/api/attempts/'+a['id']+'/answers',{'revision':a['revision'],'answers':answers})
        a=call('POST','/api/attempts/'+a['id']+'/complete',{'revision':a['revision']})
        check('All-or-nothing independent oracle',a['result']['score']==1 and a['result']['percent']==50 and not a['result']['passed'])
        check('Correction hiding honored',not a['result'].get('details'))
        check('Hidden correction not leaked in questions',all('correct' not in o for q in a['questions'] for o in q['options']))
        # Negative penalty must clamp at zero; this oracle is independent of C#.
        penalty=call('POST','/api/attempts',{'quizId':qid,'quizRevision':2,'requestId':str(uuid.uuid4())},201)
        penalty_answers=[{'questionId':'q-one','optionIds':['one-a']},{'questionId':'q-many','optionIds':['many-a','many-c']}]
        penalty=call('PUT','/api/attempts/'+penalty['id']+'/answers',{'revision':penalty['revision'],'answers':penalty_answers})
        penalty=call('POST','/api/attempts/'+penalty['id']+'/complete',{'revision':penalty['revision']})
        check('Partial wrong-answer penalty clamps to zero',penalty['result']['score']==0)
        boundary=fixture('Six bonnes possibilités');boundary['questions']=[{'id':'six','text':'Choisis des nombres parmi la liste.','mode':'multiple','explanation':'Toutes ces options sont admises.','options':[{'id':'six-'+str(i),'text':str(i),'correct':True} for i in range(6)]}];boundary['config']['passingPercent']=17
        bquiz=call('POST','/api/quizzes',boundary,201)
        boundary_attempt=call('POST','/api/attempts',{'quizId':bquiz['id'],'quizRevision':1,'requestId':str(uuid.uuid4())},201)
        boundary_attempt=call('PUT','/api/attempts/'+boundary_attempt['id']+'/answers',{'revision':boundary_attempt['revision'],'answers':[{'questionId':'six','optionIds':['six-0']}]})
        boundary_attempt=call('POST','/api/attempts/'+boundary_attempt['id']+'/complete',{'revision':boundary_attempt['revision']})
        check('Exact fraction threshold, no wrong denominator division',boundary_attempt['result']['score']==.17 and boundary_attempt['result']['percent']==16.67 and boundary_attempt['result']['passed'] is False)
        pending=call('POST','/api/attempts',{'quizId':qid,'quizRevision':2,'requestId':str(uuid.uuid4())},201)
        pending=call('PUT','/api/attempts/'+pending['id']+'/answers',{'revision':pending['revision'],'answers':[answers[0]]})
        archived=call('POST','/api/quizzes/'+qid+'/archive',{'revision':2});check('Archive retains quiz',archived['archived'] is True)
        call('POST','/api/attempts',{'quizId':qid,'quizRevision':archived['revision'],'requestId':str(uuid.uuid4())},409)
        after_archive_replay=call('POST','/api/attempts',request,200);check('Old creation receipt survives edit and archive',after_archive_replay['id']==aid)
        call('PUT','/api/account',{'displayName':'Nom changé après les parties'})
        scores=call('GET','/api/scores');groups=[g for g in scores['groups'] if g['quizId']==qid]
        check('Scores separated by revisions',{g['quizRevision'] for g in groups}=={1,2})
        entries=[e for g in scores['groups'] for e in g['entries']]
        check('No duplicate completion score',sum(e['attemptId']==aid for e in entries)==1)
        check('Old account display name is snapshotted',next(e for e in entries if e['attemptId']==aid)['playerName']=='Joueur témoin')
        duplicate=runtime.StudioProcess(build,data)
        try:
            duplicate.start();check('Second process with same data is refused',False)
        except RuntimeError:check('Second process with same data is refused',True)
        finally:duplicate.stop()
        server.stop();server=None
        server=runtime.StudioProcess(build,data).start()
        resumed=call('GET','/api/attempts/'+pending['id']);check('Restart restores in-progress answers',resumed['answers']==pending['answers'] and resumed['status']=='in-progress')
        retained=call('GET','/api/attempts/'+aid);check('Restart retains old score snapshot',retained==done)
        check('Restart retains one account',call('GET','/api/account')['id']==account['id'])
        r['status']='PASS';r['scope']+='; tests use isolated data, not user live state'
    except Exception as exc:r.update(reason=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc())
    finally:
        if server:server.stop()
        runtime.save(root/'requests.json',requests);runtime.save(root/'result.json',r)
    return r
if __name__=='__main__':
    r=qualify();print(json.dumps({'status':r['status'],'checks':len(r['checks']),'reason':r.get('reason'),'evidence':r['evidence_dir']},ensure_ascii=False));raise SystemExit(r['status']!='PASS')
