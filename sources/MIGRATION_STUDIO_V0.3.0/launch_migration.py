"""Small local launcher. Opening it never starts a comparison automatically."""
from pathlib import Path
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import uuid
import webbrowser

from rust_runtime import BASE, load, owned


def latest_report():
    pointer = BASE / 'latest-run.json'
    if not pointer.is_file():
        return None
    path = owned(load(pointer)['report'])
    return path, load(path, 32 * 1024 * 1024)


def penalty(text):
    fields = text.strip().split('/')
    if len(fields) != 2 or any(not s.isascii() or not s.isdigit() or len(s) > 6 for s in fields):
        raise ValueError('Écris une fraction, par exemple 1/2.')
    n, d = map(int, fields)
    if not 0 <= n <= d or d == 0:
        raise ValueError('Le poids doit être compris entre 0 et 1.')
    return str(n) + '/' + str(d)


def main():
    window = tk.Tk()
    window.title('MultiversIA — Migration C# vers Rust')
    window.geometry('720x490')
    window.minsize(620, 420)
    window.configure(bg='#12201f')
    style = ttk.Style(window)
    style.theme_use('clam')
    style.configure('TFrame', background='#12201f')
    style.configure('TLabel', background='#12201f', foreground='#e8f1df', font=('Segoe UI', 10))
    style.configure('Title.TLabel', font=('Segoe UI', 19, 'bold'))
    style.configure('TButton', font=('Segoe UI', 10), padding=8)
    frame = ttk.Frame(window, padding=24)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='C# → Maths → Rust', style='Title.TLabel').pack(anchor='w')
    ttk.Label(frame, text='Le calcul des scores du QCM, reconstruit depuis un modèle modifiable.', wraplength=650).pack(anchor='w', pady=(8, 14))
    status = tk.StringVar(value='Prêt. Aucune expérience ne démarre à l’ouverture.')
    ttk.Label(frame, textvariable=status, wraplength=650).pack(anchor='w')
    controls = ttk.Frame(frame)
    controls.pack(fill='x', pady=14)
    def open_report():
        try:
            found = latest_report()
            if not found:
                raise ValueError('Aucun rapport pour le moment. Lance une première comparaison.')
            html = found[0].parent / 'index.html'
            if not html.is_file():
                raise ValueError('La carte est absente ; le rapport JSON reste conservé.')
            webbrowser.open(html.as_uri())
        except Exception as exc:
            messagebox.showerror('Carte de migration', str(exc), parent=window)
    ttk.Button(controls, text='Ouvrir la carte de migration', command=open_report).pack(side='left')
    def open_files():
        try:
            found = latest_report()
            os.startfile(str(found[0].parent if found else BASE))
        except Exception as exc:
            messagebox.showerror('Dossier des preuves', str(exc), parent=window)
    ttk.Button(controls, text='Voir les fichiers', command=open_files).pack(side='left', padx=10)
    ttk.Separator(frame).pack(fill='x', pady=8)
    ttk.Label(frame, text='Explorer une modification du barème partiel', font=('Segoe UI', 12, 'bold')).pack(anchor='w', pady=(8, 6))
    ttk.Label(frame, text='Poids des mauvaises réponses : 1/1 = actuel, 1/2 = pénalité divisée par deux, 0/1 = aucune pénalité.', wraplength=650).pack(anchor='w')
    form = ttk.Frame(frame)
    form.pack(fill='x', pady=12)
    weight = tk.StringVar(value='1/2')
    entry = ttk.Entry(form, textvariable=weight, width=10, font=('Segoe UI', 12))
    entry.pack(side='left', padx=(0, 12))
    ttk.Label(frame, text='La comparaison utilise des données de test séparées. Le Studio installé conserve son barème. La migration complète du site reste à réaliser.', wraplength=650).pack(anchor='w', pady=4)
    events = queue.Queue()
    state = {'busy': False, 'closing': False}
    log = tk.StringVar(value='')
    ttk.Label(frame, textvariable=log, wraplength=650).pack(anchor='w', pady=10)
    def worker(value):
        try:
            folder = owned(BASE / 'launcher-runs') / uuid.uuid4().hex
            folder.mkdir(parents=True, exist_ok=False)
            with (folder / 'launcher.log').open('w', encoding='utf-8') as output:
                process = subprocess.Popen([sys.executable, str(BASE / 'migration_pipeline.py'), '--penalty', value],
                                           cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           encoding='utf-8', errors='replace',
                                           env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                for line in process.stdout:
                    output.write(line)
                    output.flush()
                    events.put(('log', line.strip()))
                process.stdout.close()
                code = process.wait()
                events.put(('done', code))
        except Exception as exc:
            events.put(('error', str(exc)))
    def start():
        if state['busy']:
            return
        try:
            value = penalty(weight.get())
        except ValueError as exc:
            messagebox.showerror('Poids de pénalité', str(exc), parent=window)
            return
        state['busy'] = True
        run_button.configure(state='disabled')
        entry.configure(state='disabled')
        status.set('Comparaison en cours…')
        threading.Thread(target=worker, args=(value,), daemon=False).start()
    run_button = ttk.Button(form, text='Construire et comparer', command=start)
    run_button.pack(side='left')
    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'log':
                    if not value.startswith('{'):
                        log.set(value)
                else:
                    state['busy'] = False
                    run_button.configure(state='normal')
                    entry.configure(state='normal')
                    status.set('Comparaison terminée. Ouvre la carte pour lire les résultats.' if kind == 'done' and value == 0 else 'Comparaison incomplète. Le rapport conserve les détails.')
                    if kind == 'error':
                        log.set(value)
                    if state['closing']:
                        window.destroy()
                        return
        except queue.Empty:
            pass
        window.after(150, poll)
    def close():
        if state['busy']:
            state['closing'] = True
            window.withdraw()
            # The bounded comparison finishes and its owned C# process closes normally.
        else:
            window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)
    poll()
    window.mainloop()


if __name__ == '__main__':
    main()
