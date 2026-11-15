"""Explicit local Game Editor launcher; closing this window stops its server."""
from pathlib import Path
import os,queue,threading,tkinter as tk,webbrowser
from tkinter import ttk,messagebox
import mvc_runtime

def main():
    root=tk.Tk();root.title('MultiversIA — Game Editor');root.geometry('660x350');root.minsize(560,300)
    box=ttk.Frame(root,padding=25);box.pack(fill='both',expand=True)
    ttk.Label(box,text='Game Editor · QCM',font=('Segoe UI',22,'bold')).pack(anchor='w')
    ttk.Label(box,text='Ton atelier local : questions, parties, résultats et scores.',wraplength=590).pack(anchor='w',pady=(8,18))
    status=tk.StringVar(value='Préparation du serveur ASP.NET Core MVC…')
    ttk.Label(box,textvariable=status,wraplength=590).pack(anchor='w')
    actions=ttk.Frame(box);actions.pack(anchor='w',pady=22)
    state={'server':None,'closing':False,'worker_done':False};messages=queue.Queue()
    def open_site():
        if state['server']:webbrowser.open(state['server'].url)
    button=ttk.Button(actions,text='Ouvrir la carte et le Game Editor',command=open_site,state='disabled');button.pack(side='left',padx=(0,8))
    def open_data():os.startfile(str(mvc_runtime.BASE/'local/qcm-studio'))
    data=ttk.Button(actions,text='Dossier des sauvegardes',command=open_data,state='disabled');data.pack(side='left')
    ttk.Label(box,text='Garde cette fenêtre ouverte pendant la partie. Fermer arrête le serveur ; les données sauvegardées restent disponibles à la prochaine ouverture.',wraplength=580).pack(anchor='w')
    def worker():
        try:
            build=mvc_runtime.load_or_build();server=mvc_runtime.StudioProcess(build).start();messages.put(('ready',server))
        except Exception as exc:messages.put(('error',str(exc)))
        finally:messages.put(('done',None))
    def close():
        state['closing']=True;button.config(state='disabled');data.config(state='disabled');status.set('Arrêt du serveur…')
        if state['server']:state['server'].stop();state['server']=None
        if state['worker_done']:root.destroy()
    def poll():
        try:
            while True:
                kind,value=messages.get_nowait()
                if kind=='ready':
                    state['server']=value
                    if state['closing']:value.stop();state['server']=None
                    else:
                        status.set('Prêt — '+value.url);button.config(state='normal');data.config(state='normal');open_site()
                elif kind=='error':
                    status.set('Ouverture impossible : '+value)
                    if not state['closing']:messagebox.showerror('Game Editor',value,parent=root)
                elif kind=='done':state['worker_done']=True
        except queue.Empty:pass
        if state['closing'] and state['worker_done']:root.destroy();return
        if state['server'] and state['server'].process.poll() is not None:
            status.set('Le serveur s’est arrêté. Ferme puis rouvre l’atelier.');button.config(state='disabled');state['server'].stop();state['server']=None
        root.after(150,poll)
    root.protocol('WM_DELETE_WINDOW',close);threading.Thread(target=worker,daemon=True).start();root.after(150,poll);root.mainloop()
if __name__=='__main__':main()
