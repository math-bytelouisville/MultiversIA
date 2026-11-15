"""Local code/model/code pipeline with bounded, contract-preserving repair.

Finite checks are evidence, not a universal proof. Every attempt is retained.
No operation runs on import or on reopening the workshop.
"""
from copy import deepcopy
from pathlib import Path
from common import BASE, campaign, foundations, identity, save, sha, timestamp, archive_request
import module_model
import module_backends
from emitters import TARGETS, emit_module
import verifier

SOURCES = ('rust', 'csharp')
LIMITATIONS = [
    'Profil borné de fonctions pures Int32/bool et appels internes sans récursion.',
    'Arithmétique add32/sub32/mul32/neg32 explicite : retour signé modulo 2^32 après chaque opération ; division et exceptions hors profil.',
    'Contrôles finis, sans preuve universelle ni migration de site complet.',
    'Réparation des corps depuis leurs contrats inchangés ; aucun comportement métier inventé.',
    'Originaux Rust/C# exécutés seulement après réadmission complète dans leur profil pur ; comparaison sur cas finis.',
    'Les contrôles machine ne remplacent pas les deux revues indépendantes du composant.',
]


def import_source(source, source_language, output_root=None):
    if type(source_language) is not str or source_language not in SOURCES:
        folder = campaign(output_root, 'import-refused')
        result = dict(status='REFUSED', reason='Langage source disponible requis : rust ou csharp.',
                      module=None, source_language=source_language if type(source_language) is str else None,
                      input_execution=False, evidence_dir=str(folder), limitations=LIMITATIONS)
        if type(source) in (str, bytes):
            raw = source.encode('utf-8', errors='surrogatepass') if type(source) is str else source
            if len(raw) <= 512 * 1024:
                (folder / 'source.bin').write_bytes(raw)
                result['source_preserved'] = True
        save(folder / 'report.json', result)
        return result
    foundations()
    if source_language == 'rust':
        from rust_import import import_rust
        result = import_rust(source, output_dir=output_root or BASE / 'runs')
    else:
        from csharp_import import import_csharp
        result = import_csharp(source, output_dir=output_root)
    result['source_language'] = source_language
    if result.get('evidence_dir'):
        save(Path(result['evidence_dir']) / 'import-receipt.json', result)
    return result


def revise(module, unit_id, spec, intent, output_root=None):
    folder = campaign(output_root, 'revision')
    request = {'preserved': False}
    try:
        request = archive_request(folder, dict(module=module, unit_id=unit_id, spec=spec, intent=intent))
        before = foundations()
        previous = module_model.validate_module(module)
        save(folder / 'original-module.json', previous)
        result = module_model.revise_unit(previous, unit_id, spec, intent)
        if foundations() != before:
            raise RuntimeError('Fondations modifiées pendant la révision.')
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        result = dict(status='REFUSED', reason=str(exc), module=None, synthesis=None, change={})
    except Exception as exc:
        result = dict(status='INCONCLUSIVE', reason=str(exc), module=None, synthesis=None, change={})
    result['evidence_dir'] = str(folder)
    result['request_archive'] = request
    save(folder / 'report.json', result)
    return result


def _diagnose(module):
    records, _, _ = module_backends._records(module)
    failures, missing = [], []
    for uid in module['topological_order']:
        cases = [(i, r) for i, r in enumerate(records) if r['unit_id'] == uid]
        admissible = [(i, r) for i, r in cases if r['precondition'] is True]
        if not admissible:
            missing.append(uid)
        for index, row in admissible:
            if row['expected_postcondition'] is not True or row['call_precondition_violations']:
                failures.append(dict(case_index=index, unit_id=uid, inputs=row['inputs'],
                                     actual=row['expected'], postcondition=row['expected_postcondition'],
                                     call_precondition_violations=row['call_precondition_violations']))
    return {'status': 'COUNTEREXAMPLE' if failures else 'INCONCLUSIVE' if missing else 'TESTED_FINITE',
            'cases': records, 'counterexamples': failures, 'units_without_precondition_witness': missing,
            'oracle': 'independent_direct_call_interpreter', 'formal_scope': 'NOT_PROVED'}


