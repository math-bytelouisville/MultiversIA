"""Read-only access to the previously reviewed local Roslyn/Lean bridge."""
import hashlib
from pathlib import Path
import types

PATH=Path(__file__).resolve().parent.parent/'LABORATOIRE_MULTIVERSIA_V0.2.0/csharp_bridge.py'
EXPECTED_SHA256='ba62fbf6215752f1c5f59bde158dfa382299f73293d8e4c27b44be8705d9196e'

def load_bridge():
    raw=PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=EXPECTED_SHA256:
        raise ValueError('Le pont historique a changé : nouvelle qualification requise.')
    module=types.ModuleType('mia_readonly_bridge_v020');module.__file__=str(PATH)
    # Execute exactly the checked bytes, not a second mutable filesystem read.
    exec(compile(raw,str(PATH),'exec'),module.__dict__)
    return module
