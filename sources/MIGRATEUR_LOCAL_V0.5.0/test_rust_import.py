"""Finite parser qualification. No compiler or imported source is executed."""
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import module_model
import rust_import as subject

BASE = Path(__file__).resolve().parent
EVIDENCE = BASE / 'evidence' / ('rust-import-tests-' + uuid.uuid4().hex)
SIMPLE = 'fn pick(a: i32) -> i32 { a }'


class ImportTests(unittest.TestCase):
    def admitted(self, source):
        result = subject.import_rust(source)
        self.assertEqual(result['status'], 'IMPORTED', result['reason'])
        self.assertFalse(result['input_execution'])
        self.assertTrue(result['coverage']['all_tokens_consumed'])
        self.assertEqual(module_model.validate_module(result['module']), result['module'])
        return result

    def refused(self, source, reason=None):
        result = subject.import_rust(source)
        self.assertEqual(result['status'], 'REFUSED', result['reason'])
        self.assertIsNone(result['module'])
        self.assertFalse(result['input_execution'])
        if reason: self.assertIn(reason, result['reason'])
        return result

    def test_variable_names_max_min_boundary_products_not_fixture_lookup(self):
        values = [-2147483648, -99, -1, 0, 1, 91, 2147483647]
        for function, operator, expected in [('arbitrary_max', '>=', max), ('something_else', '<=', min)]:
            source = f'pub fn {function}(first_value: i32, another_value: i32) -> i32 {{ if first_value {operator} another_value {{ first_value }} else {{ another_value }} }}'
            result = self.admitted(source)
            unit = result['module']['units'][0]
            self.assertEqual(unit['qualified_name'], function)
            self.assertEqual([p['name'] for p in unit['parameters']], ['p0', 'p1'])
            for a, b in itertools.product(values, repeat=2):
                self.assertEqual(module_model.evaluate_unit(result['module'], 'u0', [a, b]), expected(a, b))

    def test_boolean_precedence_and_parenthesized_comparison(self):
        source = 'fn logic(alpha:bool,beta:bool,gamma:bool)->bool { !alpha && beta || (beta == gamma) }'
        model = self.admitted(source)['module']
        for a, b, c in itertools.product([False, True], repeat=3):
            self.assertEqual(module_model.evaluate_unit(model, 'u0', [a, b, c]), (not a and b) or (b == c))
        model = self.admitted('fn compare(a:i32,b:i32,flag:bool)->bool {(a < b) == flag}')['module']
        for a, b, c in itertools.product([-1, 0, 1], [-1, 0, 1], [False, True]):
            self.assertEqual(module_model.evaluate_unit(model, 'u0', [a, b, c]), (a < b) == c)

    def test_forward_calls_argument_permutation_and_scoped_names(self):
        source = 'fn caller(left:i32,right:i32)->i32 { choose(true,right,left) } fn choose(flag:bool,left:i32,right:i32)->i32 { if flag {left}else{right} }'
        result = self.admitted(source)
        self.assertEqual(result['module']['topological_order'], ['u1', 'u0'])
        self.assertEqual(result['module']['dependencies'], {'u0': ['u1'], 'u1': []})
        for values in [[-2147483648, 2147483647], [5, -3], [0, 1]]:
            self.assertEqual(module_model.evaluate_unit(result['module'], 'u0', values), values[1])

    def test_nested_calls_and_trailing_commas(self):
        source = 'fn many(a:i32,b:i32,c:i32,)->i32 { mx(mx(a,b,),c,) } fn mx(x:i32,y:i32)->i32{if x>y{x}else{y}}'
        model = self.admitted(source)['module']
        for values in itertools.product([-2147483648, -1, 0, 3, 2147483647], repeat=3):
            self.assertEqual(module_model.evaluate_unit(model, 'u0', list(values)), max(values))

    def test_else_if_and_tail_return_are_semantically_preserved(self):
        sources = [
            'fn choose(a:bool,b:bool)->i32{return if a {return -2147483648;}else if b{return 2147483647}else{0};}',
            'fn choose(a:bool,b:bool)->i32{if a {-2147483648}else if b{2147483647}else{0}}',
        ]
        for source in sources:
            model = self.admitted(source)['module']
            for a, b in itertools.product([False, True], repeat=2):
                self.assertEqual(module_model.evaluate_unit(model, 'u0', [a, b]), -2147483648 if a else 2147483647 if b else 0)

    def test_return_in_non_tail_context_refused_instead_of_losing_control_flow(self):
        sources = [
            'fn f(a:bool)->bool{(if a {return true;}else{false}) == false}',
            'fn f(a:bool)->bool{!(if a{return true;}else{false})}',
            'fn f(a:bool)->bool{if (if a{return true;}else{false}) {true}else{false}}',
            'fn f(a:bool)->bool{g(if a{return true;}else{false})} fn g(x:bool)->bool{x}',
            'fn f(a:bool)->bool{(if a{return true;}else{false}) && false}',
        ]
        for source in sources:
            with self.subTest(source=source): self.refused(source, 'RETURN_OUTSIDE_TAIL_PROFILE')

    def test_exact_utf8_crlf_comments_and_source_spans(self):
        source = '// explication é漢字 📐\r\n/* parent /* enfant */ conservé */\r\npub fn valeur(entrée: i32)->i32{entrée}'
        rejected = self.refused(source)
        self.assertEqual(rejected['inventory']['source']['text'], source)
        source = source.replace('entrée', 'entree')
        result = self.admitted(source)
        self.assertEqual(result['module']['provenance']['source_text'].encode(), source.encode())
        segments = result['inventory']['segments']
        self.assertEqual(''.join(s['text'] for s in segments), source)
        previous = 0
        for segment in segments:
            self.assertEqual(segment['start'], previous)
            self.assertEqual(source[segment['start']:segment['end']], segment['text'])
            self.assertEqual(source.encode()[segment['byte_start']:segment['byte_end']], segment['text'].encode())
            prefix = source[:segment['start']]
            self.assertEqual(segment['line'], prefix.count('\n') + 1)
            self.assertEqual(segment['column'], len(prefix.rsplit('\n', 1)[-1]) + 1)
            previous = segment['end']
        self.assertEqual(previous, len(source))
        function = result['inventory']['functions'][0]
        self.assertEqual(source[function['span']['start']:function['span']['end']], source[source.index('pub'):])
        self.assertEqual(function['parameters'][0]['original'], 'entree')
        self.assertIn('entree', result['inventory']['identifiers'])

    def test_determinism_archive_and_no_mutable_alias(self):
        with tempfile.TemporaryDirectory(dir=BASE) as folder:
            first = subject.import_rust(SIMPLE, folder)
            second = subject.import_rust(SIMPLE.encode(), folder)
            memory = self.admitted(SIMPLE)
            self.assertEqual(first['module'], second['module'])
            self.assertEqual(first['module'], memory['module'])
            self.assertNotEqual(first['evidence_dir'], second['evidence_dir'])
            saved = Path(first['evidence_dir']) / 'source.rs'
            self.assertEqual(saved.read_bytes(), SIMPLE.encode())
            self.assertEqual(json.loads((saved.parent / 'receipt.json').read_text(encoding='utf-8')), first)
            first['inventory']['functions'][0]['name'] = 'tampered'
            self.assertEqual(first['module']['provenance']['source_map'][0]['name'], 'pick')

    def test_refusal_preserves_unknown_text_and_consumes_no_prefix_silently(self):
        source = SIMPLE + '\r\n// anything unknown remains\r\nstruct More {field:i32}'
        with tempfile.TemporaryDirectory(dir=BASE) as folder:
            result = subject.import_rust(source, folder)
            self.assertEqual(result['status'], 'REFUSED')
            self.assertEqual((Path(result['evidence_dir']) / 'source.rs').read_bytes(), source.encode())
            self.assertEqual(result['inventory']['source']['text'], source)
            self.assertTrue(result['inventory']['lexing_complete'])
            self.assertFalse(result['coverage']['all_tokens_consumed'])
            self.assertIsNone(result['module'])

    def test_invalid_utf8_raw_bytes_archived_with_honest_coverage(self):
        raw = SIMPLE.encode() + b'\xff'
        with tempfile.TemporaryDirectory(dir=BASE) as folder:
            result = subject.import_rust(raw, folder)
            self.assertEqual(result['status'], 'REFUSED')
            self.assertEqual((Path(result['evidence_dir']) / 'source.rs').read_bytes(), raw)
            self.assertTrue(result['coverage']['source_preserved'])
            self.assertEqual(result['inventory']['source']['sha256'], hashlib.sha256(raw).hexdigest())

    def test_source_is_never_a_path_and_input_types_are_closed(self):
        for source in [None, True, 42, Path('never-open.rs'), bytearray(b'no'), {'source': SIMPLE}]:
            with self.subTest(source=source): self.refused(source, 'SOURCE_MUST')
        self.refused('C:\\never-open\\source.rs')
        self.refused('\ud800')

    def test_invalid_identifiers_and_signatures_refused(self):
        sources = ['fn true(a:i32)->i32{a}', 'fn f(_:i32)->i32{1}', 'fn f(a:i32,a:i32)->i32{a}',
                   'fn f()->i32{1}', 'fn f(a:i32,b:i32,c:i32,d:i32)->i32{a}',
                   'fn f(a:i64)->i32{1}', 'fn f(a:i32){a}', 'fn f(a:i32)->u32{1}',
                   'pub(crate) fn f(a:i32)->i32{a}', 'fn f(a: &i32)->i32{a}',
                   'fn ' + 'x' * 81 + '(a:i32)->i32{a}']
        for source in sources:
            with self.subTest(source=source): self.refused(source)

    def test_long_and_core_keyword_parameters_canonicalized(self):
        # `result`/`not` are ordinary Rust identifiers, reserved by the math core.
        model = self.admitted('fn combine(result:i32,not:i32)->i32{if result>not{result}else{not}}')['module']
        self.assertEqual(module_model.evaluate_unit(model, 'u0', [4, 7]), 7)
        name = 'a' * 80
        self.admitted(f'fn f({name}:i32)->i32{{{name}}}')

    def test_calls_arity_type_shadowing_unknown_and_cycles(self):
        sources = [
            ('fn f(a:i32)->i32{g(a)}', 'UNRESOLVED_CALL'),
            ('fn f(a:i32)->i32{f(a)}', 'CALL_CYCLE'),
            ('fn f(a:i32)->i32{g(a)} fn g(a:i32)->i32{f(a)}', 'CALL_CYCLE'),
            ('fn f(g:i32)->i32{g(1)} fn g(a:i32)->i32{a}', 'CALL_NAME_SHADOWED'),
            ('fn f(a:i32)->i32{g()} fn g(a:i32)->i32{a}', 'CALL_ARITY'),
            ('fn f(a:i32)->i32{g(true)} fn g(a:i32)->i32{a}', 'CALL_TYPE'),
            ('fn f(a:i32)->i32{g(a,a)} fn g(a:i32)->i32{a}', 'CALL_ARITY'),
            (SIMPLE + SIMPLE, 'DUPLICATE_FUNCTION'),
        ]
        for source, reason in sources:
            with self.subTest(source=source): self.refused(source, reason)

    def test_typed_expression_refusals(self):
        sources = ['fn f(a:i32)->bool{a}', 'fn f(a:i32)->i32{true}', 'fn f(a:i32)->bool{!a}',
                   'fn f(a:i32)->bool{a && a}', 'fn f(a:bool)->bool{a < false}',
                   'fn f(a:i32)->i32{if a {1}else{2}}', 'fn f(a:i32)->i32{if true {a}else{false}}',
                   'fn f(a:i32)->bool{a == true}', 'fn f(a:i32)->i32{unknown}']
        for source in sources:
            with self.subTest(source=source): self.refused(source)

    def test_control_effects_and_other_rust_constructs_refused(self):
        bodies = ['a;', 'return a;;', 'let x=a; x', 'a=1; a', 'loop{break a;}', 'while true{} a',
                  'a+1', 'a-1', 'a*2', 'a/2', 'a%2', 'a<<1', 'a as i32', '&a', 'unsafe{a}',
                  'foo!(a)', 'match a{_=>a}', '|x| x', 'if true {a}', '{a}', 'return a; a']
        for body in bodies:
            with self.subTest(body=body): self.refused('fn f(a:i32)->i32{' + body + '}')
        for prefix in ['#[inline] ', 'unsafe ', 'extern "C" ', 'async ', 'const ', 'mod other{} ', 'use std::io; ']:
            with self.subTest(prefix=prefix): self.refused(prefix + SIMPLE)

    def test_literals_chains_and_trailing_garbage_refused(self):
        for expression in ['2147483648', '-2147483649', '--1', '-a', '1i64', '1_000', '0xff', '1.0', '"text"', "'a'", '1' * 200]:
            with self.subTest(expression=expression): self.refused('fn f(a:i32)->i32{' + expression + '}')
        self.refused('fn f(a:i32,b:i32)->bool{a < b < a}', 'CHAINED_COMPARISON')
        for suffix in [';', 'garbage', '}', '#', '\x00']:
            with self.subTest(suffix=suffix): self.refused(SIMPLE + suffix)

    def test_unterminated_empty_and_comment_depth(self):
        for source in ['', '   //only comments', '/* unterminated', SIMPLE[:-1], 'fn']:
            with self.subTest(source=source): self.refused(source)
        self.admitted('/*' * 16 + 'comment' + '*/' * 16 + SIMPLE)
        self.refused('/*' * 17 + 'comment' + '*/' * 17 + SIMPLE, 'COMMENT_DEPTH_BOUND')

    def test_depth_and_token_limits_are_explicit(self):
        self.refused('fn f(a:i32)->i32{' + '(' * 80 + 'a' + ')' * 80 + '}', 'PARSE_DEPTH_BOUND')
        self.refused('fn f(a:bool)->bool{' + '!' * 80 + 'a}', 'PARSE_DEPTH_BOUND')
        result = self.refused('x ' * (subject.MAX_TOKENS + 1), 'TOKEN_OR_SEGMENT_BOUND')
        self.assertFalse(result['inventory']['lexing_complete'])
        self.assertTrue(result['coverage']['source_preserved'])

    def test_source_and_archive_limits_keep_claims_bounded(self):
        source = ' ' * (subject.MAX_SOURCE_BYTES + 1)
        result = self.refused(source, 'PARSER_SOURCE_BOUND_EXACT_INPUT_PRESERVED')
        self.assertEqual(result['inventory']['source']['text'], source)
        self.assertTrue(result['coverage']['source_preserved'])
        result = self.refused(' ' * (subject.MAX_ARCHIVE_BYTES + 1), 'ARCHIVE_BOUND_INPUT_NOT_COPIED')
        self.assertFalse(result['coverage']['source_preserved'])

    def test_eight_function_boundary_and_inline_expansion_refusal(self):
        source = ' '.join(f'fn f{i}(x:i32)->i32{{x}}' for i in range(8))
        self.assertEqual(len(self.admitted(source)['module']['units']), 8)
        self.refused(source + ' fn more(x:i32)->i32{x}', 'FUNCTION_COUNT_BOUND')
        # Small declared source, exponentially repeated callee body after inline.
        source = 'fn f0(x:bool)->bool{x && x} ' + ' '.join(f'fn f{i}(x:bool)->bool{{f{i-1}(x) && f{i-1}(x)}}' for i in range(1, 8))
        self.refused(source, 'UNIT_CONTRACT_INVALID:EXPRESSION_SIZE')

    def test_output_confinement_and_io_failure_are_not_import_success(self):
        self.assertEqual(subject.import_rust(SIMPLE, BASE.parent)['status'], 'REFUSED')
        with patch.object(Path, 'mkdir', side_effect=OSError('test disk unavailable')):
            result = subject.import_rust(SIMPLE, BASE / 'not-created')
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertIsNone(result['module'])
        self.assertIn('test disk unavailable', result['reason'])

    def test_current_foundations_match_declared_v050_manifest(self):
        baseline = json.loads((BASE / 'FOUNDATION_V050.json').read_text(encoding='utf-8'))
        for name in ('module_model.py', 'math_core.py'):
            self.assertEqual(hashlib.sha256((BASE / name).read_bytes()).hexdigest(), baseline['files_sha256'][name])

    def test_wrapping_operators_boundary_products_against_integer_formula(self):
        wrap = lambda x: (x + 2**31) % 2**32 - 2**31
        values = [-2**31, -2**31+1, -65536, -1, 0, 1, 65536, 2**31-2, 2**31-1]
        for name, operation in [('add', lambda a,b:a+b), ('sub', lambda a,b:a-b), ('mul', lambda a,b:a*b)]:
            model = self.admitted(f'fn value(a:i32,b:i32)->i32{{a.wrapping_{name}(b)}}')['module']
            self.assertEqual(model['semantic_profile'], 'pure-int32-bool-wrap-call-1')
            self.assertEqual(model['units'][0]['body']['op'], name+'32')
            for a,b in itertools.product(values, repeat=2):
                self.assertEqual(module_model.evaluate_unit(model,'u0',[a,b]),wrap(operation(a,b)))
        model = self.admitted('fn value(a:i32)->i32{a.wrapping_neg()}')['module']
        self.assertEqual(model['units'][0]['body']['kind'],'neg32')
        for a in values: self.assertEqual(module_model.evaluate_unit(model,'u0',[a]),wrap(-a))

    def test_wrapping_chain_call_receiver_parentheses_and_precedence(self):
        source = ('fn choose(a:i32,b:i32,c:bool)->bool{add(a,b).wrapping_mul(2).wrapping_neg() > a && c} '
                  'fn add(a:i32,b:i32)->i32{a.wrapping_add(b)}')
        model = self.admitted(source)['module']
        self.assertEqual(model['dependencies']['u0'],['u1'])
        wrap=lambda x:(x+2**31)%2**32-2**31
        for a,b,c in itertools.product([-2**31,-1,0,1,2**31-1],[-2**31,-1,0,1,2**31-1],[False,True]):
            self.assertEqual(module_model.evaluate_unit(model,'u0',[a,b,c]), (wrap(-wrap(wrap(a+b)*2))>a) and c)
        model=self.admitted('fn f(a:i32,b:bool)->i32{(if b {a}else{1}).wrapping_add(2,)}')['module']
        self.assertEqual(module_model.evaluate_unit(model,'u0',[2**31-1,True]),-2**31+1)

    def test_typed_literal_receivers_and_negative_binding(self):
        for body,expected in [('2147483647i32.wrapping_add(1)',-2**31),('(-2147483648i32).wrapping_neg()',-2**31),
                              ('(-1i32).wrapping_add(2)',1),('1i32',1)]:
            model=self.admitted('fn f(a:i32)->i32{'+body+'}')['module']
            self.assertEqual(module_model.evaluate_unit(model,'u0',[0]),expected)
        for body in ['1.wrapping_add(2)','(1).wrapping_add(2)','-1i32.wrapping_add(2)',
                     '(if true {1}else{2}).wrapping_add(1)','1 i32','1u32.wrapping_add(2)']:
            self.refused('fn f(a:i32)->i32{'+body+'}')

    def test_wrapping_types_methods_arity_and_nested_return_refused(self):
        for body in ['true.wrapping_neg()','a.wrapping_add(false)','a.wrapping_neg(1)','a.wrapping_add()',
                     'a.wrapping_add(1,2)','a.wrapping_neg(,)','a.checked_add(1)','a.saturating_mul(2)',
                     'a.wrapping_div(2)','a.wrapping_rem(2)','a.add(1)','a.wrapping_add(1).abs()',
                     'a.wrapping_add(if true{return 1}else{2})']:
            self.refused('fn f(a:i32)->i32{'+body+'}')

    def test_wrapping_source_hash_determinism_and_tokens_preserved(self):
        source='// arithmétique é\r\nfn f(a:i32)->i32{a.wrapping_add(1).wrapping_neg()}'
        first=self.admitted(source);second=self.admitted(source.encode())
        self.assertEqual(first['module'],second['module'])
        self.assertEqual(first['module']['provenance']['source_sha256'],hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(''.join(s['text'] for s in first['inventory']['segments']),source)
        self.assertFalse(first['input_execution'])


if __name__ == '__main__':
    EVIDENCE.mkdir(parents=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ImportTests))
    report = {'tests': result.testsRun, 'status': 'PASS' if result.wasSuccessful() else 'FAIL',
              'failures': [{'test': str(t), 'trace': trace} for t, trace in result.failures],
              'errors': [{'test': str(t), 'trace': trace} for t, trace in result.errors],
              'skipped': result.skipped, 'scope': 'Parser/model tests only. Imported source never compiled/executed.',
              'files_sha256': {n: hashlib.sha256((BASE / n).read_bytes()).hexdigest() for n in ['rust_import.py', 'test_rust_import.py']}}
    (EVIDENCE / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(EVIDENCE / 'result.json')
    raise SystemExit(not result.wasSuccessful())
