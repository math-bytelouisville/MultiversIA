"""Independent release-gate negative checks on clearly artificial evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock
import uuid
import check_release

BASE=Path(__file__).resolve().parent
RUN=BASE/'evidence'/('evaluation-release-fixtures-'+uuid.uuid4().hex)
RUN.mkdir(exist_ok=False)
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


class ReviewerReleaseChecks(unittest.TestCase):
    def setUp(self):
        self.root=RUN/self._testMethodName
        self.root.mkdir()
        self.patch=mock.patch.object(check_release,'BASE',self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        files={name for names,_ in check_release.MODULES.values() for name in names}
        files.update(('check_release.py','installer-raccourci.ps1','DEMARRER.txt','test_integration.py'))
        for name in files:(self.root/name).write_text('INERT TEST FIXTURE '+name,encoding='utf-8')
        self.hashes={name:digest(self.root/name) for name in files}
        for module,(names,reviewers) in check_release.MODULES.items():
            for reviewer in reviewers:
                save(self.root/f'REVUE_{reviewer}_{module}.json',{'verdict':'PASS','reviewer':reviewer,'fixture':True,'files_sha256':{n:self.hashes[n] for n in names}})
        for name in ('engine-test','ui-test','csharp-unit'):
            save(self.root/f'evidence/{name}-results.json',{'tests':1,'errors':0,'failures':0,'fixture':True})
        self.qfolder=self.root/'evidence/csharp-qualification/campaign-fixture'
        self.qfolder.mkdir(parents=True)
        save(self.qfolder/'protocol.json',{'fixture':True})
        self.qsources={n:self.hashes[n] for n in ('csharp_bridge.py','qualify_csharp.py')}
        expected=(('maximum','PROUVÉ_CONDITIONNEL','CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION'),
                  ('minimum_wrong_max','RÉFUTÉ_CONDITIONNEL','CERTIFICAT_LEAN_ACCEPTE_SUR_TRADUCTION'),
                  ('arithmetic_refused','INCONCLUSIF','C# hors périmètre : UNSUPPORTED_SYNTAX'),
                  ('reflexive_contract','INCONCLUSIF','CONTRAT_NON_DISCRIMINANT_SUR_ECHANTILLON'))
        cases=[{'name':name,'pass':True,'actual':verdict,'expected':verdict,'reason':reason,'expected_reason':reason} for name,verdict,reason in expected]
        self.q={'complete':True,'all_pass':True,'cases':cases,
                'sources_and_protocol_unchanged':True,'sources_sha256_at_start':self.qsources,'sources_sha256_at_end':self.qsources,
                'protocol_sha256_at_start':digest(self.qfolder/'protocol.json'),'protocol_sha256_at_end':digest(self.qfolder/'protocol.json'),
                'run_dir':str(self.qfolder),'fixture':True}
        save(self.qfolder/'results.json',self.q)
        save(self.root/'evidence/csharp-qualification/latest.json',{'results':str(self.qfolder/'results.json')})
        save(self.root/'evidence/integration.json',{'verdict':'PASS','fixture':True,'files_sha256':self.hashes})

    def test_coherent_fixture_accepted(self):
        result=check_release.check()
        self.assertEqual(result['status'],'PASS',result['problems'])

    def test_modified_source_invalidates_reviews(self):
        (self.root/'app.py').write_text('CHANGED',encoding='utf-8')
        self.assertEqual(check_release.check()['status'],'NOT_READY')

    def test_incomplete_review_rejected(self):
        save(self.root/'REVUE_EVALUATION_MOTEUR.json',{'verdict':'PASS','reviewer':'evaluation','files_sha256':{}})
        self.assertEqual(check_release.check()['status'],'NOT_READY')

    def test_wrong_reviewer_rejected(self):
        path=self.root/'REVUE_EVALUATION_MOTEUR.json';r=json.loads(path.read_text(encoding='utf-8'));r['reviewer']='author';save(path,r)
        self.assertEqual(check_release.check()['status'],'NOT_READY')

    def test_incomplete_integration_rejected(self):
        save(self.root/'evidence/integration.json',{'verdict':'PASS','files_sha256':{'DEMARRER.txt':self.hashes['DEMARRER.txt']}})
        self.assertEqual(check_release.check()['status'],'NOT_READY')

    def test_changed_qualification_protocol_rejected(self):
        save(self.qfolder/'protocol.json',{'fixture':True,'changed':True})
        self.assertEqual(check_release.check()['status'],'NOT_READY')

    def test_missing_qualification_pointer_fails_closed(self):
        save(self.root/'evidence/csharp-qualification/latest.json',{})
        self.assertEqual(check_release.check()['status'],'NOT_READY')

    def test_failed_case_rejected_despite_success_aggregate(self):
        self.q['cases'][0]['pass']=False
        save(self.qfolder/'results.json',self.q)
        self.assertEqual(check_release.check()['status'],'NOT_READY')

    def test_different_final_hash_rejected_despite_unchanged_flag(self):
        self.q['sources_sha256_at_end']=dict(self.qsources)
        self.q['sources_sha256_at_end']['csharp_bridge.py']='0'*64
        save(self.qfolder/'results.json',self.q)
        self.assertEqual(check_release.check()['status'],'NOT_READY')

    def test_generic_refusal_does_not_replace_expected_case_reason(self):
        self.q['cases'][2].update(reason='RAM insuffisante',expected_reason='RAM insuffisante')
        save(self.qfolder/'results.json',self.q)
        self.assertEqual(check_release.check()['status'],'NOT_READY')

    def test_duplicate_case_does_not_replace_missing_case(self):
        self.q['cases'][3]=deepcopy(self.q['cases'][2])
        save(self.qfolder/'results.json',self.q)
        self.assertEqual(check_release.check()['status'],'NOT_READY')


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReviewerReleaseChecks))
    save(RUN/'result.json',{'tests':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),'scope':'Artificial fixtures for release consistency guards only; no fabricated product qualification.'})
    print(str(RUN))
    raise SystemExit(not result.wasSuccessful())
