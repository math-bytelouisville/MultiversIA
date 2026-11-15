"""Typed mathematical properties and bounded, name-independent synthesis.

No eval, exec, subprocess or network. CANDIDATE means finite checks, not proof.
"""
from __future__ import annotations
import ast
import itertools
import keyword
import math
import re
import time
from copy import deepcopy

LOW, HIGH = -(2**31), 2**31 - 1
TYPES = {'int', 'bool'}
WRAP_BINARY = {'add32', 'sub32', 'mul32'}
PUBLIC = {'schema_version', 'name', 'parameters', 'return_type', 'precondition', 'postcondition'}
DEFAULT_LIMITS = {'max_candidates':10000, 'max_depth':3, 'max_samples':256, 'max_seconds':5.0}
LIMITATIONS = [
    'Synthèse symbolique déterministe bornée, sans modèle de langage.',
    'CANDIDATE signifie compatible avec les cas finis enregistrés ; aucune preuve universelle Int32.',
    'L’absence de candidat ne démontre pas une impossibilité mathématique.',
    'Grammaire pure : Int32/bool, comparaisons, conditions et add32/sub32/mul32/neg32 modulo explicites ; aucun effet ni arithmétique générale.',
    'Précondition et caractère discriminant contrôlés sur un domaine fini ; pertinence métier non établie.'
]


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('INVALID_FIELDS')


def _name(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,31}', value) is not None and not keyword.iskeyword(value)


def _symbols(symbols):
    if type(symbols) is not dict or len(symbols) > 4:
        raise ValueError('INVALID_SYMBOLS')
    if any(not _name(n) or type(t) is not str or t not in TYPES for n,t in symbols.items()):
        raise ValueError('INVALID_SYMBOLS')


def validate_node(node, symbols):
    """Validate exact IR keys/types and scope; return its result type."""
    _symbols(symbols)
    count = 0
    def visit(n, depth=0):
        nonlocal count
        count += 1
        if depth > 32 or count > 512 or type(n) is not dict or type(n.get('type')) is not str or n.get('type') not in TYPES:
            raise ValueError('IR_SIZE_OR_TYPE')
        kind, typ = n.get('kind'), n['type']
        if kind == 'constant':
            _keys(n, ('kind','type','value'))
            if (typ=='bool' and type(n['value']) is not bool) or (typ=='int' and (type(n['value']) is not int or not LOW<=n['value']<=HIGH)):
                raise ValueError('INVALID_CONSTANT')
        elif kind == 'param':
            _keys(n, ('kind','type','name'))
            if type(n['name']) is not str or symbols.get(n['name']) != typ:
                raise ValueError('UNKNOWN_PARAMETER')
        elif kind == 'neg32':
            _keys(n, ('kind','type','operand'))
            if typ!='int' or visit(n['operand'],depth+1)!='int':
                raise ValueError('INVALID_NEG32_TYPE')
        elif kind == 'not':
            _keys(n, ('kind','type','operand'))
            if typ!='bool' or visit(n['operand'],depth+1)!='bool':
                raise ValueError('INVALID_NOT_TYPE')
        elif kind == 'binary':
            _keys(n, ('kind','type','op','left','right'))
            left,right=visit(n['left'],depth+1),visit(n['right'],depth+1)
            op=n['op']
            if type(op) is not str or left!=right or not (
                typ=='int' and left=='int' and op in WRAP_BINARY or
                typ=='bool' and (op in ('eq','ne') or op in ('lt','le','gt','ge') and left=='int' or op in ('and','or') and left=='bool')):
                raise ValueError('INVALID_BINARY_TYPE')
        elif kind == 'ite':
            _keys(n, ('kind','type','condition','when_true','when_false'))
            if visit(n['condition'],depth+1)!='bool' or visit(n['when_true'],depth+1)!=typ or visit(n['when_false'],depth+1)!=typ:
                raise ValueError('INVALID_CONDITIONAL_TYPE')
        else:
            raise ValueError('UNSUPPORTED_IR')
        return typ
    return visit(node)


def _constant(value):
    return {'kind':'constant','type':'bool' if type(value) is bool else 'int','value':value}


