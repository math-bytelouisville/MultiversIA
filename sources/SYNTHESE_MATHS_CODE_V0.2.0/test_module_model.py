"""Model tests: semantics, provenance, bounded refusal and intentional changes."""
from copy import deepcopy
import hashlib
import itertools
import json
import unittest

import math_core
import module_model as model


def param(name, typ='int'):
    return {'kind': 'param', 'type': typ, 'name': name}


def const(value):
    return {'kind': 'constant', 'type': 'bool' if type(value) is bool else 'int', 'value': value}


def call(target, args, typ='int'):
    return {'kind': 'call', 'type': typ, 'target': target, 'arguments': args}


def unit(uid, name, parameters, body, return_type='int', contract=None):
    return {'id': uid, 'name': name, 'qualified_name': 'Example.' + name,
            'parameters': [{'name': p, 'type': t} for p, t in parameters],
            'return_type': return_type, 'body': body, 'contract': contract}


def module_fixture():
    body = math_core.parse_expression('a if a >= b else b', {'a': 'int', 'b': 'int'})
    source = '// exact origin with accents: hypothèse\r\nclass Example { /* fixture */ }\r\n'
    return {
        'schema_version': 'mia-module-1', 'semantic_profile': 'pure-int32-bool-call-1',
        'module_id': 'module-fixture-001', 'name': 'Example', 'parent_revision_id': None,
        'intent': 'Import initial',
        'provenance': {'source_text': source, 'source_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                       'import_id': 'import-fixture-001', 'origin': {'language': 'csharp', 'path': 'never-open-this.cs'},
                       'limitations': ['Fixture source is not Roslyn import evidence.']},
        'units': [unit('u0', 'Max2', [('a', 'int'), ('b', 'int')], body),
                  unit('u1', 'Max3', [('a', 'int'), ('b', 'int'), ('c', 'int')],
                       call('u0', [call('u0', [param('a'), param('b')]), param('c')]))],
    }


