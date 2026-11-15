import json
from pathlib import Path
import tempfile
import unittest

import launch_migration
import rust_runtime


class AdmissionTests(unittest.TestCase):
    def test_penalty_boundaries(self):
        for text in ('0/1', '1/2', '1/1', ' 001/002 '):
            self.assertIn('/', launch_migration.penalty(text))
        for text in ('1/0', '2/1', '-1/2', 'NaN/1', '1/2;whoami', '1/٢', '1/2/3', '1/1000000'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                launch_migration.penalty(text)

    def test_outputs_cannot_escape_owned_project(self):
        with self.assertRaises(ValueError):
            rust_runtime.owned(rust_runtime.BASE / '..' / 'outside')
        with self.assertRaises(ValueError):
            rust_runtime.owned(rust_runtime.PREVIOUS / 'local/qcm-studio')

    def test_json_duplicate_nonfinite_and_oversize_refused(self):
        with tempfile.TemporaryDirectory(dir=rust_runtime.BASE) as folder:
            path = Path(folder) / 'input.json'
            for content in ('{"same":1,"same":2}', '{"x":NaN}', '{"x":Infinity}'):
                path.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    rust_runtime.load(path)
            path.write_text('{"safe": 1}', encoding='utf-8')
            self.assertEqual(rust_runtime.load(path), {'safe': 1})
            with self.assertRaises(ValueError):
                rust_runtime.load(path, maximum=2)

    def test_child_environment_only(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'RUSTC_WRAPPER': 'bad', 'PYTHONPATH': 'unwanted', 'PATH': 'preserved'}):
            env = rust_runtime.environment({'path_entries': ['owned-bin']})
            self.assertNotIn('RUSTC_WRAPPER', env)
            self.assertNotIn('PYTHONPATH', env)
            self.assertEqual(env['PATH'], 'owned-bin' + os.pathsep + 'preserved')
            self.assertEqual(os.environ['RUSTC_WRAPPER'], 'bad')

    def test_changed_compile_input_cannot_be_qualified(self):
        import scoring_model
        from types import SimpleNamespace
        from unittest.mock import patch
        tool = {'path': 'fixture-compiler', 'extra_arguments': [], 'path_entries': []}
        def substitute(command, **kwargs):
            folder = Path(kwargs['cwd'])
            (folder / 'scoring.exe').write_bytes(b'fixture only, never executed')
            with (folder / 'main.rs').open('a', encoding='utf-8') as stream:
                stream.write('// changed during compile\n')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        with tempfile.TemporaryDirectory(dir=rust_runtime.BASE) as folder:
            with patch('rust_runtime.toolchain', return_value=tool), patch('rust_runtime.subprocess.run', side_effect=substitute):
                result = rust_runtime.compile_model(scoring_model.default_model(), folder)
            self.assertEqual(result['status'], 'INCONCLUSIVE')
            self.assertIn('modifiée pendant', result['reason'])


if __name__ == '__main__':
    unittest.main()
