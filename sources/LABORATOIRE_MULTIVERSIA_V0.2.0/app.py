"""Live, freely zoomable laboratory map. Only explicit commands cause effects."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import ui_backend

BG='#101923'; PANEL='#172431'; INK='#edf4fa'; MUTED='#a9bdcb'; LINE='#3c566a'
TEAL='#61cfc2'; AMBER='#f2c16e'; RED='#fa9393'; BLUE='#9bbfff'
SECTIONS={'hypotheses':'Hypothèses','observations':'Expériences','diagnostics':'Diagnostics','knowledge':'Connaissances','events':'Preuves / événements'}
TERMINAL={'BUDGET_EXHAUSTED','REFUSED','INCONCLUSIVE'}
IDS={'hypotheses':'hypothesis_id','observations':'observation_id','diagnostics':'diagnostic_id','knowledge':'revision_id','events':'seq'}
SOURCE_EXAMPLE='public static class Example { public static int Compute(int a, int b) { return a >= b ? a : b; } }'
CONTRACT_EXAMPLE='public static class Contract { public static bool Spec(int a, int b, int result) { return result >= a && result >= b && (result == a || result == b); } }'


def safe_float(value, default, lower, upper):
    try:
        value=float(value)
        return max(lower,min(upper,value)) if math.isfinite(value) else default
    except (ValueError,TypeError):return default


def observation_summary(value):
    inputs=value.get('inputs',{})
    bit=lambda item:'1' if item is True else '0' if item is False else '—'
    return f"Entrées : a = {bit(inputs.get('a'))}, b = {bit(inputs.get('b'))}\nPrédiction : {bit(value.get('prediction'))}\nRésultat observé : {bit(value.get('observed'))}"


def object_summary(category,value,snapshot):
    """Readable projection only; complete objects remain in the raw-data tab."""
    if category=='observations':
        purpose={'exploration':'Apprentissage initial','monitor':'Surveillance de la règle','diagnostic':'Contre-vérification du diagnostic','validation':'Validation prospective de la nouvelle règle'}.get(value.get('purpose'),'Expérience')
        surprise='SURPRISE : le résultat contredit la prédiction.' if value.get('prediction')!=value.get('observed') else 'Le résultat correspond à la prédiction.'
        return purpose+'\n\n'+observation_summary(value)+'\n\n'+surprise+'\n\nCette observation provient du simulateur local.'
    if category=='diagnostics':
        status={'OPEN':'Diagnostic en cours','CLOSED':'Diagnostic terminé','INCONCLUSIVE':'Diagnostic sans conclusion'}.get(value.get('status'),'Diagnostic')
        stage={'repeat':'Reproduire la surprise','references':'Rejouer les références et départager les hypothèses','validate':'Valider la règle sur une nouvelle expérience','closed':'Validation terminée'}.get(value.get('stage'),'Consulter la conclusion')
        trigger=next((o for o in snapshot.get('observations',[]) if o.get('observation_id')==value.get('trigger_observation_id')),None)
        surprise=observation_summary(trigger) if trigger else 'Observation de déclenchement disponible dans les preuves.'
        return (status+'\n\nSURPRISE À L’ORIGINE\n'+surprise+'\n\nÉTAPE\n'+stage+
                f"\n\nTests de référence : {value.get('reference_count',0)}\nValidations prospectives : {value.get('validation_count',0)}\nHypothèses compatibles : {len(value.get('hypotheses',[]))}"+
                '\n\nCONCLUSION ACTUELLE\n'+str(value.get('conclusion','Aucune conclusion pour le moment.')))
    if category=='knowledge':
        status={'ÉTAYÉE_DANS_UN_PÉRIMÈTRE':'Étayée dans le périmètre indiqué','EN_RÉVISION':'Remise en question par une nouvelle observation','REMPLACÉE':'Remplacée par une révision plus récente'}.get(value.get('status'),str(value.get('status','—')))
        validation='Références recontrôlées puis validation prospective sur une nouvelle expérience.' if value.get('supersedes') else 'Apprentissage initial : les quatre entrées possibles ont été observées.'
        return 'RÈGLE\n'+str(value.get('rule','—'))+'\n\nSTATUT\n'+status+'\n\nCOMMENT ELLE A ÉTÉ ÉTAYÉE\n'+validation+f"\n\nObservations associées : {len(value.get('evidence_ids',[]))}"+'\n\nPÉRIMÈTRE\n'+str(value.get('scope','Consulter les preuves.'))
    if category=='hypotheses':
        return 'RÈGLE CANDIDATE\n'+str(value.get('rule','—'))+'\n\nCompatible avec les observations actuellement retenues. La validation est suivie dans Connaissances. Cette compatibilité ne détermine pas une cause physique.'
    if category=='events':
        titles={'MISSION_CREATED':'Mission créée','PREDICTION_COMMITTED':'Prédiction enregistrée avant l’expérience','DISPATCH_RECORDED':'Expérience envoyée au simulateur','RESULT_UNCERTAIN':'Résultat incertain','OBSERVATION_RECORDED':'Observation et reçu enregistrés','ACTION_REFUSED':'Action refusée avant effet','SCIENCE_UPDATED':'Hypothèses, diagnostics et connaissances actualisés','BUDGET_EXHAUSTED':'Budget de mission épuisé'}
        return titles.get(value.get('type'),str(value.get('type','Événement')))+'\n\nDate : '+str(value.get('at','—'))+'\n\nÉvénement n° '+str(value.get('seq','—'))+'\n\nLe contenu intégral et son empreinte sont accessibles dans Données brutes et Journal.'
    return 'Objet conservé. Consulter les données brutes pour son contenu complet.'


class LabApp:
    def __init__(self, root, store=None, auto_poll=True):
        self.root=root;self.store=store or ui_backend.Store();self.pool=ThreadPoolExecutor(max_workers=2)
        self.rows=[];self.cache={};self.scan_future=None;self.job=None;self.job_operation=None;self.job_mission=None
        self.closed=False;self.closing=False;self.remaining=0;self.campaign_mission=None;self.created_mission=None
        self.paused=False;self.scan_error=None;self.last_scan='Jamais';self.objects={};self.positions={};self.drag=None
        self.csharp_window=None;self.csharp_fields=None;self.analysis_payload=None;self.analysis_result=None;self.analysis_archive=None
        prefs=self.store.preferences();self.mission_id=prefs.get('mission_id');self.selected=prefs.get('selected','mission')
        self.section=prefs.get('section','observations')
        if self.section not in SECTIONS:self.section='observations'
        self.zoom=safe_float(prefs.get('zoom'),.8,.25,2.8);self.ox=safe_float(prefs.get('ox'),420,-100000,100000);self.oy=safe_float(prefs.get('oy'),60,-100000,100000)
        self.page=0;self.needs_center=not bool(prefs)
        root.title('MultiversIA — Laboratoire V0.2.0');root.geometry('1320x850');root.minsize(940,650);root.configure(bg=BG)
        style=ttk.Style(root);style.theme_use('clam');style.configure('TFrame',background=BG)
        style.configure('TLabel',background=BG,foreground=INK,font=('Segoe UI',10));style.configure('Sub.TLabel',foreground=MUTED)
        style.configure('TButton',font=('Segoe UI',10),padding=(9,6));style.configure('Title.TLabel',font=('Segoe UI',18,'bold'))
        outer=ttk.Frame(root,padding=14);outer.pack(fill='both',expand=True)
        header=ttk.Frame(outer);header.pack(fill='x',pady=(0,8))
        ttk.Label(header,text='MultiversIA',style='Title.TLabel').pack(side='left')
        ttk.Label(header,text='  Laboratoire local · agents symboliques',style='Sub.TLabel').pack(side='left')
        ttk.Button(header,text='C# → maths',command=self.open_csharp).pack(side='right')
        ttk.Button(header,text='Architecture',command=self.open_architecture).pack(side='right',padx=5)
        self.new_button=ttk.Button(header,text='+ Mission',command=self.new_dialog);self.new_button.pack(side='right')
        nav=ttk.Frame(outer);nav.pack(fill='x',pady=(0,8))
        self.mission_box=ttk.Combobox(nav,state='readonly',width=28);self.mission_box.pack(side='left');self.mission_box.bind('<<ComboboxSelected>>',self.pick_mission)
        ttk.Button(nav,text='Vue globale',command=self.overview).pack(side='left',padx=5)
        ttk.Button(nav,text='−',command=lambda:self.zoom_at(.8)).pack(side='left')
        ttk.Button(nav,text='+',command=lambda:self.zoom_at(1.25)).pack(side='left',padx=5)
        self.scale=tk.StringVar();ttk.Label(nav,textvariable=self.scale,style='Sub.TLabel').pack(side='left')
        ttk.Label(nav,text='Molette : zoom · glisser : déplacer',style='Sub.TLabel').pack(side='right')
        split=ttk.Panedwindow(outer,orient='horizontal');split.pack(fill='both',expand=True)
        left=ttk.Frame(split);split.add(left,weight=3)
        self.canvas=tk.Canvas(left,bg=BG,highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.draw());self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<ButtonPress-1>',self.press);self.canvas.bind('<B1-Motion>',self.motion);self.canvas.bind('<ButtonRelease-1>',self.release)
        self.canvas.bind('<Double-Button-1>',self.explore)
        pages=ttk.Frame(left);pages.pack(fill='x',pady=4)
        ttk.Button(pages,text='← Précédents',command=lambda:self.change_page(-1)).pack(side='left')
        self.page_text=tk.StringVar();ttk.Label(pages,textvariable=self.page_text,style='Sub.TLabel').pack(side='left',padx=8)
        ttk.Button(pages,text='Suivants →',command=lambda:self.change_page(1)).pack(side='right')
        right=ttk.Frame(split,width=395,padding=(12,0,0,0));split.add(right,weight=1)
        self.detail_title=tk.StringVar(value='Laboratoire');ttk.Label(right,textvariable=self.detail_title,font=('Segoe UI',14,'bold'),wraplength=370).pack(anchor='w',pady=(0,8))
        self.notebook=ttk.Notebook(right);self.notebook.pack(fill='both',expand=True)
        self.summary=self.text_tab(self.notebook,'Résumé');self.journal=self.text_tab(self.notebook,'Journal');self.raw=self.text_tab(self.notebook,'Données brutes')
        commands=ttk.Frame(right);commands.pack(fill='x',pady=(8,0))
        self.step_button=ttk.Button(commands,text='Une expérience',command=self.step_once);self.step_button.pack(fill='x',pady=2)
        batch=ttk.Frame(commands);batch.pack(fill='x',pady=2)
        self.count=tk.StringVar(value='8');ttk.Spinbox(batch,from_=1,to=24,width=4,textvariable=self.count).pack(side='left')
        self.run_button=ttk.Button(batch,text='Lancer la séquence',command=self.start_campaign);self.run_button.pack(side='left',fill='x',expand=True,padx=(5,0))
        self.pause_button=ttk.Button(batch,text='Pause',command=self.pause);self.pause_button.pack(side='right',padx=(5,0))
        self.resume_button=ttk.Button(commands,text='Rapprocher l’action interrompue',command=self.resume);self.resume_button.pack(fill='x',pady=2)
        self.export_button=ttk.Button(commands,text='Exporter la mission et ses preuves',command=self.export);self.export_button.pack(fill='x',pady=2)
        self.message=tk.StringVar(value='Lecture des journaux…');self.message_label=ttk.Label(outer,textvariable=self.message,wraplength=1200);self.message_label.pack(fill='x',pady=(8,2))
        outer.bind('<Configure>',lambda e:self.message_label.configure(wraplength=max(400,e.width-35)))
        self.freshness=tk.StringVar();ttk.Label(outer,textvariable=self.freshness,style='Sub.TLabel').pack(fill='x')
        root.protocol('WM_DELETE_WINDOW',self.close);root.bind('<Control-plus>',lambda e:self.zoom_at(1.25));root.bind('<Control-minus>',lambda e:self.zoom_at(.8))
        self.draw();self.details()
        if auto_poll:self.tick()

    def text_tab(self,book,name):
        frame=ttk.Frame(book);book.add(frame,text=name)
        text=tk.Text(frame,bg=PANEL,fg=INK,insertbackground=INK,wrap='word',font=('Segoe UI',10),relief='flat',padx=12,pady=12,width=38,height=10)
        scroll=ttk.Scrollbar(frame,command=text.yview);scroll.pack(side='right',fill='y');text.pack(fill='both',expand=True);text.configure(yscrollcommand=scroll.set,state='disabled');return text

    @staticmethod
    def put(widget,text):
        current=widget.get('1.0','end-1c')
        if current==text:return
        at=widget.yview()[0];widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',text);widget.configure(state='disabled');widget.yview_moveto(at)

    def current_row(self):return next((r for r in self.rows if r['mission_id']==self.mission_id),None)
    def state(self):return self.cache.get(self.mission_id,{})
    def verified(self):
        row=self.current_row();return bool(row and not row.get('error') and not self.scan_error and self.state().get('integrity')=='VERIFIED')
    def can_step(self):return self.verified() and not self.job and not self.closing and self.state().get('status') not in TERMINAL and not self.state().get('pending_action')

    def ingest(self,rows):
        self.rows=rows;self.scan_error=None
        for row in rows:
            snap=row.get('snapshot')
            if snap is not None:
                old=self.cache.get(row['mission_id'],{})
                if len(snap.get('events',[]))>=len(old.get('events',[])):self.cache[row['mission_id']]=snap
                else:row['error']='Lecture antérieure à la vue conservée : état courant non vérifié. Nouvelle lecture nécessaire.'
        if any(r['mission_id']==self.created_mission for r in rows):self.created_mission=None
        if not any(r['mission_id']==self.mission_id for r in rows) and (not self.created_mission or self.mission_id!=self.created_mission):
            self.mission_id=rows[-1]['mission_id'] if rows else None;self.selected='mission'
        self.mission_box['values']=[r.get('title',r['mission_id']) for r in rows]
        current=next((i for i,r in enumerate(rows) if r['mission_id']==self.mission_id),None)
        if current is not None:self.mission_box.current(current)
        else:self.mission_box.set('Nouvelle mission : lecture en cours' if self.created_mission else '')
        self.last_scan=datetime.now().strftime('%H:%M:%S')
        if self.needs_center:self.needs_center=False;self.overview()
        if self.message.get()=='Lecture des journaux…':self.message.set('Prêt. Crée une mission ou lance une expérience dans la mission sélectionnée.')
        self.draw();self.details()

    def tick(self):
        if self.closed:return
        if self.scan_future and self.scan_future.done():
            future=self.scan_future;self.scan_future=None
            try:self.ingest(future.result())
            except Exception as exc:self.scan_error=str(exc);self.details();self.draw()
        if self.job and self.job.done():
            job=self.job;operation=self.job_operation;target=self.job_mission;self.job=None;self.job_operation=None
            try:
                result=job.result()
                if operation=='create':
                    self.mission_id=result['mission_id'];self.created_mission=self.mission_id;self.selected='mission';self.section='observations';self.page=0;self.overview()
                    self.message.set('Mission créée ; ses preuves sont conservées.')
                elif operation=='analyze':self.analysis_done(result)
                elif operation=='export':self.message.set('Export conservé : '+result['export_path'])
                elif isinstance(result,dict) and result.get('mission_id'):
                    self.cache[result['mission_id']]=result
                    for row in self.rows:
                        if row['mission_id']==result['mission_id']:row.update(error=None,snapshot=result)
                    if result.get('status') in TERMINAL or result.get('pending_action'):
                        self.remaining=0;self.message.set(result.get('summary','Mission arrêtée ; consulter les preuves.'))
                    elif self.remaining:self.message.set(f'Séquence en cours · {self.remaining} expérience(s) maximum restante(s).')
                    else:self.message.set('Pause après l’expérience en cours.' if self.paused else 'Commande terminée ; preuves conservées.')
                self.draw();self.details()
            except Exception as exc:
                self.remaining=0;self.message.set('Arrêt de la commande : '+str(exc))
                if operation=='analyze':self.analysis_done({'verdict':'INCONCLUSIF','reason':str(exc),'limitations':['Aucun résultat accepté.']})
        if self.closing and not self.job:self.finish_close();return
        if self.remaining and not self.job:
            target=self.campaign_mission;row=next((r for r in self.rows if r['mission_id']==target),None);state=self.cache.get(target,{})
            if self.scan_error or not row or row.get('error') or state.get('pending_action') or state.get('status') in TERMINAL:
                self.remaining=0;self.message.set('Séquence arrêtée : état non vérifié, action à rapprocher ou budget terminé.')
            else:
                self.remaining-=1;self.dispatch('step',mission_id=target)
        if self.scan_future is None and not self.closing:self.scan_future=self.pool.submit(self.store.scan)
        self.root.after(700,self.tick)

    def dispatch(self,operation,**parameters):
        if self.job or self.closing:return False
        self.job_operation=operation;self.job_mission=parameters.get('mission_id');self.job=self.pool.submit(self.store.command,operation,**parameters)
        self.message.set('Commande en cours ; la carte reste consultable.');self.details();return True

    def step_once(self):
        if self.can_step():self.paused=False;self.dispatch('step',mission_id=self.mission_id)
    def start_campaign(self):
        if not self.can_step():return
        try:count=int(self.count.get())
        except ValueError:self.message.set('Choisis un nombre entier de 1 à 24.');return
        if not 1<=count<=24:self.message.set('La séquence doit contenir 1 à 24 expériences maximum.');return
        self.remaining=count-1;self.campaign_mission=self.mission_id;self.paused=False;self.dispatch('step',mission_id=self.mission_id)
    def pause(self):
        self.remaining=0;self.paused=True;self.message.set('Pause demandée : aucune nouvelle expérience après celle en cours.');self.details()
    def resume(self):
        if self.verified() and self.state().get('pending_action') and not self.job:self.remaining=0;self.dispatch('resume',mission_id=self.mission_id)
    def export(self):
        if self.verified() and not self.job:self.dispatch('export',mission_id=self.mission_id)

    def pick_mission(self,_=None):
        index=self.mission_box.current()
        if 0<=index<len(self.rows):self.mission_id=self.rows[index]['mission_id'];self.selected='mission';self.page=0;self.draw();self.details()

    def new_dialog(self):
        if self.job:return
        win=tk.Toplevel(self.root);win.title('Nouvelle mission · monde simulé');win.transient(self.root);win.resizable(False,False)
        frame=ttk.Frame(win,padding=20);frame.pack()
        ttk.Label(frame,text='Scénario contrôlé pour les agents symboliques',font=('Segoe UI',12,'bold')).grid(row=0,column=0,columnspan=2,pady=(0,12))
        fields={}
        for row,(name,label,values,default) in enumerate([
            ('title','Nom',None,'Apprendre puis détecter un changement'),('world','Règle initiale',['xor','and','or','a','b','true','false'],'xor'),
            ('change_to','Règle après changement',['Aucun changement','xor','and','or','a','b','true','false'],'or'),
            ('change_after','Changer après N expériences',None,'4'),('max_actions','Budget total',None,'24'),
            ('strategy','Stratégie',['targeted','exhaustive','none'],'targeted')],1):
            ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',padx=(0,12),pady=4)
            widget=ttk.Combobox(frame,values=values,state='readonly',width=34) if values else ttk.Entry(frame,width=37)
            widget.grid(row=row,column=1,sticky='ew',pady=4)
            if values:widget.set(default)
            else:widget.insert(0,default)
            fields[name]=widget
        error=tk.StringVar();ttk.Label(frame,textvariable=error,wraplength=470).grid(row=7,column=0,columnspan=2)
        def create():
            try:
                budget=int(fields['max_actions'].get());after=int(fields['change_after'].get())
                if not 4<=budget<=128 or not 0<=after<=128:raise ValueError()
            except ValueError:error.set('Budget : 4 à 128. Changement après : 0 à 128.');return
            values={name:widget.get() for name,widget in fields.items()};values.update(change_after=after,max_actions=budget)
            if values['change_to']=='Aucun changement':values['change_to']=values['world']
            win.destroy();self.dispatch('create',**values)
        ttk.Button(frame,text='Créer la mission',command=create).grid(row=8,column=0,columnspan=2,sticky='ew',pady=(12,0))

    def overview(self):
        self.zoom=min(.8,max(.25,(self.canvas.winfo_width()-70)/1050));self.ox=self.canvas.winfo_width()/2;self.oy=70;self.draw()
    def zoom_at(self,factor,x=None,y=None):
        x=self.canvas.winfo_width()/2 if x is None else x;y=self.canvas.winfo_height()/2 if y is None else y
        new=max(.25,min(2.8,self.zoom*factor));ratio=new/self.zoom;self.ox=x-(x-self.ox)*ratio;self.oy=y-(y-self.oy)*ratio;self.zoom=new;self.draw()
    def wheel(self,e):self.zoom_at(1.16 if e.delta>0 else 1/1.16,e.x,e.y)
    def press(self,e):self.drag=(e.x,e.y,self.ox,self.oy,False)
    def motion(self,e):
        if self.drag:
            x,y,ox,oy,moved=self.drag;self.ox=ox+e.x-x;self.oy=oy+e.y-y;self.drag=(x,y,ox,oy,moved or abs(e.x-x)+abs(e.y-y)>4);self.draw()
    def release(self,e):
        moved=self.drag and self.drag[4];self.drag=None
        if not moved:self.pick(e.x,e.y)
    def pick(self,x,y):
        for item in reversed(self.canvas.find_overlapping(x,y,x,y)):
            for tag in self.canvas.gettags(item):
                if tag.startswith('node:'):self.select(tag[5:]);return
    def select(self,key):
        self.selected=key
        if key in SECTIONS:self.section=key;self.page=0
        self.draw();self.details()
    def explore(self,e):
        self.pick(e.x,e.y)
        if self.selected in SECTIONS:self.focus_entries()
        elif self.selected in self.positions:self.focus(self.positions[self.selected],1.5)
    def focus(self,point,zoom):
        self.zoom=zoom;self.ox=self.canvas.winfo_width()/2-point[0]*zoom;self.oy=self.canvas.winfo_height()/2-point[1]*zoom;self.draw()
    def focus_entries(self):self.zoom=.95;self.ox=self.canvas.winfo_width()/2;self.oy=60-850*self.zoom;self.draw()
    def change_page(self,delta):
        count=len(self.state().get(self.section,[]));self.page=max(0,min(max(0,(count-1)//9),self.page+delta));self.draw()

    def node(self,key,x,y,title,subtitle='',color=BLUE,extra=''):
        w,h=260,104;sx=self.ox+x*self.zoom;sy=self.oy+y*self.zoom;sw=w*self.zoom;sh=h*self.zoom;tag='node:'+key;self.positions[key]=(x,y)
        self.canvas.create_rectangle(sx-sw/2,sy-sh/2,sx+sw/2,sy+sh/2,fill=PANEL,outline=color if key==self.selected else LINE,width=2 if key==self.selected else 1,tags=(tag,))
        font=max(9,min(17,round(12*self.zoom)));limit=max(8,int((sw-14)/(font*.7))*(2 if self.zoom>=.45 else 1))
        label=str(title);label=label if len(label)<=limit else label[:limit-1]+'…'
        self.canvas.create_text(sx,sy-(16*self.zoom if self.zoom>=.65 else 0),text=label,width=max(40,sw-14),fill=INK,font=('Segoe UI',font,'bold'),tags=(tag,))
        if self.zoom>=.65:self.canvas.create_text(sx,sy+12*self.zoom,text=str(subtitle),width=sw-16,fill=color,font=('Segoe UI',max(9,min(13,round(10*self.zoom)))),tags=(tag,))
        if self.zoom>=1.25 and extra:self.canvas.create_text(sx,sy+36*self.zoom,text=str(extra),width=sw-16,fill=MUTED,font=('Segoe UI',10),tags=(tag,))
    def edge(self,a,b):
        x1,y1=a;x2,y2=b;x1=self.ox+x1*self.zoom;y1=self.oy+y1*self.zoom;x2=self.ox+x2*self.zoom;y2=self.oy+y2*self.zoom;middle=(y1+y2)/2
        self.canvas.create_line(x1,y1,x1,middle,x2,middle,x2,y2,fill=LINE,width=1.4,arrow='last')

    def draw(self):
        if self.closed:return
        self.canvas.delete('all');self.positions={};self.objects={};self.scale.set(f'{round(self.zoom*100)} %')
        s=self.state();row=self.current_row();bad=self.scan_error or (row and row.get('error'));color=RED if bad else TEAL
        self.edge((0,52),(0,148));self.edge((0,252),(-330,348));self.edge((0,252),(0,348));self.edge((0,252),(330,348));self.edge((330,452),(0,558))
        self.node('mission',0,0,s.get('title','MultiversIA — aucune mission'),('Lecture non vérifiée' if bad else s.get('status','Créer une mission')),color)
        self.node('planner',0,200,'Planificateur symbolique',s.get('phase','Sans mission'),TEAL,s.get('summary',''))
        for key,x,y in [('hypotheses',-330,400),('observations',0,400),('diagnostics',330,400),('knowledge',0,610),('events',330,610)]:
            values=s.get(key,[]);self.node(key,x,y,SECTIONS[key],f'{len(values)} élément(s)',AMBER if key=='diagnostics' else BLUE)
        if not s:self.page_text.set('Aucune donnée à afficher.');return
        entries=list(s.get(self.section,[]))
        if self.section in {'observations','events','knowledge','diagnostics'}:entries.reverse()
        self.page=min(self.page,max(0,(len(entries)-1)//9));subset=entries[self.page*9:(self.page+1)*9]
        self.page_text.set(f'{SECTIONS[self.section]} · {self.page*9+1 if entries else 0}–{min(len(entries),(self.page+1)*9)} / {len(entries)}')
        for i,value in enumerate(subset):
            ident=str(value.get(IDS[self.section],self.page*9+i));key='entry:'+self.section+':'+ident;self.objects[key]=value
            x=(i%3-1)*310;y=850+(i//3)*175
            title=ident;subtitle=value.get('status',value.get('type',value.get('rule','')))
            if self.section=='observations':
                title='Expérience '+str(value.get('action_id',ident))[-12:];subtitle='Écart observé' if value.get('prediction')!=value.get('observed') else 'Prédiction conforme'
            if self.section=='knowledge':title=value.get('rule',ident)+' · '+ident
            self.node(key,x,y,title,subtitle,AMBER if self.section=='diagnostics' else BLUE)

    def details(self):
        s=self.state();row=self.current_row();error=self.scan_error or (row and row.get('error'));warning='DONNÉES NON VÉRIFIÉES / POTENTIELLEMENT PÉRIMÉES\n'+str(error)+'\n\n' if error else ''
        self.new_button.state(['disabled'] if self.job or self.closing else ['!disabled'])
        for button in (self.step_button,self.run_button):button.state(['!disabled'] if self.can_step() else ['disabled'])
        self.pause_button.state(['!disabled'] if self.job or self.remaining else ['disabled'])
        self.resume_button.state(['!disabled'] if self.verified() and s.get('pending_action') and not self.job else ['disabled'])
        self.export_button.state(['!disabled'] if self.verified() and not self.job else ['disabled'])
        self.freshness.set(f'Dernière lecture : {self.last_scan} · zoom conservé · séquence restante : {self.remaining} · '+('lecture non vérifiée' if error else 'simulation locale'))
        value=self.objects.get(self.selected)
        category=self.section
        if value is None and isinstance(self.selected,str) and self.selected.startswith('entry:'):
            _,category,ident=self.selected.split(':',2)
            if category in IDS:value=next((item for item in s.get(category,[]) if str(item.get(IDS[category]))==ident),None)
        if value is not None:
            self.detail_title.set(SECTIONS.get(category,'Élément')+' · sélection')
            summary=warning+object_summary(category,value,s)
            raw=value
        elif self.selected in SECTIONS:
            self.detail_title.set(SECTIONS[self.selected]);raw=s.get(self.selected,[])
            summary=warning+f'{len(raw)} élément(s) conservé(s).\n\nDouble-clique sur ce bloc pour voir les objets sur la carte. Les pages donnent accès à tous les éléments.\n\nLes données brutes et l’export conservent la liste intégrale.'
        elif self.selected=='planner':
            self.detail_title.set('Planificateur symbolique');raw={'phase':s.get('phase'),'hypotheses':s.get('hypotheses',[]),'pending_action':s.get('pending_action')}
            summary=warning+'MÉCANISME DÉTERMINISTE, SANS MODÈLE DE LANGAGE\n\n'+s.get('summary','Créer une mission.')+'\n\nPhase : '+str(s.get('phase','—'))+'\n\nLa planification utilise les observations publiques. Une loi compatible dans ce monde fini ne prouve pas une généralisation au monde réel.'
        else:
            self.detail_title.set(s.get('title','Laboratoire'));raw=s
            budget=s.get('budget',{})
            summary=warning+('MISSION\n'+s.get('status','—')+'\nPhase : '+s.get('phase','—')+'\n\n'+s.get('summary','')+'\n\nBUDGET GLOBAL\n'+str(budget.get('used',0))+' / '+str(budget.get('limit',0))+' expériences\n\n' if s else '')+'UTILISATION\nMolette : zoom libre. Glisser : déplacer. Clic : détail. Double-clic sur un bloc : explorer.\n\nUne séquence est bornée. Pause termine l’expérience en cours avant de s’arrêter.\n\nPÉRIMÈTRE\nAgents symboliques dans un simulateur fini. Aucun modèle IA local chargé. Le pont C# possède son propre périmètre.'
        self.put(self.summary,summary);self.put(self.raw,json.dumps(raw,ensure_ascii=False,indent=2) if raw else 'Aucune donnée.')
        events=s.get('events',[]);self.put(self.journal,'\n\n'.join('#'+str(e.get('seq'))+' · '+e.get('type','')+'\n'+e.get('at','')+'\n'+json.dumps(e.get('data'),ensure_ascii=False,indent=2)+'\nSHA-256 : '+e.get('sha256','') for e in events) or 'Aucun événement.')

    def open_csharp(self):
        if self.csharp_window and self.csharp_window.winfo_exists():self.csharp_window.lift();return
        win=tk.Toplevel(self.root);self.csharp_window=win;win.title('C# pur limité → mathématiques');win.geometry('1000x820')
        frame=ttk.Frame(win,padding=14);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='C# pur limité · analyse et vérification · aucune exécution du code utilisateur',wraplength=930).pack(anchor='w',pady=(0,8))
        ttk.Label(frame,text='Programme C# :').pack(anchor='w');source=tk.Text(frame,height=6,wrap='word',font=('Consolas',10));source.pack(fill='x');source.insert('1.0',SOURCE_EXAMPLE)
        ttk.Label(frame,text='Contrat C# bool Spec :').pack(anchor='w',pady=(8,0));contract=tk.Text(frame,height=6,wrap='word',font=('Consolas',10));contract.pack(fill='x');contract.insert('1.0',CONTRACT_EXAMPLE)
        controls=ttk.Frame(frame);controls.pack(fill='x',pady=8)
        button=ttk.Button(controls,text='Analyser et vérifier',command=self.analyze_csharp);button.pack(side='left')
        edit=ttk.Button(controls,text='Modifier les entrées',command=self.edit_csharp);edit.pack(side='left',padx=8);edit.state(['disabled'])
        book=ttk.Notebook(frame);book.pack(fill='both',expand=True)
        summary=self.text_tab(book,'Résultat');maths=self.text_tab(book,'Mathématiques');raw=self.text_tab(book,'Preuves et limites')
        self.csharp_fields={'source':source,'contract':contract,'button':button,'edit':edit,'summary':summary,'maths':maths,'raw':raw}
        self.put(summary,'Le programme et le contrat doivent appartenir au sous-ensemble accepté. Un refus n’est pas une preuve que le code est incorrect.')
        if self.analysis_payload:
            self.lock_csharp_inputs()
            if self.job_operation=='analyze':self.put(summary,'Analyse en cours… Les entrées affichées sont celles transmises au vérificateur.')
            elif self.analysis_result is not None:self.show_analysis()
    def lock_csharp_inputs(self):
        fields=self.csharp_fields
        for name,value in zip(('source','contract'),self.analysis_payload):
            fields[name].configure(state='normal');fields[name].delete('1.0','end');fields[name].insert('1.0',value);fields[name].configure(state='disabled')
        fields['button'].state(['disabled']);fields['edit'].state(['disabled'])
    def edit_csharp(self):
        if self.job_operation=='analyze':return
        self.analysis_payload=None;self.analysis_result=None;self.analysis_archive=None
        fields=self.csharp_fields
        for name in ('source','contract'):fields[name].configure(state='normal')
        fields['button'].state(['!disabled']);fields['edit'].state(['disabled'])
        self.put(fields['summary'],'Entrées modifiables. Lance une nouvelle analyse pour obtenir un résultat correspondant. Les anciens résultats restent archivés.')
        self.put(fields['maths'],'Aucune analyse des nouvelles entrées.');self.put(fields['raw'],'Aucune analyse des nouvelles entrées.')
    def analyze_csharp(self):
        if self.job:return
        fields=self.csharp_fields;source=fields['source'].get('1.0','end-1c');contract=fields['contract'].get('1.0','end-1c')
        if len(source.encode('utf-8'))>1_000_000 or len(contract.encode('utf-8'))>1_000_000:
            self.put(fields['summary'],'Source ou contrat trop volumineux (maximum 1 Mo chacun).');return
        self.analysis_payload=(source,contract);self.analysis_result=None;self.lock_csharp_inputs();self.put(fields['summary'],'Analyse en cours… Les entrées affichées sont celles transmises au vérificateur.')
        self.dispatch('analyze',source=source,contract=contract)
    def analysis_done(self,result):
        try:path=self.store.archive_analysis(*(self.analysis_payload or ('','')),result)
        except Exception as exc:path='Archivage impossible : '+str(exc)
        self.analysis_result=result;self.analysis_archive=path
        self.message.set('Analyse C# terminée : '+str(result.get('verdict','INCONCLUSIF'))+'.')
        if not self.csharp_window or not self.csharp_window.winfo_exists():return
        self.show_analysis()
    def show_analysis(self):
        fields=self.csharp_fields;result=self.analysis_result
        if self.analysis_payload:self.lock_csharp_inputs()
        fields['edit'].state(['!disabled'])
        self.put(fields['summary'],str(result.get('verdict','INCONCLUSIF'))+'\n\n'+str(result.get('reason',''))+'\n\nLes entrées affichées sont exactement celles de ce résultat. Utilise « Modifier les entrées » pour une nouvelle analyse.\n\nArchive : '+str(self.analysis_archive))
        maths=result.get('mathematics','Aucune traduction disponible.');self.put(fields['maths'],maths if isinstance(maths,str) else json.dumps(maths,ensure_ascii=False,indent=2))
        self.put(fields['raw'],json.dumps(result,ensure_ascii=False,indent=2))

    def open_architecture(self):
        win=tk.Toplevel(self.root);win.title('Architecture et limites');win.geometry('900x700')
        book=ttk.Notebook(win);book.pack(fill='both',expand=True)
        for title,path in [('Architecture complète',ui_backend.BASE.parent/'ARCHITECTURE_CHANTIER_2026-11-15/architecture.txt'),('Contrat de livraison',ui_backend.BASE/'CONTRAT_EQUIPE.txt'),('Guide',ui_backend.BASE/'GUIDE.txt')]:
            text=self.text_tab(book,title)
            try:value=path.read_text(encoding='utf-8')
            except OSError as exc:value='Document indisponible : '+str(exc)
            self.put(text,value)

    def close(self):
        self.remaining=0;self.closing=True
        if self.job:self.message.set('Fermeture après la commande en cours ; aucune étape supplémentaire.');return
        self.finish_close()
    def finish_close(self):
        try:self.store.save_preferences({'mission_id':self.mission_id,'selected':self.selected,'section':self.section,'zoom':self.zoom,'ox':self.ox,'oy':self.oy})
        except OSError:pass
        self.closed=True;self.pool.shutdown(wait=False,cancel_futures=True);self.root.destroy()


def main():
    root=tk.Tk();LabApp(root);root.mainloop()


if __name__=='__main__':main()