def _param(name, typ):
    return {'kind':'param','type':typ,'name':name}


def _binary(op, left, right):
    return {'kind':'binary','type':'bool','op':op,'left':left,'right':right}


def parse_expression(text, symbols):
    _symbols(symbols)
    if type(text) is not str or not text.strip() or len(text)>4096:
        raise ValueError('EXPRESSION_SIZE')
    normalized=text.strip().replace('≥','>=').replace('≤','<=').replace('∧',' and ').replace('∨',' or ').replace('¬',' not ').strip()
    try:
        tree=ast.parse(normalized,mode='eval')
    except (SyntaxError,RecursionError,ValueError) as exc:
        raise ValueError('INVALID_MATH_SYNTAX') from exc
    if sum(1 for _ in ast.walk(tree))>512:
        raise ValueError('EXPRESSION_SIZE')
    comparisons={ast.Eq:'eq',ast.NotEq:'ne',ast.Lt:'lt',ast.LtE:'le',ast.Gt:'gt',ast.GtE:'ge'}
    def convert(n, depth=0):
        if depth>24:raise ValueError('EXPRESSION_DEPTH')
        if isinstance(n,ast.Constant) and type(n.value) in (int,bool):
            return _constant(n.value)
        if isinstance(n,ast.Name) and n.id in symbols:
            return _param(n.id,symbols[n.id])
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and not n.keywords:
            name=n.func.id
            if name=='neg32' and len(n.args)==1:
                return {'kind':'neg32','type':'int','operand':convert(n.args[0],depth+1)}
            if name in WRAP_BINARY and len(n.args)==2:
                return {'kind':'binary','type':'int','op':name,'left':convert(n.args[0],depth+1),'right':convert(n.args[1],depth+1)}
            raise ValueError('UNSUPPORTED_MATH_CALL')
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.USub) and isinstance(n.operand,ast.Constant) and type(n.operand.value) is int:
            return _constant(-n.operand.value)
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.Not):
            return {'kind':'not','type':'bool','operand':convert(n.operand,depth+1)}
        if isinstance(n,ast.BoolOp) and isinstance(n.op,(ast.And,ast.Or)):
            nodes=[convert(v,depth+1) for v in n.values]
            result=nodes[0]
            for other in nodes[1:]:result=_binary('and' if isinstance(n.op,ast.And) else 'or',result,other)
            return result
        if isinstance(n,ast.Compare):
            terms=[convert(v,depth+1) for v in [n.left,*n.comparators]]
            nodes=[]
            for op,left,right in zip(n.ops,terms,terms[1:]):
                if type(op) not in comparisons:raise ValueError('UNSUPPORTED_COMPARISON')
                nodes.append(_binary(comparisons[type(op)],left,right))
            result=nodes[0]
            for other in nodes[1:]:result=_binary('and',result,other)
            return result
        if isinstance(n,ast.IfExp):
            yes,no=convert(n.body,depth+1),convert(n.orelse,depth+1)
            return {'kind':'ite','type':yes['type'],'condition':convert(n.test,depth+1),'when_true':yes,'when_false':no}
        raise ValueError('UNSUPPORTED_MATH_EXPRESSION')
    result=convert(tree.body)
    validate_node(result,symbols)
    return result


def _contains(node, name):
    return node.get('kind')=='param' and node.get('name')==name or any(_contains(v,name) for v in node.values() if type(v) is dict)


