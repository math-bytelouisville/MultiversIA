import hashlib
import base64
import json
from pathlib import Path
import re
import unittest
import uuid

import migration_report as subject

BASE = Path(__file__).resolve().parent
EVIDENCE = BASE / 'evidence' / ('report-tests-' + uuid.uuid4().hex)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.folder = EVIDENCE / self.id().rsplit('.', 1)[-1]
        self.folder.mkdir(parents=True)

    def test_missing_fields_remain_inconclusive_and_no_auto_run(self):
        path = Path(subject.render_report({}, self.folder / 'index.html'))
        text = path.read_text(encoding='utf-8')
        self.assertIn('>INCONCLUSIF</span>', text)
        self.assertIn("connect-src &#x27;none&#x27;", text)
        self.assertNotIn('fetch(', text)
        self.assertNotIn('XMLHttpRequest', text)

    def test_hostile_json_is_preserved_and_cannot_close_script(self):
        report = {'status':'INCONCLUSIVE','limitations':['</script><script>globalThis.PWNED=true</script>&\u2028\u2029', 'é漢字']}
        path = Path(subject.render_report(report, self.folder / 'index.html'))
        text = path.read_text(encoding='utf-8')
        encoded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', text, re.S).group(1)
        self.assertNotIn('<', encoded)
        self.assertEqual(json.loads(encoded), report)
        self.assertEqual(text.count('<script'), 3)
        self.assertNotIn('innerHTML', subject.SCRIPT)

    def test_script_hash_matches_exact_inline_code(self):
        text = Path(subject.render_report({}, self.folder / 'index.html')).read_text(encoding='utf-8')
        script = re.search(r'<script>(.*?)</script>', text, re.S).group(1)
        expected = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn('sha256-' + expected, text)

    def test_only_text_links_inside_evidence_directory(self):
        (self.folder / 'mémo # 1.json').write_text('{}', encoding='utf-8')
        outside = EVIDENCE / 'outside.json'
        outside.write_text('{}', encoding='utf-8')
        links = subject._links({'ok':'mémo # 1.json','outside':str(outside), 'bad':'javascript:alert(1)', 'remote':'https://example.org/a.json','escape':'../outside.json'}, self.folder)
        self.assertEqual(len(links), 1)
        self.assertIn('%23', links[0]['url'])
        self.assertIn('%C3%A9', links[0]['url'])

    def test_write_once_size_and_non_json_refusals(self):
        target = self.folder / 'index.html'
        subject.render_report({}, target)
        before = target.read_bytes()
        with self.assertRaises(FileExistsError):subject.render_report({'status':'PASS'}, target)
        self.assertEqual(before, target.read_bytes())
        with self.assertRaises(ValueError):subject.render_report([], self.folder / 'bad.html')
        with self.assertRaises(ValueError):subject.render_report({'v':float('nan')}, self.folder / 'bad.html')
        with self.assertRaises(ValueError):subject.render_report({}, self.folder / 'bad.exe')
        from unittest.mock import patch
        with patch.object(subject, 'MAX_REPORT_BYTES', 10):
            with self.assertRaises(ValueError):subject.render_report({'large':'x'*20}, self.folder / 'large.html')

    def test_source_and_build_links_keep_all_text_assets_but_never_executable(self):
        snapshot=self.folder/'snapshot';snapshot.mkdir()
        names=['A.cs','A.csproj','A.cshtml','A.js','A.css','A.json','not.exe']
        for name in names:(snapshot/name).write_text('fixture',encoding='utf-8')
        build=self.folder/'rust';build.mkdir()
        for name in ('main.rs','model.json','scoring.exe'):(build/name).write_text('fixture',encoding='utf-8')
        report={'source_import':{'snapshot_dir':str(snapshot),'source_inventory':[{'path':n} for n in names]+[{'path':'../escape.cs'}]},'baseline':{'build':{'folder':str(build),'files_sha256':{n:'fixture' for n in ('main.rs','model.json','scoring.exe')}}}}
        links=subject._links(report,self.folder)
        self.assertEqual(len(links),8)
        self.assertFalse(any(item['url'].endswith('.exe') for item in links))
        self.assertTrue(any(item['label']=='rust/main.rs' for item in links))


def browser_fixture():
    folder = EVIDENCE / 'browser'
    folder.mkdir(parents=True)
    report = {'schema':'mia-studio-migration-report-1','status':'PASS','source_import':{'status':'IMPORTED',
        'reason':'16 fichiers conservés, exemple de rapport de test (aucune campagne réelle).','snapshot_complete':True,
        'source_inventory':[{'path':f'file{i}.cs','role':'EXCLUDED' if i<7 else 'ACTIVE'} for i in range(16)],
        'provenance':{'mapping_kind':'CURATED_KNOWN_PROFILE_NOT_GENERIC_PARSER','mapping':[{'symbol':'StudioStore.Points','path':'Services/StudioStore.cs','start_line':167,'end_line':174,'target':'expressions.point'}]}},
        'baseline':{'model_revision':'fixture-baseline','build':{'status':'COMPILED'},'comparison':{'status':'PASS','cases':3}},
        'variant':{'model_revision':'fixture-variant','parent_revision':'fixture-baseline','penalty':{'n':1,'d':2},'build':{'status':'COMPILED'},'comparison':{'status':'PASS'},'intentional_differences':[{'case_index':1,'input':{'questions':[{'id':'question-identique'},{'id':'question-differente'}]},'baseline':{'points':[{'display':'1.00'},{'display':'0.00'}]},'variant':{'points':[{'display':'1.00'},{'display':'0.25'}]}}]},
        'checks':[{'name':'Fixture locale uniquement','pass':True}],
        'csharp_comparison':{'status':'PASS','cases':3},'csharp_oracle':{'status':'COLLECTED','scope':'HTTP seulement'},
        'limitations':['</script><script>globalThis.PWNED=true</script>', 'Pas une preuve d’équivalence complète.'],
        'coverage':{'untranslated':['État et persistance','HTTP et MVC','Interface et brouillons']}}
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    subject.render_report(report,folder/'index.html')
    subject.render_report({},folder/'empty.html')
    incomplete=dict(report);incomplete.pop('csharp_comparison');incomplete.pop('csharp_oracle')
    subject.render_report(incomplete,folder/'missing-csharp.html')
    failed=dict(report);failed['csharp_comparison']={'status':'FAIL'}
    subject.render_report(failed,folder/'failed-csharp.html')
    return folder


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReportTests))
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE/'results.json').write_text(json.dumps({'tests':result.testsRun,'passed':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors)},indent=2),encoding='utf-8')
    print(browser_fixture())
    raise SystemExit(not result.wasSuccessful())
