"""Known reviewed Roslyn extractor, new local archives, no supplied-code execution."""
from pathlib import Path
import base64
import hashlib
from common import BASE, campaign, foundations, save, sha

LIMITATIONS = [
    'Sous-ensemble C# pur Int32/bool, une classe de1 à8 méthodes ; voir le contrat des modules.',
    'Roslyn analyse la source sans exécuter le code fourni.',
    'Addition/soustraction/multiplication/négation Int32 seulement dans unchecked explicite ; modulo 2^32.',
    'Ni site entier, ni arithmétique générale, ni état ou effets dans ce profil.',
]


def import_csharp(source, output_dir=None):
    folder = campaign(output_dir, 'csharp-import')
    result = {'status': 'INCONCLUSIVE', 'reason': 'Import non terminé.', 'module': None, 'inventory': [],
              'coverage': {'accepted': False, 'whole_site_migrated': False}, 'evidence_dir': str(folder),
              'source_language': 'csharp', 'input_execution': False, 'source_preserved': False, 'limitations': LIMITATIONS}
    before = None
    try:
        if type(source) is bytes:
            raw = source
        elif type(source) is str:
            raw = source.encode('utf-8', errors='surrogatepass')
        else:
            raise ValueError('Source texte UTF8 requise.')
        if len(raw) > 65536:
            raise ValueError('Source C# de64Kio au maximum ; entrée trop grande non archivée.')
        path = folder / 'source.cs'
        path.write_bytes(raw)
        result['source_preserved'] = True
        text = raw.decode('utf-8')
        if not text.strip():
            raise ValueError('Source C# non vide requise.')
        digest = hashlib.sha256(raw).hexdigest()
        before = foundations()
        from legacy_bridge import load_bridge
        bridge = load_bridge()
        helpers = bridge.helpers()
        runtime = helpers.validate_roslyn_runtime(bridge.ROSLYN_MANIFEST, bridge.PWSH)
        result['runtime'] = runtime
        script = BASE / 'module_import.ps1'
        # Explicit UTF8 on the child output pipe: Windows otherwise inherits a
        # console code page and corrupts Unicode namespace/class/source metadata.
        bootstrap = ('[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); '
                     '$OutputEncoding=[Console]::OutputEncoding; & '
                     + helpers.ps_quote(script) + ' -SourcePath ' + helpers.ps_quote(path))
        encoded = base64.b64encode(bootstrap.encode('utf-16-le')).decode('ascii')
        response = bridge.process([str(bridge.PWSH), '-NoLogo', '-NoProfile', '-NonInteractive',
                                   '-EncodedCommand', encoded], 30, folder)
        save(folder / 'process.json', response)
        if response['exit_code'] or response['stderr'].strip():
            raise RuntimeError('Analyse Roslyn interrompue ; voir les sorties conservées.')
        ir = helpers.strict_json(response['stdout'])
        save(folder / 'roslyn-ir.json', ir)
        result['inventory'] = ir.get('inventory', [])
        if ir.get('source_sha256') != digest:
            raise ValueError('Identité source différente dans le résultat Roslyn.')
        if ir.get('status') != 'TRANSLATED':
            result['diagnostics'] = ir.get('diagnostics', [])
            raise ValueError('Source refusée : ' + str(ir.get('reason')))
        from module_model import validate_module
        model = {'schema_version': 'mia-module-1', 'semantic_profile': 'pure-int32-bool-wrap-call-1',
                 'module_id': 'csharp-' + digest, 'name': ir['class_name'], 'parent_revision_id': None,
                 'intent': 'Import C# automatique dans le profil pur déclaré.',
                 'provenance': {'source_text': text, 'source_sha256': digest, 'import_id': 'csharp-' + digest,
                                'origin': {'language': 'csharp', 'extractor_sha256': sha(script)},
                                'limitations': LIMITATIONS}, 'units': ir['units']}
        model = validate_module(model)
        runtime_after = helpers.validate_roslyn_runtime(bridge.ROSLYN_MANIFEST, bridge.PWSH)
        result['runtime_after'] = runtime_after
        stable_runtime = lambda value: {k: v for k, v in value.items() if k != 'elapsed_seconds'}
        if sha(path) != digest or foundations() != before or stable_runtime(runtime_after) != stable_runtime(runtime):
            raise RuntimeError('Source, extracteur ou runtime modifié pendant l’import.')
        save(folder / 'module.json', model)
        result.update(status='IMPORTED', reason='Toutes les méthodes sont admises et leurs appels résolus.', module=model,
                      coverage={'accepted': True, 'units': len(model['units']), 'whole_site_migrated': False})
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        result.update(status='REFUSED', reason=str(exc), module=None)
    except Exception as exc:
        result.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc), module=None)
    finally:
        try:
            if before is not None and foundations() != before:
                result.update(status='INCONCLUSIVE', reason='Fondations modifiées pendant l’import.', module=None)
        except Exception as exc:
            result.update(status='INCONCLUSIVE', reason=str(exc), module=None)
        save(folder / 'report.json', result)
    return result
