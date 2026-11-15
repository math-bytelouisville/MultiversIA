"""Bounded pure modules, scoped calls and immutable intentional revisions.

No source execution, filesystem access, network or implicit experimental replay.
Hashes bind local content; they are not signatures or proof of import fidelity.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re

import math_core


SOURCE_FIELDS = {
    'schema_version', 'semantic_profile', 'module_id', 'name',
    'parent_revision_id', 'intent', 'provenance', 'units',
}
DERIVED_FIELDS = {'dependencies', 'topological_order', 'coverage'}
UNIT_FIELDS = {'id', 'name', 'qualified_name', 'parameters', 'return_type', 'body', 'contract'}
MAX_SOURCE_BYTES = 524288
MAX_JSON_BYTES = 2097152
MAX_EXPANSION_NODES = 4096
MAX_EXPANSION_DEPTH = 64
LEGACY_PROFILE = 'pure-int32-bool-call-1'
WRAP_PROFILE = 'pure-int32-bool-wrap-call-1'
LIMITATIONS = [
    'Pure Int32/bool, 1 à 8 fonctions, appels internes sans cycle ; aucun effet ni site complet.',
    'Identités publiques conservées dans le modèle ; compatibilité binaire/HTTP non établie.',
    'Révision intentionnelle, sans revendication d’équivalence avec l’ancien comportement.',
    'Les empreintes lient des contenus locaux ; elles ne prouvent ni authenticité ni fidélité de l’import.',
    'Contrats des appelants conservés ; leur vérification appartient aux contrôleurs indépendants.',
]


def _fields(value, expected, reason):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError(reason)


def _text(value, maximum, reason):
    if type(value) is not str or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(reason)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _sha(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def _json_guard(value):
    """Reject cycles, non-JSON objects and huge metadata before deepcopy/hash."""
    ancestors = set()
    count = 0
    text_bytes = 0

    def visit(item, depth=0):
        nonlocal count, text_bytes
        count += 1
        if count > 50000 or depth > 80:
            raise ValueError('MODULE_JSON_BOUND')
        typ = type(item)
        if typ is str:
            text_bytes += len(item.encode('utf-8'))
            if text_bytes > MAX_JSON_BYTES:
                raise ValueError('MODULE_JSON_BOUND')
        elif typ in (list, dict):
            if id(item) in ancestors:
                raise ValueError('CYCLIC_JSON')
            ancestors.add(id(item))
            if typ is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError('NON_JSON_KEY')
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            else:
                for child in item:
                    visit(child, depth + 1)
            ancestors.remove(id(item))
        elif typ is int:
            if item.bit_length() > 128:
                raise ValueError('JSON_INTEGER_BOUND')
        elif typ is float:
            if not math.isfinite(item):
                raise ValueError('NONFINITE_JSON')
        elif item is not None and typ is not bool:
            raise ValueError('NON_JSON_VALUE')
    visit(value)
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_JSON_BYTES:
            raise ValueError('MODULE_JSON_BOUND')
    except (RecursionError, UnicodeError) as exc:
        raise ValueError('MODULE_JSON_ENCODING') from exc


def _spec(unit):
    contract = unit['contract']
    return {
        'schema_version': 'mia-math-spec-1',
        'name': unit['name'] if len(unit['name']) <= 80 else unit['id'],
        'parameters': deepcopy(unit['parameters']), 'return_type': unit['return_type'],
        'precondition': contract['precondition'], 'postcondition': contract['postcondition'],
    }


def _math_text(node):
    kind = node['kind']
    if kind == 'constant':
        return str(node['value'])
    if kind == 'param':
        return node['name']
    if kind == 'neg32':
        return 'neg32(' + _math_text(node['operand']) + ')'
    if kind == 'not':
        return '(not ' + _math_text(node['operand']) + ')'
    if kind == 'binary':
        if node['op'] in math_core.WRAP_BINARY:
            return node['op'] + '(' + _math_text(node['left']) + ', ' + _math_text(node['right']) + ')'
        op = {'eq': '==', 'ne': '!=', 'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>=', 'and': 'and', 'or': 'or'}[node['op']]
        return '(' + _math_text(node['left']) + ' ' + op + ' ' + _math_text(node['right']) + ')'
    if kind == 'ite':
        return '(' + _math_text(node['when_true']) + ' if ' + _math_text(node['condition']) + ' else ' + _math_text(node['when_false']) + ')'
    raise ValueError('UNSUPPORTED_INLINE_IR')


def _inline(units, unit_id):
    if type(unit_id) is not str or unit_id not in units:
        raise ValueError('UNKNOWN_UNIT')
    created = 0
    steps = 0

    def guard(depth, emit=True):
        nonlocal created, steps
        steps += 1
        created += bool(emit)
        if created > MAX_EXPANSION_NODES or depth > MAX_EXPANSION_DEPTH or steps > 16384:
            raise ValueError('INLINE_EXPANSION_BOUND')

    def copy_pure(node, depth):
        guard(depth)
        return {k: copy_pure(v, depth + 1) if type(v) is dict else v for k, v in node.items()}

    def expand(node, env, depth=0):
        kind = node['kind']
        if kind == 'param':
            return copy_pure(env[node['name']], depth)
        if kind == 'call':
            guard(depth, emit=False)
            called = units[node['target']]
            # Arguments are evaluated in the caller scope, before binding the callee.
            args = [expand(arg, env, depth + 1) for arg in node['arguments']]
            bindings = {p['name']: arg for p, arg in zip(called['parameters'], args)}
            return expand(called['body'], bindings, depth + 1)
        guard(depth)
        return {k: expand(v, env, depth + 1) if type(v) is dict else v for k, v in node.items()}

    unit = units[unit_id]
    symbols = {p['name']: p['type'] for p in unit['parameters']}
    env = {name: {'kind': 'param', 'type': typ, 'name': name} for name, typ in symbols.items()}
    result = expand(unit['body'], env)
    try:
        math_core.validate_node(result, symbols)
    except ValueError as exc:
        raise ValueError('INLINE_CORE_LIMIT_OR_TYPE:' + str(exc)) from exc
    return result


def _has_wrap(node):
    if type(node) is not dict: return False
    return node.get('kind') == 'neg32' or (node.get('kind') == 'binary' and node.get('op') in math_core.WRAP_BINARY) or any(
        _has_wrap(value) if type(value) is dict else any(_has_wrap(item) for item in value) if type(value) is list else False
        for value in node.values())


def validate_module(module):
    """Return a detached normalized model; validate supplied revision identity."""
    _json_guard(module)
    if type(module) is not dict or not SOURCE_FIELDS <= set(module) or set(module) - SOURCE_FIELDS - DERIVED_FIELDS - {'revision_id'}:
        raise ValueError('INVALID_MODULE_FIELDS')
    if module['schema_version'] != 'mia-module-1' or module['semantic_profile'] not in (LEGACY_PROFILE, WRAP_PROFILE):
        raise ValueError('UNSUPPORTED_MODULE_PROFILE')
    _text(module['module_id'], 128, 'INVALID_MODULE_ID')
    _text(module['name'], 160, 'INVALID_MODULE_NAME')
    _text(module['intent'], 4096, 'INVALID_INTENT')
    parent = module['parent_revision_id']
    if parent is not None and not _sha(parent):
        raise ValueError('INVALID_PARENT_REVISION')
    provenance = module['provenance']
    if type(provenance) is not dict or not {'source_text', 'source_sha256', 'import_id'} <= set(provenance):
        raise ValueError('INVALID_PROVENANCE_FIELDS')
    _text(provenance['import_id'], 128, 'INVALID_IMPORT_ID')
    source = provenance['source_text']
    if type(source) is not str or not source.strip() or len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise ValueError('PROVENANCE_SOURCE_BOUND')
    if not _sha(provenance['source_sha256']) or hashlib.sha256(source.encode('utf-8')).hexdigest() != provenance['source_sha256']:
        raise ValueError('PROVENANCE_SOURCE_HASH_MISMATCH')
    if type(module['units']) is not list or not 1 <= len(module['units']) <= 8:
        raise ValueError('UNIT_COUNT')
    result = {k: deepcopy(module[k]) for k in sorted(SOURCE_FIELDS)}
    units = {}
    qualified = set()
    for unit in result['units']:
        _fields(unit, UNIT_FIELDS, 'INVALID_UNIT_FIELDS')
        uid = unit['id']
        if type(uid) is not str or re.fullmatch('u[0-7]', uid) is None or uid in units:
            raise ValueError('INVALID_UNIT_ID')
        _text(unit['name'], 256, 'INVALID_UNIT_NAME')
        _text(unit['qualified_name'], 1024, 'INVALID_QUALIFIED_NAME')
        if unit['qualified_name'] in qualified:
            raise ValueError('DUPLICATE_QUALIFIED_NAME')
        qualified.add(unit['qualified_name'])
        # The existing core is the authority for parameter names/types/signatures.
        dummy = dict(unit, contract={'precondition': 'True', 'postcondition': 'result == result'})
        math_core.validate_spec(_spec(dummy))
        units[uid] = unit
    if set(units) != {'u' + str(i) for i in range(len(units))}:
        raise ValueError('UNIT_IDS_NOT_CONTIGUOUS')
    dependencies = {uid: set() for uid in units}
    for uid, unit in units.items():
        symbols = {p['name']: p['type'] for p in unit['parameters']}
        count = 0

        def visit(node, depth=0):
            nonlocal count
            count += 1
            if count > 512 or depth > 32 or type(node) is not dict or type(node.get('type')) is not str or node['type'] not in ('int', 'bool'):
                raise ValueError('MODULE_IR_BOUND_OR_TYPE')
            kind, typ = node.get('kind'), node['type']
            if kind == 'call':
                _fields(node, {'kind', 'type', 'target', 'arguments'}, 'INVALID_CALL_FIELDS')
                target = node['target']
                if type(target) is not str or target not in units:
                    raise ValueError('UNRESOLVED_CALL')
                callee = units[target]
                if type(node['arguments']) is not list or len(node['arguments']) != len(callee['parameters']):
                    raise ValueError('CALL_ARITY')
                if typ != callee['return_type'] or any(visit(arg, depth + 1) != param['type'] for arg, param in zip(node['arguments'], callee['parameters'])):
                    raise ValueError('CALL_TYPE_MISMATCH')
                dependencies[uid].add(target)
            elif kind in ('param', 'constant'):
                math_core.validate_node(node, symbols)
            elif kind == 'neg32':
                _fields(node, {'kind', 'type', 'operand'}, 'INVALID_NEG32_FIELDS')
                if typ != 'int' or visit(node['operand'], depth + 1) != 'int':
                    raise ValueError('INVALID_NEG32_TYPE')
                if result['semantic_profile'] != WRAP_PROFILE:
                    raise ValueError('ARITHMETIC_REQUIRES_WRAP_PROFILE')
            elif kind == 'not':
                _fields(node, {'kind', 'type', 'operand'}, 'INVALID_NOT_FIELDS')
                if typ != 'bool' or visit(node['operand'], depth + 1) != 'bool':
                    raise ValueError('INVALID_NOT_TYPE')
            elif kind == 'binary':
                _fields(node, {'kind', 'type', 'op', 'left', 'right'}, 'INVALID_BINARY_FIELDS')
                left, right = visit(node['left'], depth + 1), visit(node['right'], depth + 1)
                op = node['op']
                if type(op) is not str or left != right or not (
                    typ == 'int' and left == 'int' and op in math_core.WRAP_BINARY or
                    typ == 'bool' and (op in ('eq', 'ne') or op in ('lt', 'le', 'gt', 'ge') and left == 'int' or op in ('and', 'or') and left == 'bool')):
                    raise ValueError('INVALID_BINARY_TYPE')
                if op in math_core.WRAP_BINARY and result['semantic_profile'] != WRAP_PROFILE:
                    raise ValueError('ARITHMETIC_REQUIRES_WRAP_PROFILE')
            elif kind == 'ite':
                _fields(node, {'kind', 'type', 'condition', 'when_true', 'when_false'}, 'INVALID_ITE_FIELDS')
                if visit(node['condition'], depth + 1) != 'bool' or visit(node['when_true'], depth + 1) != typ or visit(node['when_false'], depth + 1) != typ:
                    raise ValueError('INVALID_CONDITIONAL_TYPE')
            else:
                raise ValueError('UNSUPPORTED_MODULE_IR')
            return typ

        if visit(unit['body']) != unit['return_type']:
            raise ValueError('BODY_RETURN_TYPE_MISMATCH')
    seen, active, order = set(), set(), []

    def topo(uid):
        if uid in active:
            raise ValueError('CALL_CYCLE')
        if uid in seen:
            return
        active.add(uid)
        for target in sorted(dependencies[uid]):
            topo(target)
        active.remove(uid)
        seen.add(uid)
        order.append(uid)
    for uid in sorted(units):
        topo(uid)
    for uid in order:
        unit = units[uid]
        expanded = _inline(units, uid)
        if unit['contract'] is None:
            unit['contract'] = {'precondition': 'True', 'postcondition': 'result == (' + _math_text(expanded) + ')'}
        else:
            _fields(unit['contract'], {'precondition', 'postcondition'}, 'INVALID_CONTRACT_FIELDS')
        try:
            spec = math_core.validate_spec(_spec(unit))
            if result['semantic_profile'] == LEGACY_PROFILE and (_has_wrap(spec['pre_ast']) or _has_wrap(spec['post_ast'])):
                raise ValueError('ARITHMETIC_REQUIRES_WRAP_PROFILE')
        except (ValueError, TypeError) as exc:
            raise ValueError('UNIT_CONTRACT_INVALID:' + str(exc)) from exc
    revision = _hash(result)
    if 'revision_id' in module and (not _sha(module['revision_id']) or module['revision_id'] != revision):
        raise ValueError('REVISION_HASH_MISMATCH')
    if parent == revision:
        raise ValueError('REVISION_SELF_PARENT')
    result.update(revision_id=revision, dependencies={k: sorted(v) for k, v in dependencies.items()}, topological_order=order)
    result['coverage'] = {
        'status': 'ALL_DECLARED_UNITS_TYPED', 'units': {uid: 'TYPED_PURE_UNIT' for uid in sorted(units)},
        'declared_units': len(units), 'source_inventory_complete': 'NOT_ESTABLISHED_BY_MODEL_ALONE',
        'formal_scope': 'NOT_PROVED_MODULE', 'site_migrated': False, 'limitations': list(LIMITATIONS),
    }
    return result


def _unit(module, unit_id):
    if type(unit_id) is not str:
        raise ValueError('UNKNOWN_UNIT')
    for unit in module['units']:
        if unit['id'] == unit_id:
            return unit
    raise ValueError('UNKNOWN_UNIT')


def inline_unit(module, unit_id):
    normalized = validate_module(module)
    return _inline({u['id']: u for u in normalized['units']}, unit_id)


def unit_spec(module, unit_id):
    normalized = validate_module(module)
    return _spec(_unit(normalized, unit_id))


def evaluate_unit(module, unit_id, inputs):
    normalized = validate_module(module)
    unit = _unit(normalized, unit_id)
    if type(inputs) is not list or len(inputs) != len(unit['parameters']):
        raise ValueError('INPUT_ARITY')
    env = {}
    for param, value in zip(unit['parameters'], inputs):
        if param['type'] == 'bool':
            valid = type(value) is bool
        else:
            valid = type(value) is int and math_core.LOW <= value <= math_core.HIGH
        if not valid:
            raise ValueError('INPUT_TYPE_OR_RANGE')
        env[param['name']] = value
    return math_core.evaluate(_inline({u['id']: u for u in normalized['units']}, unit_id), env)


def revise_unit(module, unit_id, spec, intent, limits=None):
    report = {'status': 'REFUSED', 'reason': 'NOT_STARTED', 'module': None, 'synthesis': None, 'change': {}}
    try:
        previous = validate_module(module)
        original = _unit(previous, unit_id)
        _text(intent, 4096, 'INVALID_INTENT')
        proposed = math_core.validate_spec(spec)
        if proposed['parameters'] != original['parameters'] or proposed['return_type'] != original['return_type']:
            raise ValueError('REVISION_SIGNATURE_CHANGE')
        affected = set()
        frontier = {unit_id}
        while frontier:
            following = {uid for uid, deps in previous['dependencies'].items() if set(deps) & frontier} - affected
            affected.update(following)
            frontier = following
        report['change'] = {
            'unit_id': unit_id, 'old_revision_id': previous['revision_id'], 'new_revision_id': None,
            'old_unit_sha256': _hash(original), 'new_unit_sha256': None,
            'affected_dependents': sorted(affected), 'intent': intent, 'equivalence_claimed': False,
            'caller_contracts_preserved': True, 'validation_required': [unit_id] + sorted(affected),
        }
        synthesis = math_core.solve(proposed, limits)
        report['synthesis'] = synthesis
        if synthesis['status'] != 'CANDIDATE':
            report.update(status=synthesis['status'], reason=synthesis['reason'])
            return report
        changed = {k: deepcopy(previous[k]) for k in SOURCE_FIELDS}
        changed['parent_revision_id'], changed['intent'] = previous['revision_id'], intent
        target = _unit(changed, unit_id)
        target['body'] = deepcopy(synthesis['candidate'])
        target['contract'] = {k: proposed[k] for k in ('precondition', 'postcondition')}
        revised = validate_module(changed)
        report['change'].update(new_revision_id=revised['revision_id'], new_unit_sha256=_hash(_unit(revised, unit_id)))
        report.update(status='REVISED', reason='INTENTIONAL_REVISION_FINITE_CANDIDATE_CALLERS_REQUIRE_CHECKS', module=revised)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        report.update(status='REFUSED', reason=str(exc), module=None)
    return report
