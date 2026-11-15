"""Reviewer-owned arithmetic and permutation cases, reusing the author's builds."""
from fractions import Fraction
from pathlib import Path
import random
import uuid

import scoring_model
from rust_runtime import BASE, load, run_cases, save, sha


def rounded(x):
    scaled = x * 100
    cents = scaled.numerator // scaled.denominator
    if scaled - cents >= Fraction(1, 2):
        cents += 1
    return f'{cents // 100}.{cents % 100:02d}'


def expected(case, weight):
    points = []
    for q in case['questions']:
        good = {o['id'] for o in q['options'] if o['correct']}
        bad = {o['id'] for o in q['options'] if not o['correct']}
        chosen = set(q['selected'])
        exact = good == chosen
        value = Fraction(int(exact))
        if not exact and q['mode'] == 'multiple' and case['grading'] == 'partial':
            value = max(Fraction(0), Fraction(len(good & chosen), len(good))
                        - (weight * Fraction(len(bad & chosen), len(bad)) if bad else 0))
        points.append({'n': str(value.numerator), 'd': str(value.denominator), 'display': rounded(value), 'is_correct': exact})
    total = sum((Fraction(int(p['n']), int(p['d'])) for p in points), Fraction(0))
    percent = total * 100 / len(points)
    return {'score': {'n': str(total.numerator), 'd': str(total.denominator)},
            'percent': {'n': str(percent.numerator), 'd': str(percent.denominator)},
            'score_display': rounded(total), 'percent_display': rounded(percent),
            'passed': percent >= Fraction(case['passing_percent']),
            'correct_count': sum(p['is_correct'] for p in points), 'points': points}


def run():
    rng = random.Random(927041)
    cases = []
    for index in range(64):
        questions = []
        for j in range(1 + index % 50):
            n = rng.randint(2, 8)
            single = index % 5 == 0
            g = 1 if single else rng.randint(1, n)
            order = list(range(n))
            rng.shuffle(order)
            correct = set(order[:g])
            rng.shuffle(order)
            selected = set(order[:1 if single else rng.randint(1, n)])
            prefix = 'Q-' + str(j) + '-' + 'z' * 50
            options = [{'id': prefix + '-' + str(i), 'correct': i in correct} for i in range(n)]
            rng.shuffle(options)
            choices = [prefix + '-' + str(i) for i in selected]
            rng.shuffle(choices)
            questions.append({'id': prefix, 'mode': 'single' if single else 'multiple', 'options': options, 'selected': choices})
        cases.append({'questions': questions, 'grading': 'partial' if index % 3 else 'all-or-nothing',
                      'passing_percent': ['0', '100', '33.333333333333333333333333333', '66.666666666666666666666666667'][index % 4],
                      'show_correction': index % 2 == 0})
    root = BASE / 'evidence' / ('review-root-scoring-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    author = load(BASE / 'evidence/rust-author-smoke-589ad85e747842a0990da8e9b54a7b5e/result.json')
    records = []
    for entry in author['models']:
        build = load(entry['build'])
        model = load(Path(build['folder']) / 'model.json')
        weight = Fraction(model['penalty_weight']['n'], model['penalty_weight']['d'])
        execution = run_cases(build, cases, root)
        assert execution['status'] == 'EXECUTED', execution.get('reason')
        truth = [expected(case, weight) for case in cases]
        python = [scoring_model.evaluate(model, case) for case in cases]
        assert python == truth
        assert execution['results'] == truth
        records.append({'weight': str(weight), 'cases': len(cases), 'questions': sum(len(c['questions']) for c in cases),
                        'independent_fraction_oracle_equals_python_and_rust': True, 'execution': execution['evidence_dir']})
    report = {'status': 'PASS', 'reviewer': 'root', 'records': records,
              'scope': 'Independent random fixed-seed permutations, long IDs, exact threshold decisions and aggregation; two real Rust artifacts. Not a site migration proof.',
              'files_sha256': {n: sha(BASE / n) for n in ('scoring_model.py', 'scoring_rust.py', 'rust_support.rs', 'MODEL_CONTRACT.txt')},
              'source_review': ['closed typed AST and all branches validated', 'no code execution through model',
                                'checked intermediate arithmetic aligned after fixing output bound comparisons',
                                'lazy zero denominator guard', 'revision integrity and source provenance preserved']}
    save(root / 'result.json', report)
    save(BASE / 'REVUE_ROOT_SCORING.json', {**report, 'evidence': str(root / 'result.json')})
    print(str(root / 'result.json'))


if __name__ == '__main__':
    run()
