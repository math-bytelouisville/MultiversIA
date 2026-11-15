import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import profile_import as subject

BASE = Path(__file__).resolve().parent
SOURCE = BASE.parent / 'SYNTHESE_MATHS_CODE_V0.2.0' / 'qcm_studio'
EVIDENCE = BASE / 'evidence' / ('import-tests-' + uuid.uuid4().hex)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = EVIDENCE / self.id().rsplit('.', 1)[-1]
        self.folder.mkdir(parents=True)
        self.source = self.folder / 'source'
        shutil.copytree(SOURCE, self.source)
        self.output = self.folder / 'output'

    def run_import(self):
        result = subject.create_import(self.source, self.output)
        self.assertEqual(result, json.loads((Path(result['evidence_dir']) / 'receipt.json').read_text(encoding='utf-8')))
        return result

    def test_real_profile_all_bytes_roles_and_stable_provenance(self):
        first = self.run_import()
        self.assertEqual(first['status'], 'IMPORTED', first['reason'])
        self.assertTrue(first['snapshot_complete'])
        self.assertEqual(len(first['source_inventory']), 16)
        self.assertEqual(sum(x['role'] == 'EXCLUDED' for x in first['source_inventory']), 7)
        for row in first['source_inventory']:
            self.assertEqual((self.source / row['path']).read_bytes(), (Path(first['snapshot_dir']) / row['path']).read_bytes())
        second = self.run_import()
        self.assertNotEqual(first['evidence_dir'], second['evidence_dir'])
        self.assertEqual(first['provenance'], second['provenance'])

    def test_changed_active_source_is_archived_but_not_admitted(self):
        path = self.source / 'Services/StudioStore.cs'
        path.write_bytes(path.read_bytes() + b'\n// changed\n')
        result = self.run_import()
        self.assertEqual(result['status'], 'REFUSED')
        self.assertTrue(result['snapshot_complete'])
        self.assertIsNone(result['provenance'])
        self.assertEqual(result['unsupported'][0]['kind'], 'MODIFIED_FILE')
        self.assertEqual(path.read_bytes(), (Path(result['snapshot_dir']) / 'Services/StudioStore.cs').read_bytes())

    def test_changed_excluded_source_also_refused(self):
        (self.source / 'Rules.cs').write_bytes(b'ignored by build is still preserved')
        self.assertEqual(self.run_import()['status'], 'REFUSED')

    def test_unknown_binary_is_preserved_without_translation(self):
        data = bytes(range(256))
        (self.source / 'extra.bin').write_bytes(data)
        result = self.run_import()
        self.assertEqual(result['status'], 'REFUSED')
        self.assertEqual(result['unsupported'][0]['kind'], 'UNKNOWN_FILE')
        self.assertEqual((Path(result['snapshot_dir']) / 'extra.bin').read_bytes(), data)

    def test_missing_file_is_not_silently_skipped(self):
        # Renaming preserves the negative test's original bytes.
        (self.source / 'Rules.cs').rename(self.folder / 'removed-Rules.cs')
        result = self.run_import()
        self.assertEqual(result['status'], 'REFUSED')
        self.assertIn({'kind': 'MISSING_FILE', 'path': 'Rules.cs'}, result['unsupported'])

    def test_unknown_empty_directory_is_preserved_and_refused(self):
        (self.source / 'extra').mkdir()
        result = self.run_import()
        self.assertEqual(result['status'], 'REFUSED')
        self.assertTrue((Path(result['snapshot_dir']) / 'extra').is_dir())

    def test_source_mutation_between_reads_is_refused(self):
        original = subject._scan
        calls = 0
        def scan(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                p = root / 'Rules.cs'
                p.write_bytes(p.read_bytes() + b' changed')
            return original(root)
        with patch.object(subject, '_scan', side_effect=scan):
            result = self.run_import()
        self.assertEqual(result['status'], 'REFUSED')
        self.assertFalse(result['snapshot_complete'])
        self.assertIsNone(result['provenance'])

    def test_source_addition_between_reads_is_refused(self):
        original = subject._scan
        calls = 0
        def scan(root):
            nonlocal calls
            value = original(root)
            calls += 1
            if calls == 1:
                (root / 'late.txt').write_bytes(b'late')
            return value
        with patch.object(subject, '_scan', side_effect=scan):
            result = self.run_import()
        self.assertEqual(result['status'], 'REFUSED')
        self.assertFalse(result['snapshot_complete'])

    def test_file_and_total_bounds(self):
        with patch.object(subject, 'MAX_FILE_BYTES', 4):
            self.assertEqual(self.run_import()['status'], 'REFUSED')
        with patch.object(subject, 'MAX_TOTAL_BYTES', 100):
            self.assertEqual(self.run_import()['status'], 'REFUSED')

    def test_count_and_depth_bounds(self):
        with patch.object(subject, 'MAX_ENTRIES', 2):
            self.assertEqual(self.run_import()['status'], 'REFUSED')
        with patch.object(subject, 'MAX_DEPTH', 0):
            self.assertEqual(self.run_import()['status'], 'REFUSED')

    def test_path_overlap_and_parent_navigation_refused(self):
        with self.assertRaises(ValueError):
            subject.create_import(self.source, self.source / 'output')
        with self.assertRaises(ValueError):
            subject.create_import(self.source, self.folder)
        with self.assertRaises(ValueError):
            subject.create_import(self.source, self.output / '..' / 'escape')

    def test_hard_link_metadata_refused(self):
        # Sandbox forbids creating hard links; exercise the admission guard
        # with real path metadata except for the injected link count.
        path = self.source / 'Rules.cs'
        original = Path.lstat
        def metadata(p):
            value = original(p)
            if p == path:
                fields = {k: getattr(value, k) for k in dir(value) if k.startswith('st_')}
                fields['st_nlink'] = 2
                return type('Info', (), fields)()
            return value
        with patch.object(Path, 'lstat', metadata):
            self.assertEqual(self.run_import()['status'], 'REFUSED')

    def test_reparse_attribute_and_symlink_detection(self):
        self.assertTrue(subject._is_link(type('Info', (), {'st_mode': 0o100644, 'st_file_attributes': 0x400})()))
        self.assertTrue(subject._is_link(type('Info', (), {'st_mode': 0o120777})()))
        original = subject._safe_path
        def fake(path, **kwargs):
            if Path(path).name == 'Services':
                raise subject.Refused('Point de réanalyse simulé')
            return original(path, **kwargs)
        with patch.object(subject, '_safe_path', side_effect=fake):
            self.assertEqual(self.run_import()['status'], 'REFUSED')

    def test_curated_mapping_spans_match_archived_source(self):
        result = self.run_import()
        for mapping in result['provenance']['mapping']:
            lines = (self.source / mapping['path']).read_text(encoding='utf-8').splitlines()
            fragment = '\n'.join(lines[mapping['start_line'] - 1:mapping['end_line']])
            self.assertIn(mapping['symbol'].split('.')[-1], fragment)
            self.assertTrue(fragment.endswith('}'))
        self.assertEqual(result['coverage']['behavioral_equivalence'], 'NOT_PROVED')
        self.assertEqual(len(result['coverage']['untranslated']), 6)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProfileTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / 'results.json').write_text(json.dumps({'tests': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'passed': result.wasSuccessful()}, indent=2), encoding='utf-8')
    print(EVIDENCE)
    raise SystemExit(not result.wasSuccessful())