class ModuleTests(unittest.TestCase):
    def test_max3_all_boundary_products_and_calls_preserved(self):
        module = model.validate_module(module_fixture())
        self.assertEqual(module['dependencies'], {'u0': [], 'u1': ['u0']})
        self.assertEqual(module['topological_order'], ['u0', 'u1'])
        self.assertEqual(module['units'][1]['body']['kind'], 'call')
        expression = model.inline_unit(module, 'u1')
        for values in itertools.product([math_core.LOW, -1, 0, 1, math_core.HIGH], repeat=3):
            self.assertEqual(math_core.evaluate(expression, dict(zip(('a', 'b', 'c'), values))), max(values))
        self.assertEqual(model.evaluate_unit(module, 'u1', [8, 3, 5]), 8)
        self.assertEqual(module['coverage']['formal_scope'], 'NOT_PROVED_MODULE')

    def test_detached_idempotent_and_canonical_key_order(self):
        original = module_fixture(); before = deepcopy(original)
        normalized = model.validate_module(original)
        self.assertEqual(original, before)
        self.assertEqual(model.validate_module(normalized), normalized)
        shuffled = json.loads(json.dumps(original, sort_keys=True))
        self.assertEqual(model.validate_module(shuffled)['revision_id'], normalized['revision_id'])
        normalized['provenance']['origin']['language'] = 'changed'
        self.assertEqual(original, before)

    def test_fake_derived_views_recomputed(self):
        module = model.validate_module(module_fixture()); before = module['revision_id']
        module.update(dependencies={'u0': ['external']}, topological_order=['u1'], coverage={'site_migrated': True})
        normalized = model.validate_module(module)
        self.assertEqual(normalized['revision_id'], before)
        self.assertEqual(normalized['dependencies']['u0'], [])
        self.assertFalse(normalized['coverage']['site_migrated'])

    def test_revision_tampering_refused(self):
        module = model.validate_module(module_fixture())
        variants = []
        for field, value in [('intent', 'Changed'), ('module_id', 'another'), ('parent_revision_id', 'a' * 64)]:
            altered = deepcopy(module); altered[field] = value; variants.append(altered)
        altered = deepcopy(module); altered['units'][0]['name'] = 'Other'; variants.append(altered)
        altered = deepcopy(module); altered['provenance']['origin']['language'] = 'fake'; variants.append(altered)
        altered = deepcopy(module); altered['units'][0]['contract']['precondition'] = 'False'; variants.append(altered)
        for altered in variants:
            with self.subTest(field=altered):
                with self.assertRaisesRegex(ValueError, 'REVISION_HASH_MISMATCH'):
                    model.validate_module(altered)

    def test_provenance_source_hash_and_required_keys(self):
        for mutation in ('text', 'hash', 'missing'):
            module = module_fixture()
            if mutation == 'text': module['provenance']['source_text'] += 'changed'
            elif mutation == 'hash': module['provenance']['source_sha256'] = '0' * 64
            else: del module['provenance']['import_id']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                model.validate_module(module)

    def test_source_preserved_bytes_and_arbitrary_json_not_executed(self):
        module = module_fixture()
        module['provenance']['extra'] = {'command': 'do not run', 'path': 'C:/never-open', 'nested': [1, True, None, 2.0]}
        normalized = model.validate_module(module)
        self.assertEqual(normalized['provenance'], module['provenance'])
        self.assertIn('\r\n', normalized['provenance']['source_text'])

    def test_direct_and_indirect_recursion_refused(self):
        direct = module_fixture(); direct['units'][0]['body'] = call('u0', [param('a'), param('b')])
        indirect = module_fixture(); indirect['units'][0]['body'] = call('u1', [param('a'), param('b'), param('a')])
        for module in (direct, indirect):
            with self.assertRaisesRegex(ValueError, 'CALL_CYCLE'):
                model.validate_module(module)

    def test_external_target_wrong_arity_and_call_type(self):
        for replacement, reason in [(call('external', [param('a')]), 'UNRESOLVED_CALL'),
                                    (call('u0', [param('a')]), 'CALL_ARITY'),
                                    (call('u0', [param('a'), const(True)]), 'CALL_TYPE_MISMATCH'),
                                    (call('u0', [param('a'), param('b')], 'bool'), 'CALL_TYPE_MISMATCH')]:
            module = module_fixture(); module['units'][1]['body'] = replacement
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                model.validate_module(module)

    def test_scope_substitution_is_simultaneous(self):
        module = module_fixture()
        module['units'][0]['body'] = param('a')
        module['units'][1]['body'] = call('u0', [param('b'), param('a')])
        self.assertEqual(model.evaluate_unit(module, 'u1', [99, 7, -9]), 7)
        self.assertEqual(model.inline_unit(module, 'u1'), param('b'))

    def test_mixed_boolean_call_and_strict_input_types(self):
        module = module_fixture()
        module['units'] = [unit('u0', 'Select', [('flag', 'bool'), ('a', 'int'), ('b', 'int')],
                               math_core.parse_expression('a if flag else b', {'flag': 'bool', 'a': 'int', 'b': 'int'})),
                           unit('u1', 'Forward', [('a', 'int'), ('flag', 'bool')],
                                call('u0', [param('flag', 'bool'), param('a'), const(-4)]))]
        self.assertEqual(model.evaluate_unit(module, 'u1', [11, True]), 11)
        self.assertEqual(model.evaluate_unit(module, 'u1', [11, False]), -4)
        for inputs in ([True, True], [11, 1], [math_core.HIGH + 1, True], [11], (11, True)):
            with self.assertRaises(ValueError):
                model.evaluate_unit(module, 'u1', inputs)

    def test_unknown_parameter_and_injection_nodes(self):
        for body in (param('missing'), {'kind': 'exec', 'type': 'int', 'text': 'danger'},
                     {'kind': 'constant', 'type': 'int', 'value': True},
                     {'kind': 'param', 'type': 'int', 'name': 'a', 'unexpected': 1}):
            module = module_fixture(); module['units'][0]['body'] = body
            with self.assertRaises(ValueError):
                model.validate_module(module)

    def test_all_branches_checked_even_unreachable(self):
        module = module_fixture()
        module['units'][0]['body'] = {'kind': 'ite', 'type': 'int', 'condition': const(True), 'when_true': param('a'),
                                      'when_false': call('external', [param('a')])}
        with self.assertRaisesRegex(ValueError, 'UNRESOLVED_CALL'):
            model.validate_module(module)

    def test_duplicate_and_gap_ids_and_qualified_names(self):
        for mutation in ('duplicate', 'gap', 'qualified'):
            module = module_fixture()
            if mutation == 'duplicate': module['units'][1]['id'] = 'u0'
            elif mutation == 'gap': module['units'][1]['id'] = 'u4'
            else: module['units'][1]['qualified_name'] = module['units'][0]['qualified_name']
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                model.validate_module(module)

    def test_non_json_cycle_and_bounds(self):
        variants = []
        module = module_fixture(); module['provenance']['cycle'] = module; variants.append(module)
        module = module_fixture(); module['provenance']['nan'] = float('nan'); variants.append(module)
        module = module_fixture(); module['provenance']['object'] = object(); variants.append(module)
        module = module_fixture(); module['units'] *= 5; variants.append(module)
        module = module_fixture(); module['provenance']['source_text'] = 'x' * (model.MAX_SOURCE_BYTES + 1); variants.append(module)
        for module in variants:
            with self.assertRaises(ValueError):
                model.validate_module(module)

    def test_expansion_bomb_bounded(self):
        def tree(depth, leaf):
            if depth == 0: return deepcopy(leaf)
            return {'kind': 'ite', 'type': 'int', 'condition': const(True),
                    'when_true': tree(depth - 1, leaf), 'when_false': tree(depth - 1, leaf)}
        module = module_fixture(); contract = {'precondition': 'True', 'postcondition': 'result == a'}
        module['units'] = [unit('u0', 'Large', [('a', 'int')], tree(7, param('a')), contract=contract),
                           unit('u1', 'Expand', [('a', 'int')], tree(5, call('u0', [param('a')])), contract=contract)]
        with self.assertRaisesRegex(ValueError, 'INLINE_EXPANSION_BOUND'):
            model.validate_module(module)

    def test_stricter_core_limit_explicit(self):
        def tree(depth, leaf):
            return deepcopy(leaf) if not depth else {'kind': 'ite', 'type': 'int', 'condition': const(True),
                'when_true': tree(depth - 1, leaf), 'when_false': tree(depth - 1, leaf)}
        module = module_fixture(); contract = {'precondition': 'True', 'postcondition': 'result == a'}
        module['units'] = [unit('u0', 'Small', [('a', 'int')], tree(6, param('a')), contract=contract),
                           unit('u1', 'TooBigForCore', [('a', 'int')], tree(2, call('u0', [param('a')])), contract=contract)]
        with self.assertRaisesRegex(ValueError, 'INLINE_CORE_LIMIT_OR_TYPE'):
            model.validate_module(module)

    def test_contract_is_public_spec_and_no_hidden_ast_trust(self):
        module = model.validate_module(module_fixture())
        spec = model.unit_spec(module, 'u1')
        self.assertEqual(set(spec), math_core.PUBLIC)
        parsed = math_core.validate_spec(spec)
        for values in [(1, 2, 3), (-9, -2, -4)]:
            env = dict(zip(('a', 'b', 'c'), values)); env['result'] = max(values)
            self.assertTrue(math_core.evaluate(parsed['post_ast'], env))
        module['units'][1]['contract']['post_ast'] = const(True)
        with self.assertRaisesRegex(ValueError, 'INVALID_CONTRACT_FIELDS'):
            model.validate_module(module)

    def test_revision_changes_callee_keeps_caller_contract_and_origin(self):
        previous = model.validate_module(module_fixture()); untouched = deepcopy(previous)
        spec = model.unit_spec(previous, 'u0')
        spec['postcondition'] = 'result <= a and result <= b and (result == a or result == b)'
        result = model.revise_unit(previous, 'u0', spec, 'Maximum devient minimum')
        self.assertEqual(result['status'], 'REVISED', result['reason'])
        revised = result['module']
        self.assertEqual(previous, untouched)
        self.assertEqual(revised['parent_revision_id'], previous['revision_id'])
        self.assertNotEqual(revised['revision_id'], previous['revision_id'])
        self.assertEqual(revised['module_id'], previous['module_id'])
        self.assertEqual(revised['provenance'], previous['provenance'])
        self.assertEqual(revised['units'][1], previous['units'][1])
        self.assertEqual(result['change']['affected_dependents'], ['u1'])
        self.assertFalse(result['change']['equivalence_claimed'])
        actual = model.evaluate_unit(revised, 'u1', [9, 4, 7])
        self.assertEqual(actual, 4)
        old_spec = math_core.validate_spec(model.unit_spec(revised, 'u1'))
        self.assertFalse(math_core.evaluate(old_spec['post_ast'], {'a': 9, 'b': 4, 'c': 7, 'result': actual}))

    def test_transitive_dependents_diamond(self):
        module = module_fixture()
        module['units'].append(unit('u2', 'Forward3', [('a', 'int'), ('b', 'int'), ('c', 'int')],
                                    call('u1', [param('a'), param('b'), param('c')])))
        module['units'].append(unit('u3', 'Independent', [('x', 'int')], param('x')))
        previous = model.validate_module(module)
        spec = model.unit_spec(previous, 'u0'); spec['postcondition'] = 'result == a'
        result = model.revise_unit(previous, 'u0', spec, 'Choisir premier')
        self.assertEqual(result['status'], 'REVISED')
        self.assertEqual(result['change']['affected_dependents'], ['u1', 'u2'])
        self.assertEqual(result['module']['units'][3], previous['units'][3])

    def test_failed_and_signature_revision_preserve_original(self):
        module = model.validate_module(module_fixture()); original = deepcopy(module)
        spec = model.unit_spec(module, 'u0'); spec['postcondition'] = 'result > result'
        result = model.revise_unit(module, 'u0', spec, 'Impossible')
        self.assertEqual(result['status'], 'INCONCLUSIVE')
        self.assertIsNone(result['module'])
        spec = model.unit_spec(module, 'u0'); spec['parameters'][0]['name'] = 'other'
        spec['postcondition'] = 'result == other'
        result = model.revise_unit(module, 'u0', spec, 'Signature changed')
        self.assertEqual(result['status'], 'REFUSED')
        self.assertEqual(result['reason'], 'REVISION_SIGNATURE_CHANGE')
        self.assertEqual(module, original)

    def test_invalid_contract_signature_and_schema(self):
        for mutate in ('schema', 'parameters', 'return', 'intent', 'unknown', 'contract'):
            module = module_fixture()
            if mutate == 'schema': module['semantic_profile'] = 'effects'
            elif mutate == 'parameters': module['units'][0]['parameters'][0]['name'] = 'result'
            elif mutate == 'return': module['units'][0]['return_type'] = 'bool'
            elif mutate == 'intent': module['intent'] = ''
            elif mutate == 'unknown': module['execute'] = 'anything'
            else: module['units'][0]['contract'] = {'precondition': 'True', 'postcondition': '__import__("os")'}
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                model.validate_module(module)

    def test_unknown_unit_and_invalid_limits_refused(self):
        module = model.validate_module(module_fixture())
        for function in (model.inline_unit, model.unit_spec):
            with self.assertRaisesRegex(ValueError, 'UNKNOWN_UNIT'):
                function(module, 'u7')
        spec = model.unit_spec(module, 'u0')
        result = model.revise_unit(module, 'u0', spec, 'Bound invalid', {'max_seconds': float('inf')})
        self.assertEqual(result['status'], 'REFUSED')
        self.assertIsNone(result['module'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
