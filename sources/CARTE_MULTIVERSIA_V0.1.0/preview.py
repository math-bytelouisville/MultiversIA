"""Visible QA window with real isolated simulation data, never user missions."""
import tkinter as tk
import uuid
import backend
from app import MapApp

folder=backend.BASE/'evidence'/('visual-'+uuid.uuid4().hex[:10])
store=backend.Store(folder,legacy=folder/'absent')
key=store.create('Contrôle visuel · simulation')
store.command(key,'run',a=True,b=False,prediction=True)
store.command(key,'run',a=False,b=False,prediction=True)
last=store.command(key,'run',a=True,b=True,prediction=False,crash='after_effect')
root=tk.Tk();ui=MapApp(root,store);root.title('MultiversIA — Vérification visuelle isolée')
ui.source_id=key;ui.selected='mission:'+key
ui.ingest(store.scan());root.after(350,ui.focus_mission)
root.mainloop()
