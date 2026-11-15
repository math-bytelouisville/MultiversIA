"""Real Roslyn analysis of explicit wrapping arithmetic; supplied C# never runs."""
from pathlib import Path
import hashlib
import itertools
import json
import unittest
import uuid

import csharp_import
import module_model

BASE = Path(__file__).resolve().parent
EVIDENCE = BASE / 'evidence' / ('arithmetic-import-' + uuid.uuid4().hex)
EVIDENCE.mkdir(parents=True)
SOURCE = '''// Source exacte : arithmétique é, noms échappés.
namespace Démo;
internal static class @class {
    private static int Add(int a,int b) => unchecked(a+b);
    public static int Sub(int a,int b) { unchecked { return a-b; } }
    public static int Mul(int a,int b) => unchecked(a*b);
    public static int Neg(int a) => unchecked(-a);
    public static int Constant(int a) => unchecked(2147483647+1);
    public static int Mix(int a,int b) => unchecked(Add(a,b)*2);
    public static bool Negative(int a,int b) => unchecked(a+b)<0;
    public static int Historical(int a) => -2147483648;
}'''
wrap = lambda x: (x + 2**31) % 2**32 - 2**31


class ArithmeticImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = csharp_import.import_csharp(SOURCE, EVIDENCE)
        if cls.receipt['status'] != 'IMPORTED':
            raise AssertionError(json.dumps(cls.receipt,ensure_ascii=True))
        cls.model = cls.receipt['module']

    def refused(self, source):
        receipt = csharp_import.import_csharp(source, EVIDENCE)
        self.assertEqual(receipt['status'],'REFUSED',receipt)
        self.assertFalse(receipt['input_execution'])
        self.assertEqual(Path(receipt['evidence_dir'],'source.cs').read_bytes(),source.encode())
        return receipt

    def test_actual_roslyn_profile_source_and_options(self):
        self.assertEqual(self.model['semantic_profile'],'pure-int32-bool-wrap-call-1')
        self.assertEqual(self.model['provenance']['source_text'],SOURCE)
        self.assertEqual(self.model['provenance']['source_sha256'],hashlib.sha256(SOURCE.encode()).hexdigest())
        self.assertFalse(self.receipt['input_execution'])
        ir=json.loads(Path(self.receipt['evidence_dir'],'roslyn-ir.json').read_text(encoding='utf-8'))
        self.assertIs(ir['analysis_metadata']['check_overflow'],False)
        self.assertEqual(ir['analysis_metadata']['language_version'],'CSharp12')
        self.assertEqual([u['body']['op'] for u in self.model['units'][:3]],['add32','sub32','mul32'])
        self.assertEqual(self.model['units'][3]['body']['kind'],'neg32')

    def test_independent_integer_formula_on_extrema(self):
        values=[-2**31,-2**31+1,-65536,-1,0,1,65536,2**31-2,2**31-1]
        for a,b in itertools.product(values,repeat=2):
            for uid, expected in [('u0',wrap(a+b)),('u1',wrap(a-b)),('u2',wrap(a*b)),
                                  ('u5',wrap(wrap(a+b)*2)),('u6',wrap(a+b)<0)]:
                self.assertEqual(module_model.evaluate_unit(self.model,uid,[a,b]),expected)
        for a in values:
            self.assertEqual(module_model.evaluate_unit(self.model,'u3',[a]),wrap(-a))
            self.assertEqual(module_model.evaluate_unit(self.model,'u4',[a]),-2**31)
            self.assertEqual(module_model.evaluate_unit(self.model,'u7',[a]),-2**31)

    def test_calls_and_original_names_preserved(self):
        self.assertEqual(self.model['dependencies']['u5'],['u0'])
        self.assertEqual(self.model['units'][0]['qualified_name'],'Démo.@class.Add')
        self.assertEqual(self.model['units'][0]['parameters'],[{'name':'a','type':'int'},{'name':'b','type':'int'}])

    def test_nested_unchecked_and_literal_negation(self):
        source='class C { public static int F(int a,int b,int c) => unchecked(a+unchecked(b*c)); private static int G(int a) => unchecked(-(-2147483648)); }'
        receipt=csharp_import.import_csharp(source,EVIDENCE)
        self.assertEqual(receipt['status'],'IMPORTED',receipt)
        self.assertEqual(module_model.evaluate_unit(receipt['module'],'u0',[2**31-1,65536,65536]),2**31-1)
        self.assertEqual(module_model.evaluate_unit(receipt['module'],'u1',[0]),-2**31)

    def test_implicit_arithmetic_is_not_guessed(self):
        for expression in ['a+b','a-b','a*b','-a']:
            with self.subTest(expression=expression):
                self.refused('class C { public static int F(int a,int b) => '+expression+'; }')

    def test_checked_even_unreachable_or_inside_unchecked_is_refused(self):
        for body in ['return checked(a+b);','return unchecked(checked(a+b));',
                     'if (false) { checked { return a+b; } } return a;',
                     'checked { return a; }']:
            with self.subTest(body=body):
                self.refused('class C { public static int F(int a,int b) { '+body+' } }')

    def test_division_modulo_conversion_mutation_and_shift_refused(self):
        for expression in ['a/b','a%b','(int)a','a+=b','a<<b','a&b']:
            with self.subTest(expression=expression):
                self.refused('class C { public static int F(int a,int b) => unchecked('+expression+'); }')

    def test_all_bodies_and_unreachable_operations_are_analyzed(self):
        self.refused('class C { public static int F(int a,int b) { if(true) return a; return a+b; } }')
        self.refused('class C { public static int F(int a,int b) => unchecked(G(a,b)); private static int G(int a,int b) => a+b; }')

    def test_boolean_and_other_numeric_types_not_coerced(self):
        self.refused('class C { public static bool F(bool a,bool b) => unchecked(a+b); }')
        self.refused('class C { public static long F(int a,int b) => unchecked((long)a+b); }')


if __name__ == '__main__':
    names=['rust_import.py','csharp_import.py','module_import.ps1','test_arithmetic_import.py',
           'module_model.py','math_core.py','FOUNDATION_V050.json']
    before={n:hashlib.sha256((BASE/n).read_bytes()).hexdigest() for n in names}
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ArithmeticImportTests))
    after={n:hashlib.sha256((BASE/n).read_bytes()).hexdigest() for n in names}
    report={'status':'PASS' if result.wasSuccessful() and before==after else 'FAIL','tests':result.testsRun,
            'failures':[(str(t),err) for t,err in result.failures],'errors':[(str(t),err) for t,err in result.errors],
            'files_sha256':before,'files_after_sha256':after,'scope':'Real Roslyn analysis, model evaluation and whole-source refusals; no supplied source compiled to DLL/executed.'}
    (EVIDENCE/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(EVIDENCE);raise SystemExit(report['status']!='PASS')
