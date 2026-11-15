"""Local explicit-action workshop. UI and subprocess protocol share this file."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid

BASE = Path(__file__).resolve().parent
VERSION = '0.5.0'
PYTHON = str(Path(sys.executable).with_name('python.exe')) if Path(sys.executable).name.lower() == 'pythonw.exe' else sys.executable
OPERATIONS = {'import': 180, 'revise': 120, 'translate': 600}
MAX_JSON = 4 * 1024 * 1024
SOURCE_EXAMPLES = {
    'rust': '// Exemple : overflow_demo(1) renvoie -2147483648.\npub fn add_pair(a: i32, b: i32) -> i32 {\n    a.wrapping_add(b)\n}\n\npub fn overflow_demo(a: i32) -> i32 {\n    add_pair(a, 2147483647)\n}\n',
    'csharp': '// Exemple : OverflowDemo(1) renvoie -2147483648.\npublic static class Example {\n    public static int AddPair(int a, int b) { return unchecked(a + b); }\n    public static int OverflowDemo(int a) { return AddPair(a, 2147483647); }\n}\n',
}
PROFILE = 'Profil actuel : 1–8 fonctions pures Int32/bool, calculs qui rebouclent sur 32 bits, conditions et appels sans cycle. Rust wrapping_* ; C# unchecked explicite. Hors profil : refus. Pas de migration générale de site.'
WRAP_RULE = 'Les Int32 vont de -2147483648 à 2147483647. Chaque addition, soustraction, multiplication ou opposé reboucle sur 32 bits (modulo 2^32). Exemple : add32(2147483647, 1) = -2147483648.'


def pretty(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_JSON:
        raise ValueError('Document JSON trop volumineux.')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Clé JSON répétée : ' + key)
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nombre non fini.')))


def read_json(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_JSON + 1)
    return parse(data.decode('utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = pretty(value)
    if len(text.encode('utf-8')) > MAX_JSON:
        raise ValueError('Archive trop volumineuse.')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    temporary.write_text(text + '\n', encoding='utf-8', newline='\n')
    os.replace(temporary, path)


def contained(path):
    path = Path(path).absolute()
    if not path.resolve().is_relative_to(BASE):
        raise ValueError('Chemin hors de cet atelier.')
    for p in (path, *path.parents):
        if p == BASE.parent:
            break
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ValueError('Lien de dossier refusé.')
    return path.resolve()


class ProcessTreeUncertain(RuntimeError):
    pass


def stop_owned_process(process):
    """Terminate this still-owned process tree, never processes selected by name."""
    if process.poll() is not None:
        return
    if os.name == 'nt':
        try:
            stopped = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            if stopped.returncode:
                raise OSError('taskkill a refusé l’arrêt de l’arbre.')
        except Exception as exc:
            # Keep the live process handle as authority for this fallback. It
            # kills the owned parent only, never an unrelated/name-matched PID.
            try:
                if process.poll() is None: process.kill()
                process.wait(timeout=15)
            except Exception as fallback:
                raise ProcessTreeUncertain('Arrêt du worker et de ses descendants non confirmé ; PID ' + str(process.pid) + ' : ' + str(fallback)) from exc
            raise ProcessTreeUncertain('Worker arrêté et attendu ; arrêt de ses descendants non confirmé après refus de taskkill. PID ' + str(process.pid)) from exc
    else:
        import signal
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=15)


def run_worker(command, *, input, timeout, **kwargs):
    """Own the live PID until completion/timeout, including descendant cleanup."""
    kwargs.pop('capture_output', None)
    if os.name != 'nt': kwargs['start_new_session'] = True
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, **kwargs)
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        try:
            stop_owned_process(process)
        except ProcessTreeUncertain as uncertain:
            uncertain.stdout, uncertain.stderr = exc.output, exc.stderr
            raise
        stdout, stderr = process.communicate(timeout=5)
        raise subprocess.TimeoutExpired(command, timeout, output=stdout or exc.output, stderr=stderr or exc.stderr) from exc
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None: stream.close()


def worker_main(operation, output_root):
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        if operation not in OPERATIONS:
            raise ValueError('Opération inconnue.')
        request = parse(sys.stdin.read(MAX_JSON + 1))
        if not isinstance(request, dict):
            raise ValueError('Objet JSON attendu.')
        output = contained(output_root)
        # Backend diagnostics cannot corrupt the JSON result channel.
        from contextlib import redirect_stdout
        with redirect_stdout(sys.stderr):
            import migrator
            if operation == 'import':
                if set(request) != {'source', 'source_language'}:
                    raise ValueError('Champs d’import inattendus.')
                result = migrator.import_source(**request, output_root=output)
            elif operation == 'revise':
                if set(request) != {'module', 'unit_id', 'spec', 'intent'}:
                    raise ValueError('Champs de révision inattendus.')
                result = migrator.revise(**request, output_root=output)
            else:
                if set(request) != {'module', 'targets', 'source_import', 'auto_correct', 'max_repairs'}:
                    raise ValueError('Champs de traduction inattendus.')
                result = migrator.translate(**request, output_root=output)
        print(pretty({'ok': True, 'result': result}), flush=True)
        return 0
    except Exception as exc:
        print(pretty({'ok': False, 'result': {'status': 'INCONCLUSIVE', 'reason': type(exc).__name__ + ': ' + str(exc)}}), flush=True)
        return 1


class Store:
    def __init__(self, root=None):
        self.root = contained(root or BASE / 'local')

    def command(self, operation, parameters, context=None):
        if operation not in OPERATIONS:
            raise ValueError('Opération inconnue.')
        # Serialize before launching; the submitted parameters are immutable.
        exact = pretty(parameters)
        if len(exact.encode('utf-8')) > MAX_JSON:
            raise ValueError('Requête trop volumineuse.')
        folder = self.root / 'requests' / ('request-' + uuid.uuid4().hex)
        folder.mkdir(parents=True, exist_ok=False)
        request = {'operation': operation, 'parameters': parse(exact), 'context': deepcopy(context or {}), 'at': datetime.now(timezone.utc).isoformat()}
        write_json(folder / 'request.json', request)
        result = {'status': 'INCONCLUSIVE', 'reason': 'Opération non terminée.'}
        try:
            env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP')}
            env['PYTHONIOENCODING'] = 'utf-8'
            process = run_worker([PYTHON, str(BASE / 'app.py'), '--worker', operation, str(folder / 'backend')],
                input=exact, capture_output=True, encoding='utf-8', errors='strict', cwd=BASE,
                env=env, timeout=OPERATIONS[operation], creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            (folder / 'stdout.txt').write_text(process.stdout, encoding='utf-8')
            (folder / 'stderr.txt').write_text(process.stderr, encoding='utf-8')
            reply = parse(process.stdout)
            result = reply['result']
            if not isinstance(result, dict):
                raise ValueError('Résultat non structuré.')
            if process.returncode != 0 and reply.get('ok') is True:
                raise ValueError('Code de sortie incompatible avec le reçu.')
        except (subprocess.TimeoutExpired, ProcessTreeUncertain) as exc:
            reason = str(exc) if isinstance(exc, ProcessTreeUncertain) else 'Délai dépassé : résultat incertain. Les archives sont conservées ; aucune relance automatique.'
            result = {'status': 'INCONCLUSIVE', 'reason': reason, 'uncertain': True}
            for name, data in (('stdout.txt', exc.stdout), ('stderr.txt', exc.stderr)):
                if data:
                    (folder / name).write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
        except Exception as exc:
            result = {'status': 'INCONCLUSIVE', 'reason': type(exc).__name__ + ': ' + str(exc)}
        write_json(folder / 'result.json', result)
        write_json(folder / 'receipt.json', {'files_sha256': {name: digest((folder / name).read_bytes()) for name in ('request.json', 'result.json')}})
        return self.read(folder)

    def read(self, folder):
        folder = contained(folder)
        if not folder.is_relative_to(self.root / 'requests'):
            raise ValueError('Reçu hors de la liste des requêtes.')
        receipt = read_json(folder / 'receipt.json')
        if set(receipt.get('files_sha256', {})) != {'request.json', 'result.json'}:
            raise ValueError('Inventaire du reçu invalide.')
        for name, expected in receipt['files_sha256'].items():
            if digest((folder / name).read_bytes()) != expected:
                raise ValueError('Archive modifiée ; résultat non vérifié.')
        return {'archive': str(folder), 'request': read_json(folder / 'request.json'), 'result': read_json(folder / 'result.json')}

    def history(self):
        rows = []
        if not (self.root / 'requests').is_dir():
            return rows
        for folder in sorted((self.root / 'requests').glob('request-*'), key=lambda p: p.name):
            try:
                rows.append(self.read(folder))
            except Exception as exc:
                rows.append({'archive': str(folder), 'error': 'Reçu interrompu ou non vérifié : ' + str(exc)})
        return sorted(rows, key=lambda r: r.get('request', {}).get('at', ''), reverse=True)


def unit_spec(unit):
    contract = unit.get('contract') or {}
    return {'schema_version': 'mia-math-spec-1', 'name': unit.get('name', '') if len(unit.get('name', '')) <= 80 else unit['id'],
        'parameters': deepcopy(unit.get('parameters', [])), 'return_type': unit.get('return_type'),
        'precondition': contract.get('precondition', 'True'), 'postcondition': contract.get('postcondition', 'True')}


def equation(node, names=None, depth=0):
    if not isinstance(node, dict) or depth > 32:
        return '?'
    kind = node.get('kind')
    recurse = lambda n: equation(n, names, depth + 1)
    if kind == 'constant': return str(node.get('value'))
    if kind == 'param': return str(node.get('name'))
    if kind == 'not': return 'non ' + recurse(node.get('operand'))
    if kind == 'neg32': return 'neg32(' + recurse(node.get('operand')) + ')'
    if kind == 'binary':
        if node.get('op') in ('add32', 'sub32', 'mul32'):
            return node['op'] + '(' + recurse(node.get('left')) + ', ' + recurse(node.get('right')) + ')'
        op = {'eq': '=', 'ne': '≠', 'lt': '<', 'le': '≤', 'gt': '>', 'ge': '≥', 'and': 'et', 'or': 'ou'}.get(node.get('op'), '?')
        return '(' + recurse(node.get('left')) + ' ' + op + ' ' + recurse(node.get('right')) + ')'
    if kind == 'ite': return 'si ' + recurse(node.get('condition')) + ' alors ' + recurse(node.get('when_true')) + ' sinon ' + recurse(node.get('when_false'))
    if kind == 'call': return (names or {}).get(node.get('target'), str(node.get('target'))) + '(' + ', '.join(recurse(n) for n in node.get('arguments', [])) + ')'
    return '[forme non affichée : voir JSON exact]'


class Workshop:
    def __init__(self, root, store=None, auto_poll=True):
        self.root, self.store = root, store or Store()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.job = None
        self.closed = False
        self.closing = False
        self.suppress = False
        self.module = None
        self.source_import = None
        self.source_basis = None
        self.record = None
        self.rows = []
        self.property_drafts = {}
        self.source_drafts = deepcopy(SOURCE_EXAMPLES)
        self.current_language = 'rust'
        self.current_unit = None
        self.zoom, self.ox, self.oy = .75, 45., 75.
        self.drag = None
        self.selected = 'source'
        self.status = tk.StringVar(value='Prêt. Aucune expérience ne démarre à l’ouverture.')
        self.source_language = tk.StringVar(value='rust')
        self.target = tk.StringVar(value='csharp')
        self.intent = tk.StringVar(value='')
        self.auto_correct = tk.BooleanVar(value=True)
        self.max_repairs = tk.StringVar(value='2')
        self.unit_choice = tk.StringVar(value='')
        self.session_note = None
        self.adopted_revision = None
        self.restoring = False
        self.needs_initial_fit = True
        self.build()
        self.restore_session()
        self.render()
        self.refresh_history()
        self.update_controls()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        if auto_poll:
            self.root.after(100, self.tick)

    def build(self):
        self.root.title('MultiversIA — Migrateur local V' + VERSION)
        self.root.geometry('1380x880')
        self.root.minsize(1020, 680)
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background='#14232b')
        style.configure('TLabel', background='#14232b', foreground='#e7eef1', font=('Segoe UI', 10))
        style.configure('TButton', font=('Segoe UI', 10), padding=7)
        style.configure('TCheckbutton', background='#14232b', foreground='#e7eef1')
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill='both', expand=True)
        ttk.Label(main, text='Source → modèle commun → langage cible', font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        ttk.Label(main, text=PROFILE, wraplength=1300).pack(anchor='w', pady=(6, 12))
        commands = ttk.Frame(main)
        commands.pack(fill='x', pady=(0, 8))
        ttk.Label(commands, text='Source').pack(side='left')
        self.lang_box = ttk.Combobox(commands, textvariable=self.source_language, values=['rust', 'csharp'], state='readonly', width=9)
        self.lang_box.pack(side='left', padx=7)
        self.lang_box.bind('<<ComboboxSelected>>', self.change_language)
        self.import_button = ttk.Button(commands, text='1 · Importer', command=self.import_source)
        self.import_button.pack(side='left', padx=(0, 15))
        ttk.Label(commands, text='Cible').pack(side='left')
        self.target_box = ttk.Combobox(commands, textvariable=self.target, values=['csharp', 'rust', 'python'], state='readonly', width=9)
        self.target_box.pack(side='left', padx=7)
        self.target_box.bind('<<ComboboxSelected>>', lambda _: (self.render(), self.save_session()))
        self.correct_box = ttk.Checkbutton(commands, text='Correcteur borné', variable=self.auto_correct, command=self.changed)
        self.correct_box.pack(side='left', padx=4)
        self.repairs_box = ttk.Combobox(commands, textvariable=self.max_repairs, values=['0', '1', '2'], state='readonly', width=3)
        self.repairs_box.pack(side='left')
        ttk.Label(commands, text='essais max.').pack(side='left', padx=5)
        self.run_button = ttk.Button(commands, text='3 · Générer et vérifier', command=self.translate)
        self.run_button.pack(side='left', padx=10)
        ttk.Label(main, textvariable=self.status, wraplength=1300).pack(anchor='w', pady=(0, 9))
        panes = ttk.Panedwindow(main, orient='horizontal')
        panes.pack(fill='both', expand=True)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=3)
        panes.add(right, weight=3)
        toolbar = ttk.Frame(left)
        toolbar.pack(fill='x')
        ttk.Button(toolbar, text='−', command=lambda: self.zoom_at(.8, 300, 250)).pack(side='left')
        ttk.Button(toolbar, text='+', command=lambda: self.zoom_at(1.25, 300, 250)).pack(side='left', padx=5)
        ttk.Button(toolbar, text='Vue globale', command=self.fit).pack(side='left')
        ttk.Label(toolbar, text='Molette : zoom · glisser : déplacer').pack(side='left', padx=10)
        self.canvas = tk.Canvas(left, background='#102029', highlightthickness=0, takefocus=True)
        self.canvas.pack(fill='both', expand=True, pady=(8, 0))
        self.canvas.bind('<MouseWheel>', lambda e: self.zoom_at(1.12 if e.delta > 0 else 1 / 1.12, e.x, e.y))
        self.canvas.bind('<ButtonPress-1>', self.pointer_down)
        self.canvas.bind('<B1-Motion>', self.pointer_move)
        self.canvas.bind('<ButtonRelease-1>', self.pointer_up)
        self.canvas.bind('<Configure>', self.initial_fit)
        for key, delta in {'Left': (40, 0), 'Right': (-40, 0), 'Up': (0, 40), 'Down': (0, -40)}.items():
            self.canvas.bind('<' + key + '>', lambda e, d=delta: self.pan(*d))
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill='both', expand=True, padx=(10, 0))
        self.source_tab = ttk.Frame(self.tabs, padding=10)
        self.math_tab = ttk.Frame(self.tabs, padding=10)
        self.property_tab = ttk.Frame(self.tabs, padding=10)
        self.results_tab = ttk.Frame(self.tabs, padding=10)
        self.evidence_tab = ttk.Frame(self.tabs, padding=10)
        for title, frame in [('Source', self.source_tab), ('Mathématiques', self.math_tab), ('Propriété', self.property_tab), ('Résultats', self.results_tab), ('Preuves', self.evidence_tab)]:
            self.tabs.add(frame, text=title)
        ttk.Label(self.source_tab, text='Texte exact à importer. Le code hors du profil est refusé.', wraplength=570).pack(anchor='w', pady=(0, 8))
        self.source_text = self.text_widget(self.source_tab)
        self.source_text.bind('<<Modified>>', self.source_changed)
        self.math_text = self.text_widget(self.math_tab, readonly=True)
        self.units_box = ttk.Combobox(self.property_tab, textvariable=self.unit_choice, state='readonly')
        self.units_box.pack(fill='x')
        self.units_box.bind('<<ComboboxSelected>>', self.change_unit)
        ttk.Label(self.property_tab, text='Propriété typée de la fonction sélectionnée. Les contrats des autres fonctions restent conservés.', wraplength=570).pack(anchor='w', pady=8)
        self.spec_text = self.text_widget(self.property_tab)
        self.spec_text.bind('<<Modified>>', self.property_changed)
        ttk.Label(self.property_tab, text='Intention de la nouvelle révision').pack(anchor='w', pady=(7, 3))
        self.intent_entry = ttk.Entry(self.property_tab, textvariable=self.intent)
        self.intent_entry.pack(fill='x')
        self.revise_button = ttk.Button(self.property_tab, text='2 · Créer une nouvelle révision', command=self.revise)
        self.revise_button.pack(anchor='w', pady=8)
        self.result_text = self.text_widget(self.results_tab, readonly=True)
        self.adopt_button = ttk.Button(self.results_tab, text='Utiliser le modèle corrigé promu', command=self.adopt_repair)
        self.adopt_button.pack(anchor='w', pady=8)
        tools = ttk.Frame(self.evidence_tab)
        tools.pack(fill='x')
        ttk.Button(tools, text='Actualiser les reçus', command=self.refresh_history).pack(side='left')
        ttk.Button(tools, text='Ouvrir les preuves', command=self.open_evidence).pack(side='left', padx=5)
        ttk.Button(tools, text='Exporter JSON', command=self.export).pack(side='left')
        self.history_box = tk.Listbox(self.evidence_tab, height=5, exportselection=False, background='#20333f', foreground='#e7eef1')
        self.history_box.pack(fill='x', pady=8)
        self.history_box.bind('<<ListboxSelect>>', self.select_history)
        self.raw_text = self.text_widget(self.evidence_tab, readonly=True)
        self.put(self.source_text, self.source_drafts['rust'])

    @staticmethod
    def text_widget(parent, readonly=False):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        text = tk.Text(frame, wrap='word', undo=True, background='#0f1b23', foreground='#dbe9ed', insertbackground='white', font=('Consolas', 10), relief='flat', padx=10, pady=10)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        if readonly: text.configure(state='disabled')
        return text

    def put(self, widget, text):
        old = self.suppress
        self.suppress = True
        state = str(widget.cget('state'))
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.edit_modified(False)
        widget.configure(state=state)
        self.suppress = old

    @staticmethod
    def get(widget):
        return widget.get('1.0', 'end-1c')

    def key(self, unit=None, module=None):
        m = module or self.module
        if not m or not (unit or self.current_unit): return None
        return ':'.join((m['module_id'], m['revision_id'], unit or self.current_unit))

    def capture(self):
        self.source_drafts[self.current_language] = self.get(self.source_text)
        if self.key(): self.property_drafts[self.key()] = self.get(self.spec_text)

    def source_changed(self, _=None):
        if not self.source_text.edit_modified(): return
        self.source_text.edit_modified(False)
        if not self.suppress:
            self.source_drafts[self.current_language] = self.get(self.source_text)
            self.changed()

    def property_changed(self, _=None):
        if not self.spec_text.edit_modified(): return
        self.spec_text.edit_modified(False)
        if not self.suppress and self.key():
            self.property_drafts[self.key()] = self.get(self.spec_text)
            self.changed()

    def changed(self):
        if self.future is None:
            self.status.set('Brouillon conservé. Importer ou réviser pour prendre en compte les modifications.')
        self.update_controls()
        self.save_session()

    def change_language(self, _=None):
        self.capture()
        self.current_language = self.source_language.get()
        self.put(self.source_text, self.source_drafts.get(self.current_language, ''))
        self.changed()

    def load_module(self, module, *, preserve_child=False, changed_unit=None):
        self.capture()
        previous = self.module
        if preserve_child and previous:
            for unit in module.get('units', []):
                oldkey, newkey = self.key(unit['id'], previous), self.key(unit['id'], module)
                if unit['id'] != changed_unit and oldkey in self.property_drafts and newkey not in self.property_drafts:
                    self.property_drafts[newkey] = self.property_drafts[oldkey]
        self.module = deepcopy(module)
        ids = [u['id'] for u in module.get('units', [])]
        self.current_unit = self.current_unit if self.current_unit in ids else ids[0] if ids else None
        self.units_box.configure(values=[u['id'] + ' · ' + u['name'] for u in module.get('units', [])])
        self.show_unit()
        names = {u['id']: u['name'] for u in module.get('units', [])}
        profile = module.get('semantic_profile')
        if profile == 'pure-int32-bool-wrap-call-1':
            lines = ['Modèle commun · Int32 modulo 2^32 / booléen', '', WRAP_RULE, '',
                'Contrats explicites : add32(a,b), sub32(a,b), mul32(a,b), neg32(a). Les signes + et - généraux ne remplacent pas ces opérations.', '']
        elif profile == 'pure-int32-bool-call-1':
            lines = ['Modèle commun · ancien profil Int32 / booléen', '',
                'Ce modèle accepte les conditions, comparaisons et appels sans arithmétique. Réimportez la source avec le nouveau profil pour utiliser les calculs modulo Int32.', '']
        else:
            lines = ['Modèle commun · profil non reconnu : ' + str(profile), '',
                'Les opérations admises ne sont pas établies par cette vue. Consultez les données exactes et le résultat de validation.', '']
        for unit in module.get('units', []):
            lines += [unit['name'] + '(' + ', '.join(p['name'] for p in unit['parameters']) + ') =', equation(unit.get('body'), names),
                'Avant : ' + str((unit.get('contract') or {}).get('precondition')), 'Après : ' + str((unit.get('contract') or {}).get('postcondition')), '']
        lines += ['Les équations sont une vue lisible. Le JSON complet, les noms originaux et la provenance restent dans Preuves.']
        self.put(self.math_text, '\n'.join(lines))
        self.put(self.raw_text, pretty({'record': self.record, 'active_module': self.module}))
        self.render()

    def show_unit(self):
        unit = next((u for u in (self.module or {}).get('units', []) if u['id'] == self.current_unit), None)
        self.unit_choice.set(unit['id'] + ' · ' + unit['name'] if unit else '')
        text = self.property_drafts.get(self.key(), pretty(unit_spec(unit)) if unit else '')
        self.put(self.spec_text, text)

    def change_unit(self, _=None):
        if self.future is not None: return
        self.capture()
        self.current_unit = self.unit_choice.get().split(' · ', 1)[0]
        self.show_unit()
        self.save_session()

    def source_matches(self):
        return bool(self.source_basis and self.current_language == self.source_basis['source_language'] and self.get(self.source_text) == self.source_basis['source'])

    def properties_clean(self):
        if not self.module: return False
        self.capture()
        for unit in self.module.get('units', []):
            draft = self.property_drafts.get(self.key(unit['id']))
            if draft is not None:
                try:
                    if parse(draft) != unit_spec(unit): return False
                except (ValueError, RecursionError): return False
        return True

    def update_controls(self):
        busy = self.future is not None
        for widget in (self.import_button,): widget.configure(state='disabled' if busy else 'normal')
        for widget in (self.lang_box, self.target_box, self.repairs_box, self.units_box): widget.configure(state='disabled' if busy else 'readonly')
        self.correct_box.configure(state='disabled' if busy else 'normal')
        self.source_text.configure(state='disabled' if busy else 'normal')
        self.spec_text.configure(state='disabled' if busy or not self.module else 'normal')
        self.intent_entry.configure(state='disabled' if busy else 'normal')
        self.revise_button.configure(state='normal' if not busy and self.module and self.source_matches() else 'disabled')
        self.run_button.configure(state='normal' if not busy and self.module and self.source_matches() and self.properties_clean() else 'disabled')
        repair = ((self.record or {}).get('result', {}).get('repair') or {})
        self.adopt_button.configure(state='normal' if not busy and repair.get('promoted') is True else 'disabled')

    def start(self, operation, parameters):
        if self.future is not None: return
        self.capture()
        self.save_session()
        self.job = {'operation': operation, 'parameters': deepcopy(parameters)}
        context = {'source_import': deepcopy(self.source_import), 'source_basis': deepcopy(self.source_basis)}
        self.future = self.pool.submit(self.store.command, operation, deepcopy(parameters), context)
        self.status.set('Opération en cours : ' + operation + '. La carte reste consultable ; aucune progression inventée.')
        self.update_controls()

    def import_source(self):
        self.start('import', {'source': self.get(self.source_text), 'source_language': self.current_language})

    def revise(self):
        try:
            if not self.module or not self.source_matches(): raise ValueError('Importer d’abord la source affichée.')
            if not self.intent.get().strip(): raise ValueError('Décris l’intention de cette nouvelle révision.')
            spec = parse(self.get(self.spec_text))
            self.start('revise', {'module': self.module, 'unit_id': self.current_unit, 'spec': spec, 'intent': self.intent.get().strip()})
        except Exception as exc:
            self.status.set('Révision non lancée : ' + str(exc))

    def translate(self):
        if not self.module or not self.source_matches() or not self.properties_clean():
            self.status.set('Source ou propriété modifiée : importer/réviser avant de générer.')
            return
        self.start('translate', {'module': self.module, 'targets': [self.target.get()], 'source_import': self.source_import,
            'auto_correct': bool(self.auto_correct.get()), 'max_repairs': int(self.max_repairs.get())})

    def apply_record(self, record, *, completed=False):
        self.capture()
        request, result = record.get('request', {}), record.get('result', {})
        operation, params = request.get('operation'), request.get('parameters', {})
        self.record = deepcopy(record)
        self.adopted_revision = None
        context = request.get('context') or {}
        if operation != 'import':
            self.source_import = deepcopy(context.get('source_import') or params.get('source_import'))
            self.source_basis = deepcopy(context.get('source_basis') or self.basis_from_import(self.source_import))
            if self.source_basis:
                self.current_language = self.source_basis['source_language']
                self.source_language.set(self.current_language)
                self.put(self.source_text, self.source_drafts.get(self.current_language, self.source_basis['source']))
        module = result.get('module') if operation in ('import', 'revise') else params.get('module')
        if operation == 'import':
            self.current_language = params['source_language']
            self.source_language.set(self.current_language)
            self.source_basis = deepcopy(params)
            # Never overwrite a different unsent source draft when browsing history.
            if completed: self.source_drafts[self.current_language] = params['source']
            self.put(self.source_text, self.source_drafts.get(self.current_language, params['source']))
            self.source_import = deepcopy(result) if result.get('status') == 'IMPORTED' else None
            if not self.source_import: module = None
        if module:
            self.load_module(module, preserve_child=completed and operation == 'revise', changed_unit=params.get('unit_id'))
        elif operation == 'revise' and isinstance(params.get('module'), dict):
            self.load_module(params['module'])
        elif operation == 'import':
            self.module = None
            self.current_unit = None
            self.show_unit()
            self.put(self.math_text, 'Import non admis. Aucun modèle actif pour cette source.')
        self.show_result()
        self.put(self.raw_text, pretty({'record': record, 'active_module': self.module}))
        self.status.set('Opération terminée : ' + str(result.get('status', 'INCONCLUSIVE')) + '. ' + str(result.get('reason', ''))[:240])
        if self.module and not self.source_matches():
            self.status.set('Reçu consulté ; le brouillon source affiché diffère de son origine. Importer ce brouillon avant de générer.')
        self.render()
        self.update_controls()
        self.save_session()

    @staticmethod
    def basis_from_import(receipt):
        if not isinstance(receipt, dict): return None
        provenance = (receipt.get('module') or {}).get('provenance') or {}
        source = provenance.get('source_text')
        language = receipt.get('source_language') or (provenance.get('origin') or {}).get('language')
        return {'source': source, 'source_language': language} if isinstance(source, str) and language in ('rust', 'csharp') else None

    def show_result(self):
        result = (self.record or {}).get('result', {})
        verification = result.get('verification') or {}
        source_check = verification.get('source_check') or {}
        source_language = {'csharp': 'C#', 'rust': 'Rust', 'python': 'Python'}.get(source_check.get('language'), 'langage non renseigné')
        lines = [str(result.get('status', 'Aucun résultat')), str(result.get('reason', '')), '',
            'Source originale · ' + source_language + ' : ' + str(source_check.get('status', 'NOT_TESTED')),
            '  Compilation : ' + str(source_check.get('compile_status', 'NOT_TESTED')) + ' · exécution : ' + str(source_check.get('execution_status', 'NOT_TESTED')),
            '  Accord avec le modèle importé : ' + str(source_check.get('baseline_model_agreement', 'Non établi')),
            '  Accord avec le modèle courant : ' + str(source_check.get('agreement', 'Non établi')), '', 'Cibles générées :']
        for row in verification.get('generated_checks', []):
            lines.append('• ' + str(row.get('language', '?')) + ' : ' + str(row.get('status', 'INCONCLUSIVE')))
            lines.append('  Compilation : ' + str(row.get('compile_status', 'NOT_TESTED')) + ' · exécution : ' + str(row.get('execution_status', 'NOT_TESTED')))
            lines.append('  Accord au modèle : ' + str(row.get('model_agreement', 'NOT_TESTED')))
            for unit in row.get('units', []):
                tests = unit.get('tests') or {}
                lines.append('  Contrat ' + str(unit.get('unit_id', '?')) + ' : ' + str(unit.get('status', 'NOT_TESTED')) +
                    ' · ' + str(tests.get('contract_checked', 0)) + ' cas contrôlés')
            witnesses = row.get('counterexamples') or []
            if witnesses:
                lines.append('  ' + str(len(witnesses)) + ' contre-exemples conservés ; premier : ' + pretty(witnesses[0])[:420])
        repair = result.get('repair') or {}
        lines += ['', 'Correction : ' + str(repair.get('status', 'Non renseignée')), 'Essais consignés : ' + str(len(repair.get('attempts') or [])),
            'Promotion : ' + ('oui' if repair.get('promoted') is True else 'non établie'),
            '', 'Un test fini n’est pas une preuve universelle. Contrats, accord au modèle et accord à la source sont des contrôles distincts.']
        if result.get('candidate_module'):
            lines.append('Le dernier candidat reste non promu. Ses contrôles ne valident pas le modèle éditable d’origine.')
        for limitation in result.get('limitations', []): lines.append('• ' + str(limitation))
        self.put(self.result_text, '\n'.join(lines))

    def adopt_repair(self):
        if self.future is not None or not self.record: return
        result = self.record.get('result', {})
        if (result.get('repair') or {}).get('promoted') is not True or not isinstance(result.get('module'), dict): return
        self.load_module(result['module'])
        self.adopted_revision = self.module.get('revision_id')
        self.status.set('Modèle corrigé promu adopté explicitement. L’original et le reçu restent conservés.')
        self.update_controls()
        self.save_session()

    def tick(self, schedule=True):
        if self.closed: return
        if self.future is not None and self.future.done():
            future = self.future
            self.future = None
            try:
                self.apply_record(future.result(), completed=True)
            except Exception as exc:
                self.status.set('Résultat non disponible : ' + str(exc))
                self.update_controls()
            self.job = None
            self.refresh_history()
            if self.closing:
                self.finish_close()
                return
        if schedule: self.root.after(100, self.tick)

    def refresh_history(self):
        try:
            self.rows = self.store.history()
            self.history_box.delete(0, 'end')
            for row in self.rows:
                text = row.get('error') or row['request']['operation'] + ' · ' + str(row['result'].get('status')) + ' · ' + row['request']['at'][:19]
                self.history_box.insert('end', text)
            if self.record:
                self.store.read(self.record['archive'])
        except Exception as exc:
            self.module = None
            self.source_import = None
            self.record = None
            self.current_unit = None
            self.show_unit()
            self.put(self.math_text, 'Archive non vérifiée. Aucun modèle actif.')
            self.put(self.result_text, 'Ancien résultat invalidé : ' + str(exc))
            self.status.set('Historique non vérifié : ' + str(exc))
            self.render()
            self.update_controls()

    def select_history(self, _=None):
        if self.future is not None or not self.history_box.curselection(): return
        row = self.rows[self.history_box.curselection()[0]]
        try:
            self.apply_record(self.store.read(row['archive']))
        except Exception as exc:
            self.status.set('Reçu non vérifié : ' + str(exc))
            self.module = None
            self.record = None
            self.current_unit = None
            self.show_unit()
            self.put(self.math_text, 'Archive non vérifiée. Aucun modèle actif.')
            self.put(self.result_text, 'Résultat historique non vérifié.')
            self.render()
            self.update_controls()

    def save_session(self):
        if self.closed or self.restoring: return
        try:
            self.capture()
            write_json(self.store.root / 'session.json', {'source_drafts': self.source_drafts, 'property_drafts': self.property_drafts,
                'language': self.current_language, 'target': self.target.get(), 'intent': self.intent.get(), 'auto_correct': self.auto_correct.get(),
                'max_repairs': self.max_repairs.get(), 'archive': (self.record or {}).get('archive'),
                'camera': [self.zoom, self.ox, self.oy], 'unit_id': self.current_unit,
                'adopted_revision': self.adopted_revision})
        except Exception as exc:
            self.status.set('Brouillon en mémoire seulement : sauvegarde locale impossible (' + str(exc) + ').')

    def restore_session(self):
        path = self.store.root / 'session.json'
        if not path.exists(): return
        self.restoring = True
        try:
            data = read_json(path)
            self.source_drafts.update({k: v for k, v in data.get('source_drafts', {}).items() if k in SOURCE_EXAMPLES and isinstance(v, str)})
            self.property_drafts = {k: v for k, v in data.get('property_drafts', {}).items() if isinstance(k, str) and isinstance(v, str)}
            self.current_language = data.get('language') if data.get('language') in SOURCE_EXAMPLES else 'rust'
            self.source_language.set(self.current_language)
            self.put(self.source_text, self.source_drafts[self.current_language])
            self.target.set(data.get('target') if data.get('target') in ('rust', 'csharp', 'python') else 'csharp')
            self.intent.set(data.get('intent', ''))
            self.auto_correct.set(data.get('auto_correct') is True)
            self.max_repairs.set(data.get('max_repairs') if data.get('max_repairs') in ('0', '1', '2') else '2')
            self.current_unit = data.get('unit_id')
            camera = data.get('camera')
            if isinstance(camera, list) and len(camera) == 3 and all(type(v) in (int, float) and abs(v) < 1e7 for v in camera) and .15 <= camera[0] <= 3:
                self.zoom, self.ox, self.oy = camera
                self.needs_initial_fit = False
            if data.get('archive'): self.apply_record(self.store.read(data['archive']))
            if data.get('adopted_revision') and self.record:
                result = self.record.get('result', {})
                if (result.get('repair') or {}).get('promoted') is True and (result.get('module') or {}).get('revision_id') == data['adopted_revision']:
                    self.load_module(result['module'])
                    self.adopted_revision = data['adopted_revision']
            # Restore the user's selected source draft even if the receipt is
            # for another language; the mismatch gate remains closed.
            self.current_language = data.get('language') if data.get('language') in SOURCE_EXAMPLES else 'rust'
            self.source_language.set(self.current_language)
            self.put(self.source_text, self.source_drafts[self.current_language])
            self.status.set('Brouillons restaurés. Aucune expérience relancée.')
        except Exception as exc:
            self.session_note = str(exc)
            self.status.set('Reprise partielle : ' + str(exc))
        finally:
            self.restoring = False

    def nodes(self):
        nodes = [('source', 0, 100, 'Source ' + self.current_language.upper(), 'Importer explicitement')]
        units = (self.module or {}).get('units', [])
        if not units: nodes.append(('model', 330, 100, 'Modèle commun', 'En attente d’import'))
        for i, unit in enumerate(units): nodes.append((unit['id'], 330, 30 + i * 140, unit['name'], equation(unit.get('body'))[:52]))
        result = (self.record or {}).get('result', {})
        verified_targets = {row.get('language') for row in (result.get('verification') or {}).get('generated_checks', [])}
        checked_revision = result.get('verified_revision_id')
        matches = self.target.get() in verified_targets and checked_revision == (self.module or {}).get('revision_id') and checked_revision is not None
        check_status = str((result.get('verification') or {}).get('status', 'Non exécutés')) if matches else 'Ce choix n’a pas été vérifié'
        nodes += [('target', 680, 100, 'Cible choisie ' + self.target.get().upper(), 'Pour la prochaine génération'),
            ('checks', 1010, 100, 'Contrôles de ce choix', check_status),
            ('repair', 1010, 350, 'Correction bornée', str((result.get('repair') or {}).get('status', 'Option avant lancement')))]
        return nodes

    def render(self):
        self.canvas.delete('all')
        nodes = self.nodes()
        coords = {n[0]: (n[1], n[2]) for n in nodes}
        edges = []
        units = (self.module or {}).get('units', [])
        for unit in units:
            edges += [('source', unit['id']), (unit['id'], 'target')]
        if not units: edges += [('source', 'model'), ('model', 'target')]
        edges += [('target', 'checks'), ('checks', 'repair')]
        for caller, callees in (self.module or {}).get('dependencies', {}).items():
            edges += [(caller, callee) for callee in callees]
        for start, end in edges:
            if start not in coords or end not in coords: continue
            a, b = coords[start], coords[end]
            self.canvas.create_line(self.ox + (a[0] + 220) * self.zoom, self.oy + (a[1] + 50) * self.zoom,
                self.ox + b[0] * self.zoom, self.oy + (b[1] + 50) * self.zoom, fill='#52798b', width=2, arrow='last')
        for ident, x, y, title, detail in nodes:
            px, py = self.ox + x * self.zoom, self.oy + y * self.zoom
            tags = ('node', 'node:' + ident)
            self.canvas.create_rectangle(px, py, px + 240 * self.zoom, py + 104 * self.zoom, fill='#213c48', outline='#e5c17c' if ident == self.selected else '#6594a3', width=2, tags=tags)
            self.canvas.create_text(px + 15 * self.zoom, py + 30 * self.zoom, text=title, anchor='w', fill='#f0f6f5', font=('Segoe UI', max(8, int(15 * self.zoom)), 'bold'), width=212 * self.zoom, tags=tags)
            if self.zoom >= .55: self.canvas.create_text(px + 15 * self.zoom, py + 70 * self.zoom, text=detail, anchor='w', fill='#b9d4dc', font=('Segoe UI', max(8, int(10 * self.zoom))), width=212 * self.zoom, tags=tags)

    def zoom_at(self, factor, x, y):
        old = self.zoom
        self.zoom = min(3., max(.15, old * factor))
        self.ox = x - (x - self.ox) * self.zoom / old
        self.oy = y - (y - self.oy) * self.zoom / old
        self.render()

    def pan(self, x, y):
        self.ox += x
        self.oy += y
        self.render()

    def fit(self):
        nodes = self.nodes()
        height = max(n[2] + 110 for n in nodes)
        self.zoom = max(.15, min(1., (max(100, self.canvas.winfo_width()) - 50) / 1250, (max(100, self.canvas.winfo_height()) - 50) / (height + 30)))
        self.ox, self.oy = 25., 30.
        self.render()

    def initial_fit(self, event):
        if self.needs_initial_fit and event.width > 250 and event.height > 200:
            self.needs_initial_fit = False
            self.fit()

    def pointer_down(self, event):
        self.canvas.focus_set()
        self.drag = (event.x, event.y, self.ox, self.oy)

    def pointer_move(self, event):
        if self.drag:
            self.ox = self.drag[2] + event.x - self.drag[0]
            self.oy = self.drag[3] + event.y - self.drag[1]
            self.render()

    def pointer_up(self, event):
        if not self.drag: return
        moved = abs(event.x - self.drag[0]) + abs(event.y - self.drag[1]) > 5
        self.drag = None
        if moved: return
        items = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in reversed(items):
            tag = next((t for t in self.canvas.gettags(item) if t.startswith('node:')), None)
            if not tag: continue
            ident = tag[5:]
            self.selected = ident
            if ident == 'source': self.tabs.select(self.source_tab)
            elif ident in ('checks', 'repair', 'target'): self.tabs.select(self.results_tab)
            else:
                if self.future is None and ident != 'model':
                    self.capture()
                    self.current_unit = ident
                    self.show_unit()
                self.tabs.select(self.math_tab)
            self.render()
            break

    def open_evidence(self):
        try:
            if not self.record: raise ValueError('Aucun reçu sélectionné.')
            self.store.read(self.record['archive'])
            os.startfile(str(contained(self.record['archive'])))
        except Exception as exc: self.status.set('Preuves non ouvertes : ' + str(exc))

    def export(self):
        value = {'module': self.module, 'record': self.record}
        name = filedialog.asksaveasfilename(parent=self.root, title='Exporter le modèle et le reçu', defaultextension='.json', filetypes=[('JSON', '*.json')])
        if name:
            try:
                Path(name).write_text(pretty(value), encoding='utf-8')
                self.status.set('Export enregistré : ' + name)
            except Exception as exc: self.status.set('Export impossible : ' + str(exc))

    def close(self):
        self.save_session()
        if self.future is not None:
            self.closing = True
            self.root.withdraw()
        else: self.finish_close()

    def finish_close(self):
        if self.closed: return
        self.save_session()
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=False)
        self.root.destroy()


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        return worker_main(sys.argv[2], sys.argv[3])
    root = tk.Tk()
    Workshop(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
