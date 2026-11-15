"""Bounded typed scoring mathematics. No eval, source execution or inference."""
from copy import deepcopy
from fractions import Fraction
import hashlib,json,math,re

MAX_I128=(1<<127)-1
MAX_DECIMAL=(1<<96)-1
class Refused(ValueError):pass
def need(ok,message):
    if not ok:raise Refused(message)
def exact_keys(value,keys):
    need(type(value) is dict and set(value)==set(keys),'Unknown or missing object fields')
def bounded_json(value):
    count=0;active=set()
    def walk(v,depth):
        nonlocal count
        count+=1;need(count<=20000 and depth<=40,'JSON complexity limit')
        if v is None or type(v) is bool:return
        if type(v) is int:need(abs(v)<=MAX_I128,'JSON integer limit');return
        if type(v) is str:need(len(v)<=65536,'JSON string limit');return
        need(type(v) in (dict,list),'JSON value type refused')
        need(id(v) not in active,'JSON cycle refused');active.add(id(v))
        if type(v) is dict:
            for k,x in v.items():need(type(k) is str and len(k)<=256,'JSON key refused');walk(x,depth+1)
        else:
            for x in v:walk(x,depth+1)
        active.remove(id(v))
    walk(value,0)
    need(len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8'))<=262144,'JSON byte limit')
def identity(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def rat(n,d=1):return {'op':'lit','type':'rat','n':n,'d':d}
def boolean(v):return {'op':'bool','type':'bool','value':v}
def var(name,typ='rat'):return {'op':'var','type':typ,'name':name}
def op(name,*args,typ='rat'):return {'op':name,'type':typ,'args':list(args)}
def cond(c,t,f):return op('if',c,t,f,typ=t['type'])
POINT_ENV={**{n:'rat' for n in ['g','b','sg','sb','w']},**{n:'bool' for n in ['exact','single','partial']}}
ENVS={'point':POINT_ENV,'score':{'sum':'rat','count':'rat'},'percent':{'score':'rat','count':'rat'},'passed':{'percent':'rat','threshold':'rat'}}
def validate_node(node,env):
    count=0
    def visit(n,depth):
        nonlocal count
        count+=1;need(count<=512 and depth<=24,'AST complexity limit')
        need(type(n) is dict and n.get('type') in ('rat','bool'),'Typed AST required')
        kind=n.get('op');typ=n['type']
        if kind=='lit':
            exact_keys(n,['op','type','n','d']);need(typ=='rat' and type(n['n']) is int and type(n['d']) is int,'Rational literal type')
            need(abs(n['n'])<=1000000 and 1<=n['d']<=1000000 and math.gcd(n['n'],n['d'])==1,'Canonical bounded literal required')
        elif kind=='bool':exact_keys(n,['op','type','value']);need(typ=='bool' and type(n['value']) is bool,'Boolean literal type')
        elif kind=='var':exact_keys(n,['op','type','name']);need(type(n['name']) is str and env.get(n['name'])==typ,'Unknown or mistyped variable')
        else:
            exact_keys(n,['op','type','args']);need(type(n['args']) is list,'AST arguments list required')
            arity=3 if kind=='if' else 1 if kind=='not' else 2
            need(kind in ('if','not','and','or','add','sub','mul','div','lt','le','eq','ge','gt') and len(n['args'])==arity,'AST operator/arity refused')
            ts=[visit(a,depth+1) for a in n['args']]
            if kind=='if':need(ts[0]=='bool' and ts[1]==ts[2]==typ,'Conditional types differ')
            elif kind in ('not','and','or'):need(typ=='bool' and all(t=='bool' for t in ts),'Boolean operand type')
            elif kind in ('lt','le','eq','ge','gt'):need(typ=='bool' and ts==['rat','rat'],'Comparison operand type')
            else:need(typ=='rat' and ts==['rat','rat'],'Arithmetic operand type')
        return typ
    return visit(node,0)
def validate_model(model):
    bounded_json(model);need(type(model) is dict,'Model object required')
    required={'schema','expressions','penalty_weight','provenance','intent','parent_revision_id'}
    need(set(model) in (required,required|{'revision_id'}),'Unknown or missing model fields')
    need(model['schema']=='mia-scoring-1','Model schema refused')
    need(type(model['intent']) is str and len(model['intent'])<=1000,'Intent limit')
    parent=model['parent_revision_id'];need(parent is None or type(parent) is str and re.fullmatch('[0-9a-f]{64}',parent),'Parent identity invalid')
    need(type(model['provenance']) is dict,'Provenance object required')
    weight=model['penalty_weight'];exact_keys(weight,['n','d']);need(type(weight['n']) is int and type(weight['d']) is int and 0<=weight['n']<=weight['d']<=1000000 and weight['d']>0 and math.gcd(weight['n'],weight['d'])==1,'Penalty must be canonical rational0..1')
    exact_keys(model['expressions'],ENVS)
    for name,env in ENVS.items():need(validate_node(model['expressions'][name],env)==('bool' if name=='passed' else 'rat'),'Expression result type')
    result=deepcopy(model);claimed=result.pop('revision_id',None);actual=identity(result)
    if 'revision_id' in model:need(type(claimed) is str and claimed==actual,'Model revision identity mismatch')
    result['revision_id']=actual;return result
def default_model(provenance=None):
    zero,one=rat(0),rat(1)
    subtract=op('sub',op('div',var('sg'),var('g')),op('mul',var('w'),cond(op('eq',var('b'),zero,typ='bool'),zero,op('div',var('sb'),var('b')))))
    points=cond(var('exact','bool'),one,cond(op('or',var('single','bool'),op('not',var('partial','bool'),typ='bool'),typ='bool'),zero,cond(op('lt',subtract,zero,typ='bool'),zero,subtract)))
    return validate_model({'schema':'mia-scoring-1','expressions':{'point':points,'score':var('sum'),'percent':op('div',op('mul',var('score'),rat(100)),var('count')),'passed':op('ge',var('percent'),var('threshold'),typ='bool')},'penalty_weight':{'n':1,'d':1},'provenance':{} if provenance is None else deepcopy(provenance),'intent':'Known C# scoring profile baseline; no general extraction claim.','parent_revision_id':None})
def revise_model(model,penalty_n=1,penalty_d=2,intent='Change the penalty weight intentionally.'):
    original=validate_model(model);result=deepcopy(original);result.pop('revision_id');result['parent_revision_id']=original['revision_id'];result['penalty_weight']={'n':penalty_n,'d':penalty_d};result['intent']=intent;return validate_model(result)
def threshold_fraction(text):
    need(type(text) is str and len(text)<=32 and re.fullmatch(r'(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,28})?',text),'Exact decimal string required')
    a,_,b=text.partition('.');b=b.rstrip('0');n=int(a+b);need(n<=MAX_DECIMAL,'System.Decimal coefficient overflow');value=Fraction(n,10**len(b));need(0<=value<=100,'Threshold outside0..100');return value
def validate_case(case):
    bounded_json(case);exact_keys(case,['questions','grading','passing_percent','show_correction'])
    need(case['grading'] in ('partial','all-or-nothing') and type(case['show_correction']) is bool,'Case grading/correction invalid');threshold_fraction(case['passing_percent'])
    need(type(case['questions']) is list and 1<=len(case['questions'])<=50,'Question count1..50 required');ids=set()
    def identifier(value):
        need(type(value) is str and re.fullmatch('[A-Za-z0-9-]{1,80}',value) and value not in ids,'Invalid or duplicate question/option ID');ids.add(value)
    for q in case['questions']:
        exact_keys(q,['id','mode','options','selected']);identifier(q['id']);need(q['mode'] in ('single','multiple'),'Question mode invalid')
        need(type(q['options']) is list and 2<=len(q['options'])<=8,'Option count2..8 required');good=0;options=set()
        for o in q['options']:
            exact_keys(o,['id','correct']);identifier(o['id']);options.add(o['id']);need(type(o['correct']) is bool,'Correct must be boolean');good+=o['correct']
        need(good>=1 and (q['mode']!='single' or good==1),'Correct option count invalid')
        selected=q['selected'];need(type(selected) is list and 1<=len(selected)<=8 and all(type(s) is str for s in selected),'Selection list invalid');need(len(set(selected))==len(selected) and set(selected)<=options,'Unknown/duplicate selected option');need(q['mode']!='single' or len(selected)==1,'Single requires exactly one selected option')
    return deepcopy(case)
def lower_question(q):
    good={o['id'] for o in q['options'] if o['correct']};chosen=set(q['selected']);return {'g':len(good),'b':len(q['options'])-len(good),'sg':len(chosen&good),'sb':len(chosen-good),'exact':chosen==good,'single':q['mode']=='single'}
def checked(n):need(type(n) is int and -MAX_I128<=n<=MAX_I128,'i128 overflow');return n
def normalized(n,d):
    checked(n);checked(d);need(d!=0,'Division by zero');return Fraction(n,d)
def arithmetic(kind,a,b):
    if kind in ('add','sub'):
        left=checked(a.numerator*b.denominator);right=checked(b.numerator*a.denominator);n=checked(left+right if kind=='add' else left-right);d=checked(a.denominator*b.denominator)
    elif kind=='mul':n=checked(a.numerator*b.numerator);d=checked(a.denominator*b.denominator)
    else:n=checked(a.numerator*b.denominator);d=checked(a.denominator*b.numerator)
    return normalized(n,d)
def compare(a,b):return checked(a.numerator*b.denominator)-checked(b.numerator*a.denominator)
def interpret(node,env):
    kind=node['op']
    if kind=='lit':return Fraction(node['n'],node['d'])
    if kind=='bool':return node['value']
    if kind=='var':return env[node['name']]
    args=node['args']
    if kind=='if':return interpret(args[1] if interpret(args[0],env) else args[2],env)
    a=interpret(args[0],env)
    if kind=='not':return not a
    if kind=='and':return a and interpret(args[1],env)
    if kind=='or':return a or interpret(args[1],env)
    b=interpret(args[1],env)
    if kind in ('add','sub','mul','div'):return arithmetic(kind,a,b)
    cmp=compare(a,b);return {'lt':cmp<0,'le':cmp<=0,'eq':cmp==0,'ge':cmp>=0,'gt':cmp>0}[kind]
def display(value):
    need(value>=0,'Negative display refused');cents,remainder=divmod(checked(value.numerator*100),value.denominator)
    if checked(remainder*2)>=value.denominator:cents+=1
    return str(cents//100)+'.'+str(cents%100).zfill(2)
def rational_record(value):return {'n':str(value.numerator),'d':str(value.denominator)}
def evaluate(model,case):
    model=validate_model(model);case=validate_case(case);expr=model['expressions'];weight=Fraction(**{'numerator':model['penalty_weight']['n'],'denominator':model['penalty_weight']['d']});points=[];total=Fraction(0);correct=0
    for q in case['questions']:
        low=lower_question(q);env={k:Fraction(v) if type(v) is int else v for k,v in low.items()};env.update(w=weight,partial=case['grading']=='partial');value=interpret(expr['point'],env)
        need(value.numerator>=0 and compare(value,Fraction(1))<=0,'Point result outside0..1');total=arithmetic('add',total,value);correct+=low['exact'];points.append({**rational_record(value),'display':display(value),'is_correct':low['exact']})
    count=Fraction(len(points));score=interpret(expr['score'],{'sum':total,'count':count});need(score.numerator>=0 and compare(score,count)<=0,'Score outside0..question count');percent=interpret(expr['percent'],{'score':score,'count':count});need(percent.numerator>=0 and compare(percent,Fraction(100))<=0,'Percent outside0..100');passed=interpret(expr['passed'],{'percent':percent,'threshold':threshold_fraction(case['passing_percent'])})
    return {'score':rational_record(score),'percent':rational_record(percent),'score_display':display(score),'percent_display':display(percent),'passed':passed,'correct_count':correct,'points':points}
