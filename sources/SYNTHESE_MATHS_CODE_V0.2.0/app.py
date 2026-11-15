"""Native, freely zoomable mathematics-to-code workshop. No user code execution."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import tkinter as tk
from tkinter import ttk
import ui_backend

BG='#101923';PANEL='#172431';INK='#edf4fa';MUTED='#b1c4d2';LINE='#415b70';TEAL='#62d4c2';AMBER='#f1c16e'
SOURCE='public static class Example { public static int Compute(int a, int b) { return a >= b ? a : b; } }'
PUBLIC=('schema_version','name','parameters','return_type','precondition','postcondition')
def example(kind='maximum'):
    op='>=' if kind=='maximum' else '<='
    return {'schema_version':'mia-math-spec-1','name':kind,'parameters':[{'name':'a','type':'int'},{'name':'b','type':'int'}],
        'return_type':'int','precondition':'True','postcondition':f'result {op} a and result {op} b and (result == a or result == b)'}
def pretty(value):return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
def number(value,default,lo,hi):
    try:
        value=float(value);return max(lo,min(hi,value)) if math.isfinite(value) else default
    except (ValueError,TypeError):return default

class Workshop:
    def __init__(self,root,store=None,auto_poll=True):
        self.root=root;self.store=store or ui_backend.Store();self.pool=ThreadPoolExecutor(max_workers=2)
        self.job=None;self.operation=None;self.scan=None;self.generation=0;self.scan_generation=0;self.rows=[];self.record=None;self.origin=None
        self.import_window=None;self.import_fields=None;self.import_record=None;self.pending_source=None
        self.closed=False;self.closing=False;self.drag=None;self.selected='maths';self.last_history_error=None
        prefs=self.store.preferences();self.zoom=number(prefs.get('zoom'),.6,.25,2.8);self.ox=number(prefs.get('ox'),400,-100000,100000);self.oy=number(prefs.get('oy'),180,-100000,100000)
        self.origin=prefs.get('draft_origin') if isinstance(prefs.get('draft_origin'),dict) else None
        self.pending_source=prefs.get('import_draft_source') if isinstance(prefs.get('import_draft_source'),str) else None
        root.title('MultiversIA — Atelier maths → code V0.1.0');root.geometry('1420x900');root.minsize(1050,700);root.configure(bg=BG)
        style=ttk.Style(root);style.theme_use('clam');style.configure('TFrame',background=BG);style.configure('TLabel',background=BG,foreground=INK,font=('Segoe UI',10));style.configure('TButton',padding=(8,5));style.configure('Muted.TLabel',foreground=MUTED)
        outer=ttk.Frame(root,padding=14);outer.pack(fill='both',expand=True)
        header=ttk.Frame(outer);header.pack(fill='x',pady=(0,8))
        ttk.Label(header,text='Atelier maths → code',font=('Segoe UI',19,'bold')).pack(side='left')
        ttk.Label(header,text='  Synthèse symbolique bornée · Python / C# / Rust',style='Muted.TLabel').pack(side='left')
        self.import_button=ttk.Button(header,text='Importer une fonction C#',command=self.open_import);self.import_button.pack(side='right')
        nav=ttk.Frame(outer);nav.pack(fill='x',pady=(0,8))
        self.example_box=ttk.Combobox(nav,state='readonly',values=['maximum','minimum'],width=12);self.example_box.set('maximum');self.example_box.pack(side='left');self.example_box.bind('<<ComboboxSelected>>',lambda e:self.new_draft(example(self.example_box.get())))
        self.edit_button=ttk.Button(nav,text='Modifier / nouvel essai',command=self.edit);self.edit_button.pack(side='left',padx=6)
        ttk.Button(nav,text='Vue globale',command=self.overview).pack(side='left');ttk.Button(nav,text='−',command=lambda:self.zoom_at(.8)).pack(side='left',padx=3);ttk.Button(nav,text='+',command=lambda:self.zoom_at(1.25)).pack(side='left')
        self.zoom_label=tk.StringVar();ttk.Label(nav,textvariable=self.zoom_label,style='Muted.TLabel').pack(side='left',padx=5)
        self.history_box=ttk.Combobox(nav,state='readonly',width=37);self.history_box.pack(side='right');self.history_box.bind('<<ComboboxSelected>>',self.select_history)
        split=ttk.Panedwindow(outer,orient='horizontal');split.pack(fill='both',expand=True)
        left=ttk.Frame(split);right=ttk.Frame(split,width=500,padding=(12,0,0,0));split.add(left,weight=3);split.add(right,weight=2)
        self.canvas=tk.Canvas(left,bg=BG,highlightthickness=0);self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.draw());self.canvas.bind('<MouseWheel>',lambda e:self.zoom_at(1.16 if e.delta>0 else 1/1.16,e.x,e.y))
        self.canvas.bind('<ButtonPress-1>',self.press);self.canvas.bind('<B1-Motion>',self.motion);self.canvas.bind('<ButtonRelease-1>',self.release)
        ttk.Label(left,text='Molette : zoom libre · glisser : déplacer · cliquer : comprendre une étape',style='Muted.TLabel',wraplength=650).pack(fill='x',pady=5)
        self.book=ttk.Notebook(right);self.book.pack(fill='both',expand=True)
        self.summary=self.tab('Résumé');self.spec_text=self.tab('Propriété JSON',editable=True);self.maths=self.tab('Mathématiques');self.code=self.tab('Code produit');self.events=self.tab('Journal');self.raw=self.tab('Preuves complètes')
        options=ttk.Frame(right);options.pack(fill='x',pady=7);self.targets={};self.target_widgets=[]
        for language in ('python','csharp','rust'):
            value=tk.BooleanVar(value=True);self.targets[language]=value;widget=ttk.Checkbutton(options,text={'python':'Python','csharp':'C#','rust':'Rust'}[language],variable=value);widget.pack(side='left');self.target_widgets.append(widget)
        self.prove=tk.BooleanVar(value=False);self.proof_widget=ttk.Checkbutton(right,text='Demander aussi une preuve conditionnelle Lean',variable=self.prove);self.proof_widget.pack(anchor='w')
        choose=ttk.Frame(right);choose.pack(fill='x',pady=5);ttk.Label(choose,text='Préférence :').pack(side='left');self.preference=ttk.Combobox(choose,state='readonly',values=['auto','python','csharp','rust'],width=12);self.preference.set('auto');self.preference.pack(side='left',padx=5)
        self.run_button=ttk.Button(right,text='Chercher un programme puis vérifier',command=self.run);self.run_button.pack(fill='x',pady=5)
        self.message=tk.StringVar(value='Prêt. La propriété décrit le résultat souhaité, sans fournir son programme.');ttk.Label(outer,textvariable=self.message,wraplength=1300).pack(fill='x',pady=(9,0))
        self.freshness=tk.StringVar(value='Aucune opération ne redémarre automatiquement.');ttk.Label(outer,textvariable=self.freshness,style='Muted.TLabel').pack(fill='x')
        self.put(self.spec_text,prefs.get('draft_text') if isinstance(prefs.get('draft_text'),str) else pretty(example()),editable=True);self.render();root.protocol('WM_DELETE_WINDOW',self.close)
        if auto_poll:self.tick()

    def tab(self,title,editable=False,book=None):
        book=book or self.book;frame=ttk.Frame(book);book.add(frame,text=title)
        widget=tk.Text(frame,wrap='word',bg=PANEL,fg=INK,insertbackground=INK,font=('Consolas',10),padx=12,pady=12,width=45,height=12,relief='flat')
        scroll=ttk.Scrollbar(frame,command=widget.yview);scroll.pack(side='right',fill='y');widget.pack(fill='both',expand=True);widget.configure(yscrollcommand=scroll.set,state='normal' if editable else 'disabled');return widget
    @staticmethod
    def put(widget,text,editable=False):
        at=widget.yview()[0];widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',text);widget.configure(state='normal' if editable else 'disabled');widget.yview_moveto(at)
    def draft(self):
        raw=self.spec_text.get('1.0','end-1c')
        if len(raw.encode('utf-8'))>65536:raise ValueError('La propriété JSON dépasse 64 Kio.')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('La propriété doit être un objet JSON.')
        return value
    def new_draft(self,spec,origin=None):
        if self.job:return
        self.record=None;self.origin=origin;self.put(self.spec_text,pretty(spec),editable=True);self.message.set('Nouvelle propriété prête à modifier. Les anciens essais restent archivés.');self.render()
    def edit(self):
        if self.job:return
        self.record=None;self.spec_text.configure(state='normal');self.message.set('Entrées déverrouillées. Les résultats précédents restent archivés ; un nouvel essai sera distinct.');self.render()
    def run(self):
        if self.job or self.record is not None or self.closing:return
        try:spec=self.draft()
        except (ValueError,TypeError) as exc:self.message.set('Propriété à corriger : '+str(exc));self.book.select(self.spec_text.master);return
        targets=[language for language,value in self.targets.items() if value.get()]
        if not targets:self.message.set('Choisis au moins un langage cible.');return
        self.dispatch('run',spec=spec,targets=targets,preference=self.preference.get(),prove=self.prove.get(),import_origin=self.origin,spec_text_exact=self.spec_text.get('1.0','end-1c'))
    def dispatch(self,operation,**parameters):
        if self.job or self.closing:return False
        self.operation=operation;self.job=self.pool.submit(self.store.command,operation,**parameters)
        self.message.set(('Import C#' if operation=='import' else 'Synthèse et vérifications')+' en cours. Les sous-étapes ne sont pas encore observées.');self.render();return True
    def apply_record(self,record):
        request=record.get('request',{});parameters=request.get('parameters',{})
        if record.get('operation')=='run':
            self.record=record
            self.origin=parameters.get('import_origin');self.put(self.spec_text,parameters.get('spec_text_exact',pretty(parameters.get('spec',{}))))
            for language,value in self.targets.items():value.set(language in parameters.get('targets',[]))
            self.preference.set(parameters.get('preference','auto'));self.prove.set(bool(parameters.get('prove')))
        else:
            self.import_record=record;self.pending_source=parameters.get('source','')
        result=record.get('result',{});self.message.set(('Import' if record.get('operation')=='import' else 'Essai')+' terminé : '+str(result.get('status','NON VÉRIFIÉ'))+'. Preuves : '+record.get('archive','—'))
        self.render()
        if record.get('operation')=='import' and self.import_window and self.import_window.winfo_exists():self.show_import()
    def tick(self):
        if self.closed:return
        if self.job and self.job.done():
            job=self.job;self.job=None;operation=self.operation;self.operation=None;self.generation+=1
            try:self.apply_record(job.result())
            except Exception as exc:self.message.set('Opération arrêtée : '+str(exc));self.render()
        if self.scan and self.scan.done():
            scan=self.scan;self.scan=None
            try:
                rows=scan.result()
                if self.scan_generation!=self.generation:rows=None
                if rows is not None:self.apply_history(rows)
            except Exception as exc:self.last_history_error=str(exc);self.freshness.set('Historique non vérifié : '+str(exc))
        if self.closing and not self.job:self.finish_close();return
        if self.scan is None and not self.closing:self.scan_generation=self.generation;self.scan=self.pool.submit(self.store.history)
        self.root.after(700,self.tick)
    def apply_history(self,rows):
        self.rows=rows;self.history_box['values']=[r.get('request',{}).get('at','?')[:19]+' · '+str(r.get('operation','archive'))+' · '+('NON VÉRIFIÉ' if r.get('error') else str(r.get('result',{}).get('status','INCOMPLET'))) for r in self.rows]
        for attr in ('record','import_record'):
            current=getattr(self,attr)
            if current:
                changed=next((r for r in self.rows if r['request_id']==current.get('request_id') and r.get('error')),None)
                if changed:
                    changed=dict(changed,request=current.get('request',{}));setattr(self,attr,changed);self.message.set('Archive sélectionnée non vérifiée : '+changed['error']);self.render();self.show_import()
        self.last_history_error=None;self.freshness.set(f'{len(self.rows)} archive(s) accessible(s) · lecture seule · aucun rejeu automatique.')
    def select_history(self,_=None):
        if self.job:return
        index=self.history_box.current()
        if not 0<=index<len(self.rows):return
        row=self.rows[index]
        if row.get('error'):
            self.record=row;self.message.set('Archive non vérifiée : '+row['error']);self.render();return
        self.apply_record(row)
        if row.get('operation')=='import':self.open_import()

    def render(self):
        busy=bool(self.job or self.closing);locked=busy or self.record is not None
        self.spec_text.configure(state='disabled' if locked else 'normal')
        for widget in self.target_widgets+[self.proof_widget]:widget.state(['disabled'] if locked else ['!disabled'])
        self.preference.configure(state='disabled' if locked else 'readonly');self.example_box.configure(state='disabled' if busy else 'readonly')
        self.history_box.configure(state='disabled' if busy else 'readonly')
        self.run_button.state(['disabled'] if locked else ['!disabled']);self.edit_button.state(['disabled'] if busy else ['!disabled']);self.import_button.state(['disabled'] if busy else ['!disabled'])
        result=(self.record or {}).get('result',{});parameters=(self.record or {}).get('request',{}).get('parameters',{})
        if self.record and self.record.get('error'):
            summary='ARCHIVE NON VÉRIFIÉE\n'+self.record['error']+'\n\nAucun résultat présenté comme accepté.'
        elif result:
            summary=str(result.get('status','INCONCLUSIVE'))+'\n\n'+str(result.get('reason',''))
            if result.get('selected_language'):summary+='\n\nLangage retenu : '+result['selected_language']+'\n'+str(result.get('selection_reason',''))
            if self.selected=='source':summary+='\n\nOrigine importée : '+('conservée avec la demande.' if self.origin else 'aucune ; propriété écrite directement.')
            elif self.selected=='synthesis':summary+='\n\nSYNTHÈSE\n'+str((result.get('synthesis') or {}).get('status','—'))+'\n'+str((result.get('synthesis') or {}).get('reason',''))
            elif self.selected in {'python','csharp','rust','verification'}:
                for item in result.get('verification',[]):
                    if self.selected=='verification' or item.get('language')==self.selected:summary+='\n\n'+str(item.get('language','Vérification'))+' : '+str(item.get('status','—'))+'\n'+str(item.get('reason',''))+'\nCompilation : '+str(item.get('compile_status','—'))+' · exécution : '+str(item.get('execution_status','—'))
            elif self.selected=='proof':
                proof=result.get('proof');summary+='\n\nPREUVE FORMELLE\n'+(str(proof.get('status','—'))+'\n'+str(proof.get('reason','')) if isinstance(proof,dict) else 'Non demandée ou non obtenue.')
            summary+='\n\nLIMITES\n'+'\n'.join(str(x) for x in result.get('limitations',[]))
        else:
            summary='DÉCRIRE LE RÉSULTAT SOUHAITÉ\n\nDans Propriété JSON, int désigne Int32 signé. precondition décrit les entrées admises ; postcondition relie result aux paramètres.\n\nExemple maximum : le résultat est au moins égal à chaque entrée et égal à l’une d’elles. Aucun programme candidat n’est fourni dans cet exemple.\n\nLes expressions acceptent comparaisons, and/or/not et conditionnelle. Pas de boucle, appel, arithmétique générale ou effet.\n\nUne recherche bornée ne prouve pas une impossibilité si elle échoue. Un programme qui passe les tests finis reste un candidat. Un site entier n’est pas pris en charge.'
        if busy:summary='OPÉRATION EN COURS\nLe processus travaille ; ses sous-étapes ne sont pas encore observées. Aucun résultat nouveau n’est accepté avant retour.\n\n'+summary
        self.put(self.summary,summary)
        spec=result.get('spec') or parameters.get('spec')
        mathematics=result.get('mathematics') or (('Paramètres : '+pretty(spec.get('parameters',[]))+'\n\nPrécondition : '+str(spec.get('precondition',''))+'\n\nPostcondition : '+str(spec.get('postcondition',''))) if isinstance(spec,dict) else 'La propriété éditable se trouve dans Propriété JSON.')
        candidate=(result.get('synthesis') or {}).get('candidate')
        if candidate:mathematics+='\n\nExpression candidate structurée :\n'+pretty(candidate)
        self.put(self.maths,mathematics if isinstance(mathematics,str) else pretty(mathematics))
        code='\n\n'.join('=== '+str(a.get('language','cible'))+' ===\n'+str(a.get('source','Source disponible dans les preuves.')) for a in result.get('artifacts',[]))
        self.put(self.code,code or 'Aucun code accepté pour les entrées affichées.')
        self.put(self.events,'\n\n'.join('#'+str(e.get('seq',''))+' · '+str(e.get('type',''))+' · '+str(e.get('at',''))+'\n'+pretty(e.get('data')) for e in result.get('events',[])) or 'Aucun événement final disponible.')
        self.put(self.raw,pretty(self.record) if self.record else 'Aucun résultat. Chaque demande et chaque résultat seront archivés séparément.');self.draw()

    def draw(self):
        if self.closed:return
        self.canvas.delete('all');self.zoom_label.set(str(round(self.zoom*100))+' %')
        result=(self.record or {}).get('result',{});artifacts={a.get('language'):a for a in result.get('artifacts',[])};checks={v.get('language'):v for v in result.get('verification',[])}
        nodes=[('source',-540,0,'Source C# facultative','Origine liée' if self.origin else 'Import disponible' if self.import_record else 'Sans import'),('maths',-270,0,'Mathématiques','Propriété typée modifiable'),('synthesis',0,0,'Synthèse bornée',str((result.get('synthesis') or {}).get('status','Non lancée'))),('verification',270,0,'Vérifications','Tests finis / compilation'),('proof',540,0,'Preuves','Archives et preuve conditionnelle')]
        for i,(key,x,y,title,subtitle) in enumerate(nodes):
            if i:self.edge((nodes[i-1][1]+110,y),(x-110,y))
        for i,language in enumerate(('python','csharp','rust')):
            checked=checks.get(language,{});status=checked.get('status','Source produite' if language in artifacts else 'Aucun source produit')
            label={'TESTED_FINITE':'Testé sur cas finis','NON_COMPILE':'Source non compilé','INCONCLUSIVE':'Contrôle inconclusif','REFUSED':'Contrôle refusé','FAILED':'Échec du contrôle','COUNTEREXAMPLE':'Contre-exemple trouvé'}.get(status,status)
            x=(i-1)*300;self.edge((0,55),(x,205));nodes.append((language,x,260,{'python':'Python','csharp':'C#','rust':'Rust'}[language],label))
        for key,x,y,title,subtitle in nodes:
            sx=self.ox+x*self.zoom;sy=self.oy+y*self.zoom;w=225*self.zoom;h=105*self.zoom;tag='node:'+key
            self.canvas.create_rectangle(sx-w/2,sy-h/2,sx+w/2,sy+h/2,fill=PANEL,outline=TEAL if key==self.selected else LINE,width=2,tags=(tag,))
            self.canvas.create_text(sx,sy-(14*self.zoom if self.zoom>=.55 else 0),text=title,width=max(50,w-10),fill=INK,font=('Segoe UI',max(9,min(17,round(12*self.zoom))),'bold'),tags=(tag,))
            if self.zoom>=.55:self.canvas.create_text(sx,sy+19*self.zoom,text=subtitle,width=w-12,fill=AMBER,font=('Segoe UI',max(9,min(13,round(10*self.zoom)))),tags=(tag,))
    def edge(self,a,b):self.canvas.create_line(self.ox+a[0]*self.zoom,self.oy+a[1]*self.zoom,self.ox+b[0]*self.zoom,self.oy+b[1]*self.zoom,fill=LINE,arrow='last',width=1.5)
    def overview(self):self.zoom=max(.25,min(.8,(self.canvas.winfo_width()-40)/1350));self.ox=self.canvas.winfo_width()/2;self.oy=160;self.draw()
    def zoom_at(self,factor,x=None,y=None):
        x=self.canvas.winfo_width()/2 if x is None else x;y=self.canvas.winfo_height()/2 if y is None else y
        new=max(.25,min(2.8,self.zoom*factor));ratio=new/self.zoom;self.ox=x-(x-self.ox)*ratio;self.oy=y-(y-self.oy)*ratio;self.zoom=new;self.draw()
    def press(self,event):self.drag=(event.x,event.y,self.ox,self.oy,False)
    def motion(self,event):
        if self.drag:
            x,y,ox,oy,moved=self.drag;self.ox=ox+event.x-x;self.oy=oy+event.y-y;self.drag=(x,y,ox,oy,moved or abs(event.x-x)+abs(event.y-y)>4);self.draw()
    def release(self,event):
        moved=self.drag and self.drag[4];self.drag=None
        if moved:return
        for item in reversed(self.canvas.find_overlapping(event.x,event.y,event.x,event.y)):
            for tag in self.canvas.gettags(item):
                if tag.startswith('node:'):self.selected=tag[5:];self.render();self.book.select(self.summary.master);return

    def open_import(self):
        if self.import_window and self.import_window.winfo_exists():self.import_window.lift();return
        win=tk.Toplevel(self.root);self.import_window=win;win.title('Importer une fonction C# pure');win.geometry('1000x780')
        win.protocol('WM_DELETE_WINDOW',self.close_import)
        frame=ttk.Frame(win,padding=14);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Extraction Roslyn vers une propriété mathématique. Le C# collé n’est jamais exécuté.',wraplength=930).pack(anchor='w',pady=6)
        source=tk.Text(frame,height=9,font=('Consolas',10),wrap='word');source.pack(fill='x');source.insert('1.0',self.pending_source if self.pending_source is not None else SOURCE)
        controls=ttk.Frame(frame);controls.pack(fill='x',pady=8)
        start=ttk.Button(controls,text='Extraire les mathématiques',command=self.start_import);start.pack(side='left')
        edit=ttk.Button(controls,text='Modifier la source',command=self.edit_import);edit.pack(side='left',padx=6)
        load=ttk.Button(controls,text='Charger la propriété dans l’atelier',command=self.load_import);load.pack(side='left')
        book=ttk.Notebook(frame);book.pack(fill='both',expand=True)
        summary=self.tab('Résumé',book=book);maths=self.tab('Mathématiques',book=book);raw=self.tab('Preuves complètes',book=book)
        self.import_fields={'source':source,'start':start,'edit':edit,'load':load,'summary':summary,'maths':maths,'raw':raw};self.show_import()
    def close_import(self):
        if self.import_window and self.import_window.winfo_exists():
            self.pending_source=self.import_fields['source'].get('1.0','end-1c');self.import_window.destroy()
    def show_import(self):
        if not self.import_window or not self.import_window.winfo_exists():return
        fields=self.import_fields;record=self.import_record;busy=self.operation=='import';locked=busy or record is not None
        if record:self.pending_source=record.get('request',{}).get('parameters',{}).get('source','')
        if locked and self.pending_source is not None:self.put(fields['source'],self.pending_source)
        else:fields['source'].configure(state='normal')
        fields['start'].state(['disabled'] if self.job or record else ['!disabled']);fields['edit'].state(['disabled'] if self.job else ['!disabled'])
        result=(record or {}).get('result',{});fields['load'].state(['!disabled'] if not self.job and result.get('status')=='IMPORTED' and isinstance(result.get('spec'),dict) else ['disabled'])
        self.put(fields['summary'],'Import en cours ; entrées verrouillées.' if busy else str(result.get('status','Prêt à importer'))+'\n\n'+str(result.get('reason','Seules les fonctions pures du périmètre accepté sont importées. Un site complet ne l’est pas.'))+'\n\n'+'\n'.join(result.get('limitations',[])))
        if record and record.get('error'):self.put(fields['summary'],'ARCHIVE NON VÉRIFIÉE\n'+record['error'])
        self.put(fields['maths'],str(result.get('mathematics','Aucune représentation importée.')));self.put(fields['raw'],pretty(record) if record else 'Aucune preuve.')
    def start_import(self):
        if self.job or self.import_record:return
        source=self.import_fields['source'].get('1.0','end-1c')
        if len(source.encode('utf-8'))>65536:self.put(self.import_fields['summary'],'Source trop volumineuse (64 Kio maximum dans cet éditeur).');return
        self.pending_source=source;self.dispatch('import',source=source);self.show_import()
    def edit_import(self):
        if self.job:return
        self.import_record=None;self.pending_source=self.import_fields['source'].get('1.0','end-1c');self.show_import()
    def load_import(self):
        if self.job:return
        result=(self.import_record or {}).get('result',{})
        if result.get('status')!='IMPORTED' or not isinstance(result.get('spec'),dict):return
        origin={'import_request_id':self.import_record['request_id'],'source':self.pending_source,'archive':self.import_record.get('archive'),'evidence_dir':result.get('evidence_dir'),'imported_spec':result['spec']}
        self.new_draft({key:result['spec'][key] for key in PUBLIC if key in result['spec']},origin=origin);self.book.select(self.spec_text.master)
        self.message.set('Propriété importée chargée. Tu peux la modifier avant une nouvelle synthèse ; son origine est conservée.')
    def close(self):
        self.closing=True
        if self.job:self.message.set('Fermeture après archivage de l’opération en cours ; aucun nouvel essai.');self.render();return
        self.finish_close()
    def finish_close(self):
        source=self.pending_source
        if self.import_window and self.import_window.winfo_exists():source=self.import_fields['source'].get('1.0','end-1c')
        try:self.store.save_preferences({'zoom':self.zoom,'ox':self.ox,'oy':self.oy,'draft_text':self.spec_text.get('1.0','end-1c'),'draft_origin':self.origin,'import_draft_source':source,**self.extra_preferences()})
        except OSError:pass
        self.closed=True;self.pool.shutdown(wait=False,cancel_futures=True);self.root.destroy()
    def extra_preferences(self):return {}


MODULE_SOURCE='''public static class ExampleModule
{
    public static int Max2(int a, int b) { return a >= b ? a : b; }
    public static int Max3(int a, int b, int c) { return Max2(Max2(a, b), c); }
}'''

class ModuleWorkshop(Workshop):
    """Module mode shares the reviewed transport, while keeping function mode explicit."""
    def __init__(self,root,store=None,auto_poll=True):
        self.module_ready=False;self.mode='module';self.module=None;self.unit_id=None;self.unit_drafts={};self.unit_intents={};self.draft_history={};self.site_process=None
        self.records={'module':None,'function':None};self.import_records={'module':None,'function':None}
        self.sources={'module':MODULE_SOURCE,'function':SOURCE};self.function_draft=pretty(example());self.function_origin=None
        super().__init__(root,store,auto_poll=False)
        prefs=self.store.preferences();self.sources.update(prefs.get('mode_sources',{}) if isinstance(prefs.get('mode_sources'),dict) else {})
        self.function_draft=prefs.get('function_draft',pretty(example()));self.function_origin=prefs.get('function_origin')
        try:
            saved=prefs.get('module')
            if saved is not None:
                from module_model import validate_module
                self.module=validate_module(saved)
        except Exception as exc:self.message.set('Module restauré non vérifié : '+str(exc))
        self.unit_drafts=prefs.get('unit_drafts',{}) if isinstance(prefs.get('unit_drafts'),dict) else {}
        self.unit_intents=prefs.get('unit_intents',{}) if isinstance(prefs.get('unit_intents'),dict) else {}
        self.draft_history=prefs.get('module_draft_history',{}) if isinstance(prefs.get('module_draft_history'),dict) else {}
        self.unit_id=prefs.get('unit_id');self.pending_source=self.sources['module'];self.origin=None
        outer=root.winfo_children()[0]
        bar=ttk.Frame(outer);bar.pack(fill='x',pady=(0,8),before=outer.winfo_children()[2]);self.module_bar=bar
        self.mode_box=ttk.Combobox(bar,values=['Modules reliés','Fonction pure'],state='readonly',width=17);self.mode_box.set('Modules reliés');self.mode_box.pack(side='left');self.mode_box.bind('<<ComboboxSelected>>',lambda e:self.switch_mode('module' if self.mode_box.current()==0 else 'function'))
        self.unit_box=ttk.Combobox(bar,state='readonly',width=27);self.unit_box.pack(side='left',padx=6);self.unit_box.bind('<<ComboboxSelected>>',self.pick_unit)
        self.intent=tk.StringVar();self.intent_label=ttk.Label(bar,text='Intention :');self.intent_label.pack(side='left')
        self.intent_entry=ttk.Entry(bar,textvariable=self.intent,width=35);self.intent_entry.pack(side='left',fill='x',expand=True,padx=6)
        self.revise_button=ttk.Button(bar,text='Créer cette révision',command=self.revise);self.revise_button.pack(side='right')
        self.site_button=ttk.Button(self.import_button.master,text='Game Editor · QCM local',command=self.open_site);self.site_button.pack(side='right',padx=6)
        self.module_ready=True;root.title('MultiversIA — Atelier modules V0.2.0')
        self.select_unit(self.unit_id,save=False);self.configure_mode();self.render()
        self.message.set('Mode modules : importer une classe C# pure, ou ouvrir explicitement le site de test QCM.')
        if prefs.get('mode')=='function':self.switch_mode('function')
        if auto_poll:self.tick()
    def current_unit(self):return next((u for u in (self.module or {}).get('units',[]) if u['id']==self.unit_id),None)
    def open_site(self):
        if self.closing:return
        if self.site_process is not None and self.site_process.poll() is None:
            self.message.set('Le lanceur du site est déjà actif. Son navigateur affiche les états réels.');return
        try:
            self.site_process=ui_backend.launch_site()
            self.message.set('Lanceur du site démarré ; la qualification et l’ouverture restent à confirmer par son résultat.')
        except Exception as exc:self.message.set('Site non lancé : '+str(exc))
    @staticmethod
    def spec_for(unit):
        contract=unit.get('contract') or {}
        return {'schema_version':'mia-math-spec-1','name':unit['name'] if len(unit['name'])<=80 else unit['id'],'parameters':unit['parameters'],'return_type':unit['return_type'],
                'precondition':contract.get('precondition','True'),'postcondition':contract.get('postcondition','')}
    def save_unit_draft(self):
        if self.mode=='module' and self.unit_id and self.module_ready:
            self.unit_drafts[self.unit_id]=self.spec_text.get('1.0','end-1c');self.unit_intents[self.unit_id]=self.intent.get()
    def remember_module_drafts(self):
        self.save_unit_draft()
        if self.module:
            key=str(self.module.get('module_id'))+':'+str(self.module.get('revision_id'))
            self.draft_history[key]={'unit_drafts':dict(self.unit_drafts),'unit_intents':dict(self.unit_intents),'unit_id':self.unit_id}
    def select_unit(self,unit_id,save=True):
        if save:self.save_unit_draft()
        units=(self.module or {}).get('units',[])
        self.unit_id=unit_id if any(u['id']==unit_id for u in units) else units[0]['id'] if units else None
        self.unit_box['values']=[u.get('qualified_name',u['name'])+' · '+u['id'] for u in units]
        if self.unit_id:
            self.unit_box.current(next(i for i,u in enumerate(units) if u['id']==self.unit_id));unit=self.current_unit()
            self.put(self.spec_text,self.unit_drafts.get(self.unit_id,pretty(self.spec_for(unit))),editable=True);self.intent.set(self.unit_intents.get(self.unit_id,''))
        else:self.unit_box.set('Aucun module');self.put(self.spec_text,'Importer un module pour sélectionner sa propriété.');self.intent.set('')
        if self.module_ready:self.render()
    def pick_unit(self,_=None):
        units=(self.module or {}).get('units',[]);index=self.unit_box.current()
        if 0<=index<len(units):self.select_unit(units[index]['id'])
    def configure_mode(self):
        module_mode=self.mode=='module';self.mode_box.set('Modules reliés' if module_mode else 'Fonction pure')
        for widget in (self.unit_box,self.intent_label,self.intent_entry,self.revise_button):
            if module_mode:widget.pack(side='right' if widget==self.revise_button else 'left',padx=4,fill='x',expand=widget==self.intent_entry)
            else:widget.pack_forget()
        if module_mode:self.proof_widget.pack_forget();self.example_box.pack_forget()
        else:self.proof_widget.pack(anchor='w',before=self.run_button);self.example_box.pack(side='left',before=self.edit_button)
        self.import_button.configure(text='Importer un module C#' if module_mode else 'Importer une fonction C#')
        self.run_button.configure(text='Générer et vérifier le module' if module_mode else 'Chercher un programme puis vérifier')
        self.edit_button.configure(text='Recharger la propriété de l’unité' if module_mode else 'Modifier / nouvel essai')
    def switch_mode(self,mode):
        if self.job or mode==self.mode:return
        if self.import_window and self.import_window.winfo_exists():self.close_import()
        self.records[self.mode]=self.record;self.import_records[self.mode]=self.import_record;self.sources[self.mode]=self.pending_source
        if self.mode=='module':self.save_unit_draft()
        else:self.function_draft=self.spec_text.get('1.0','end-1c');self.function_origin=self.origin
        self.mode=mode;self.record=self.records[mode];self.import_record=self.import_records[mode];self.pending_source=self.sources[mode]
        self.origin=None if mode=='module' else self.function_origin
        if mode=='module':self.select_unit(self.unit_id,save=False)
        else:self.put(self.spec_text,self.function_draft,editable=self.record is None)
        self.configure_mode();self.render();self.message.set('Mode '+('modules reliés' if mode=='module' else 'fonction pure')+' sélectionné ; aucune opération lancée.')
    def edit(self):
        if self.mode=='function':return super().edit()
        if self.job or not self.current_unit():return
        self.unit_drafts.pop(self.unit_id,None);self.unit_intents.pop(self.unit_id,None);self.select_unit(self.unit_id,save=False)
        self.message.set('Propriété de la révision actuelle rechargée. Le module reste inchangé.')
    def run(self):
        if self.mode=='function':return super().run()
        if self.job or not self.module or self.closing or (self.record or {}).get('error'):return
        self.save_unit_draft()
        for unit in self.module['units']:
            try:spec=json.loads(self.unit_drafts.get(unit['id'],pretty(self.spec_for(unit))))
            except ValueError:self.message.set('Brouillon JSON invalide pour '+unit['name']+'.');return
            if spec!=self.spec_for(unit):self.message.set('Applique la révision du brouillon '+unit['name']+' avant de générer le module, ou recharge sa propriété.');return
        targets=[k for k,v in self.targets.items() if v.get()]
        if not targets:self.message.set('Choisis au moins une cible.');return
        self.dispatch('module_run',module=self.module,targets=targets,preference=self.preference.get())
    def revise(self):
        if self.job or not self.current_unit() or self.closing or (self.record or {}).get('error'):return
        try:spec=self.draft()
        except (ValueError,TypeError) as exc:self.message.set('Propriété à corriger : '+str(exc));return
        intent=self.intent.get().strip()
        if not intent:self.message.set('Décris l’intention du changement avant de créer une révision.');return
        self.save_unit_draft();self.dispatch('module_revise',module=self.module,unit_id=self.unit_id,spec=spec,intent=intent)
    def dispatch(self,operation,**parameters):
        accepted=super().dispatch(operation,**parameters)
        if accepted and operation.startswith('module_'):self.message.set({'module_import':'Import du module','module_run':'Génération et contrôle du module','module_revise':'Recherche de la nouvelle révision'}[operation]+' en cours. Aucun résultat nouveau accepté avant retour.')
        return accepted
    def apply_record(self,record):
        operation=record.get('operation','');module_mode=operation.startswith('module_')
        wanted='module' if module_mode else 'function'
        if self.mode!=wanted:self.switch_mode(wanted)
        if not module_mode:return super().apply_record(record)
        result=record.get('result',{});parameters=record.get('request',{}).get('parameters',{})
        if operation=='module_import':
            self.import_record=record;self.import_records['module']=record;self.pending_source=parameters.get('source','');self.sources['module']=self.pending_source;self.show_import()
        else:
            previous=self.module;self.remember_module_drafts();self.record=record;self.records['module']=record
            if isinstance(result.get('module'),dict) and (operation=='module_run' or result.get('status')=='REVISED'):
                self.module=result['module']
                key=str(self.module.get('module_id'))+':'+str(self.module.get('revision_id'));saved=self.draft_history.get(key)
                parent=parameters.get('module') or {}
                from_active_parent=bool(previous and parent.get('module_id')==previous.get('module_id')==self.module.get('module_id') and parent.get('revision_id')==previous.get('revision_id')==self.module.get('parent_revision_id'))
                if saved is not None:
                    self.unit_drafts=dict(saved.get('unit_drafts',{}));self.unit_intents=dict(saved.get('unit_intents',{}));self.unit_id=saved.get('unit_id')
                elif operation=='module_revise' and from_active_parent:
                    self.unit_drafts.pop(parameters.get('unit_id'),None);self.unit_intents.pop(parameters.get('unit_id'),None)
                else:
                    self.unit_drafts={};self.unit_intents={};self.unit_id=None
                self.select_unit(self.unit_id,save=False)
        self.message.set('Opération module terminée : '+str(result.get('status','INCONCLUSIVE'))+'. Archives : '+record.get('archive','—'));self.render()
    def apply_history(self,rows):
        super().apply_history(rows)
        self.records[self.mode]=self.record;self.import_records[self.mode]=self.import_record
        for cache in (self.records,self.import_records):
            for mode,current in tuple(cache.items()):
                if not current:continue
                changed=next((r for r in rows if r.get('request_id')==current.get('request_id') and r.get('error')),None)
                if changed:cache[mode]=dict(changed,request=current.get('request',{}))
    def select_history(self,event=None):
        super().select_history(event)
        if not self.job and self.mode=='module' and self.import_record and self.history_box.current()>=0:
            row=self.rows[self.history_box.current()]
            if row.get('operation')=='module_import' and not row.get('error'):self.open_import()
    def open_import(self):
        super().open_import()
        if self.mode=='module' and self.import_window and self.import_window.winfo_exists():self.import_window.title('Importer un module C# pur · toutes les unités inventoriées')
    def start_import(self):
        if self.mode=='function':return super().start_import()
        if self.job or self.import_record:return
        source=self.import_fields['source'].get('1.0','end-1c')
        if len(source.encode('utf-8'))>65536:self.put(self.import_fields['summary'],'Module trop volumineux pour cet éditeur (64 Kio maximum).');return
        self.pending_source=source;self.sources['module']=source;self.dispatch('module_import',source=source);self.show_import()
    def show_import(self):
        if not self.module_ready or self.mode=='function':return super().show_import()
        if not self.import_window or not self.import_window.winfo_exists():return
        f=self.import_fields;r=self.import_record;result=(r or {}).get('result',{});busy=self.operation=='module_import';locked=busy or r is not None
        if r:self.pending_source=r.get('request',{}).get('parameters',{}).get('source',self.pending_source)
        if locked and self.pending_source is not None:self.put(f['source'],self.pending_source)
        else:f['source'].configure(state='normal')
        f['start'].state(['disabled'] if self.job or r else ['!disabled']);f['edit'].state(['disabled'] if self.job else ['!disabled'])
        f['load'].configure(text='Charger le module dans l’atelier');f['load'].state(['!disabled'] if not self.job and result.get('status')=='IMPORTED' and isinstance(result.get('module'),dict) and not (r or {}).get('error') else ['disabled'])
        summary='Import du module en cours ; source exacte verrouillée.' if busy else str(result.get('status','Prêt à importer un module'))+'\n\n'+str(result.get('reason','Une classe de 1 à 8 méthodes pures. Aucun projet ou site complet prétendument couvert.'))
        if result.get('inventory') is not None:summary+='\n\nInventaire : '+str(len(result['inventory']))+' élément(s) conservé(s) dans Preuves complètes.'
        units=(result.get('module') or {}).get('units',[])
        if units:summary+='\nUnités acceptées : '+', '.join(u.get('qualified_name',u['name']) for u in units)
        coverage=result.get('coverage') or {}
        if coverage:summary+='\nCouverture admise : '+('oui' if coverage.get('accepted') else 'non établie')+' ; site entier : '+('oui' if coverage.get('whole_site_migrated') else 'non')
        if result.get('limitations'):summary+='\n\nLimites :\n'+'\n'.join(map(str,result['limitations']))
        if r and r.get('error'):summary='ARCHIVE NON VÉRIFIÉE\n'+r['error']
        self.put(f['summary'],summary);self.put(f['maths'],pretty(result.get('module')) if result.get('module') else 'Aucun modèle accepté.');self.put(f['raw'],pretty(r) if r else 'Aucune preuve.')
    def load_import(self):
        if self.mode=='function':return super().load_import()
        result=(self.import_record or {}).get('result',{})
        if self.job or (self.import_record or {}).get('error') or result.get('status')!='IMPORTED' or not isinstance(result.get('module'),dict):return
        self.remember_module_drafts();self.module=result['module'];self.record=self.import_record;self.records['module']=self.record
        key=str(self.module.get('module_id'))+':'+str(self.module.get('revision_id'));saved=self.draft_history.get(key,{})
        self.unit_drafts=dict(saved.get('unit_drafts',{}));self.unit_intents=dict(saved.get('unit_intents',{}));self.select_unit(saved.get('unit_id'),save=False)
        self.message.set('Module importé chargé. Les propriétés des unités se révisent avec une intention explicite.');self.book.select(self.summary.master)
    def render(self):
        if not self.module_ready:return super().render()
        if self.mode=='function':
            super().render();self.mode_box.configure(state='disabled' if self.job or self.closing else 'readonly');return
        super().render();busy=bool(self.job or self.closing);self.mode_box.configure(state='disabled' if busy else 'readonly');self.unit_box.configure(state='disabled' if busy else 'readonly')
        self.spec_text.configure(state='normal' if self.current_unit() and not busy else 'disabled');self.intent_entry.configure(state='disabled' if busy or not self.current_unit() else 'normal')
        unverified=bool((self.record or {}).get('error'))
        self.revise_button.state(['disabled'] if busy or unverified or not self.current_unit() else ['!disabled']);self.run_button.state(['disabled'] if busy or unverified or not self.module else ['!disabled'])
        for widget in self.target_widgets:widget.state(['disabled'] if busy else ['!disabled'])
        self.preference.configure(state='disabled' if busy else 'readonly');self.edit_button.state(['disabled'] if busy or not self.current_unit() else ['!disabled'])
        m=self.module;unit=self.current_unit();result=(self.record or {}).get('result',{})
        summary='MODULES PURS RELIÉS\n\nImporter une classe C# admise, puis explorer ses unités et appels. Le bouton « Game Editor · QCM local » ouvre le Studio ASP.NET Core MVC configurable. Ce Studio avec état n’est pas migré par cet atelier de fonctions pures. Le prototype séparé de QCM à profil HTTP fixe garde sa propre portée. Ce parcours ne reconnaît pas automatiquement les routes, effets, bases de données ou interfaces d’un site quelconque.'
        if m:
            summary='MODULE\n'+m.get('name','—')+'\nRévision : '+str(m.get('revision_id','—'))+'\nParent : '+str(m.get('parent_revision_id') or 'import initial')+'\n\nIntention : '+str(m.get('intent','—'))
            if unit:
                dependencies=m.get('dependencies',{}).get(unit['id'],[]);names={u['id']:u.get('qualified_name',u['name']) for u in m['units']}
                summary+='\n\nUNITÉ SÉLECTIONNÉE\n'+unit.get('qualified_name',unit['name'])+' · '+unit['id']+'\nAppelle : '+(', '.join(names.get(x,x) for x in dependencies) or 'aucune autre unité')
            summary+='\n\nLa propriété JSON est un brouillon. « Créer cette révision » construit un nouveau module ; les appelants conservent leurs contrats, donc une rupture doit rester visible. Les résultats affichés concernent la révision archivée, jamais un brouillon non appliqué.'
        if result:
            summary+='\n\nDERNIÈRE OPÉRATION\n'+str(result.get('status','—'))+'\n'+str(result.get('reason',''))
            requested=(self.record or {}).get('request',{}).get('parameters',{}).get('module')
            if not result.get('module') and isinstance(requested,dict):
                summary+='\n\nENTRÉE DE CETTE OPÉRATION ARCHIVÉE\n'+str(requested.get('name','—'))+' · révision '+str(requested.get('revision_id','—'))+'\nEntrée exacte conservée dans Preuves complètes.'
                if m and (requested.get('module_id'),requested.get('revision_id'))!=(m.get('module_id'),m.get('revision_id')):summary+='\nAttention : la carte et le brouillon restent ceux du module actif ci-dessus, différent de cette entrée historique.'
            if result.get('change'):
                change=result['change'];summary+='\n\nCHANGEMENT\nUnité : '+str(change.get('unit_id','—'))+'\nAppelants affectés : '+(', '.join(change.get('affected_dependents',[])) or 'aucun')+'\nÉquivalence avec l’ancien comportement : non revendiquée. Détails complets dans les preuves.'
            for check in result.get('verification',[]):
                summary+='\n\n'+str(check.get('language','cible'))+' : '+str(check.get('status','—'))+'\nCompilation : '+str(check.get('compile_status','—'))+' · exécution : '+str(check.get('execution_status','—'))
                if self.selected==check.get('language'):
                    summary+='\n'+str(check.get('reason',''))
                    for item in check.get('units',[]):
                        tests=item.get('tests',{});summary+='\n'+str(item.get('unit_id','unité'))+' : '+str(item.get('status','—'))
                        if isinstance(tests,dict):summary+=' · cas prévus '+str(tests.get('planned',0))+', exécutés '+str(tests.get('executed',0))+', contrats contrôlés '+str(tests.get('contract_checked',0))+', contre-exemples '+str(len(tests.get('counterexample_indices',[])))+', préconditions d’appel violées '+str(len(tests.get('call_precondition_violation_indices',[])))
                        else:summary+=' · cas '+str(tests)
                    summary+='\nCas détaillés, sorties et contre-exemples conservés dans Preuves complètes.'
        summary+='\n\nPORTÉE FORMELLE\nModule non prouvé universellement. La preuve de fonction du mode séparé n’est pas une preuve de tout le module.'
        if busy:summary='OPÉRATION EN COURS — sous-étapes non encore observées.\n\n'+summary
        if self.record and self.record.get('error'):summary='ARCHIVE NON VÉRIFIÉE\n'+self.record['error']+'\n\n'+summary
        self.put(self.summary,summary)
        if unit:self.put(self.maths,'Signature : '+pretty(unit['parameters'])+' → '+unit['return_type']+'\n\nContrat actuel (hors brouillon) :\n'+pretty(unit.get('contract'))+'\n\nCorps avec appels :\n'+pretty(unit['body']))
        self.put(self.raw,pretty({'module_current':m,'operation':self.record,'draft_unit':self.unit_id}) if m or self.record else 'Aucun modèle de module accepté.');self.draw()
    def draw(self):
        if not self.module_ready or self.mode=='function':return super().draw()
        if self.closed:return
        self.canvas.delete('all');self.zoom_label.set(str(round(self.zoom*100))+' %');m=self.module or {};units=m.get('units',[]);coords={u['id']:((i%4-1.5)*280,220+(i//4)*180) for i,u in enumerate(units)}
        def node(key,x,y,title,sub):
            sx=self.ox+x*self.zoom;sy=self.oy+y*self.zoom;w=235*self.zoom;h=104*self.zoom;tag='node:'+key
            self.canvas.create_rectangle(sx-w/2,sy-h/2,sx+w/2,sy+h/2,fill=PANEL,outline=TEAL if key==self.unit_id else LINE,width=2,tags=(tag,))
            self.canvas.create_text(sx,sy-13*self.zoom,text=title,width=max(45,w-10),fill=INK,font=('Segoe UI',max(9,min(16,round(12*self.zoom))),'bold'),tags=(tag,))
            if self.zoom>=.55:self.canvas.create_text(sx,sy+20*self.zoom,text=sub,width=w-10,fill=AMBER,font=('Segoe UI',max(9,min(12,round(10*self.zoom)))),tags=(tag,))
        for unit_id,(x,y) in coords.items():
            self.canvas.create_line(self.ox,self.oy+52*self.zoom,self.ox+x*self.zoom,self.oy+(y-52)*self.zoom,fill=LINE,dash=(3,5),tags=('membership',))
        for i in range(3):self.edge((0,510),((i-1)*300,598))
        if units:self.canvas.create_text(self.ox,self.oy+510*self.zoom,text='Module complet → cibles vérifiées',fill=MUTED,font=('Segoe UI',max(9,round(11*self.zoom))))
        node('module',0,0,m.get('name','Importer un module C#'),str(m.get('revision_id','Aucun module accepté'))[:16])
        for caller,targets in m.get('dependencies',{}).items():
            for callee in targets:
                if caller in coords and callee in coords:self.edge(coords[caller],coords[callee])
        for unit in units:node(unit['id'],*coords[unit['id']],unit.get('name',unit['id']),unit['id']+' · '+str(len(m.get('dependencies',{}).get(unit['id'],[])))+' appel(s)')
        checks={v.get('language'):v for v in (self.record or {}).get('result',{}).get('verification',[])}
        for i,language in enumerate(('python','csharp','rust')):node(language,(i-1)*300,650,{'python':'Python','csharp':'C#','rust':'Rust'}[language],str(checks.get(language,{}).get('status','Non contrôlé')))
    def release(self,event):
        if not self.module_ready or self.mode=='function':return super().release(event)
        moved=self.drag and self.drag[4];self.drag=None
        if moved:return
        for item in reversed(self.canvas.find_overlapping(event.x,event.y,event.x,event.y)):
            for tag in self.canvas.gettags(item):
                if tag.startswith('node:u'):self.selected=tag[5:];self.select_unit(tag[5:]);self.book.select(self.summary.master);return
                if tag.startswith('node:'):self.selected=tag[5:];self.render();self.book.select(self.summary.master);return
    def overview(self):
        if not self.module_ready or self.mode=='function':return super().overview()
        self.zoom=max(.25,min(.8,(self.canvas.winfo_width()-40)/1150,(self.canvas.winfo_height()-50)/760));self.ox=self.canvas.winfo_width()/2;self.oy=60;self.draw()
    def extra_preferences(self):
        if not self.module_ready:return {}
        if self.import_window and self.import_window.winfo_exists():self.sources[self.mode]=self.import_fields['source'].get('1.0','end-1c')
        else:self.sources[self.mode]=self.pending_source
        if self.mode=='module':self.remember_module_drafts()
        else:self.function_draft=self.spec_text.get('1.0','end-1c');self.function_origin=self.origin
        return {'mode':self.mode,'module':self.module,'unit_id':self.unit_id,'unit_drafts':self.unit_drafts,'unit_intents':self.unit_intents,'module_draft_history':self.draft_history,'mode_sources':self.sources,'function_draft':self.function_draft,'function_origin':self.function_origin}

def main():
    root=tk.Tk();ModuleWorkshop(root);root.mainloop()
if __name__=='__main__':main()
