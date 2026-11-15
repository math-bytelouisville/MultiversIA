"""AST-to-Rust emitter and closed protocol. Never compiles or executes a model."""
from pathlib import Path
from fractions import Fraction
import json,re
from scoring_model import validate_model,validate_case,lower_question,threshold_fraction,Refused,need,exact_keys,display,MAX_I128,compare

def emit_rust(model):
    m=validate_model(model);w=m['penalty_weight']
    def emit(n):
        kind=n['op']
        if kind=='lit':return f"Rat::new({n['n']}i128,{n['d']}i128)?"
        if kind=='bool':return 'true' if n['value'] else 'false'
        if kind=='var':return f"Rat::new({w['n']}i128,{w['d']}i128)?" if n['name']=='w' else n['name']
        a=[emit(x) for x in n['args']]
        if kind=='if':return f"(if {a[0]} {{ {a[1]} }} else {{ {a[2]} }})"
        if kind=='not':return f"(!({a[0]}))"
        if kind in ('and','or'):return f"(({a[0]}) {'&&' if kind=='and' else '||'} ({a[1]}))"
        if kind in ('add','sub','mul','div'):return f"({a[0]}).{kind}({a[1]})?"
        expr=f"({a[0]}).cmp({a[1]})?"
        return f"({expr} "+{'lt':'== Ordering::Less','le':'!= Ordering::Greater','eq':'== Ordering::Equal','ge':'!= Ordering::Less','gt':'== Ordering::Greater'}[kind]+')'
    declarations=[('point','g:Rat,b:Rat,sg:Rat,sb:Rat,exact:bool,single:bool,partial:bool','Rat','point'),('aggregate_score','sum:Rat,count:Rat','Rat','score'),('aggregate_percent','score:Rat,count:Rat','Rat','percent'),('aggregate_passed','percent:Rat,threshold:Rat','bool','passed')]
    generated='\n'.join(f"fn {name}({params})->R<{typ}>{{Ok({emit(m['expressions'][key])})}}" for name,params,typ,key in declarations)
    return '// model revision '+m['revision_id']+'\n#![allow(unused_variables,unused_parens,dead_code)]\n'+Path(__file__).with_name('rust_support.rs').read_text(encoding='utf-8')+'\n'+generated+'\n'
def encode_case(case):
    case=validate_case(case);threshold=threshold_fraction(case['passing_percent']);parts=[len(case['questions']),int(case['grading']=='partial'),threshold.numerator,threshold.denominator]
    for q in case['questions']:
        low=lower_question(q);parts.extend(int(low[k]) for k in ('g','b','sg','sb','exact','single'))
    return ' '.join(str(x) for x in parts)
def parse_result(line):
    need(type(line) is str and len(line.encode('utf-8'))<=65536 and len(line.strip().splitlines())==1,'One bounded JSON result line required')
    def object_pairs(pairs):
        d={}
        for k,v in pairs:need(k not in d,'Duplicate result key');d[k]=v
        return d
    try:value=json.loads(line,object_pairs_hook=object_pairs,parse_constant=lambda x:(_ for _ in ()).throw(Refused('Nonfinite result')))
    except (json.JSONDecodeError,RecursionError) as e:raise Refused('Malformed result JSON') from e
    need(type(value) is dict and 'error' not in value,'Rust calculation refused')
    exact_keys(value,['score','percent','score_display','percent_display','passed','correct_count','points'])
    def rational(item,point=False):
        exact_keys(item,['n','d','display','is_correct'] if point else ['n','d'])
        need(all(type(item[k]) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,38})',item[k]) for k in ('n','d')),'Rational result integer encoding')
        need(0<=int(item['n'])<=MAX_I128 and 0<int(item['d'])<=MAX_I128,'Rational result i128 domain');f=Fraction(int(item['n']),int(item['d']));need(str(f.numerator)==item['n'] and str(f.denominator)==item['d'],'Result not reduced')
        if point:need(0<=f<=1 and type(item['is_correct']) is bool and item['display']==display(f),'Invalid point result')
        return f
    need(type(value['points']) is list and 1<=len(value['points'])<=50 and type(value['passed']) is bool and type(value['correct_count']) is int,'Result shape')
    for p in value['points']:rational(p,True)
    score=rational(value['score']);percent=rational(value['percent']);need(compare(score,Fraction(len(value['points'])))<=0 and compare(percent,Fraction(100))<=0,'Result bounds')
    need(value['score_display']==display(score) and value['percent_display']==display(percent),'Result display mismatch')
    need(value['correct_count']==sum(p['is_correct'] for p in value['points']),'Result correct_count mismatch')
    return value
