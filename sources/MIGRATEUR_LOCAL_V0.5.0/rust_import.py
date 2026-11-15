"""Deterministic, non-executing import of an explicit pure Rust subset.

This is a lexer/parser, not a known-source hash lookup. Unsupported source is
preserved within archival bounds and refused as a whole. No input is executed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

import module_model

BASE = Path(__file__).resolve().parent
MAX_SOURCE_BYTES = 524288
MAX_ARCHIVE_BYTES = 2097152
MAX_TOKENS = 12000
MAX_SEGMENTS = 24000
MAX_DEPTH = 32
MAX_COMMENT_DEPTH = 16
KEYWORDS = set(('as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod '
                'move mut pub ref return self Self static struct super trait true type unsafe use where while abstract '
                'become box do final macro override priv typeof unsized virtual yield try union gen').split())
OPERATORS = {'||': (1, 'or'), '&&': (2, 'and'), '==': (3, 'eq'), '!=': (3, 'ne'),
             '<': (3, 'lt'), '<=': (3, 'le'), '>': (3, 'gt'), '>=': (3, 'ge')}
LIMITATIONS = [
    'Sous-ensemble Rust pur explicite, pas Rust général ni migration de site complet.',
    'Aucun code source compilé ou exécuté par cet importeur ; IMPORTED signifie parsé et typé.',
    'Les sources, commentaires et positions conservés ne constituent pas une preuve universelle de fidélité.',
    'Int32/bool et appels internes sans cycle ; arithmétique seulement wrapping_add/sub/mul/neg explicite.',
    'Arithmétique brute, division, conversions, état, effets, modules et macros hors profil.',
    'Les limites du modèle commun et de ses contrats restent applicables après expansion des appels.',
]


class Refused(ValueError):
    def __init__(self, code, token=None):
        self.code = code
        self.position = None if token is None else {k: token[k] for k in ('start', 'byte_start', 'line', 'column')}
        super().__init__(code + ('' if token is None else f" at {token['line']}:{token['column']}"))


def _span(first, last):
    return {k: first[k] for k in ('start', 'byte_start', 'line', 'column')} | {
        k: last[k] for k in ('end', 'byte_end', 'end_line', 'end_column')}


class Lexer:
    def __init__(self, source, inventory):
        self.source, self.inventory = source, inventory
        self.at = self.byte = 0
        self.line = self.column = 1

    def segment(self, end, kind):
        start, byte, line, column = self.at, self.byte, self.line, self.column
        text = self.source[start:end]
        self.byte += len(text.encode('utf-8'))
        for char in text:
            if char == '\n':
                self.line, self.column = self.line + 1, 1
            else:
                self.column += 1
        self.at = end
        item = dict(kind=kind, text=text, start=start, end=end, byte_start=byte,
                    byte_end=self.byte, line=line, column=column,
                    end_line=self.line, end_column=self.column)
        self.inventory['segments'].append(item)
        self.inventory['lexed_chars'] = end
        if kind not in ('whitespace', 'comment'):
            self.inventory['tokens'].append(item)
        if len(self.inventory['segments']) > MAX_SEGMENTS or len(self.inventory['tokens']) > MAX_TOKENS:
            raise Refused('TOKEN_OR_SEGMENT_BOUND', item)
        return item

    def scan(self):
        source, length = self.source, len(self.source)
        while self.at < length:
            start = self.at
            char = source[start]
            if char in ' \t\r\n':
                end = start + 1
                while end < length and source[end] in ' \t\r\n': end += 1
                self.segment(end, 'whitespace')
            elif source.startswith('//', start):
                end = source.find('\n', start)
                self.segment(length if end == -1 else end, 'comment')
            elif source.startswith('/*', start):
                end, depth, excessive = start + 2, 1, False
                while end < length and depth:
                    if source.startswith('/*', end):
                        depth += 1; excessive |= depth > MAX_COMMENT_DEPTH; end += 2
                    elif source.startswith('*/', end):
                        depth -= 1; end += 2
                    else:
                        end += 1
                item = self.segment(end, 'comment')
                if depth: raise Refused('UNTERMINATED_COMMENT', item)
                if excessive: raise Refused('COMMENT_DEPTH_BOUND', item)
            elif char.isascii() and (char.isalpha() or char == '_'):
                end = start + 1
                while end < length and source[end].isascii() and (source[end].isalnum() or source[end] == '_'): end += 1
                word = source[start:end]
                self.segment(end, 'keyword' if word in KEYWORDS else 'identifier')
            elif char in '0123456789':
                end = start + 1
                while end < length and source[end] in '0123456789': end += 1
                self.segment(end, 'integer')
            elif source[start:start + 2] in ('->', '==', '!=', '<=', '>=', '&&', '||'):
                self.segment(start + 2, 'symbol')
            else:
                self.segment(start + 1, 'symbol' if char in '(){}:,;<>!-.' else 'unsupported')
        self.inventory['lexing_complete'] = True
        eof = dict(kind='eof', text='', start=length, end=length, byte_start=self.byte,
                   byte_end=self.byte, line=self.line, column=self.column,
                   end_line=self.line, end_column=self.column)
        return self.inventory['tokens'] + [eof]


class Parser:
    def __init__(self, tokens, inventory):
        self.tokens, self.inventory, self.index = tokens, inventory, 0
        self.functions = []

    @property
    def token(self): return self.tokens[self.index]

    def take(self, value=None):
        token = self.token
        if value is not None and token['text'] != value:
            raise Refused('EXPECTED_' + value, token)
        if token['kind'] == 'eof': raise Refused('UNEXPECTED_EOF', token)
        self.index += 1
        return token

    def optional(self, value):
        if self.token['text'] == value:
            return self.take()
        return None

    def name(self):
        token = self.token
        if token['kind'] != 'identifier' or token['text'] == '_' or len(token['text']) > 80:
            raise Refused('IDENTIFIER_PROFILE_ASCII_1_TO_80', token)
        return self.take()

    def typ(self):
        token = self.take()
        if token['text'] not in ('i32', 'bool'): raise Refused('TYPE_PROFILE_I32_BOOL', token)
        return 'int' if token['text'] == 'i32' else 'bool'

    def guard(self, depth):
        if depth > MAX_DEPTH: raise Refused('PARSE_DEPTH_BOUND', self.token)

    def block(self, depth=0):
        self.guard(depth)
        opening = self.take('{')
        returned = self.optional('return')
        value = self.expression(depth=depth + 1)
        if returned:
            self.optional(';')
            value = {'tag': 'return', 'value': value, 'token': returned}
        closing = self.take('}')  # Ordinary expr; would return (), not i32/bool.
        return value, _span(opening, closing)

    def expression(self, minimum=0, depth=0):
        self.guard(depth)
        first = self.take()
        value, text = None, first['text']
        if text == 'if':
            condition = self.expression(depth=depth + 1)
            yes, _ = self.block(depth + 1)
            self.take('else')
            if self.token['text'] == 'if': no = self.expression(minimum=4, depth=depth + 1)
            else: no, _ = self.block(depth + 1)
            value = dict(tag='if', condition=condition, yes=yes, no=no, token=first)
        elif text == '!':
            value = dict(tag='not', operand=self.expression(4, depth + 1), token=first)
        elif text == '(':
            value = self.expression(depth=depth + 1)
            self.take(')')
            value = dict(value, parenthesized=True)
        elif text in ('true', 'false'):
            value = dict(tag='constant', value=text == 'true', token=first)
        elif text == '-' or first['kind'] == 'integer':
            number = self.take() if text == '-' else first
            if number['kind'] != 'integer' or len(number['text']) > 11:
                raise Refused('DECIMAL_INT32_LITERAL_REQUIRED', number)
            integer = int(number['text']) * (-1 if text == '-' else 1)
            if not -(2 ** 31) <= integer <= 2 ** 31 - 1: raise Refused('INT32_LITERAL_RANGE', first)
            explicit_i32 = self.token['text'] == 'i32' and number['end'] == self.token['start']
            if explicit_i32: self.take('i32')
            value = dict(tag='constant', value=integer, token=first, explicit_i32=explicit_i32,
                         negative_literal=text == '-')
        elif first['kind'] == 'identifier' and text != '_' and len(text) <= 80:
            if self.optional('('):
                arguments = []
                while self.token['text'] != ')':
                    if len(arguments) >= 3: raise Refused('CALL_ARGUMENT_BOUND', self.token)
                    arguments.append(self.expression(depth=depth + 1))
                    if not self.optional(','): break
                self.take(')')
                value = dict(tag='call', name=text, arguments=arguments, token=first)
            else: value = dict(tag='name', name=text, token=first)
        else:
            raise Refused('UNSUPPORTED_EXPRESSION', first)
        while self.optional('.'):
            method = self.name()
            if method['text'] not in ('wrapping_add', 'wrapping_sub', 'wrapping_mul', 'wrapping_neg'):
                raise Refused('ONLY_EXPLICIT_I32_WRAPPING_METHODS', method)
            # Rust binds a method before unary minus. Require grouping to avoid
            # silently changing -1i32.wrapping_add(2) from -3 into +1.
            if value['tag'] == 'constant' and value.get('negative_literal') and not value.get('parenthesized'):
                raise Refused('NEGATIVE_METHOD_RECEIVER_REQUIRES_PARENTHESES', method)
            if value['tag'] == 'constant' and type(value['value']) is int and not value.get('explicit_i32'):
                raise Refused('LITERAL_METHOD_RECEIVER_REQUIRES_I32_SUFFIX', method)
            if value['tag'] == 'if' and not value.get('parenthesized'):
                raise Refused('CONDITIONAL_METHOD_RECEIVER_REQUIRES_PARENTHESES', method)
            self.take('(')
            argument = None
            if method['text'] != 'wrapping_neg':
                argument = self.expression(depth=depth + 1)
                self.optional(',')
            self.take(')')
            value = dict(tag='wrapping', op=method['text'], receiver=value, argument=argument, token=method)
        comparison = False
        while self.token['text'] in OPERATORS and OPERATORS[self.token['text']][0] >= minimum:
            operator = self.take(); precedence, op = OPERATORS[operator['text']]
            if precedence == 3 and comparison: raise Refused('CHAINED_COMPARISON_REQUIRES_PARENTHESES', operator)
            comparison |= precedence == 3
            right = self.expression(precedence + 1, depth + 1)
            value = dict(tag='binary', op=op, left=value, right=right, token=operator)
        return value

    def parse(self):
        names = set()
        while self.token['kind'] != 'eof':
            if len(self.functions) >= 8: raise Refused('FUNCTION_COUNT_BOUND', self.token)
            first = self.token
            public = self.optional('pub') is not None
            self.take('fn'); name = self.name()
            if name['text'] in names: raise Refused('DUPLICATE_FUNCTION', name)
            names.add(name['text'])
            self.take('('); parameters, parameter_names = [], set()
            while self.token['text'] != ')':
                if len(parameters) >= 3: raise Refused('PARAMETER_COUNT_BOUND', self.token)
                pname = self.name()
                if pname['text'] in parameter_names: raise Refused('DUPLICATE_PARAMETER', pname)
                parameter_names.add(pname['text']); self.take(':'); typ = self.typ()
                parameters.append(dict(original=pname['text'], canonical='p' + str(len(parameters)),
                                       type=typ, span=_span(pname, self.tokens[self.index - 1])))
                if not self.optional(','): break
            self.take(')')
            if not parameters: raise Refused('ONE_TO_THREE_PARAMETERS_REQUIRED', name)
            self.take('->'); return_type = self.typ()
            record = dict(unit_id='u' + str(len(self.functions)), name=name['text'], parameters=parameters,
                          return_type=return_type, pub=public, declaration_complete=False)
            self.inventory['functions'].append(record)
            body, body_span = self.block()
            record.update(span=_span(first, self.tokens[self.index - 1]), body_span=body_span, declaration_complete=True)
            self.functions.append(dict(record=record, body=body))
        if not self.functions: raise Refused('ONE_TO_EIGHT_FUNCTIONS_REQUIRED', self.token)
        self.inventory['parsing_complete'] = True
        return self.functions


def _typed(functions):
    signatures = {f['record']['name']: f['record'] for f in functions}
    units = []
    for function in functions:
        record = function['record']
        parameters = {p['original']: p for p in record['parameters']}
        count = 0
        def concrete_receiver(node):
            # Rust method lookup on an otherwise unconstrained integer literal
            # is ambiguous even when the function's return type is i32.
            if node['tag'] == 'constant': return type(node['value']) is bool or node.get('explicit_i32', False)
            if node['tag'] == 'if': return concrete_receiver(node['yes']) or concrete_receiver(node['no'])
            return node['tag'] in ('name', 'call', 'wrapping')
        def visit(node, tail=False, depth=0):
            nonlocal count
            count += 1
            if count > 512 or depth > 32: raise Refused('TYPED_IR_BOUND', node['token'])
            kind = node['tag']
            if kind == 'return':
                if not tail: raise Refused('RETURN_OUTSIDE_TAIL_PROFILE', node['token'])
                return visit(node['value'], True, depth + 1)
            if kind == 'constant': return {'kind': 'constant', 'type': 'bool' if type(node['value']) is bool else 'int', 'value': node['value']}
            if kind == 'name':
                if node['name'] not in parameters: raise Refused('UNBOUND_PARAMETER', node['token'])
                p = parameters[node['name']]
                return {'kind': 'param', 'type': p['type'], 'name': p['canonical']}
            if kind == 'call':
                if node['name'] in parameters: raise Refused('CALL_NAME_SHADOWED_BY_PARAMETER', node['token'])
                if node['name'] not in signatures: raise Refused('UNRESOLVED_CALL', node['token'])
                callee = signatures[node['name']]
                return {'kind': 'call', 'type': callee['return_type'], 'target': callee['unit_id'],
                        'arguments': [visit(a, False, depth + 1) for a in node['arguments']]}
            if kind == 'not': return {'kind': 'not', 'type': 'bool', 'operand': visit(node['operand'], False, depth + 1)}
            if kind == 'wrapping':
                receiver = visit(node['receiver'], False, depth + 1)
                if receiver['type'] != 'int': raise Refused('WRAPPING_RECEIVER_REQUIRES_I32', node['token'])
                if not concrete_receiver(node['receiver']): raise Refused('AMBIGUOUS_LITERAL_WRAPPING_RECEIVER', node['token'])
                if node['op'] == 'wrapping_neg':
                    return {'kind': 'neg32', 'type': 'int', 'operand': receiver}
                argument = visit(node['argument'], False, depth + 1)
                if argument['type'] != 'int': raise Refused('WRAPPING_ARGUMENT_REQUIRES_I32', node['token'])
                return {'kind': 'binary', 'type': 'int', 'op': {'wrapping_add': 'add32', 'wrapping_sub': 'sub32', 'wrapping_mul': 'mul32'}[node['op']],
                        'left': receiver, 'right': argument}
            if kind == 'binary': return {'kind': 'binary', 'type': 'bool', 'op': node['op'],
                                         'left': visit(node['left'], False, depth + 1), 'right': visit(node['right'], False, depth + 1)}
            condition, yes, no = visit(node['condition'], False, depth + 1), visit(node['yes'], tail, depth + 1), visit(node['no'], tail, depth + 1)
            return {'kind': 'ite', 'type': yes['type'], 'condition': condition, 'when_true': yes, 'when_false': no}
        body = visit(function['body'], True)
        units.append({'id': record['unit_id'], 'name': record['name'], 'qualified_name': record['name'],
                      'parameters': [{'name': p['canonical'], 'type': p['type']} for p in record['parameters']],
                      'return_type': record['return_type'], 'body': body, 'contract': None})
    return units


def _safe_output(value):
    path = Path(value).absolute()
    if not path.resolve().is_relative_to(BASE): raise ValueError('OUTPUT_OUTSIDE_V04')
    for parent in [path, *path.parents]:
        if parent == BASE.parent: break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('OUTPUT_LINK_REFUSED')
    return path.resolve()


def import_rust(source, output_dir=None):
    """Return a deterministic typed receipt; optional archival has its own UUID."""
    inventory = {'source': {}, 'tokens': [], 'segments': [], 'functions': [], 'identifiers': [],
                 'lexed_chars': 0, 'lexing_complete': False, 'parsing_complete': False}
    result = {'status': 'REFUSED', 'reason': 'NOT_STARTED', 'module': None, 'inventory': inventory,
              'coverage': {'source_preserved': False, 'all_tokens_consumed': False, 'represented': [],
                           'unrepresented': ['Entire input until admission'], 'site_migrated': False,
                           'semantic_equivalence': 'NOT_TESTED'},
              'evidence_dir': None, 'input_execution': False, 'limitations': list(LIMITATIONS)}
    folder = None
    try:
        if type(source) not in (str, bytes): raise Refused('SOURCE_MUST_BE_UTF8_TEXT_OR_BYTES_NOT_PATH')
        if len(source) > MAX_ARCHIVE_BYTES: raise Refused('ARCHIVE_BOUND_INPUT_NOT_COPIED')
        raw = source.encode('utf-8') if type(source) is str else source
        if len(raw) > MAX_ARCHIVE_BYTES: raise Refused('ARCHIVE_BOUND_INPUT_NOT_COPIED')
        digest = hashlib.sha256(raw).hexdigest()
        inventory['source'] = {'byte_count': len(raw), 'sha256': digest, 'encoding': 'utf-8'}
        if output_dir is not None:
            parent = _safe_output(output_dir)
            parent.mkdir(parents=True, exist_ok=True); _safe_output(parent)
            folder = parent / ('rust-import-' + uuid.uuid4().hex)
            folder.mkdir(exist_ok=False); _safe_output(folder)
            result['evidence_dir'] = str(folder)
            with (folder / 'source.rs').open('xb') as stream: stream.write(raw)
            inventory['source']['archive_path'] = str(folder / 'source.rs')
            result['coverage']['source_preserved'] = True
        text = raw.decode('utf-8', errors='strict')
        inventory['source']['text'] = text
        result['coverage']['source_preserved'] = True
        if len(raw) > MAX_SOURCE_BYTES: raise Refused('PARSER_SOURCE_BOUND_EXACT_INPUT_PRESERVED')
        tokens = Lexer(text, inventory).scan()
        inventory['identifiers'] = sorted({t['text'] for t in tokens if t['kind'] == 'identifier'})
        functions = Parser(tokens, inventory).parse()
        result['coverage']['all_tokens_consumed'] = True
        units = _typed(functions)
        module = {'schema_version': 'mia-module-1', 'semantic_profile': 'pure-int32-bool-wrap-call-1',
                  'module_id': 'rust-' + digest, 'name': 'Rust module', 'parent_revision_id': None,
                  'intent': 'Import automatique du sous-ensemble Rust pur, sans exécution.',
                  'provenance': {'source_text': text, 'source_sha256': digest, 'import_id': 'rust-' + digest,
                                 'origin': {'language': 'rust', 'profile': 'rust-pure-wrapping-functions-1'},
                                 'source_map': deepcopy(inventory['functions']), 'limitations': list(LIMITATIONS)},
                  'units': units}
        result['module'] = module_model.validate_module(module)
        result.update(status='IMPORTED', reason='ALL_SOURCE_PARSED_TYPED_NO_INPUT_EXECUTION')
        result['coverage'].update(represented=[u['id'] for u in units], unrepresented=[],
                                  functions=len(units), comments_preserved=True,
                                  parser_profile='rust-pure-wrapping-functions-1')
    except (Refused, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        result.update(status='REFUSED', reason=str(exc), module=None)
        if isinstance(exc, Refused): result['failure_position'] = exc.position
    except Exception as exc:
        result.update(status='INCONCLUSIVE', reason=type(exc).__name__ + ': ' + str(exc), module=None)
    finally:
        if folder is not None:
            try:
                with (folder / 'receipt.json').open('x', encoding='utf-8', newline='\n') as stream:
                    json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
                    stream.write('\n')
            except Exception as exc:
                result.update(status='INCONCLUSIVE', reason='EVIDENCE_WRITE_FAILED:' + type(exc).__name__ + ':' + str(exc), module=None)
    return result
