"""One target registry over the preserved language-independent module emitter."""
from common import foundations

TARGETS = {'python': '.py', 'csharp': '.cs', 'rust': '.rs'}


def emit_module(module, language):
    foundations()
    if type(language) is not str or language not in TARGETS:
        raise ValueError('Cible disponible requise : Python, C# ou Rust.')
    from module_backends import emit_module as emit
    return emit(module, language)
