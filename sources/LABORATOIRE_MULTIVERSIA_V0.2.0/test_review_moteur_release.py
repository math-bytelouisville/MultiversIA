"""Independent consistency review; inert fixture evidence, never product proof."""
from copy import deepcopy
import unittest
import check_release
import test_review_evaluation_release as fixtures


class MoteurReleaseChecks(fixtures.ReviewerReleaseChecks):
    def setUp(self):
        super().setUp()
        expected = [
            ('maximum', 'PROUVÉ_CONDITIONNEL', 'CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION'),
            ('minimum_wrong_max', 'RÉFUTÉ_CONDITIONNEL', 'CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION'),
            ('arithmetic_refused', 'INCONCLUSIF', 'C# hors périmètre : UNSUPPORTED_SYNTAX'),
            ('reflexive_contract', 'INCONCLUSIF', 'CONTRAT_NON_DISCRIMINANT_SUR_ECHANTILLON'),
        ]
        self.q['cases'] = [{'name': name, 'actual': verdict, 'expected': verdict,
                            'reason': reason, 'expected_reason': reason, 'pass': True}
                           for name, verdict, reason in expected]
        fixtures.save(self.qfolder / 'results.json', self.q)

    def test_case_failure_overrides_success_flag(self):
        changed = deepcopy(self.q)
        changed['cases'][0]['pass'] = False
        fixtures.save(self.qfolder / 'results.json', changed)
        self.assertEqual(check_release.check()['status'], 'NOT_READY')

    def test_changed_end_source_overrides_unchanged_flag(self):
        changed = deepcopy(self.q)
        changed['sources_sha256_at_end']['csharp_bridge.py'] = '0' * 64
        fixtures.save(self.qfolder / 'results.json', changed)
        self.assertEqual(check_release.check()['status'], 'NOT_READY')

    def test_generic_inconclusive_not_a_successful_refusal(self):
        changed = deepcopy(self.q)
        changed['cases'][2]['reason'] = 'RAM indisponible'
        fixtures.save(self.qfolder / 'results.json', changed)
        self.assertEqual(check_release.check()['status'], 'NOT_READY')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MoteurReleaseChecks))
    fixtures.save(fixtures.RUN / 'moteur-final-results.json', {
        'reviewer': 'moteur', 'tests': result.testsRun, 'errors': len(result.errors),
        'failures': len(result.failures),
        'scope': 'Inert fixtures only, shared setup with evaluator; independent negative checks and rerun.'})
    print(fixtures.RUN)
    raise SystemExit(not result.wasSuccessful())