def validate_spec(spec):
    if type(spec) is not dict or not PUBLIC <= set(spec) or set(spec)-PUBLIC-{'pre_ast','post_ast'}:
        raise ValueError('INVALID_SPEC_FIELDS')
    if spec['schema_version']!='mia-math-spec-1':raise ValueError('INVALID_SCHEMA')
    if type(spec['name']) is not str or not 1<=len(spec['name'].strip())<=80 or any(ord(c)<32 for c in spec['name']):
        raise ValueError('INVALID_SPEC_NAME')
    if type(spec['parameters']) is not list or not 1<=len(spec['parameters'])<=3:
        raise ValueError('PARAMETER_COUNT')
    symbols={}
    for p in spec['parameters']:
        _keys(p,('name','type'))
        if not _name(p['name']) or p['name']=='result' or p['name'] in symbols or type(p['type']) is not str or p['type'] not in TYPES:
            raise ValueError('INVALID_PARAMETER')
        symbols[p['name']]=p['type']
    if type(spec['return_type']) is not str or spec['return_type'] not in TYPES:raise ValueError('INVALID_RETURN_TYPE')
    pre=parse_expression(spec['precondition'],symbols)
    post=parse_expression(spec['postcondition'],dict(symbols,result=spec['return_type']))
    if pre['type']!='bool' or post['type']!='bool':raise ValueError('CONDITIONS_MUST_BE_BOOL')
    if not _contains(post,'result'):raise ValueError('POSTCONDITION_MUST_REFERENCE_RESULT')
    normalized={k:deepcopy(spec[k]) for k in sorted(PUBLIC)}
    normalized['pre_ast'],normalized['post_ast']=pre,post
    return normalized


def _wrap32(value):
    return ((value + 2**31) % 2**32) - 2**31


def _eval(node, env):
    kind=node['kind']
    if kind=='constant':return node['value']
    if kind=='param':return env[node['name']]
    if kind=='neg32':return _wrap32(-_eval(node['operand'],env))
    if kind=='not':return not _eval(node['operand'],env)
    if kind=='ite':return _eval(node['when_true'] if _eval(node['condition'],env) else node['when_false'],env)
    op=node['op'];left=_eval(node['left'],env)
    if op=='and':return left and _eval(node['right'],env)
    if op=='or':return left or _eval(node['right'],env)
    right=_eval(node['right'],env)
    if op=='add32':return _wrap32(left+right)
    if op=='sub32':return _wrap32(left-right)
    if op=='mul32':return _wrap32(left*right)
    if op=='eq':return left==right
    if op=='ne':return left!=right
    if op=='lt':return left<right
    if op=='le':return left<=right
    if op=='gt':return left>right
    if op=='ge':return left>=right
    raise ValueError('UNSUPPORTED_IR')


def evaluate(node, env):
    if type(env) is not dict:raise ValueError('INVALID_ENVIRONMENT')
    symbols={}
    for name,value in env.items():
        if type(value) is bool:symbols[name]='bool'
        elif type(value) is int and LOW<=value<=HIGH:symbols[name]='int'
        else:raise ValueError('ENVIRONMENT_TYPE_OR_RANGE')
    validate_node(node,symbols)
    return _eval(node,env)


def _integers(node):
    out=[]
    def visit(n):
        if n['kind']=='constant' and n['type']=='int' and n['value'] not in out:out.append(n['value'])
        for value in n.values():
            if type(value) is dict:visit(value)
    visit(node)
    return out


class _Budget(Exception):pass


