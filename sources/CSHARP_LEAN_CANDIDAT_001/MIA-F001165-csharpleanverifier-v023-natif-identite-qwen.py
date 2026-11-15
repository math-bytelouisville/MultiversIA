"""CSharpLeanVerifier: real Roslyn, restricted faithful IR, local AI, Lean kernel.

The AI selects a trusted tactic template or a concrete counterexample. It cannot
edit the theorem, the translation, the imports, or execute arbitrary Lean code.
PROUVE is conditional on the explicitly documented Roslyn/translation trust base;
this tool does not claim a mechanically proved C# compiler correctness theorem.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

LOW, HIGH = -2147483648, 2147483647
TYPES = frozenset(('int', 'bool'))
EXPECTED_MODEL_KEY = 'qwen3.5-9b'
STRATEGIES = frozenset(('simp', 'omega', 'split_omega', 'decide', 'grind'))
APPROVED_AXIOMS = frozenset(('propext', 'Classical.choice', 'Quot.sound'))
HERE = Path(__file__).resolve().parent
APPROVED_LEAN_EXE_SHA256 = 'ec7a472d1c12a09b0c0c718f7427c2be527b3f5f07810fdbe076c2507c7a91ea'
APPROVED_PROFILE_SHA256 = '8e185b6463f0dfcb8cb9a3c6408d9d3511b02aa5c4ce38f2f622175cac11c70a'
APPROVED_ARCHIVE_SHA256 = '5fc033e93e1a7bc59c5ebf311103d2c9cd18d4b3eddda4ffa3414e8adb0ef79e'
APPROVED_EXTRACTOR_SHA256 = '5a72348d683ae2a7c550e497411c0da5e679af657908f8679b39076d9be5b8a1'
APPROVED_ALLOCATOR_SHA256 = '86d9afe604633fef5c51299e26abaabde11f15198eda9e87a880c879766f23d7'
APPROVED_ROSLYN_PROFILE_SHA256 = 'ba2a248ffea2839cef605fdf10b20eb8427f3b9a6a1a69b9007b1f8d6c3cc0d7'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(text):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('Duplicate JSON key')
            out[key] = value
        return out

    def invalid_constant(value):
        raise ValueError('Nonfinite JSON number: ' + value)

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('Nonfinite JSON number')
        return result

    if type(text) is not str:
        raise ValueError('JSON input must be text')
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant,
                          parse_float=finite_float)
    except RecursionError:
        raise ValueError('JSON nesting is excessive') from None


def keys(obj, expected):
    if type(obj) is not dict or set(obj) != set(expected):
        raise ValueError('Unexpected object fields')


def validate_method(method):
    keys(method, ('name', 'return_type', 'parameters', 'body'))
    if type(method['name']) is not str or not method['name']:
        raise ValueError('Invalid method name')
    if (type(method['return_type']) is not str or method['return_type'] not in TYPES
            or type(method['parameters']) is not list):
        raise ValueError('Unsupported method type')
    if len(method['parameters']) > 16:
        raise ValueError('Too many parameters')
    env = {}
    for parameter in method['parameters']:
        keys(parameter, ('name', 'type'))
        name = parameter['name']
        if (type(name) is not str or not name or name in env
                or type(parameter['type']) is not str or parameter['type'] not in TYPES):
            raise ValueError('Invalid or repeated parameter')
        env[name] = parameter['type']

    def node(n, depth=0):
        if (depth > 64 or type(n) is not dict or type(n.get('type')) is not str
                or n['type'] not in TYPES or type(n.get('kind')) is not str):
            raise ValueError('Invalid expression or excessive depth')
        kind, typ = n.get('kind'), n['type']
        if kind == 'constant':
            keys(n, ('kind', 'type', 'value'))
            if typ == 'int':
                if type(n['value']) is not int or not LOW <= n['value'] <= HIGH:
                    raise ValueError('Literal outside Int32')
            elif type(n['value']) is not bool:
                raise ValueError('Invalid Boolean literal')
        elif kind == 'param':
            keys(n, ('kind', 'type', 'name'))
            if type(n['name']) is not str or env.get(n['name']) != typ:
                raise ValueError('Unknown parameter or type mismatch')
        elif kind == 'binary':
            keys(n, ('kind', 'type', 'op', 'left', 'right'))
            if type(n['op']) is not str:
                raise ValueError('Invalid operator representation')
            lt, rt = node(n['left'], depth + 1), node(n['right'], depth + 1)
            if typ != 'bool' or lt != rt:
                raise ValueError('Invalid binary types')
            if n['op'] in ('eq', 'ne'):
                pass
            elif n['op'] in ('lt', 'le', 'gt', 'ge') and lt == 'int':
                pass
            elif n['op'] in ('and', 'or') and lt == 'bool':
                pass
            else:
                raise ValueError('Unsupported binary operator')
        elif kind == 'not':
            keys(n, ('kind', 'type', 'operand'))
            if typ != 'bool' or node(n['operand'], depth + 1) != 'bool':
                raise ValueError('Invalid Boolean negation')
        elif kind == 'ite':
            keys(n, ('kind', 'type', 'condition', 'when_true', 'when_false'))
            if (node(n['condition'], depth + 1) != 'bool'
                    or node(n['when_true'], depth + 1) != typ
                    or node(n['when_false'], depth + 1) != typ):
                raise ValueError('Invalid conditional types')
        else:
            raise ValueError('Unsupported IR node')
        return typ

    if node(method['body']) != method['return_type']:
        raise ValueError('Wrong return type')


def lean_int(value):
    return f'({value} : Int)' if value >= 0 else f'(-{abs(value)} : Int)'


def render_prop(n, env):
    kind = n['kind']
    if kind == 'constant':
        return 'True' if n['value'] else 'False'
    if kind == 'param':
        return f'({env[n["name"]]} = true)'
    if kind == 'not':
        return f'(¬ {render_prop(n["operand"], env)})'
    if kind == 'ite':
        return (f'(if {render_prop(n["condition"], env)} then '
                f'{render_prop(n["when_true"], env)} else {render_prop(n["when_false"], env)})')
    op = n['op']
    if op in ('and', 'or'):
        symbol = '∧' if op == 'and' else '∨'
        return f'({render_prop(n["left"], env)} {symbol} {render_prop(n["right"], env)})'
    if n['left']['type'] == 'bool':
        equality = f'({render_prop(n["left"], env)} ↔ {render_prop(n["right"], env)})'
        return equality if op == 'eq' else f'(¬ {equality})'
    symbol = {'eq': '=', 'ne': '≠', 'lt': '<', 'le': '≤', 'gt': '>', 'ge': '≥'}[op]
    return f'({render_value(n["left"], env)} {symbol} {render_value(n["right"], env)})'


def render_value(n, env):
    if n['type'] == 'bool':
        return f'(decide {render_prop(n, env)})'
    if n['kind'] == 'constant':
        return lean_int(n['value'])
    if n['kind'] == 'param':
        return env[n['name']]
    if n['kind'] == 'ite':
        return (f'(if {render_prop(n["condition"], env)} then '
                f'{render_value(n["when_true"], env)} else {render_value(n["when_false"], env)})')
    raise ValueError('Invalid integer IR')


def prepare_translation(source_ir, contract_ir):
    for ir in (source_ir, contract_ir):
        if (type(ir) is not dict or ir.get('schema') != 'mia-csharp-ir-v1'
                or ir.get('status') != 'TRANSLATED' or ir.get('reason') != 'SUPPORTED_SUBSET'):
            raise ValueError('Roslyn did not establish the supported subset')
        if 'method' not in ir:
            raise ValueError('Roslyn method is missing')
        validate_method(ir['method'])
    program, contract = source_ir['method'], contract_ir['method']
    parameters = program['parameters']
    expected = parameters + [{'name': 'result', 'type': program['return_type']}]
    if contract['return_type'] != 'bool' or contract['parameters'] != expected:
        raise ValueError('Contract must return bool with program parameters followed by result')
    if any(p['name'] == 'result' for p in parameters):
        raise ValueError('Program parameter result is reserved for the postcondition')
    env = {p['name']: f'p{i}' for i, p in enumerate(parameters)}
    lean_types = {'int': 'Int', 'bool': 'Bool'}
    binders = ' '.join(f'(p{i} : {lean_types[p["type"]]})' for i, p in enumerate(parameters))
    arguments = ' '.join(env[p['name']] for p in parameters)
    program_call = '(mia_program' + (' ' + arguments if arguments else '') + ')'
    spec_env = dict(env, result='r')
    definitions = ('import Std\n\n'
                   'set_option autoImplicit false\n'
                   'set_option linter.unusedVariables false\n'
                   'set_option linter.unusedSimpArgs false\n\n'
                   f'def mia_program {binders} : {lean_types[program["return_type"]]} :=\n'
                   f'  {render_value(program["body"], env)}\n\n'
                   f'def mia_spec {binders} (r : {lean_types[program["return_type"]]}) : Prop :=\n'
                   f'  {render_prop(contract["body"], spec_env)}\n\n')
    bound_list = [f'({LOW} ≤ p{i} ∧ p{i} ≤ {HIGH})'
                  for i, p in enumerate(parameters) if p['type'] == 'int']
    property_text = '(mia_spec' + (' ' + arguments if arguments else '') + ' ' + program_call + ')'
    goal = ('∀ ' + binders + ', ' if binders else '') + ' → '.join(bound_list + [property_text])
    justification = {
        'status': 'RULE_JUSTIFIED_WITH_EXPLICIT_TRUST_BASE',
        'formal_csharp_lean_equivalence_proved': False,
        'source_semantics': 'Roslyn IOperation; pure Int32/Boolean expressions only',
        'rules': [
            'Int32 literals and parameters embed injectively into Int with explicit input bounds.',
            'No arithmetic, casts, side effects, calls or undefined/unsupported constructions are accepted.',
            'Integer comparisons preserve the signed Int32 order under this embedding.',
            'Boolean operations and equality map to propositions; Bool values are produced by decide.',
            'Pure conditional selection preserves branch selection and the selected value.',
            'Pure if/else and return-only blocks are lowered by continuation-passing branch selection without mutable state.',
            'Namespace wrappers do not alter the accepted method operations or parameter types.',
            'All possible integer results are in-range literals or in-range parameters selected by conditions.',
            'Short-circuit and eager proposition representations agree because all accepted operands are pure and total.'
        ],
        'trusted_components': ['Roslyn semantic extraction', 'reviewed IR whitelist and translation rules',
                               'official Lean kernel and approved axioms', 'host runtime and operating system'],
        'scope': 'The named method and the fixed postcondition, all admitted Int32/Bool inputs.'
    }
    return {'definitions': definitions, 'goal': goal, 'parameters': parameters,
            'return_type': program['return_type'], 'justification': justification}


def validate_proposal(obj, parameters):
    keys(obj, ('kind', 'strategy', 'values'))
    if (type(obj['kind']) is not str or obj['kind'] not in ('proof', 'counterexample')
            or type(obj['strategy']) is not str or obj['strategy'] not in STRATEGIES):
        raise ValueError('Unknown proof proposal')
    if type(parameters) is not list:
        raise ValueError('Parameters must be a list')
    for parameter in parameters:
        keys(parameter, ('name', 'type'))
        if (type(parameter['name']) is not str or not parameter['name']
                or type(parameter['type']) is not str or parameter['type'] not in TYPES):
            raise ValueError('Invalid proposal parameter')
    if type(obj['values']) is not list:
        raise ValueError('Counterexample values must be a list')
    if obj['kind'] == 'proof':
        if obj['values']:
            raise ValueError('Proof proposal must not contain input values')
    else:
        if len(obj['values']) != len(parameters):
            raise ValueError('Wrong counterexample arity')
        for value, parameter in zip(obj['values'], parameters):
            if parameter['type'] == 'int':
                if type(value) is not int or not LOW <= value <= HIGH:
                    raise ValueError('Counterexample outside Int32')
            elif type(value) is not bool:
                raise ValueError('Counterexample Boolean type mismatch')
    return obj


def certificate_text(translation, proposal):
    validate_proposal(proposal, translation['parameters'])
    if proposal['kind'] == 'proof':
        goal = translation['goal']
        strategies = {
            'simp': '  intros\n  simp_all [mia_program, mia_spec]\n',
            'omega': '  intros\n  simp_all [mia_program, mia_spec] <;> omega\n',
            'split_omega': '  intros\n  simp only [mia_program, mia_spec]\n  split <;> simp_all <;> omega\n',
            'decide': '  decide\n',
            'grind': '  intros\n  unfold mia_program mia_spec\n  grind\n'
        }
        proof = strategies[proposal['strategy']]
    else:
        values = ['true' if v is True else 'false' if v is False else lean_int(v) for v in proposal['values']]
        arguments = ' '.join(values)
        call = '(mia_program' + (' ' + arguments if arguments else '') + ')'
        prop = '(mia_spec' + (' ' + arguments if arguments else '') + ' ' + call + ')'
        bounds = [f'({LOW} ≤ {v} ∧ {v} ≤ {HIGH})'
                  for p, v in zip(translation['parameters'], values) if p['type'] == 'int']
        goal = ' ∧ '.join(bounds + ['(¬ ' + prop + ')'])
        proof = '  simp only [mia_spec, mia_program] <;> decide\n'
    return (translation['definitions'] + 'set_option maxHeartbeats 400000\n\n'
            + 'theorem mia_certificate : ' + goal + ' := by\n' + proof
            + '\n#print axioms mia_certificate\n')


def check_certificate_output(returncode, stdout, stderr):
    if type(returncode) is not int or type(stdout) is not str or type(stderr) is not str:
        return {'accepted': False, 'axioms': [], 'reason': 'MALFORMED_PROCESS_RESULT'}
    if returncode != 0:
        return {'accepted': False, 'axioms': [], 'reason': 'LEAN_REJECTED'}
    if stderr.strip() or re.search(r'\b(sorry|sorryAx|error|warning)\b', stdout, re.I):
        return {'accepted': False, 'axioms': [], 'reason': 'LEAN_DIAGNOSTIC'}
    text = stdout.strip()
    if re.fullmatch(r"'?mia_certificate'? does not depend on any axioms", text):
        axioms = []
    else:
        match = re.fullmatch(r"'?mia_certificate'? depends on axioms:\s*\[([^\]]*)\]", text, re.S)
        if not match:
            return {'accepted': False, 'axioms': [], 'reason': 'AXIOM_REPORT_MISSING_OR_UNEXPECTED_OUTPUT'}
        axioms = [a.strip() for a in match.group(1).split(',') if a.strip()]
        if len(axioms) != len(set(axioms)) or not set(axioms) <= APPROVED_AXIOMS:
            return {'accepted': False, 'axioms': axioms, 'reason': 'UNAPPROVED_AXIOM'}
    return {'accepted': True, 'axioms': axioms, 'reason': 'KERNEL_CHECKED_TRUSTED_CERTIFICATE'}


def validate_endpoint(endpoint):
    url = urllib.parse.urlparse(endpoint)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username or url.password or url.query or url.fragment
            or url.path != '/api/v1/chat'):
        raise ValueError('Only a local LM Studio native chat endpoint is permitted')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('HTTP redirects are forbidden')


def model_request(translation, model, previous):
    schema = {'type': 'object', 'additionalProperties': False,
              'properties': {'kind': {'type': 'string', 'enum': ['proof', 'counterexample']},
                             'strategy': {'type': 'string', 'enum': sorted(STRATEGIES)},
                             'values': {'type': 'array', 'items': {'anyOf': [{'type': 'integer'}, {'type': 'boolean'}]}}},
              'required': ['kind', 'strategy', 'values']}
    system = ('You are a mathematical proof search assistant. You cannot declare a proof valid. '
              'Your task is to select a strategy, not to derive a full proof. Select immediately from the goal shape '
              'and return only one JSON object matching the schema. No derivation, code, or prose. '
              'For a true universal property choose kind=proof and values=[]. '
              'For a universal property with conditional expressions, integer comparisons, or arithmetic constraints, '
              'prefer grind as the first proof strategy unless you can see a counterexample. '
              'grind unfolds the definitions and searches logical and arithmetic consequences, including nested conditions. '
              'simp performs simplification but does not generally solve branch analysis. '
              'omega adds linear integer arithmetic after simplification. '
              'split_omega handles one conditional split; prefer grind for nested conditions. '
              'decide is for closed goals, not universal goals quantified over Int. '
              'For a false property choose kind=counterexample and concrete input values in parameter order, '
              'respecting Int32 bounds; use strategy=decide for that closed counterexample. '
              'The checker independently validates everything. Do not spend the response budget proving the theorem yourself. '
              'Make the selection and output the final JSON now. /no_think')
    mathematics = (translation['definitions'] + '\nTarget proposition:\n' + translation['goal']
                   + '\nPrevious rejected proposals (try a different valid proposal):\n'
                   + json.dumps(previous, ensure_ascii=False))
    system += '\nThe entire response must be a JSON object satisfying this schema: ' + json.dumps(schema, separators=(',', ':'))
    return {'model': model, 'input': mathematics, 'system_prompt': system,
            'temperature': 0, 'max_output_tokens': 1536, 'reasoning': 'off',
            'store': False, 'stream': False, 'integrations': []}


def validate_native_request(request):
    keys(request, ('model', 'input', 'system_prompt', 'temperature', 'max_output_tokens',
                   'reasoning', 'store', 'stream', 'integrations'))
    for key in ('model', 'input', 'system_prompt'):
        if type(request[key]) is not str or not request[key].strip():
            raise ValueError('Native request text field is missing')
    if (type(request['temperature']) not in (int, float) or request['temperature'] != 0
            or type(request['max_output_tokens']) is not int or request['max_output_tokens'] != 1536
            or request['reasoning'] != 'off' or request['store'] is not False
            or request['stream'] is not False or type(request['integrations']) is not list
            or request['integrations']):
        raise ValueError('Native generation policy differs from the approved policy')
    if len(json.dumps(request, ensure_ascii=False).encode('utf8')) > 1024 * 1024:
        raise ValueError('Native request is too large')


def validate_loaded_model(catalog, expected_model):
    keys(catalog, ('models',))
    if type(expected_model) is not str or not expected_model or type(catalog['models']) is not list:
        raise ValueError('Malformed local model catalog')
    matches = []
    for model in catalog['models']:
        if (type(model) is not dict or type(model.get('key')) is not str or not model['key']
                or type(model.get('type')) is not str or model['type'] not in ('llm', 'embedding', 'decision')
                or type(model.get('loaded_instances')) is not list):
            raise ValueError('Malformed local model entry')
        for instance in model['loaded_instances']:
            if (type(instance) is not dict or type(instance.get('id')) is not str or not instance['id']
                    or type(instance.get('config')) is not dict):
                raise ValueError('Malformed loaded model instance')
            if instance['id'] == expected_model:
                if model['type'] != 'llm' or model['key'] != EXPECTED_MODEL_KEY or model['key'] == expected_model:
                    raise ValueError('The instance must be a distinct alias of the approved Qwen3.5-9B model key')
                capabilities = model.get('capabilities')
                reasoning = capabilities.get('reasoning') if type(capabilities) is dict else None
                allowed = reasoning.get('allowed_options') if type(reasoning) is dict else None
                if type(allowed) is not list or any(type(option) is not str for option in allowed) or 'off' not in allowed:
                    raise ValueError('The loaded model does not advertise reasoning off support')
                matches.append({'model_instance_id': instance['id'], 'model_key': model['key'],
                                'config': instance['config']})
    if len(matches) != 1:
        raise ValueError('The requested model instance is not uniquely loaded; automatic loading is disabled')
    return matches[0]


def validate_native_response(obj, expected_model):
    keys(obj, ('model_instance_id', 'output', 'stats'))
    if type(obj['model_instance_id']) is not str or obj['model_instance_id'] != expected_model:
        raise ValueError('Native response model identity mismatch')
    if type(obj['output']) is not list or len(obj['output']) != 1:
        raise ValueError('Exactly one native message is required; tools and reasoning items are forbidden')
    item = obj['output'][0]
    keys(item, ('type', 'content'))
    if item['type'] != 'message' or type(item['content']) is not str or not item['content'].strip():
        raise ValueError('Native output must contain one nonempty text message')
    stats = obj['stats']
    keys(stats, ('input_tokens', 'total_output_tokens', 'reasoning_output_tokens',
                 'tokens_per_second', 'time_to_first_token_seconds'))
    for key in ('input_tokens', 'total_output_tokens', 'reasoning_output_tokens'):
        if type(stats[key]) is not int or stats[key] < 0:
            raise ValueError('Malformed native token accounting')
    if stats['reasoning_output_tokens'] != 0:
        raise ValueError('Native reasoning was not disabled')
    if not 0 < stats['total_output_tokens'] < 1536:
        raise ValueError('Native output is empty or reached the token limit; completion is unconfirmed')
    for key in ('tokens_per_second', 'time_to_first_token_seconds'):
        if type(stats[key]) not in (int, float) or not math.isfinite(stats[key]) or stats[key] < 0:
            raise ValueError('Malformed native timing statistics')
    return item['content']


def call_model(endpoint, request, timeout, raw_callback=None, preflight_callback=None):
    validate_endpoint(endpoint)
    validate_native_request(request)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def receive(req, callback):
        try:
            with opener.open(req, timeout=timeout) as response:
                data = response.read(4 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            data = error.read(4 * 1024 * 1024 + 1)
            if callback is not None:
                callback(data)
            raise
        if callback is not None:
            callback(data)
        if len(data) > 4 * 1024 * 1024:
            raise ValueError('Local response too large')
        return strict_json(data.decode('utf-8'))

    parsed = urllib.parse.urlparse(endpoint)
    catalog_url = urllib.parse.urlunparse(parsed._replace(path='/api/v1/models'))
    catalog = receive(urllib.request.Request(catalog_url, method='GET'), preflight_callback)
    validate_loaded_model(catalog, request['model'])
    payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')
    req = urllib.request.Request(endpoint, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    obj = receive(req, raw_callback)
    validate_native_response(obj, request['model'])
    return obj


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def extract_ir(pwsh, extractor, snapshot, method, timeout):
    command = ("$ErrorActionPreference='Stop'; $env:PSModulePath=$PSHOME+'\\Modules'; & ([scriptblock]::Create([IO.File]::ReadAllText("
               + ps_quote(extractor) + '))) -SourcePath ' + ps_quote(snapshot)
               + ' -MethodName ' + ps_quote(method))
    encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    ps_env = dict(os.environ)
    for variable in ('DOTNET_STARTUP_HOOKS', 'DOTNET_ADDITIONAL_DEPS', 'DOTNET_SHARED_STORE',
                     'DOTNET_ROOT', 'DOTNET_ROOT_X64'):
        ps_env.pop(variable, None)
    ps_env['PSModulePath'] = str(Path(pwsh).parent / 'Modules')
    process = subprocess.run([str(pwsh), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                             capture_output=True, text=True, encoding='utf-8', timeout=timeout, env=ps_env)
    if process.returncode or process.stderr.strip():
        raise RuntimeError('Roslyn subprocess failed: ' + process.stderr[:4000])
    return strict_json(process.stdout)


class ToolchainError(ValueError):
    """The executable/import closure does not match the pinned official profile."""


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_toolchain(manifest_path, lean_path):
    started = time.monotonic()
    manifest_path, lean_path = Path(manifest_path), Path(lean_path)
    try:
        raw = manifest_path.read_bytes()
        if sha(raw) != APPROVED_PROFILE_SHA256:
            raise ToolchainError('Unapproved toolchain manifest fingerprint')
        profile = strict_json(raw.decode('utf-8'))
        keys(profile, ('schema', 'version', 'official_archive_sha256', 'coverage', 'files'))
        if (profile['schema'] != 'mia-approved-lean-toolchain-v1' or profile['version'] != '4.35.0'
                or profile['official_archive_sha256'] != APPROVED_ARCHIVE_SHA256
                or profile['coverage'] != ['bin/**', 'lib/lean/**'] or type(profile['files']) is not dict):
            raise ToolchainError('Invalid approved profile metadata')
        root = lean_path.parent.parent
        if lean_path.name != 'lean.exe' or lean_path.parent.name != 'bin':
            raise ToolchainError('Expected the official bin/lean.exe layout')
        if lean_path.absolute() != lean_path.resolve() or root.is_symlink() or root.is_junction():
            raise ToolchainError('Toolchain links are not accepted')
        if file_sha(lean_path) != APPROVED_LEAN_EXE_SHA256:
            raise ToolchainError('Unapproved Lean executable fingerprint')
        expected = set()
        for relative, item in profile['files'].items():
            if type(relative) is not str:
                raise ToolchainError('Non-string profile path')
            rel = PurePosixPath(relative)
            if (rel.is_absolute() or rel.as_posix() != relative or '..' in rel.parts
                    or any(':' in part or '\\' in part for part in rel.parts)
                    or not (relative.startswith('bin/') or relative.startswith('lib/lean/'))):
                raise ToolchainError('Unsafe or noncanonical profile path')
            keys(item, ('sha256', 'bytes'))
            if (type(item['sha256']) is not str or re.fullmatch('[0-9a-f]{64}', item['sha256']) is None
                    or type(item['bytes']) is not int or item['bytes'] < 0):
                raise ToolchainError('Invalid profile file metadata')
            expected.add(relative)
        actual = set()
        for subtree in (root / 'bin', root / 'lib' / 'lean'):
            if not subtree.is_dir() or subtree.is_symlink() or subtree.is_junction():
                raise ToolchainError('Missing or linked toolchain subtree')
            for directory, dirs, names in os.walk(subtree, followlinks=False):
                for name in dirs + names:
                    path = Path(directory) / name
                    if path.is_symlink() or path.is_junction():
                        raise ToolchainError('Linked toolchain entry')
                for name in names:
                    actual.add((Path(directory) / name).relative_to(root).as_posix())
        if actual != expected:
            raise ToolchainError('Toolchain inventory differs from the approved profile')
        total = 0
        for relative in sorted(expected):
            item = profile['files'][relative]
            path = root.joinpath(*PurePosixPath(relative).parts)
            if path.stat().st_size != item['bytes'] or file_sha(path) != item['sha256']:
                raise ToolchainError('Toolchain file integrity failure: ' + relative)
            total += item['bytes']
        return {'status': 'APPROVED_FULL_INVENTORY_VERIFIED', 'root': str(root),
                'manifest': str(manifest_path), 'manifest_sha256': APPROVED_PROFILE_SHA256,
                'official_archive_sha256': APPROVED_ARCHIVE_SHA256,
                'lean_exe_sha256': APPROVED_LEAN_EXE_SHA256, 'files_checked': len(expected),
                'bytes_checked': total, 'elapsed_seconds': time.monotonic() - started}
    except ToolchainError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ToolchainError('Toolchain verification failed: ' + str(error)) from error


class TranslatorError(ValueError):
    """A translation component differs from the reviewed, pinned implementation."""


def validate_roslyn_runtime(manifest_path, pwsh_path):
    started = time.monotonic()
    manifest_path, pwsh_path = Path(manifest_path), Path(pwsh_path)
    try:
        raw = manifest_path.read_bytes()
        if sha(raw) != APPROVED_ROSLYN_PROFILE_SHA256:
            raise TranslatorError('Unapproved Roslyn runtime profile fingerprint')
        profile = strict_json(raw.decode('utf-8'))
        keys(profile, ('source', 'isolated_root', 'files'))
        if (type(profile['source']) is not str or type(profile['isolated_root']) is not str
                or type(profile['files']) is not dict):
            raise TranslatorError('Invalid Roslyn runtime profile')
        root = pwsh_path.parent
        if pwsh_path.name != 'pwsh.exe' or pwsh_path.absolute() != pwsh_path.resolve():
            raise TranslatorError('Expected a direct pwsh.exe from the approved isolated runtime')
        if root.is_symlink() or root.is_junction():
            raise TranslatorError('Linked runtime root is not accepted')
        expected = set()
        for relative, item in profile['files'].items():
            if type(relative) is not str:
                raise TranslatorError('Non-string runtime path')
            rel = PurePosixPath(relative)
            if (not rel.parts or rel.is_absolute() or rel.as_posix() != relative or '..' in rel.parts
                    or any(':' in part or '\\' in part for part in rel.parts)):
                raise TranslatorError('Unsafe runtime profile path')
            keys(item, ('sha256', 'bytes'))
            if (type(item['sha256']) is not str or re.fullmatch('[0-9a-f]{64}', item['sha256']) is None
                    or type(item['bytes']) is not int or item['bytes'] < 0):
                raise TranslatorError('Invalid runtime profile entry')
            expected.add(relative)
        if 'pwsh.exe' not in expected:
            raise TranslatorError('Runtime executable is absent from the approved profile')
        actual = set()
        for directory, dirs, names in os.walk(root, followlinks=False):
            for name in dirs + names:
                path = Path(directory) / name
                if path.is_symlink() or path.is_junction():
                    raise TranslatorError('Linked runtime entry')
            for name in names:
                actual.add((Path(directory) / name).relative_to(root).as_posix())
        if actual != expected:
            raise TranslatorError('Roslyn runtime inventory differs from the approved profile')
        total = 0
        for relative in sorted(expected):
            item = profile['files'][relative]
            path = root.joinpath(*PurePosixPath(relative).parts)
            if path.stat().st_size != item['bytes'] or file_sha(path) != item['sha256']:
                raise TranslatorError('Runtime file integrity failure: ' + relative)
            total += item['bytes']
        return {'status': 'APPROVED_FULL_INVENTORY_VERIFIED', 'root': str(root),
                'manifest': str(manifest_path), 'manifest_sha256': APPROVED_ROSLYN_PROFILE_SHA256,
                'files_checked': len(expected), 'bytes_checked': total,
                'elapsed_seconds': time.monotonic() - started}
    except TranslatorError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise TranslatorError('Roslyn runtime verification failed: ' + str(error)) from error


def sanitize_diagnostic(text, paths):
    """Keep mathematical diagnostics while removing Windows/POSIX source locations."""
    for path in paths:
        variants = {str(path), Path(path).as_posix(), str(path).replace('\\', '/')}
        for variant in variants:
            text = re.sub(re.escape(variant), '<mathematical-certificate>', text, flags=re.IGNORECASE)
    text = re.sub(r'(?m)^<mathematical-certificate>:\d+:\d+(?::\d+)?:\s*', '', text)
    text = re.sub(r'(?m)^[^\r\n]*?\.lean:\d+:\d+(?::\d+)?:\s*', '', text)
    return text


def human_report(report, json_path):
    lines = ['# CSharpLeanVerifier — ' + report['verdict'], '',
             '- Version : ' + report['version'],
             '- Motif : `' + report['reason'] + '`',
             '- Méthode : `' + str(report.get('source_method', 'non analysée')) + '`',
             '- Source SHA-256 : `' + str(report.get('source_sha256', 'non disponible')) + '`',
             '- Contrat SHA-256 : `' + str(report.get('contract_sha256', 'non disponible')) + '`',
             '', '## Définitions mathématiques anonymisées', '',
             '```lean', report.get('definitions', 'Définitions indisponibles : traduction non établie.'), '```', '',
             '## Propriété et portée', '',
             '```lean', report.get('goal', 'Traduction indisponible : aucune propriété prouvée.'), '```', '',
             'Les paramètres int sont bornés à [-2147483648, 2147483647] ; bool possède ses deux valeurs.',
             'La propriété porte uniquement sur la méthode et le contrat identifiés ci-dessus.', '',
             '## Garanties et limites', '',
             'Lean contrôle le certificat mathématique et ses dépendances. La fidélité C#–Lean repose sur les règles explicites du sous-ensemble et les composants épinglés.',
             '**Aucune démonstration formelle générale de l’équivalence du compilateur C#–Lean n’est revendiquée. Les tests ne remplacent pas cette démonstration.**', '']
    lines.extend('- ' + text for text in report['limitations'])
    lines.extend(['', '## Sources et contrat figés', ''])
    for field, label in (('source_snapshot', 'Source C#'), ('contract_snapshot', 'Contrat C#')):
        if report.get(field):
            snapshot = Path(report[field])
            lines.append('- ' + label + ' : [' + snapshot.name + '](' + snapshot.name + ').')
    justification = report.get('translation_justification', {})
    if justification.get('rules'):
        lines.extend(['', '### Justification explicite de la traduction', ''])
        lines.extend('- ' + rule for rule in justification['rules'])
    lines.extend(['', '## Preuves et diagnostic', ''])
    if report.get('certificate'):
        path = Path(report['certificate'])
        lines.append('- Certificat : [' + path.name + '](' + path.name + ')')
    if 'counterexample' in report:
        lines.append('- Contre-exemple validé, dans l’ordre des paramètres : `' + json.dumps(report['counterexample']) + '`.')
    if 'approved_axioms_used' in report:
        lines.append('- Axiomes approuvés utilisés : `' + ', '.join(report['approved_axioms_used']) + '` (liste vide : aucun).')
    if report.get('error'):
        lines.extend(['', 'Erreur technique explicite :', '', '```text', report['error'], '```'])
    for attempt in report['attempts']:
        lines.append('- Essai ' + str(attempt['attempt']) + ' : `' + str(attempt.get('reason', 'non terminé')) + '`.')
    lines.extend(['', '- Rapport JSON et inventaire détaillé : [' + json_path.name + '](' + json_path.name + ').',
                  '', 'PROUVÉ signifie que le certificat de la propriété a été accepté sous la base de confiance documentée. RÉFUTÉ signifie qu’un contre-exemple admissible a été contrôlé par Lean. INCONCLUSIF ne signifie pas que le programme est faux.', ''])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--method', required=True)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--contract-method', default='Spec')
    parser.add_argument('--lean', type=Path, default=HERE / 'outils-tiers-lean-4.35.0' / 'lean-4.35.0-windows' / 'bin' / 'lean.exe')
    parser.add_argument('--pwsh', type=Path, default=HERE / 'outils-tiers-powershell-7.6.9' / 'pwsh.exe')
    parser.add_argument('--extractor', type=Path, default=HERE / 'MIA-F000860-extracteur-roslyn-v020-blocs-purs.ps1')
    parser.add_argument('--allocator', type=Path, default=HERE / 'MIA-F000813-allocation-immuable-csharplean.py')
    parser.add_argument('--roslyn-manifest', type=Path,
                        default=HERE / 'MIA-F000877-manifest-runtime-powershell-roslyn-isole.json')
    parser.add_argument('--toolchain-manifest', type=Path,
                        default=HERE / 'MIA-F000847-manifest-toolchain-lean435-approuve.json')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--model', default='mia-proof-qwen35-9b')
    parser.add_argument('--endpoint', default='http://127.0.0.1:1234/api/v1/chat')
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--max-attempts', type=int, default=3)
    args = parser.parse_args(argv)
    if not 1 <= args.max_attempts <= 10 or not 1 <= args.timeout <= 600:
        parser.error('Invalid attempt or time budget')
    validate_endpoint(args.endpoint)
    try:
        allocator_bytes = args.allocator.read_bytes()
        if sha(allocator_bytes) != APPROVED_ALLOCATOR_SHA256:
            raise ValueError('Allocator fingerprint does not match the approved implementation')
    except (OSError, ValueError) as error:
        print(json.dumps({'verdict': 'INCONCLUSIF', 'reason': 'UNAPPROVED_ALLOCATOR',
                          'error': str(error)}, ensure_ascii=False))
        return 2
    allocator_scope = {'__name__': 'csharplean_allocator', '__file__': str(args.allocator.resolve())}
    exec(compile(allocator_bytes, str(args.allocator), 'exec'), allocator_scope)
    allocate = allocator_scope['allocate']
    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence = []

    def save(role, extension, content):
        data = content if isinstance(content, bytes) else content.encode('utf-8')
        path = allocate(args.output_dir, role, extension, data)
        evidence.append({'path': str(path), 'sha256': sha(data), 'bytes': len(data)})
        return path

    def save_json(role, obj):
        return save(role, 'json', json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n')

    report = {'tool': 'CSharpLeanVerifier', 'version': '0.2.3', 'verdict': 'INCONCLUSIF',
              'reason': 'NOT_COMPLETED', 'model': args.model, 'attempts': [],
              'transport': 'lmstudio-native-v1', 'requested_reasoning': 'off',
              'automatic_model_loading': False,
              'resource_limit_qualified': False,
              'formal_csharp_lean_equivalence_proved': False,
              'limitations': ['Pure, total Int32/Boolean expression subset only.',
                              'Pure if/else and return-only blocks plus namespace wrappers are supported; arithmetic, local variables, loops, calls, mutable state and concurrency remain unsupported.',
                              'Rule-justified translation; no machine-checked proof of Roslyn or the translator.',
                              'Local Qwen proof search is restricted to five trusted tactic templates and concrete counterexamples; EasyProver is not integrated and this is not an unrestricted general prover.']}
    exit_code = 2
    try:
        for base in ('Std', 'Lean', 'Init'):
            for suffix in ('', '.lean', '.olean', '.ilean'):
                if (args.output_dir / (base + suffix)).exists():
                    raise ValueError('Output directory can shadow a trusted Lean import')
        source_bytes, contract_bytes = args.source.read_bytes(), args.contract.read_bytes()
        for data in (source_bytes, contract_bytes):
            if len(data) > 1024 * 1024:
                raise ValueError('Source exceeds one MiB')
            data.decode('utf-8-sig', errors='strict')
        source_snapshot = save('source-csharp-snapshot', 'cs', source_bytes)
        contract_snapshot = save('contrat-csharp-snapshot', 'cs', contract_bytes)
        report['source_snapshot'] = str(source_snapshot)
        report['contract_snapshot'] = str(contract_snapshot)
        report['source_sha256'], report['contract_sha256'] = sha(source_bytes), sha(contract_bytes)
        report['source_method'], report['contract_method'] = args.method, args.contract_method
        report['components_sha256'] = {str(p): sha(p.read_bytes())
                                       for p in (Path(__file__), args.extractor, args.allocator, args.lean,
                                                 args.toolchain_manifest, args.roslyn_manifest, args.pwsh)}
        report['components_sha256'][str(args.allocator)] = sha(allocator_bytes)
        if report['components_sha256'][str(args.extractor)] != APPROVED_EXTRACTOR_SHA256:
            raise TranslatorError('Unapproved extractor fingerprint')
        report['roslyn_runtime_verification'] = validate_roslyn_runtime(args.roslyn_manifest, args.pwsh)
        ir = extract_ir(args.pwsh, args.extractor, source_snapshot, args.method, args.timeout)
        contract_ir = extract_ir(args.pwsh, args.extractor, contract_snapshot, args.contract_method, args.timeout)
        save_json('roslyn-programme-ir', ir)
        save_json('roslyn-contrat-ir', contract_ir)
        if type(ir) is not dict or type(contract_ir) is not dict:
            raise ValueError('Roslyn IR root must be an object')
        if ir.get('source_sha256') != sha(source_bytes) or contract_ir.get('source_sha256') != sha(contract_bytes):
            raise ValueError('Roslyn snapshot fingerprint mismatch')
        for obj in (ir, contract_ir):
            if type(obj) is not dict:
                raise ValueError('Roslyn IR root must be an object')
            if obj.get('status') != 'TRANSLATED':
                report['reason'] = obj.get('reason', 'ROSLYN_REFUSED')
                raise ValueError('Unsupported or invalid C# input: ' + report['reason'])
        translation = prepare_translation(ir, contract_ir)
        report['translation_justification'] = translation['justification']
        report['goal'] = translation['goal']
        report['definitions'] = translation['definitions']
        report['input_bounds'] = {'int': [LOW, HIGH], 'bool': [False, True]}
        report['analysis_metadata'] = [ir.get('analysis_metadata'), contract_ir.get('analysis_metadata')]
        report['roslyn_versions'] = [ir.get('roslyn_version'), contract_ir.get('roslyn_version')]
        save_json('traduction-mathematique', translation)
        import os
        lean_env = dict(os.environ)
        for name in ('LEAN_PATH', 'LEAN_SRC_PATH', 'LEAN_SYSROOT'):
            lean_env.pop(name, None)
        report['toolchain_verification'] = validate_toolchain(args.toolchain_manifest, args.lean)
        version = subprocess.run([str(args.lean), '--version'], capture_output=True, text=True,
                                 encoding='utf-8', timeout=args.timeout, env=lean_env)
        if version.returncode:
            raise RuntimeError('Lean version check failed')
        report['lean_version'] = version.stdout.strip()
        previous = []
        attempted_proposals = set()
        def check_components():
            for path, fingerprint in report['components_sha256'].items():
                if sha(Path(path).read_bytes()) != fingerprint:
                    raise ValueError('A trusted component changed during verification')
        report['reason'] = 'PROOF_SEARCH_EXHAUSTED'
        for attempt_index in range(1, args.max_attempts + 1):
            attempt = {'attempt': attempt_index, 'accepted': False}
            report['attempts'].append(attempt)
            request = model_request(translation, args.model, previous)
            attempt['request'] = str(save_json('requete-ia-mathematique', request))
            def save_raw(data):
                attempt['raw_response'] = str(save('reponse-ia-brute', 'txt', data))
            def save_preflight(data):
                attempt['preflight_response'] = str(save('inventaire-ia-preflight-brut', 'txt', data))
            response = call_model(args.endpoint, request, args.timeout, raw_callback=save_raw,
                                  preflight_callback=save_preflight)
            attempt['response'] = str(save_json('reponse-ia-locale', response))
            attempt['model_instance_id'] = response['model_instance_id']
            attempt['native_stats'] = response['stats']
            try:
                content = validate_native_response(response, args.model)
                proposal = validate_proposal(strict_json(content), translation['parameters'])
            except (ValueError, KeyError, TypeError) as error:
                attempt['reason'] = 'INVALID_MODEL_PROPOSAL'
                attempt['detail'] = str(error)
                previous.append({'reason': attempt['reason']})
                continue
            attempt['proposal'] = proposal
            proposal_key = json.dumps(proposal, sort_keys=True, separators=(',', ':'))
            if proposal_key in attempted_proposals:
                attempt['reason'] = 'REPEATED_PROPOSAL'
                previous.append({'proposal': proposal, 'reason': attempt['reason']})
                continue
            attempted_proposals.add(proposal_key)
            certificate = certificate_text(translation, proposal)
            proof_path = save('certificat-lean-propose', 'lean', certificate)
            attempt['certificate'] = str(proof_path)
            try:
                check_components()
                process = subprocess.run([str(args.lean), str(proof_path)], capture_output=True,
                                         text=True, encoding='utf-8', timeout=args.timeout,
                                         cwd=args.output_dir, env=lean_env)
                output = {'exit_code': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}
                verification = check_certificate_output(process.returncode, process.stdout, process.stderr)
            except subprocess.TimeoutExpired as error:
                output = {'exit_code': None, 'timeout': True,
                          'stdout': str(error.stdout or ''), 'stderr': str(error.stderr or '')}
                verification = {'accepted': False, 'axioms': [], 'reason': 'LEAN_TIMEOUT'}
            attempt['lean_output'] = str(save_json('sortie-verificateur-lean', output))
            attempt.update(verification)
            if verification['accepted']:
                report['toolchain_verification_after'] = validate_toolchain(args.toolchain_manifest, args.lean)
                report['roslyn_runtime_verification_after'] = validate_roslyn_runtime(args.roslyn_manifest, args.pwsh)
                check_components()
                if source_snapshot.read_bytes() != source_bytes or contract_snapshot.read_bytes() != contract_bytes:
                    raise ValueError('Source snapshot changed during verification')
                if proof_path.read_bytes() != certificate.encode('utf-8'):
                    raise ValueError('Certificate changed during verification')
                report['verdict'] = 'PROUVÉ' if proposal['kind'] == 'proof' else 'RÉFUTÉ'
                report['reason'] = verification['reason']
                report['certificate'] = str(proof_path)
                report['approved_axioms_used'] = verification['axioms']
                if proposal['kind'] == 'counterexample':
                    report['counterexample'] = proposal['values']
                exit_code = 0
                break
            diagnostic = sanitize_diagnostic(output.get('stdout', '') + '\n' + output.get('stderr', ''),
                                             (proof_path, args.output_dir, args.lean.parent.parent))
            previous.append({'proposal': proposal, 'reason': verification['reason'],
                             'mathematical_diagnostic': diagnostic[:4000]})
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, RecursionError,
            subprocess.SubprocessError, urllib.error.URLError) as error:
        if isinstance(error, ToolchainError):
            report['reason'] = 'UNAPPROVED_TOOLCHAIN'
        elif isinstance(error, TranslatorError):
            report['reason'] = 'UNAPPROVED_TRANSLATOR'
        elif report['reason'] in ('NOT_COMPLETED', 'PROOF_SEARCH_EXHAUSTED'):
            report['reason'] = type(error).__name__.upper()
        report['error'] = str(error)
        report['verdict'] = 'INCONCLUSIF'
    report['evidence'] = list(evidence)
    report_path = save_json('rapport-verdict-csharplean', report)
    human_path = save('rapport-lisible-csharplean', 'md', human_report(report, report_path))
    manifest = save_json('manifeste-execution-csharplean', {'evidence': list(evidence)})
    print(json.dumps({'verdict': report['verdict'], 'reason': report['reason'],
                      'report': str(report_path), 'human_report': str(human_path),
                      'manifest': str(manifest)}, ensure_ascii=False))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
