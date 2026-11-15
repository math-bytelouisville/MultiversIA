"""Local live map. Panning/zooming changes presentation, never the experiment."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import backend

BG='#101923'; PANEL='#172431'; INK='#edf4fa'; MUTED='#a9bdcb'; LINE='#3c566a'
TEAL='#61cfc2'; AMBER='#f2c16e'; RED='#fa9393'; BLUE='#9bbfff'
CRASHES={'Aucune':None,'Avant intention':'before_intent','Après intention':'after_intent',
         'Après envoi':'after_dispatch','Après effet':'after_effect'}


class MapApp:
    def __init__(self, root, store=None, auto_poll=True):
        self.root=root; self.store=store or backend.Store(); self.pool=ThreadPoolExecutor(max_workers=2)
        self.sources=[]; self.cache={}; self.pending=None; self.command_future=None
        self.closed=False; self.closing=False; self.last_scan='Jamais'; self.generation=0
        prefs=self.store.preferences();self.needs_overview=not bool(prefs)
        self.source_id=prefs.get('source'); self.action_id=prefs.get('action');self.proof_id=prefs.get('proof')
        self.zoom=self.number(prefs.get('zoom'),.9,.25,2.8)
        self.ox=self.number(prefs.get('ox'),420,-100000,100000); self.oy=self.number(prefs.get('oy'),80,-100000,100000)
        self.page=0; self.selected=None; self.items={}; self.drag=None; self.arch_window=None
        root.title('MultiversIA — Carte de suivi'); root.geometry('1280x820'); root.minsize(900,620); root.configure(bg=BG)
        style=ttk.Style(root); style.theme_use('clam')
        style.configure('TFrame',background=BG);style.configure('TLabel',background=BG,foreground=INK,font=('Segoe UI',10))
        style.configure('Sub.TLabel',foreground=MUTED);style.configure('Title.TLabel',font=('Segoe UI',18,'bold'))
        style.configure('TButton',font=('Segoe UI',10),padding=(10,7))
        style.configure('Treeview',rowheight=27,font=('Segoe UI',9))
        outer=ttk.Frame(root,padding=14);outer.pack(fill='both',expand=True)
        head=ttk.Frame(outer);head.pack(fill='x',pady=(0,10))
        ttk.Label(head,text='MultiversIA',style='Title.TLabel').pack(side='left')
        ttk.Label(head,text='  Carte de suivi · simulateur local',style='Sub.TLabel').pack(side='left')
        ttk.Button(head,text='Architecture',command=self.architecture).pack(side='right')
        self.new_button=ttk.Button(head,text='+ Nouvelle mission',command=self.new_mission);self.new_button.pack(side='right',padx=8)
        tools=ttk.Frame(outer);tools.pack(fill='x',pady=(0,10))
        ttk.Button(tools,text='Vue globale',command=self.overview).pack(side='left')
        ttk.Button(tools,text='Voir la mission',command=self.focus_mission).pack(side='left',padx=5)
        ttk.Button(tools,text='−',command=lambda:self.zoom_at(.8)).pack(side='left')
        ttk.Button(tools,text='+',command=lambda:self.zoom_at(1.25)).pack(side='left',padx=5)
        self.scale_label=tk.StringVar();ttk.Label(tools,textvariable=self.scale_label,style='Sub.TLabel').pack(side='left',padx=5)
        ttk.Label(tools,text='Molette : zoom · glisser : déplacer',style='Sub.TLabel').pack(side='right')
        split=ttk.Panedwindow(outer,orient='horizontal');split.pack(fill='both',expand=True)
        map_frame=ttk.Frame(split);split.add(map_frame,weight=3)
        self.canvas=tk.Canvas(map_frame,bg=BG,highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.draw());self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<ButtonPress-1>',self.press);self.canvas.bind('<B1-Motion>',self.motion)
        self.canvas.bind('<ButtonRelease-1>',self.release);self.canvas.bind('<Double-Button-1>',self.explore)
        pagebar=ttk.Frame(map_frame);pagebar.pack(fill='x',pady=5)
        ttk.Button(pagebar,text='← Plus récentes',command=lambda:self.change_page(-1)).pack(side='left')
        self.page_label=tk.StringVar();ttk.Label(pagebar,textvariable=self.page_label,style='Sub.TLabel').pack(side='left',padx=8)
        ttk.Button(pagebar,text='Plus anciennes →',command=lambda:self.change_page(1)).pack(side='right')
        self.side=ttk.Frame(split,width=365,padding=(12,0,0,0));split.add(self.side,weight=1)
        self.detail_title=tk.StringVar(value='Bienvenue');ttk.Label(self.side,textvariable=self.detail_title,font=('Segoe UI',14,'bold'),wraplength=335).pack(anchor='w',pady=(0,8))
        self.tabs=ttk.Notebook(self.side);self.tabs.pack(fill='both',expand=True)
        self.summary=self.text_tab('Résumé');self.events=self.text_tab('Journal');self.raw=self.text_tab('Données brutes')
        self.actions_frame=ttk.Frame(self.side);self.actions_frame.pack(fill='x',pady=(8,0))
        self.run_button=ttk.Button(self.actions_frame,text='Nouvelle expérience',command=self.experiment_dialog);self.run_button.pack(fill='x',pady=3)
        self.proof_button=ttk.Button(self.actions_frame,text='Voir le parcours et les preuves',command=self.focus_proofs);self.proof_button.pack(fill='x',pady=3)
        self.resume_button=ttk.Button(self.actions_frame,text='Rapprocher / reprendre',command=self.resume);self.resume_button.pack(fill='x',pady=3)
        self.export_button=ttk.Button(self.actions_frame,text='Exporter les preuves de la mission',command=self.export);self.export_button.pack(fill='x',pady=3)
        self.message=tk.StringVar(value='Lecture des journaux…');self.message_label=ttk.Label(outer,textvariable=self.message,wraplength=1180);self.message_label.pack(fill='x',pady=(10,2))
        outer.bind('<Configure>',lambda e:self.message_label.configure(wraplength=max(400,e.width-35)))
        self.freshness=tk.StringVar();ttk.Label(outer,textvariable=self.freshness,style='Sub.TLabel').pack(fill='x')
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.bind('<Control-plus>',lambda e:self.zoom_at(1.25));root.bind('<Control-minus>',lambda e:self.zoom_at(.8))
        self.render_detail(); self.draw()
        if auto_poll:self.tick()

    @staticmethod
    def number(value,default,low,high):
        try:
            n=float(value)
            return max(low,min(high,n)) if math.isfinite(n) else default
        except (TypeError,ValueError):return default

    def text_tab(self,name):
        frame=ttk.Frame(self.tabs);self.tabs.add(frame,text=name)
        box=tk.Text(frame,bg=PANEL,fg=INK,insertbackground=INK,wrap='word',font=('Segoe UI',10),relief='flat',padx=12,pady=12,width=35,height=10)
        scroll=ttk.Scrollbar(frame,command=box.yview);scroll.pack(side='right',fill='y');box.pack(fill='both',expand=True);box.configure(yscrollcommand=scroll.set,state='disabled')
        return box

    def text(self,box,value):
        old=box.yview()[0];box.configure(state='normal');box.delete('1.0','end');box.insert('1.0',value);box.configure(state='disabled');box.yview_moveto(old)

    def current(self):return next((s for s in self.sources if s['id']==self.source_id),None)
    def state(self):return self.cache.get(self.source_id,{})
    def action(self):return next((a for a in self.state().get('actions',[]) if a['action_id']==self.action_id),None)

    def ingest(self,sources):
        self.sources=sources
        for s in sources:
            if s.get('snapshot') is not None:self.cache[s['id']]=s['snapshot']
        if not any(s['id']==self.source_id for s in sources):
            self.source_id=sources[-1]['id'] if sources else None;self.action_id=None
        self.last_scan=datetime.now().strftime('%H:%M:%S')
        if self.message.get()=='Lecture des journaux…':self.message.set('Suivi actif. Sélectionne une mission ou crée une nouvelle mission simulée.')
        self.generation+=1
        if self.needs_overview:self.needs_overview=False;self.overview()
        self.draw();self.render_detail()

    def tick(self):
        if self.closed:return
        if self.command_future and self.command_future.done():
            future=self.command_future;self.command_future=None
            try:
                result=future.result()
                if isinstance(result,str) and result.startswith('M-'):
                    self.source_id=result;self.action_id=None;self.page=0;self.selected='mission:'+result
                    self.focus_mission()
                elif isinstance(result,dict) and result.get('action_id') and self.source_id==getattr(self,'command_source',None):
                    self.action_id=result['action_id'];self.selected='action:'+self.action_id;self.proof_id=None
                self.message.set('Commande terminée ; lecture des preuves…' if not isinstance(result,dict) or not result.get('crashed') else 'Panne simulée conservée. Utilise « Rapprocher / reprendre » sur l’expérience.')
                if isinstance(result,dict) and 'export' in result:
                    self.message.set('Preuves exportées : '+result['export'])
            except Exception as exc:self.message.set('Commande arrêtée : '+str(exc))
            self.pending=None if self.pending is None or self.pending.done() else self.pending
            self.render_detail()
        if self.pending and self.pending.done():
            future=self.pending;self.pending=None
            try:self.ingest(future.result())
            except Exception as exc:
                self.message.set('Suivi indisponible : '+str(exc))
                for s in self.sources:s['error']=str(exc)
                self.render_detail();self.draw()
        if self.closing and not self.command_future:
            self.finish_close();return
        if self.pending is None and not self.closing:self.pending=self.pool.submit(self.store.scan)
        self.root.after(1200,self.tick)

    def dispatch(self,fn,*args,**kwargs):
        if self.command_future or self.closing:return
        self.command_source=args[0] if args and fn==self.store.command else None
        self.command_future=self.pool.submit(fn,*args,**kwargs)
        self.message.set('Commande en cours. La carte reste consultable.');self.render_detail()

    def new_mission(self):
        name=simpledialog.askstring('Nouvelle mission','Nom de la mission simulée :',initialvalue='Expériences lampe',parent=self.root)
        if name is not None:self.dispatch(self.store.create,name)

    def experiment_dialog(self):
        if not self.can_write():return
        mission_id=self.source_id
        win=tk.Toplevel(self.root);win.title('Nouvelle expérience · lampe simulée');win.resizable(False,False);win.transient(self.root)
        frame=ttk.Frame(win,padding=20);frame.pack()
        ttk.Label(frame,text='Prédire avant de mesurer',font=('Segoe UI',14,'bold')).pack(anchor='w',pady=(0,10))
        a=tk.BooleanVar(value=True);b=tk.BooleanVar(value=False)
        ttk.Checkbutton(frame,text='Interrupteur A activé',variable=a).pack(anchor='w',pady=4)
        ttk.Checkbutton(frame,text='Interrupteur B activé',variable=b).pack(anchor='w',pady=4)
        ttk.Label(frame,text='Ta prédiction :').pack(anchor='w',pady=(10,4))
        pred=ttk.Combobox(frame,values=['Lampe allumée','Lampe éteinte'],state='readonly',width=30);pred.current(0);pred.pack(fill='x')
        ttk.Label(frame,text='Panne simulée (facultatif) :').pack(anchor='w',pady=(10,4))
        crash=ttk.Combobox(frame,values=list(CRASHES),state='readonly',width=30);crash.current(0);crash.pack(fill='x')
        def send():
            values=dict(a=a.get(),b=b.get(),prediction=pred.current()==0,crash=CRASHES[crash.get()]);win.destroy()
            self.action_id=None;self.page=0;self.dispatch(self.store.command,mission_id,'run',**values)
        ttk.Button(frame,text='Enregistrer et lancer',command=send).pack(fill='x',pady=(16,0))

    def can_write(self):
        s=self.current();return bool(s and not s['readonly'] and not s.get('error') and not self.command_future and not self.closing)

    def resume(self):
        if self.can_write() and self.action():self.dispatch(self.store.command,self.source_id,'resume',action_id=self.action_id)

    def export(self):
        if self.current() and not self.current().get('error'):
            # Fixed source identity, even if navigation changes while working.
            key=self.source_id
            self.dispatch(lambda:{'export':self.store.export(key)})

    def save_view(self):
        self.store.save_preferences({'source':self.source_id,'action':self.action_id,'proof':self.proof_id,'zoom':self.zoom,'ox':self.ox,'oy':self.oy})

    def close(self):
        if self.command_future:
            self.closing=True;self.message.set('Fermeture après la commande en cours ; conservation des preuves.');return
        self.finish_close()

    def finish_close(self):
        try:self.save_view()
        except OSError:pass
        self.closed=True;self.pool.shutdown(wait=False,cancel_futures=True);self.root.destroy()

    def zoom_at(self,factor,x=None,y=None):
        x=self.canvas.winfo_width()/2 if x is None else x;y=self.canvas.winfo_height()/2 if y is None else y
        new=max(.25,min(2.8,self.zoom*factor));ratio=new/self.zoom
        self.ox=x-(x-self.ox)*ratio;self.oy=y-(y-self.oy)*ratio;self.zoom=new;self.draw()

    def wheel(self,e):self.zoom_at(1.16 if e.delta>0 else 1/1.16,e.x,e.y)
    def press(self,e):self.drag=(e.x,e.y,self.ox,self.oy,False)
    def motion(self,e):
        if self.drag:
            x,y,ox,oy,_=self.drag;self.ox=ox+e.x-x;self.oy=oy+e.y-y
            self.drag=(x,y,ox,oy,abs(e.x-x)+abs(e.y-y)>4);self.draw()
    def release(self,e):
        moved=self.drag and self.drag[4];self.drag=None
        if not moved:self.pick(e.x,e.y)
    def pick(self,x,y):
        for item in reversed(self.canvas.find_overlapping(x,y,x,y)):
            for tag in self.canvas.gettags(item):
                if tag.startswith('node:'):
                    self.select_node(tag[5:]);return
    def explore(self,e):
        self.pick(e.x,e.y)
        if self.action_id:self.focus_action()
        elif self.source_id:self.focus_mission()

    def select_node(self,key):
        self.selected=key
        if key.startswith('mission:'):
            self.source_id=key[8:];self.action_id=None;self.proof_id=None;self.page=0
        elif key.startswith('action:'):self.action_id=key[7:];self.proof_id=None
        elif key.startswith('proof:'):self.proof_id=int(key[6:])
        elif key in {'global','planned'}:self.action_id=None;self.proof_id=None
        self.draw();self.render_detail()

    def overview(self):
        self.zoom=min(1.0,max(.25,(self.canvas.winfo_width()-80)/max(800,len(self.sources)*270)))
        self.ox=self.canvas.winfo_width()/2;self.oy=210*self.zoom;self.selected='global';self.action_id=None;self.draw();self.render_detail()

    def focus_mission(self):
        self.zoom=.85;self.ox=self.canvas.winfo_width()/2;self.oy=-100*self.zoom+25;self.draw()
    def focus_action(self):
        p=self.items.get('action:'+str(self.action_id))
        if p:
            self.zoom=1.5;self.ox=self.canvas.winfo_width()/2-p[0]*self.zoom;self.oy=self.canvas.winfo_height()/2-p[1]*self.zoom;self.draw()
    def proof_start(self):
        count=min(12,max(0,len(self.state().get('actions',[]))-self.page*12))
        return 560+math.ceil(count/3)*170+100
    def focus_proofs(self):
        if self.action():
            self.zoom=1.35;self.ox=self.canvas.winfo_width()/2;self.oy=90-self.proof_start()*self.zoom;self.draw()
    def change_page(self,delta):
        count=len(self.state().get('actions',[]));self.page=max(0,min(max(0,(count-1)//12),self.page+delta));self.draw()

    def node(self,key,x,y,title,subtitle='',color=BLUE,extra=''):
        w,h=240,94; sx=self.ox+x*self.zoom;sy=self.oy+y*self.zoom;sw=w*self.zoom;sh=h*self.zoom
        self.items[key]=(x,y)
        selected=(key=='action:'+str(self.action_id)) or (key=='proof:'+str(self.proof_id)) or (not self.action_id and key=='mission:'+str(self.source_id))
        tag='node:'+key
        self.canvas.create_rectangle(sx-sw/2,sy-sh/2,sx+sw/2,sy+sh/2,fill=PANEL,outline=color if selected else LINE,width=2 if selected else 1,tags=(tag,))
        size=max(9,min(17,round(12*self.zoom)))
        if self.zoom<.45:
            label=('E '+title.split()[-1]) if key.startswith('action:') else ('MIA' if key=='global' else title[:7]+'…')
        else:
            limit=max(16,int((sw-14)/(size*.7))*2)
            label=title if len(title)<=limit else title[:limit-1]+'…'
        self.canvas.create_text(sx,sy-(16*self.zoom if self.zoom>=.65 else 0),text=label,width=max(45,sw-14),fill=INK,font=('Segoe UI',size,'bold'),tags=(tag,))
        if self.zoom>=.65:
            self.canvas.create_text(sx,sy+10*self.zoom,text=subtitle,width=sw-16,fill=color,font=('Segoe UI',max(9,min(13,round(10*self.zoom)))),tags=(tag,))
        if self.zoom>=1.25 and extra:
            self.canvas.create_text(sx,sy+31*self.zoom,text=extra,width=sw-16,fill=MUTED,font=('Segoe UI',10),tags=(tag,))

    def edge(self,x1,y1,x2,y2):
        x1=self.ox+x1*self.zoom;y1=self.oy+y1*self.zoom;x2=self.ox+x2*self.zoom;y2=self.oy+y2*self.zoom
        mid=(y1+y2)/2
        self.canvas.create_line(x1,y1,x1,mid,x2,mid,x2,y2,fill=LINE,width=1.5,arrow='last')

    def draw(self):
        if self.closed:return
        self.canvas.delete('all');self.items={}
        self.scale_label.set(f'{round(self.zoom*100)} %')
        self.node('global',0,0,'MultiversIA','Activité observée · pas de scénario inventé',TEAL)
        n=len(self.sources)
        for i,s in enumerate(self.sources):
            x=(i-(n-1)/2)*270;y=185
            snapshot=self.cache.get(s['id'],{});count=len(snapshot.get('actions',[]))
            self.edge(0,47,x,y-47)
            subtitle='Lecture non vérifiée' if s.get('error') else f'{count} expérience(s)'+(' · lecture seule' if s['readonly'] else ' · simulation')
            self.node('mission:'+s['id'],x,y,s['title'],subtitle,RED if s.get('error') else TEAL)
            if s['id']==self.source_id:
                self.edge(x,y+47,0,313)
        self.node('planned',0,-165,'Modules à construire','Diagnostic · mémoire · IA · Lean non raccordés',MUTED)
        if not self.sources:self.page_label.set('Crée une mission pour démarrer.');return
        current=self.current()
        if current:
            self.node('branch',0,360,'Mission sélectionnée',current['title'],TEAL)
            actions=list(reversed(self.state().get('actions',[])))
            self.page=min(self.page,max(0,(len(actions)-1)//12))
            subset=actions[self.page*12:(self.page+1)*12]
            self.page_label.set(f'Expériences {self.page*12+1 if actions else 0}–{min(len(actions),(self.page+1)*12)} / {len(actions)}')
            if not subset:self.node('empty',0,560,'Aucune expérience','Nouvelle expérience dans le panneau à droite',MUTED)
            for index,a in enumerate(subset):
                x=(index%3-1)*270;y=560+(index//3)*170
                rail=-440
                self.canvas.create_line(self.ox,self.oy+407*self.zoom,self.ox+rail*self.zoom,self.oy+430*self.zoom,
                    self.ox+rail*self.zoom,self.oy+(y-65)*self.zoom,self.ox+x*self.zoom,self.oy+(y-65)*self.zoom,
                    self.ox+x*self.zoom,self.oy+(y-47)*self.zoom,fill=LINE,width=1.5,arrow='last')
                d=a.get('detail',{});color=AMBER
                if a['state']=='TERMINÉE':color=TEAL if d.get('prediction_correct') else AMBER
                elif a['state'] in {'REFUSÉE','ÉCHEC_DOCUMENTÉ'}:color=RED
                state='Écart de prédiction' if a['state']=='TERMINÉE' and not d.get('prediction_correct') else a['state']
                if current.get('error'):state='Ancien état · lecture non vérifiée';color=RED
                r=a['request'];self.node('action:'+a['action_id'],x,y,'Expérience '+str(len(actions)-(self.page*12+index)),state,color,f'A={int(r["a"])} · B={int(r["b"])} · prédiction={int(r["prediction"])}')
            if self.action_id and self.zoom>=1.15:
                events=[e for e in self.state().get('events',[]) if e['action_id']==self.action_id]
                names={'ACTION_PREPARED':'Prédiction enregistrée','INTENT_RECORDED':'Intention conservée','DISPATCH_RECORDED':'Envoi enregistré','OBSERVATION_RECORDED':'Observation vérifiée','RESULT_UNCERTAIN':'Résultat à vérifier','FAILURE_RECORDED':'Échec documenté','REFUSED':'Action refusée'}
                y0=self.proof_start();previous=self.items.get('action:'+self.action_id)
                for index,event in enumerate(events):
                    y=y0+index*145
                    if previous:self.edge(previous[0],previous[1]+47,0,y-47)
                    self.node('proof:'+str(event['seq']),0,y,names.get(event['type'],event['type']),'Événement #'+str(event['seq']),BLUE,event['at'][11:19]+' UTC')
                    previous=(0,y)

    def render_detail(self):
        s=self.current();state=self.state();a=self.action();busy=bool(self.command_future)
        self.new_button.state(['disabled'] if busy else ['!disabled'])
        self.run_button.state(['!disabled'] if self.can_write() else ['disabled'])
        self.proof_button.state(['!disabled'] if a else ['disabled'])
        self.resume_button.state(['!disabled'] if self.can_write() and a and a['state'] not in backend.core.TERMINAL else ['disabled'])
        self.export_button.state(['!disabled'] if s and not s.get('error') and not busy else ['disabled'])
        errors=sum(bool(x.get('error')) for x in self.sources)
        self.freshness.set(f'Lecture périodique ~1,2 s · dernier retour {self.last_scan} · {errors} source(s) non vérifiée(s)')
        warning=('LECTURE NON VÉRIFIÉE — données précédentes conservées, potentiellement périmées.\n'+s['error']+'\n\n') if s and s.get('error') else ''
        if self.selected=='planned':
            self.detail_title.set('Architecture en chantier')
            summary='PRÉVU, NON RACCORDÉ\n\n• Diagnostic causal automatique\n• Mémoire de connaissances révisables\n• Modèles locaux et critique Aigle\n• Vérificateur Lean\n\nLa carte ne leur attribue aucune activité.\n\nLe bouton Architecture donne accès aux 14 sections complètes et à la revue de cette interface.'
            events=[];raw={}
        elif a:
            self.detail_title.set('Expérience sélectionnée')
            r=a['request'];d=a.get('detail',{})
            lamp=('allumée' if d.get('lamp_on') else 'éteinte') if 'lamp_on' in d else 'inconnue'
            summary=warning+f'ÉXÉCUTION\n{a["state"]}\n\nOBSERVATION\n{backend.observation_label(a)}\n\nPRÉDICTION ET RÉSULTAT\nA={int(r["a"])} · B={int(r["b"])}\nPrédiction : '+('allumée' if r['prediction'] else 'éteinte')+f'\nLampe observée : {lamp}\n\n'+backend.diagnosis(a)+'\n\n'+d.get('explanation','')+'\n\nCONNAISSANCE\nAucune règle automatiquement confirmée ou révisée.'
            events=[e for e in state.get('events',[]) if e['action_id']==self.action_id];raw=a
            proof=next((e for e in events if e['seq']==self.proof_id),None)
            if proof:
                self.detail_title.set('Preuve · événement #'+str(proof['seq']))
                summary=warning+proof['type']+'\n\n'+proof['at']+'\n\nSOURCE\nJournal de la mission sélectionnée.\n\n'+json.dumps(proof['data'],ensure_ascii=False,indent=2)+'\n\nEMPREINTE\n'+proof['sha256']+'\n\nLes données brutes conservent l’événement complet et son lien avec le précédent.'
                raw=proof
        elif s and self.selected!='global':
            self.detail_title.set(s['title']);actions=state.get('actions',[])
            uncertain=sum(a['state'] not in backend.core.TERMINAL for a in actions)
            wrong=sum(a.get('detail',{}).get('prediction_correct') is False for a in actions)
            summary=warning+('LECTURE SEULE — historique conservé\n\n' if s['readonly'] else 'MISSION LOCALE — simulation lampe\n\n')+f'{len(actions)} expérience(s)\n{state.get("effect_count",0)} effet(s) simulé(s)\n{uncertain} résultat(s) à examiner/reprendre\n{wrong} écart(s) de prédiction\n\n'+('Intégrité vérifiée lors de la lecture.\n\n' if not s.get('error') else '')+'Double-clique sur la mission pour explorer ses expériences. Les boutons sous ce panneau lancent des commandes explicites.'
            events=state.get('events',[]);raw=state
        else:
            self.detail_title.set('Vue d’ensemble')
            summary='UNE CARTE, PLUSIEURS ÉCHELLES\n\nMolette : zoom libre autour du pointeur.\nGlisser le fond : se déplacer.\nCliquer : sélectionner.\nDouble-cliquer : explorer.\n\nLes journaux sont relus périodiquement. La carte ne recentre pas ton exploration.\n\nCrée une mission pour réaliser des expériences simulées. Le cycle existant reste en lecture seule.\n\nLes modules scientifiques non raccordés restent indiqués comme prévus.'
            events=[];raw={}
        self.text(self.summary,summary)
        self.text(self.events,'\n\n'.join(f'#{e["seq"]} · {e["type"]}\n{e["at"]}\n'+json.dumps(e['data'],ensure_ascii=False,indent=2) for e in events) or 'Aucun événement pour cette sélection.')
        self.text(self.raw,json.dumps(raw,ensure_ascii=False,indent=2) if raw else 'Sélectionne une mission ou une expérience pour consulter ses données complètes.')

    def architecture(self):
        if self.arch_window and self.arch_window.winfo_exists():self.arch_window.lift();return
        win=tk.Toplevel(self.root);self.arch_window=win;win.title('Architecture complète · MultiversIA');win.geometry('930x700')
        frame=ttk.Frame(win,padding=15);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Architecture complète et revue de la carte',style='Title.TLabel').pack(anchor='w',pady=(0,10))
        text=tk.Text(frame,wrap='word',font=('Segoe UI',11),padx=15,pady=15)
        scroll=ttk.Scrollbar(frame,command=text.yview);scroll.pack(side='right',fill='y');text.pack(fill='both',expand=True);text.configure(yscrollcommand=scroll.set)
        source=backend.BASE.parent/'ARCHITECTURE_CHANTIER_2026-11-15/architecture.txt'
        try:content=(backend.BASE/'REVUE_ARCHITECTURE.txt').read_text(encoding='utf-8')+'\n\n'+'═'*50+'\n\n'+source.read_text(encoding='utf-8')
        except OSError as exc:content='Document indisponible : '+str(exc)
        text.insert('1.0',content);text.configure(state='disabled')


def main():
    root=tk.Tk();MapApp(root);root.mainloop()


if __name__=='__main__':main()
