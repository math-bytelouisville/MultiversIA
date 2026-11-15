"""Explicit finite qualification of the Studio scoring migration and one revision."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fractions import Fraction
import json
from pathlib import Path
import traceback
import uuid

from rust_runtime import BASE, PREVIOUS, compile_model, load, owned, run_cases, save, sha

COMPONENT_FILES = ('scoring_model.py', 'scoring_rust.py', 'rust_support.rs', 'profile_import.py', 'migration_report.py',
                   'csharp_oracle.py', 'rust_runtime.py', 'migration_pipeline.py')
LIMITATIONS = [
    'Profil exact du QCM Studio livré, adapté explicitement ; pas un traducteur C# général.',
    'Seul le noyau de notation est généré en Rust. État, comptes, sauvegardes, HTTP et rendu restent à traduire.',
    'Les contrôles C# observent les résultats exposés par HTTP ; les fractions internes ne sont pas observées.',
    'Python et Rust comparent leurs rationnels exacts sur un corpus fini ; aucune preuve universelle annoncée.',
    'La variante modifie volontairement le barème et ne remplace pas le Studio C# installé.',
    'Les sources conservées et leurs empreintes ne constituent ni une signature ni une preuve de migration complète.',
]


def _code_hashes():
    return {name: sha(BASE / name) for name in COMPONENT_FILES}


def _compare_models(model, cases, execution):
    import scoring_model
    if execution.get('status') != 'EXECUTED' or len(execution.get('results', [])) != len(cases):
        raise RuntimeError('La campagne Rust ne fournit pas tous ses résultats.')
    expected = [scoring_model.evaluate(model, case) for case in cases]
    differences = [{'case_index': i, 'python': a, 'rust': b}
                   for i, (a, b) in enumerate(zip(expected, execution['results'])) if a != b]
    return expected, {'status': 'PASS' if not differences else 'FAIL', 'cases': len(cases),
                      'different_cases': len(differences), 'differences': differences}


def run(*, source_dir=None, penalty_n=1, penalty_d=2, progress=print):
    import csharp_oracle
    import migration_report
    import profile_import
    import scoring_model
    if type(penalty_n) is not int or type(penalty_d) is not int or penalty_d <= 0 or not 0 <= penalty_n <= penalty_d:
        raise ValueError('Le poids doit être une fraction comprise entre 0 et 1.')
    if penalty_n > 1_000_000 or penalty_d > 1_000_000:
        raise ValueError('La fraction dépasse les bornes du modèle.')
    weight = Fraction(penalty_n, penalty_d)
    penalty_n, penalty_d = weight.numerator, weight.denominator
    folder = owned(BASE / 'runs') / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'mia-studio-migration-report-1', 'status': 'INCONCLUSIVE',
              'at': datetime.now(timezone.utc).isoformat(), 'evidence_dir': str(folder),
              'scope': 'Noyau de notation du Studio : profil C# connu → modèle rationnel → Rust exécuté',
              'checks': [], 'limitations': LIMITATIONS, 'full_site_migrated': False}
    def check(name, condition):
        report['checks'].append({'name': name, 'pass': bool(condition)})
        if not condition:
            raise AssertionError(name)
    def checkpoint():
        save(folder / 'report.json', report)
    checkpoint()
    try:
        code = _code_hashes()
        report['implementation_sha256'] = code
        progress('1/6 — Conservation et contrôle du profil C#.', flush=True)
        receipt = profile_import.create_import(source_dir or PREVIOUS / 'qcm_studio', folder / 'imports')
        report['source_import'] = receipt
        check('Profil source admis', receipt.get('status') == 'IMPORTED')
        report['coverage'] = receipt.get('coverage', {})
        # Source identity is stable; the separately linked import receipt owns its paths and date.
        provenance = receipt['provenance']
        baseline = scoring_model.default_model(provenance)
        variant = scoring_model.revise_model(baseline, penalty_n=penalty_n, penalty_d=penalty_d,
                                            intent='Changer volontairement le poids des mauvaises sélections en barème partiel.')
        save(folder / 'baseline-model.json', baseline)
        save(folder / 'variant-model.json', variant)
        cases = csharp_oracle.generate_cases()
        check('Corpus explicite non vide et borné', type(cases) is list and 1 <= len(cases) <= 4096)
        save(folder / 'cases.json', cases)
        report['cases_sha256'] = sha(folder / 'cases.json')
        report['baseline'] = {'model_revision': baseline['revision_id'], 'model_file': str(folder / 'baseline-model.json')}
        report['variant'] = {'model_revision': variant['revision_id'], 'parent_revision': variant['parent_revision_id'],
                             'model_file': str(folder / 'variant-model.json'), 'penalty': {'n': penalty_n, 'd': penalty_d}}
        check('Révision reliée au modèle original', variant['parent_revision_id'] == baseline['revision_id'])
        checkpoint()
        progress('2/6 — Compilation du Rust généré depuis le modèle original.', flush=True)
        base_build = compile_model(baseline, folder / 'baseline')
        report['baseline']['build'] = base_build
        check('Compilation du modèle original', base_build['status'] == 'COMPILED')
        base_run = run_cases(base_build, cases, folder / 'baseline')
        base_values, comparison = _compare_models(baseline, cases, base_run)
        report['baseline'].update(execution=base_run, comparison=comparison)
        check('Python et Rust originaux concordent exactement', comparison['status'] == 'PASS')
        checkpoint()
        progress('3/6 — Comparaison avec les résultats du véritable serveur C#.', flush=True)
        oracle = csharp_oracle.collect_cases(cases, folder / 'csharp')
        # Raw C# decimals remain exact in the oracle's own archived JSON.
        # The presentation report embeds only its string-valued observations.
        report['csharp_oracle'] = {key: value for key, value in oracle.items() if key != 'results'}
        report['csharp_oracle']['observations'] = [
            {key: value for key, value in row.items() if key != 'result'} for row in oracle.get('results', [])]
        check('Oracle C# observé et complet', oracle.get('status') == 'COLLECTED' and len(oracle.get('results', [])) == len(cases))
        imported_files = {row['path']: row['sha256'] for row in receipt['source_inventory']}
        check('Le build C# correspond exactement aux sources importées',
              oracle.get('reference', {}).get('source_files_sha256') == imported_files)
        differences = []
        for index, (case, value, row) in enumerate(zip(cases, base_values, oracle['results'])):
            check('Observation C# n°' + str(index + 1), row.get('status') == 'OBSERVED' and row.get('index') == index
                  and row.get('case_sha256') == csharp_oracle.case_sha256(case))
            observed = csharp_oracle.observed_projection(value, case)
            if row['observed'] != observed:
                differences.append({'case_index': index, 'csharp': row['observed'], 'model': observed, 'input': case})
        report['csharp_comparison'] = {'status': 'PASS' if not differences else 'FAIL', 'cases': len(cases),
                                       'different_cases': len(differences), 'differences': differences,
                                       'scope': 'Projection HTTP ; rationnels internes C# non observés'}
        check('Résultats C# conformes à la projection du modèle', not differences)
        checkpoint()
        progress('4/6 — Reconstruction en Rust de la variante mathématique.', flush=True)
        variant_build = compile_model(variant, folder / 'variant')
        report['variant']['build'] = variant_build
        check('Compilation du modèle modifié', variant_build['status'] == 'COMPILED')
        variant_run = run_cases(variant_build, cases, folder / 'variant')
        variant_values, variant_comparison = _compare_models(variant, cases, variant_run)
        report['variant'].update(execution=variant_run, comparison=variant_comparison)
        check('Python et Rust modifiés concordent exactement', variant_comparison['status'] == 'PASS')
        changes = [{'case_index': i, 'input': cases[i], 'baseline': before, 'variant': after}
                   for i, (before, after) in enumerate(zip(base_values, variant_values)) if before != after]
        report['variant']['intentional_differences'] = changes
        report['variant']['different_cases'] = len(changes)
        should_change = Fraction(penalty_n, penalty_d) != 1
        check('Changement volontaire rendu observable', bool(changes) if should_change else not changes)
        if should_change:
            check('Le modèle modifié produit un code Rust différent',
                  base_build['files_sha256']['main.rs'] != variant_build['files_sha256']['main.rs'])
        progress('5/6 — Vérification des empreintes et conservation des preuves.', flush=True)
        check('Implémentation inchangée pendant la campagne', _code_hashes() == code)
        report['status'] = 'PASS'
    except Exception as exc:
        report['status'] = 'FAIL' if isinstance(exc, AssertionError) else 'INCONCLUSIVE'
        report['reason'] = type(exc).__name__ + ': ' + str(exc)
        (folder / 'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        checkpoint()
        try:
            report['report_html'] = str(folder / 'index.html')
            migration_report.render_report(report, folder / 'index.html')
        except Exception as exc:
            report['render_error'] = type(exc).__name__ + ': ' + str(exc)
            report['status'] = 'INCONCLUSIVE'
        checkpoint()
        # Only the pointer is replaceable; previous campaign folders remain untouched.
        save(BASE / 'latest-run.json', {'report': str(folder / 'report.json'), 'status': report['status']})
    progress('6/6 — Rapport enregistré : ' + str(folder / 'index.html'), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description='Comparer le noyau de notation C# → maths → Rust.')
    parser.add_argument('--source', help='Copie exacte du Studio livré ; sinon référence V0.2.')
    parser.add_argument('--penalty', default='1/2', help='Poids de pénalité de la variante, entre 0 et 1 (ex. 1/2).')
    args = parser.parse_args()
    try:
        parts = args.penalty.split('/')
        if len(parts) != 2 or any(not p.isascii() or not p.isdigit() or len(p) > 6 for p in parts):
            raise ValueError('Utilise une fraction comme 1/2.')
        result = run(source_dir=args.source, penalty_n=int(parts[0]), penalty_d=int(parts[1]))
        print(json.dumps({'status': result['status'], 'report': result['evidence_dir'], 'reason': result.get('reason')}, ensure_ascii=False), flush=True)
        return 0 if result['status'] == 'PASS' else 1
    except Exception as exc:
        print(str(exc), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
