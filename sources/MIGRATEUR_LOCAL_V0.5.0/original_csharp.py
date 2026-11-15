"""Readmit exact pure C#, emit a DLL with pinned Roslyn, invoke in a separate process."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import uuid

import module_model
import csharp_import
from common import BASE, archive_request, campaign, foundations, load_json, save, sha
from legacy_bridge import load_bridge

FILES = ('original_csharp.py', 'original_csharp_compile.ps1', 'original_csharp_run.ps1',
         'csharp_import.py', 'common.py', 'FOUNDATION_V050.json')
LIMITATIONS = ['Profil C# pur Int32/bool avec arithmétique unchecked explicite ; aucune isolation système.',
               'Les valeurs sont des observations finies, pas une preuve universelle.',
               'Source exacte conservée ; compilation DLL puis invocation statique par réflexion.',
               'Sorties lues à 4 Mio maximum ; ce contrôle ne limite pas les écritures disque.']


def _signatures(model):
    return [{k: unit[k] for k in ('id', 'name', 'qualified_name', 'parameters', 'return_type')}
            for unit in model['units']]


def _producer_hashes():
    return {name: sha(BASE / name) for name in FILES}


def _tools():
    bridge = load_bridge()
    tools = bridge.helpers().validate_roslyn_runtime(bridge.ROSLYN_MANIFEST, bridge.PWSH)
    tools.pop('elapsed_seconds', None)
    tools['pwsh'] = str(bridge.PWSH)
    return tools


def _archive(folder, request):
    try:
        receipt = archive_request(folder, request)
        return {'request_preserved': receipt['preserved'], 'request_file': receipt['file'],
                'request_sha256': receipt['sha256']}
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        return {'request_preserved': False, 'request_preservation_reason': str(exc)}


def admit(module, source_import, output_dir=None):
    folder = campaign(output_dir, 'csharp-admit')
    result = {'status': 'INCONCLUSIVE', 'reason': 'Admission non terminée.', 'language': 'csharp',
              'baseline': None, 'current_model': None, 'source': None, 'source_import': None,
              'mapping': [], 'tools': None, 'evidence_dir': str(folder), 'input_execution': False,
              'limitations': LIMITATIONS}
    result.update(_archive(folder, {'module': module, 'source_import': source_import}))
    before = None
    try:
        if not result['request_preserved']:
            raise ValueError('Admission hors borne d’archive : ' + result['request_preservation_reason'])
        before = _producer_hashes()
        basis = foundations()
        if type(source_import) is not dict or source_import.get('status') != 'IMPORTED':
            raise ValueError('Reçu source non importé.')
        baseline = module_model.validate_module(source_import['module'])
        normalized = module_model.validate_module(module)
        provenance = baseline['provenance']
        if provenance.get('origin', {}).get('language') != 'csharp':
            raise ValueError('Source originale C# requise.')
        if normalized['provenance'] != provenance or normalized['module_id'] != baseline['module_id']:
            raise ValueError('Provenance du modèle courant différente.')
        if _signatures(normalized) != _signatures(baseline):
            raise ValueError('Signatures ou identités du modèle courant différentes.')
        raw = provenance['source_text'].encode('utf-8')
        if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != provenance['source_sha256']:
            raise ValueError('Identité ou borne source invalide.')
        (folder / 'source.cs').write_bytes(raw)
        tools = _tools()
        # The legacy PWSH launcher cannot use a cwd beyond Windows MAX_PATH.
        # Keep readmission evidence in its own short, unique V0.5.0 directory.
        reparsed = csharp_import.import_csharp(raw, BASE / 'runtime')
        result['readmission_evidence_dir'] = reparsed['evidence_dir']
        if reparsed.get('status') != 'IMPORTED':
            if reparsed.get('status') == 'REFUSED':
                raise ValueError('Source refusée par la réanalyse : ' + reparsed['reason'])
            raise RuntimeError('Réanalyse inconclusive : ' + reparsed['reason'])
        if module_model.validate_module(reparsed['module']) != baseline:
            raise ValueError('Le baseline fourni diffère de la réanalyse complète du source.')
        if _producer_hashes() != before or foundations() != basis or _tools() != tools:
            raise RuntimeError('Producteurs ou outils modifiés pendant l’admission.')
        if sha(folder / 'source.cs') != provenance['source_sha256']:
            raise RuntimeError('Source archivée modifiée pendant l’admission.')
        result.update(status='ADMITTED', reason='Source C# entière réanalysée, baseline et signatures exacts.',
                      baseline=baseline, current_model=normalized, source=provenance['source_text'],
                      source_import=source_import, mapping=_signatures(baseline), tools=tools,
                      producer_files_sha256=before, foundation_files_sha256=basis)
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        result.update(status='REFUSED', reason=str(exc))
    except Exception as exc:
        result.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc))
    finally:
        if before is not None:
            try:
                if _producer_hashes() != before:
                    result.update(status='INCONCLUSIVE', reason='Producteurs modifiés pendant admission.')
            except Exception as exc:
                result.update(status='INCONCLUSIVE', reason=str(exc))
        save(folder / 'report.json', result)
    return result


def _cases(records, baseline):
    if type(records) is not list or not 1 <= len(records) <= 4096:
        raise ValueError('Entre 1 et 4096 cas communs requis.')
    units = {unit['id']: unit for unit in baseline['units']}
    counts = dict.fromkeys(units, 0)
    cases = []
    for index, record in enumerate(records):
        if type(record) is not dict or type(record.get('unit_id')) is not str or record['unit_id'] not in units:
            raise ValueError('Identité du cas invalide.')
        uid = record['unit_id']; unit = units[uid]; inputs = record.get('inputs')
        if type(inputs) is not list or len(inputs) != len(unit['parameters']):
            raise ValueError('Nombre d’arguments du cas invalide.')
        for value, parameter in zip(inputs, unit['parameters']):
            if parameter['type'] == 'bool':
                if type(value) is not bool: raise ValueError('Argument bool exact requis.')
            elif type(value) is not int or not -2147483648 <= value <= 2147483647:
                raise ValueError('Argument Int32 exact requis.')
        counts[uid] += 1
        if counts[uid] > 512: raise ValueError('Plus de 512 cas par fonction.')
        cases.append({'index': index, 'unit_id': uid, 'inputs': inputs[:]})
    return cases


def _parse_output(text, cases, baseline):
    lines = text.splitlines()
    if len(lines) != len(cases): raise ValueError('Nombre de résultats différent des cas.')
    types = {u['id']: u['return_type'] for u in baseline['units']}
    values = []
    for line, case in zip(lines, cases):
        def unique(pairs):
            out = {}
            for key, value in pairs:
                if key in out: raise ValueError('Clé de résultat répétée.')
                out[key] = value
            return out
        row = json.loads(line, object_pairs_hook=unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Résultat non fini.')))
        if type(row) is not dict or set(row) != {'index', 'unit_id', 'value'}:
            raise ValueError('Champs de résultat différents.')
        if type(row['index']) is not int or row['index'] != case['index'] or row['unit_id'] != case['unit_id']:
            raise ValueError('Association cas/fonction différente.')
        value = row['value']
        if types[case['unit_id']] == 'bool':
            if type(value) is not bool: raise ValueError('Résultat bool exact requis.')
        elif type(value) is not int or not -2147483648 <= value <= 2147483647:
            raise ValueError('Résultat Int32 exact requis.')
        values.append(value)
    return values


def _process(command, folder, phase, timeout):
    env = dict(os.environ)
    for key in ('DOTNET_STARTUP_HOOKS', 'DOTNET_ADDITIONAL_DEPS', 'DOTNET_SHARED_STORE',
                'DOTNET_ROOT', 'DOTNET_ROOT_X64', 'CORECLR_ENABLE_PROFILING', 'COR_ENABLE_PROFILING'):
        env.pop(key, None)
    env['PSModulePath'] = str(Path(command[0]).parent / 'Modules')
    temp = folder / 'temp'; temp.mkdir(exist_ok=True)
    env.update(TEMP=str(temp), TMP=str(temp))
    stdout_path, stderr_path = folder / (phase + '-stdout.txt'), folder / (phase + '-stderr.txt')
    result = {'command': [str(x) for x in command], 'timeout_seconds': timeout, 'timed_out': False,
              'process_exited': False, 'stdout_file': str(stdout_path), 'stderr_file': str(stderr_path)}
    process = None
    try:
        with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            process = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr,
                                       creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
            result['pid'] = process.pid
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result['timed_out'] = True
                if os.name == 'nt':
                    taskkill = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/taskkill.exe'
                    stop = subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                                          timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                    result['tree_stop_exit_code'] = stop.returncode
                if process.poll() is None: process.kill()
                process.wait(timeout=5)
            result['exit_code'] = process.returncode
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
            result['process_exited'] = process.poll() is not None
        save(folder / (phase + '.json'), result)
    if stdout_path.stat().st_size > 4 * 1024 * 1024 or stderr_path.stat().st_size > 4 * 1024 * 1024:
        raise RuntimeError('Sortie processus au-delà de 4 Mio.')
    result['stdout'] = stdout_path.read_text(encoding='utf-8')
    result['stderr'] = stderr_path.read_text(encoding='utf-8')
    return result


def _mapping(mapping, baseline):
    if type(mapping) is not list or len(mapping) != len(baseline['units']):
        raise ValueError('Inventaire compilation différent.')
    keys = {'id', 'name', 'qualified_name', 'declaring_type', 'parameter_types', 'return_type'}
    for item, unit in zip(mapping, baseline['units']):
        if type(item) is not dict or set(item) != keys or item['id'] != unit['id'] or item['name'] != unit['name']:
            raise ValueError('Identité compilation différente.')
        if item['qualified_name'] != unit['qualified_name'] or item['return_type'] != unit['return_type']:
            raise ValueError('Signature compilation différente.')
        if item['parameter_types'] != [p['type'] for p in unit['parameters']]:
            raise ValueError('Types compilation différents.')
        if type(item['declaring_type']) is not str or not 1 <= len(item['declaring_type']) <= 1024:
            raise ValueError('Type metadata invalide.')
    return mapping


def execute(admission, records, output_dir):
    folder = campaign(output_dir, 'csharp-execute')
    result = {'status': 'INCONCLUSIVE', 'reason': 'Exécution non terminée.', 'compile_status': 'NOT_STARTED',
              'execution_status': 'NOT_STARTED', 'values': [], 'mapping': [], 'evidence_dir': str(folder),
              'language': 'csharp', 'limitations': LIMITATIONS}
    result.update(_archive(folder, {'admission': admission, 'records': records}))
    runtime = None; watched = {}; before = None
    try:
        if not result['request_preserved']:
            raise ValueError('Demande hors borne : ' + result['request_preservation_reason'])
        before = _producer_hashes()
        if type(admission) is not dict or admission.get('status') != 'ADMITTED':
            raise ValueError('Admission C# requise.')
        fresh = admit(admission['current_model'], admission['source_import'], folder)
        result['readmission'] = fresh['evidence_dir']
        if fresh['status'] != 'ADMITTED':
            result.update(status=fresh['status'], reason=fresh['reason']); return result
        for key in ('baseline', 'current_model', 'source', 'mapping', 'tools', 'producer_files_sha256', 'foundation_files_sha256'):
            if admission.get(key) != fresh[key]: raise ValueError('Admission altérée ou périmée : ' + key)
        baseline = fresh['baseline']; tools = fresh['tools']
        cases = _cases(records, baseline)
        runtime = campaign(BASE / 'runtime', 'c')
        result['runtime_dir'] = str(runtime)
        raw = fresh['source'].encode('utf-8')
        (runtime / 'source.cs').write_bytes(raw)
        save(runtime / 'expected.json', {'source_sha256': baseline['provenance']['source_sha256'], 'units': _signatures(baseline)})
        save(runtime / 'cases.json', cases)
        for name in ('original_csharp_compile.ps1', 'original_csharp_run.ps1'):
            shutil.copyfile(BASE / name, runtime / name)
        def guard():
            if _producer_hashes() != before or foundations() != fresh['foundation_files_sha256'] or _tools() != tools:
                raise RuntimeError('Producteurs/fondations/outils modifiés avant ou pendant effet.')
            if any(sha(path) != digest for path, digest in watched.items()):
                raise RuntimeError('Fichier de travail ou preuve modifié.')
        def archive_new():
            for path in runtime.iterdir():
                if path.is_file() and not (folder / path.name).exists():
                    shutil.copyfile(path, folder / path.name)
                    original_hash = sha(path); copy_hash = sha(folder / path.name)
                    if original_hash != copy_hash:
                        raise RuntimeError('Copie de preuve différente du fichier de travail.')
                    watched[path] = original_hash; watched[folder / path.name] = copy_hash
        archive_new(); guard()
        command = [tools['pwsh'], '-NoLogo', '-NoProfile', '-NonInteractive', '-File']
        result['compile_status'] = 'STARTED'
        compiled = _process(command + [str(runtime / 'original_csharp_compile.ps1'), '-SourcePath', str(runtime / 'source.cs'),
                                      '-ExpectedPath', str(runtime / 'expected.json'), '-AssemblyPath', str(runtime / 'original.dll')], runtime, 'compile', 45)
        result['compile_process'] = compiled
        if compiled['timed_out'] or compiled['exit_code'] or compiled['stderr'].strip():
            raise RuntimeError('Compilation originale interrompue ou en erreur.')
        (runtime / 'compile-result.json').write_text(compiled['stdout'], encoding='utf-8')
        receipt = load_json(runtime / 'compile-result.json')
        result['compile_receipt'] = receipt
        if receipt.get('status') != 'COMPILED': raise RuntimeError('Compilation DLL inconclusive : ' + str(receipt.get('reason')))
        if receipt.get('language_version') != 'CSharp12' or receipt.get('check_overflow') is not False:
            raise RuntimeError('Options de compilation originales différentes du profil qualifié.')
        if receipt['source_sha256'] != baseline['provenance']['source_sha256'] or sha(runtime / 'original.dll') != receipt['assembly_sha256']:
            raise RuntimeError('Identité source/DLL différente.')
        mapping = _mapping(receipt['mapping'], baseline)
        result.update(compile_status='COMPILED', mapping=mapping)
        save(runtime / 'runner-request.json', {'assembly_sha256': receipt['assembly_sha256'], 'mapping': mapping, 'cases': cases})
        archive_new(); guard()
        result['execution_status'] = 'STARTED'
        run = _process(command + [str(runtime / 'original_csharp_run.ps1'), '-AssemblyPath', str(runtime / 'original.dll'),
                                 '-RequestPath', str(runtime / 'runner-request.json')], runtime, 'execute', 15)
        result['execution_process'] = run
        if run['timed_out'] or run['exit_code'] or run['stderr'].strip():
            raise RuntimeError('Exécution originale interrompue ou en erreur.')
        values = _parse_output(run['stdout'], cases, baseline)
        archive_new(); guard()
        tools_after = _tools()
        if tools_after != tools: raise RuntimeError('Identité finale des outils différente.')
        result.update(status='EXECUTED', execution_status='EXECUTED', values=values,
                      reason='Source originale compilée et exécutée sur les cas communs.',
                      producer_files_sha256=before, tools_before=tools, tools_after=tools_after, cases_count=len(cases))
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        result.update(status='REFUSED', reason=str(exc), values=[])
    except Exception as exc:
        result.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc), values=[])
    finally:
        for key in ('compile_status', 'execution_status'):
            if result[key] == 'STARTED': result[key] = 'INCONCLUSIVE'
        if runtime is not None:
            try:
                for path in runtime.iterdir():
                    if path.is_file() and not (folder / path.name).exists(): shutil.copyfile(path, folder / path.name)
                result['runtime_files_sha256'] = {p.name: sha(p) for p in runtime.iterdir() if p.is_file()}
                result['saved_files_sha256'] = {p.name: sha(p) for p in folder.iterdir() if p.is_file() and p.name != 'report.json'}
                if any(sha(path) != digest for path, digest in watched.items()):
                    result.update(status='INCONCLUSIVE', reason='Identité finale de preuve différente.', values=[])
                if before != _producer_hashes():
                    result.update(status='INCONCLUSIVE', reason='Producteurs modifiés.', values=[])
            except Exception as exc:
                result.update(status='INCONCLUSIVE', reason='Vérification finale : ' + str(exc), values=[])
        save(folder / 'report.json', result)
    return result