def _semantic_key(module):
    return identity([{'id': u['id'], 'body': u['body'], 'contract': u['contract']} for u in module['units']])


def _repair_invariants(original, candidate):
    if any(original[k] != candidate[k] for k in ('schema_version', 'semantic_profile', 'module_id', 'name', 'provenance')):
        raise ValueError('Une réparation ne peut pas changer l’identité ou la provenance du modèle.')
    fields = ('id', 'name', 'qualified_name', 'parameters', 'return_type', 'contract')
    if len(original['units']) != len(candidate['units']) or any(
        any(a[k] != b[k] for k in fields) for a, b in zip(original['units'], candidate['units'])
    ):
        raise ValueError('Une réparation ne peut pas modifier les signatures ou les contrats.')


def _verified(report, targets):
    checks = report.get('generated_checks', [])
    return (report.get('status') == 'TESTED_FINITE' and len(checks) == len(targets)
            and {c.get('language') for c in checks} == set(targets)
            and all(c.get('status') == 'TESTED_FINITE' and c.get('model_agreement') == 'PASS'
                    and c.get('execution_status') == 'EXECUTED' for c in checks))


def translate(module, targets, source_import=None, auto_correct=True, max_repairs=2, output_root=None):
    folder = campaign(output_root, 'translation')
    result = dict(status='REFUSED', reason='Admission non terminée.', module=None, artifacts=[], verification=None,
                  repair={'status': 'UNRESOLVED', 'promoted': False, 'attempts': [], 'max_repairs': None},
                  events=[], evidence_dir=str(folder), limitations=LIMITATIONS, formal_scope='NOT_PROVED')
    result['request_archive'] = {'preserved': False}
    original = current = None

    def event(kind, **fields):
        result['events'].append(dict(index=len(result['events']), at=timestamp(), kind=kind, **fields))
        save(folder / 'state.json', result)

    try:
        result['request_archive'] = archive_request(folder, {'module': module, 'targets': targets,
            'source_import': source_import, 'auto_correct': auto_correct, 'max_repairs': max_repairs})
        baseline = foundations()
        code_hashes = {name: sha(BASE / name) for name in ('migrator.py', 'common.py', 'emitters.py', 'verifier.py')}
        result['implementation_files_sha256'] = code_hashes
        if type(auto_correct) is not bool or type(max_repairs) is not int or not 0 <= max_repairs <= 3:
            raise ValueError('Correction booléenne et budget entier de0 à3 requis.')
        if (type(targets) is not list or not 1 <= len(targets) <= 3
                or any(type(t) is not str or t not in TARGETS for t in targets)
                or len(set(targets)) != len(targets)):
            raise ValueError('Une à trois cibles distinctes disponibles sont requises.')
        original = module_model.validate_module(module)
        current = deepcopy(original)
        result['module'] = original
        result['repair'].update(status='DISABLED' if not auto_correct else 'NOT_NEEDED', max_repairs=max_repairs)
        seen = {_semantic_key(current)}
        event('ADMITTED', revision_id=current['revision_id'])
        for iteration in range(max_repairs + 1):
            attempt_dir = folder / ('attempt-' + str(iteration))
            attempt_dir.mkdir()
            save(attempt_dir / 'module.json', current)
            diagnosis = _diagnose(current)
            save(attempt_dir / 'diagnosis.json', diagnosis)
            artifacts = [emit_module(current, language) for language in targets]
            for artifact in artifacts:
                (attempt_dir / ('generated-' + artifact['language'] + artifact['extension'])).write_text(
                    artifact['source'], encoding='utf-8', newline='\n')
            save(attempt_dir / 'artifacts.json', artifacts)
            event('VERIFYING', attempt=iteration, revision_id=current['revision_id'], diagnosis=diagnosis['status'])
            checked = verifier.verify(current, artifacts, attempt_dir, source_import=source_import)
            result.update(artifacts=artifacts, verification=checked, verified_revision_id=current['revision_id'])
            event('VERIFIED', attempt=iteration, status=checked['status'], evidence_dir=checked['evidence_dir'])
            if diagnosis['status'] == 'TESTED_FINITE' and _verified(checked, targets):
                result.update(status='TESTED_FINITE', reason='Contrats et cibles contrôlés sur les cas finis archivés.', module=current)
                result['repair'].update(status='REPAIRED_FINITE' if iteration else 'NOT_NEEDED' if auto_correct else 'DISABLED',
                                        promoted=iteration > 0)
                break
            if checked['status'] == 'TESTED_FINITE' and not _verified(checked, targets):
                result.update(status='INCONCLUSIVE', reason='Reçu de succès incomplet : toutes les cibles exécutées doivent être attestées.')
            else:
                result.update(status=checked['status'] if checked['status'] != 'TESTED_FINITE' else diagnosis['status'],
                              reason=checked['reason'] if checked['status'] != 'TESTED_FINITE' else 'Diagnostic modèle incomplet ou en échec.')
            if not auto_correct:
                break
            result['repair']['status'] = 'UNRESOLVED'
            # Only a witnessed contract error with correctly executing generated targets is repairable here.
            if (diagnosis['status'] != 'COUNTEREXAMPLE' or checked['status'] != 'COUNTEREXAMPLE'
                    or len(checked['generated_checks']) != len(targets)
                    or {c.get('language') for c in checked['generated_checks']} != set(targets)
                    or any(c.get('model_agreement') != 'PASS' or c.get('execution_status') != 'EXECUTED'
                           for c in checked['generated_checks'])):
                event('REPAIR_STOPPED', reason='Aucun défaut de contrat réparable établi, ou vérification indisponible.')
                break
            if iteration == max_repairs:
                result['repair']['status'] = 'BUDGET_EXHAUSTED'
                event('REPAIR_STOPPED', reason='Budget de réparations atteint.')
                break
            failed = {c['unit_id'] for c in diagnosis['counterexamples']}
            uid = next(uid for uid in current['topological_order'] if uid in failed)
            event('REPAIRING', unit_id=uid, attempt=iteration + 1)
            correction = module_model.revise_unit(current, uid, module_model.unit_spec(current, uid),
                'Auto-correction : respecter le contrat conservé de ' + uid + '.',
                limits={'max_seconds': 5, 'max_candidates': 10000, 'max_depth': 3, 'max_samples': 256})
            save(attempt_dir / 'repair.json', correction)
            trace = dict(attempt=iteration + 1, unit_id=uid, status=correction['status'], reason=correction['reason'],
                         old_revision_id=current['revision_id'], evidence_dir=str(attempt_dir),
                         contracts_changed=False)
            result['repair']['attempts'].append(trace)
            if correction['status'] != 'REVISED':
                event('REPAIR_STOPPED', reason='Synthèse sans candidat : ' + correction['reason'])
                break
            candidate = module_model.validate_module(correction['module'])
            _repair_invariants(original, candidate)
            trace['new_revision_id'] = candidate['revision_id']
            result['candidate_module'] = candidate
            key = _semantic_key(candidate)
            if key in seen:
                event('REPAIR_STOPPED', reason='Aucun progrès sémantique : candidat déjà rencontré.')
                break
            seen.add(key)
            current = candidate
        if foundations() != baseline or any(sha(BASE / name) != h for name, h in code_hashes.items()):
            raise RuntimeError('Implémentation modifiée pendant la campagne ; qualification non valable.')
        if result['repair']['promoted']:
            result.pop('candidate_module', None)
        event('FINISHED', status=result['status'], repair_status=result['repair']['status'], promoted=result['repair']['promoted'])
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        result.update(status='REFUSED', reason=str(exc), module=original)
        result['repair'].update(status='UNRESOLVED', promoted=False)
    except Exception as exc:
        result.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc), module=original)
        result['repair'].update(status='UNRESOLVED', promoted=False)
    save(folder / 'report.json', result)
    save(folder / 'state.json', result)
    return result