def solve(spec, limits=None):
    started=time.monotonic()
    result={'status':'REFUSED','reason':'NOT_STARTED','spec':None,'candidate':None,'stats':{},'evidence':{},'limitations':list(LIMITATIONS)}
    try:
        spec=validate_spec(spec);result['spec']=spec
        if limits is not None and (type(limits) is not dict or set(limits)-set(DEFAULT_LIMITS)):
            raise ValueError('INVALID_LIMIT_FIELDS')
        bound=dict(DEFAULT_LIMITS,**(limits or {}))
        for name,low,high in [('max_candidates',1,50000),('max_depth',0,4),('max_samples',8,2048)]:
            if type(bound[name]) is not int or not low<=bound[name]<=high:raise ValueError('INVALID_LIMIT_'+name)
        if type(bound['max_seconds']) not in (int,float) or not math.isfinite(bound['max_seconds']) or not 0.01<=bound['max_seconds']<=20:
            raise ValueError('INVALID_LIMIT_max_seconds')
    except (ValueError,TypeError,RecursionError) as exc:
        result['reason']=str(exc);result['stats']['elapsed_seconds']=time.monotonic()-started
        return result
    stats=result['stats'];stats.update(limits=bound,candidates_considered=0,search_states=0,expression_evaluations=0)
    evidence=result['evidence'];evidence.update(samples=[],terminals=[],conditions=[],search_trace=[])
    deadline=started+bound['max_seconds']
    def guard(candidate=False):
        if candidate:
            if stats['candidates_considered']>=bound['max_candidates']:raise _Budget('MAX_CANDIDATES')
            stats['candidates_considered']+=1
        if time.monotonic()>=deadline:raise _Budget('MAX_SECONDS')
        if stats['expression_evaluations']>=2000000:raise _Budget('MAX_EVALUATIONS')
    def ev(node,env):
        guard();stats['expression_evaluations']+=1
        return _eval(node,env)
    try:
        integers=list(dict.fromkeys(_integers(spec['pre_ast'])+_integers(spec['post_ast'])))
        if len(integers)>16:
            result.update(status='REFUSED',reason='TOO_MANY_INTEGER_CONSTANTS');return result
        values=sorted(set([LOW,-1,0,1,HIGH]+[v+d for v in integers for d in (-1,0,1) if LOW<=v+d<=HIGH]))
        domains=[values if p['type']=='int' else [False,True] for p in spec['parameters']]
        size=math.prod(map(len,domains));count=min(size,bound['max_samples'])
        indices=list(range(size)) if size<=count else sorted({round(i*(size-1)/(count-1)) for i in range(count)})
        active=[]
        for index in indices:
            guard();digits=[];position=index
            for domain in reversed(domains):position,digit=divmod(position,len(domain));digits.append(domain[digit])
            env={p['name']:v for p,v in zip(spec['parameters'],reversed(digits))};pre=ev(spec['pre_ast'],env)
            row={'sample_id':len(evidence['samples']),'inputs':env,'precondition':pre};evidence['samples'].append(row)
            if pre:active.append(row)
        stats.update(samples=len(indices),precondition_witnesses=len(active),finite_domain_size=size)
        evidence['sampling']={'integer_values':values,'policy':'Cartesian critical values; evenly spaced deterministic indices if truncated','indices':indices,'exhaustive_over_selected_values':count==size}
        if not active:
            result.update(status='INCONCLUSIVE',reason='PRECONDITION_NO_WITNESS_IN_FINITE_SAMPLE');return result
        symbols={p['name']:p['type'] for p in spec['parameters']}
        params=[_param(p['name'],p['type']) for p in spec['parameters']]
        constants=list(dict.fromkeys(integers+[-1,0,1,LOW,HIGH]))
        terms=[p for p in params if p['type']==spec['return_type']]+[_constant(v) for v in (constants if spec['return_type']=='int' else [False,True])]
        # The result must reject at least one sampled mutant value; no claim of
        # sufficient business specification follows from this necessary check.
        rejected=0
        for row in active:
            for term in terms:
                env=dict(row['inputs'],result=ev(term,row['inputs']))
                rejected+=not ev(spec['post_ast'],env)
        evidence['discrimination']={'sampled_mutants_rejected':rejected,'scope':'finite necessary check only'}
        if not rejected:
            result.update(status='INCONCLUSIVE',reason='POSTCONDITION_NONDISCRIMINATING_ON_SAMPLE');return result
        def check_candidate(candidate):
            checks=[]
            evidence['candidate_validation']=checks
            for row in active:
                outcome=ev(candidate,row['inputs']);ok=ev(spec['post_ast'],dict(row['inputs'],result=outcome))
                checks.append({'sample_id':row['sample_id'],'result':outcome,'postcondition':ok})
                if not ok:return False
            return True
        post=spec['post_ast'];direct=None
        if post['kind']=='binary' and post['op']=='eq':
            for a,b in ((post['left'],post['right']),(post['right'],post['left'])):
                if a['kind']=='param' and a['name']=='result' and not _contains(b,'result'):
                    validate_node(b,symbols);direct=b
        if direct is not None:
            guard(candidate=True)
            if check_candidate(direct):
                result.update(status='CANDIDATE',reason='CONSTRUCTIVE_EQUALITY_FINITE_CHECKS',candidate=deepcopy(direct));stats['method']='constructive_equality';return result
        conditions=[p for p in params if p['type']=='bool']
        intterms=[p for p in params if p['type']=='int']+[_constant(v) for v in constants]
        for a,b in itertools.combinations(intterms,2):
            if a['kind']=='constant' and b['kind']=='constant':continue
            for op in ('lt','le','eq'):conditions.append(_binary(op,a,b))
        if spec['return_type']=='bool':
            terms += [{'kind':'not','type':'bool','operand':p} for p in params if p['type']=='bool'] + conditions
        allmask=(1<<len(active))-1;leaf_masks=[]
        for term in terms:
            guard(candidate=True);mask=0;counterexample=None
            for j,row in enumerate(active):
                outcome=ev(term,row['inputs']);ok=ev(post,dict(row['inputs'],result=outcome))
                if ok:mask|=1<<j
                elif counterexample is None:counterexample={'sample_id':row['sample_id'],'result':outcome}
            leaf_masks.append(mask)
            evidence['terminals'].append({'candidate':term,'satisfying_active_mask_hex':hex(mask),'first_counterexample':counterexample})
        covered=0
        for mask in leaf_masks:covered|=mask
        if covered!=allmask:
            evidence['no_allowed_sample_ids']=[row['sample_id'] for j,row in enumerate(active) if not covered&(1<<j)]
            evidence['no_allowed_output_scope']='No terminal of the finite grammar satisfies these sampled inputs; contradiction or insufficient grammar, not a universal impossibility proof.'
            result.update(status='INCONCLUSIVE',reason='POSTCONDITION_NO_OUTPUT_IN_FINITE_GRAMMAR_SAMPLE');return result
        signatures=set();splits=[]
        for condition in conditions:
            mask=0
            for j,row in enumerate(active):
                if ev(condition,row['inputs']):mask|=1<<j
            if mask not in (0,allmask) and mask not in signatures and (allmask^mask) not in signatures:
                signatures.add(mask);splits.append((condition,mask))
                evidence['conditions'].append({'condition':condition,'true_active_mask_hex':hex(mask)})
        evidence['active_sample_ids']=[row['sample_id'] for row in active]
        memo={}
        def search(mask,depth):
            guard()
            key=(mask,depth)
            if key in memo:return memo[key]
            stats['search_states']+=1
            entry={'state':stats['search_states'],'mask_hex':hex(mask),'depth':depth,'outcome':'IN_PROGRESS'}
            evidence['search_trace'].append(entry)
            for index,good in enumerate(leaf_masks):
                guard(candidate=True)
                if mask & ~good == 0:
                    entry.update(outcome='TERMINAL',terminal_index=index);memo[key]=terms[index];return terms[index]
            if depth:
                for index,(condition,yesmask) in enumerate(splits):
                    yes,no=mask&yesmask,mask&~yesmask
                    if not yes or not no:continue
                    left=search(yes,depth-1)
                    if left is None:continue
                    right=search(no,depth-1)
                    if right is None:continue
                    guard(candidate=True)
                    node={'kind':'ite','type':spec['return_type'],'condition':condition,'when_true':left,'when_false':right}
                    entry.update(outcome='CONDITIONAL',condition_index=index);memo[key]=node;return node
            entry['outcome']='NO_CANDIDATE';memo[key]=None;return None
        for depth in range(bound['max_depth']+1):
            candidate=search(allmask,depth)
            if candidate is not None:
                validate_node(candidate,symbols)
                if not check_candidate(candidate):
                    raise ValueError('INTERNAL_CANDIDATE_CHECK_FAILED')
                stats.update(method='bounded_decision_tree',solution_depth=depth)
                result.update(status='CANDIDATE',reason='FINITE_PROPERTY_CANDIDATE_FOUND',candidate=deepcopy(candidate));return result
        result.update(status='INCONCLUSIVE',reason='NO_CANDIDATE_IN_BOUNDED_GRAMMAR')
    except _Budget as exc:
        result.update(status='INCONCLUSIVE',reason=str(exc),candidate=None)
    except (ValueError,TypeError,RecursionError) as exc:
        result.update(status='REFUSED',reason=str(exc),candidate=None)
    finally:
        stats['elapsed_seconds']=time.monotonic()-started
    return result
