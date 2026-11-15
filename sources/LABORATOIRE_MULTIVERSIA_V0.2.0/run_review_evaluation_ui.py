"""Independent execution of UI contracts; all Tk windows are withdrawn."""
import hashlib
import json
from pathlib import Path
import tkinter as tk
import unittest
from unittest import mock
import uuid
import test_ui

BASE=Path(__file__).resolve().parent
FILES=('app.py','ui_backend.py','test_ui.py','GUIDE.txt')
before={name:hashlib.sha256((BASE/name).read_bytes()).hexdigest() for name in FILES}
real_toplevel=tk.Toplevel
def hidden_toplevel(*args,**kwargs):
    window=real_toplevel(*args,**kwargs)
    window.withdraw()
    return window

with mock.patch.object(tk,'Toplevel',side_effect=hidden_toplevel):
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_ui))
after={name:hashlib.sha256((BASE/name).read_bytes()).hexdigest() for name in FILES}
path=BASE/'evidence'/('evaluation-ui-review-'+uuid.uuid4().hex+'.json')
path.write_text(json.dumps({'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),'sources_unchanged':before==after,'source_sha256':after,'evidence_dir':str(test_ui.RUN),'scope':'UI contracts, all Tk windows withdrawn; no visual usability certification and no real Lean run.'},ensure_ascii=False,indent=2),encoding='utf-8')
print(str(path))
raise SystemExit(not(result.wasSuccessful() and before==after))
