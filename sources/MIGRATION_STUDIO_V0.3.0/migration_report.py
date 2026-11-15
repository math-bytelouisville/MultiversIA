"""Read-only, offline report map; it never executes experiments or source code."""
from __future__ import annotations
import base64
import hashlib
import html
import json
from pathlib import Path

from profile_import import _safe_path

MAX_REPORT_BYTES = 16 * 1024 * 1024

STYLE = r'''
:root{color-scheme:dark;font-family:Segoe UI,system-ui,sans-serif;color:#e6edf5;background:#111820;font-synthesis:none}
*{box-sizing:border-box}body{margin:0}button,a{touch-action:manipulation}button{font:inherit;color:inherit;background:#20303e;border:1px solid #415467;border-radius:8px;padding:.6rem .85rem;cursor:pointer}button:hover{background:#304859}button:focus-visible,a:focus-visible,[tabindex]:focus-visible{outline:3px solid #f7cf78;outline-offset:3px}button[aria-selected=true]{background:#345e67;border-color:#7ccdbd}header{padding:22px 28px 18px;border-bottom:1px solid #344452;display:flex;gap:16px;align-items:start;justify-content:space-between}h1{font-size:24px;margin:5px 0 8px;font-weight:650;letter-spacing:-.5px}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:2px;color:#8eb9c5}.subtitle{color:#b8c6d2;max-width:900px;font-size:14px;margin:0;line-height:1.6}.badge{display:inline-block;border:1px solid #65778a;border-radius:99px;padding:7px 13px;font-size:12px;font-weight:700;white-space:nowrap}.badge.pass{border-color:#6cbf9f;color:#a2e0c3}.badge.fail{border-color:#e89084;color:#ffb6ab}.workspace{display:grid;grid-template-columns:minmax(0,1fr) 410px;height:calc(100vh - 169px);min-height:560px}.map-wrap{position:relative;overflow:hidden;background:radial-gradient(ellipse at 50% 40%,#23323c 0%,#131d27 75%);border-right:1px solid #344452}.toolbar{position:absolute;z-index:2;top:14px;left:18px;display:flex;gap:7px;align-items:center;flex-wrap:wrap}.toolbar output{min-width:50px;text-align:center;color:#a9c4d3;font-size:13px}.hint{position:absolute;left:20px;bottom:14px;color:#93aab8;font-size:12px;pointer-events:none}svg{width:100%;height:100%;touch-action:none;cursor:grab;display:block}svg.dragging{cursor:grabbing}.edge{stroke:#526f7e;stroke-width:2;fill:none;marker-end:url(#arrow)}.edge.branch{stroke:#806e55;stroke-dasharray:6 7;marker-end:none}.node{cursor:pointer}.node rect{fill:#192936;stroke:#516b80;stroke-width:1.5}.node:hover rect,.node:focus rect{fill:#263c4a;stroke:#b6d3de}.node.selected rect{stroke:#e5c178;stroke-width:3}.node text{fill:#edf5fa;pointer-events:none}.node .kicker{font-size:11px;letter-spacing:1.5px;fill:#a9c6d2}.node .title{font-size:20px;font-weight:600}.node .status{font-size:13px;fill:#c2d4de}.node .detail{font-size:11px;fill:#91acbd}.node.source .bar{fill:#83d8b5}.node.math .bar{fill:#8abef0}.node.rust .bar{fill:#f5b17c}.node.compare .bar{fill:#c5a1f0}.node.uncovered .bar{fill:#b6a68c}.map-low .detail,.map-low .status,.map-medium .detail{display:none}aside{overflow:auto;padding:23px 22px;background:#17212c;min-width:0}h2{font-size:22px;margin:6px 0 10px}h3{font-size:15px;margin:20px 0 10px}aside p,aside li{font-size:14px;line-height:1.65;color:#c0ced9}aside ul{padding-left:20px}nav{display:flex;gap:6px;margin:20px 0 18px;flex-wrap:wrap}nav button{font-size:12px;padding:8px 10px}.panel[hidden]{display:none}pre{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.5;background:#0f1720;border:1px solid #344452;padding:14px;border-radius:8px;max-height:62vh;overflow:auto}.fact{padding:12px 0;border-bottom:1px solid #30404e}.fact strong{display:block;font-size:13px;color:#e1edf4}.fact span{font-size:13px;line-height:1.55;color:#afc0cd;overflow-wrap:anywhere}.link{color:#9edccc;overflow-wrap:anywhere;font-size:13px}footer{border-top:1px solid #344452;min-height:44px;padding:12px 26px;font-size:12px;color:#b4bec6}.scope{border-left:3px solid #c6a26e;padding:9px 12px;background:#292c2a;font-size:13px;color:#e1ccb0}.counter{font-variant-numeric:tabular-nums}.tabs-row{display:flex;gap:8px;align-items:center;justify-content:space-between}.download{font-size:12px;padding:7px 10px}.side-note{font-size:12px!important;color:#93aab8!important}.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
@media(max-width:850px){header{padding:18px;flex-direction:column;gap:10px}h1{font-size:21px}.workspace{height:auto;grid-template-columns:1fr}.map-wrap{height:55vh;min-height:390px;border-right:0;border-bottom:1px solid #344452}aside{padding:22px;max-height:none}pre{max-height:55vh}.hint{font-size:11px;right:10px}footer{padding:14px 20px}.toolbar{left:12px;top:12px}}
'''

SCRIPT = r'''
'use strict';
const REPORT=JSON.parse(document.getElementById('report-data').textContent);
const LINKS=JSON.parse(document.getElementById('artifact-data').textContent);
const $=s=>document.querySelector(s);
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v)?v:{};
const arr=v=>Array.isArray(v)?v:[];
const receipt=obj(obj(REPORT.source_import).receipt||REPORT.source_import);
const base=obj(REPORT.baseline),variant=obj(REPORT.variant);
const csharp=obj(REPORT.csharp_comparison),oracle=obj(REPORT.csharp_oracle);
const coverage=obj(REPORT.coverage||receipt.coverage);
const status=v=>typeof v==='string'&&v?v:'NON RENSEIGNÉ';
const compared=[REPORT.status,obj(base.comparison).status,obj(variant.comparison).status,csharp.status];
const comparisonState=compared.some(s=>s==='FAIL'||s==='FAILED')?'ÉCHEC':compared.every(s=>s==='PASS')&&oracle.status==='COLLECTED'?'PASS · tests finis':'INCONCLUSIF';
const limitText='Des comparaisons finies peuvent détecter un écart ; elles ne prouvent pas l’équivalence du site complet.';
const stages=[
 {id:'source',kind:'source',x:50,y:75,kicker:'01 · SOURCE',title:'Studio C#',state:status(receipt.status),detail:arr(receipt.source_inventory).length+' fichiers inventoriés',data:receipt},
 {id:'math',kind:'math',x:350,y:75,kicker:'02 · MODÈLE',title:'Règles exactes',state:base.model_revision?'Révision disponible':'Modèle non renseigné',detail:'Points · total · seuil · arrondi',data:{baseline:base.model_revision,variant:variant.model_revision,parent:variant.parent_revision,penalty:variant.penalty,mapping:obj(receipt.provenance).mapping}},
 {id:'rust',kind:'rust',x:650,y:75,kicker:'03 · CIBLE',title:'Calcul en Rust',state:status(obj(base.build).status),detail:'Compilation distincte de validation',data:{baseline:base.build,variant:variant.build}},
 {id:'compare',kind:'compare',x:950,y:75,kicker:'04 · COMPARAISON',title:'Observer les écarts',state:comparisonState,detail:arr(variant.intentional_differences).length+' différences volontaires consignées',data:{global_status:REPORT.status,csharp_comparison:csharp,csharp_oracle:oracle,baseline_python_rust:base.comparison,variant_python_rust:variant.comparison,intentional_differences:variant.intentional_differences,checks:REPORT.checks}},
 {id:'variant',kind:'math',x:350,y:325,kicker:'RÉVISION VOLONTAIRE',title:'Modifier la règle',state:variant.model_revision?'Variante disponible':'Variante non renseignée',detail:'Pénalité et parent conservés',data:variant},
 {id:'uncovered',kind:'uncovered',x:650,y:550,kicker:'PÉRIMÈTRE RESTANT',title:'Le site complet',state:'Pas encore traduit',detail:'HTTP · état · compte · interface',data:{coverage,unsupported:receipt.unsupported,limitations:REPORT.limitations}}
];
const svg=$('#map'),layer=$('#nodes'),NS='http://www.w3.org/2000/svg';
function el(name,attrs={},text){const n=document.createElementNS(NS,name);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));if(text!==undefined)n.textContent=text;return n;}
for(const s of stages){const g=el('g',{class:'node '+s.kind,id:'node-'+s.id,transform:`translate(${s.x} ${s.y})`,tabindex:0,role:'button','aria-label':s.title+' : '+s.state});g.append(el('rect',{width:240,height:150,rx:14}),el('rect',{class:'bar',x:0,y:25,width:4,height:42,rx:2}),el('text',{x:20,y:29,class:'kicker'},s.kicker),el('text',{x:20,y:62,class:'title'},s.title),el('text',{x:20,y:94,class:'status'},s.state.length>29?s.state.slice(0,27)+'…':s.state),el('text',{x:20,y:125,class:'detail'},s.detail));g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(s.id)}});layer.append(g);}
function fact(title,text){const n=document.createElement('div');n.className='fact';const h=document.createElement('strong');h.textContent=title;const p=document.createElement('span');p.textContent=String(text);n.append(h,p);$('#summary').append(n);}
function paragraph(text,css=''){const p=document.createElement('p');p.textContent=text;if(css)p.className=css;$('#summary').append(p);}
function firstChangedPoint(){for(const item of arr(variant.intentional_differences)){const d=obj(item),before=arr(obj(d.baseline).points),after=arr(obj(d.variant).points),qs=arr(obj(d.input).questions);for(let i=0;i<Math.min(before.length,after.length,qs.length);i++){const a=obj(before[i]),b=obj(after[i]);if(typeof a.display==='string'&&typeof b.display==='string'&&a.display!==b.display)return{caseIndex:d.case_index,question:obj(qs[i]).id,before:a.display,after:b.display};}}return null;}
function select(id){const s=stages.find(x=>x.id===id);if(!s)return;document.querySelectorAll('.node').forEach(n=>n.classList.toggle('selected',n.id==='node-'+id));$('#detail-kicker').textContent=s.kicker;$('#detail-title').textContent=s.title;$('#summary').replaceChildren();$('#raw').textContent=JSON.stringify(s.data,null,2);if(id==='source'){paragraph(receipt.reason||'Aucun reçu d’import disponible.');fact('Conservation',receipt.snapshot_complete===true?'Instantané complet déclaré par le reçu.':'Instantané complet non établi.');fact('Inventaire',arr(receipt.source_inventory).length+' fichiers, dont '+arr(receipt.source_inventory).filter(x=>x.role==='EXCLUDED').length+' exclus du projet compilé.');fact('Reconnaissance',obj(receipt.provenance).mapping_kind||'Correspondance au profil non admise.');paragraph('Le profil est connu et vérifié par empreintes. Ce parcours ne prétend pas comprendre n’importe quel projet C#.','side-note');}
else if(id==='math'){if(receipt.status!=='IMPORTED'||!base.model_revision){paragraph('Le modèle du profil reconnu n’est pas renseigné. Consulter les données exactes.');}else{paragraph('Règle du profil reconnu : une sélection exactement correcte vaut 1 point.');fact('Choix unique ou tout-ou-rien','Toute autre sélection vaut 0 point.');fact('Choix multiple, barème partiel','max(0, bonnes choisies / bonnes possibles − poids × mauvaises choisies / mauvaises possibles)');paragraph('S’il n’existe aucune mauvaise option, la fraction des mauvaises réponses vaut 0 : aucune division par zéro.','side-note');fact('Total et pourcentage','Total = somme des points. Pourcentage = 100 × total / nombre de questions.');fact('Réussite','Le pourcentage exact est comparé au seuil exact. Seul l’affichage est ensuite arrondi à deux décimales.');paragraph('Le poids d’origine est 1/1. La variante montre la modification choisie. Correspondances C#, plages source et identités de révision : onglet Données exactes.','side-note');}}
else if(id==='rust'){paragraph('Le Rust est produit depuis le modèle. Son état de compilation et les comparaisons sont des constats séparés.');fact('Référence',status(obj(base.build).status));fact('Variante',status(obj(variant.build).status));paragraph(limitText,'scope');}
else if(id==='compare'){paragraph(limitText,'scope');fact('1 · C# observé ↔ modèle',status(csharp.status)+' · oracle '+status(oracle.status));paragraph('Le C# est observé par ses réponses HTTP. Ses fractions internes ne sont pas observées.','side-note');fact('2 · Modèle ↔ Rust de référence',status(obj(base.comparison).status));fact('3 · Modèle ↔ Rust de variante',status(obj(variant.comparison).status));const checks=arr(REPORT.checks);fact('Contrôles du rapport',checks.filter(c=>c.pass===true).length+' réussis / '+checks.length+' consignés');fact('Différences volontaires',arr(variant.intentional_differences).length+' cas ; entrées et résultats intégraux dans « Données exactes ».');const issues=checks.filter(c=>c.pass!==true);for(const c of issues.slice(0,8))fact(c.name||'Contrôle sans nom',c.pass===false?'Échec':'Non renseigné');if(issues.length>8)paragraph((issues.length-8)+' autres contrôles non réussis dans les données exactes.');}
else if(id==='variant'){paragraph('Une modification intentionnelle crée une nouvelle révision. Elle ne remplace pas silencieusement la règle d’origine.');const w=obj(variant.penalty);fact('Poids de pénalité',w.n!==undefined&&w.d!==undefined?String(w.n)+'/'+String(w.d):'Non renseigné');paragraph('Ce poids s’applique aux mauvaises options sélectionnées, uniquement pour les questions multiples en barème partiel.');const change=firstChangedPoint();if(change){fact('Premier effet observé',change.before+' → '+change.after+' point');fact('Question concernée','Cas '+(Number.isInteger(change.caseIndex)?change.caseIndex+1:'non renseigné')+' · question '+String(change.question));paragraph('Ces deux valeurs viennent des résultats consignés, avant et après la modification.','side-note');}else{paragraph('Aucun changement de point affiché n’est consigné. D’autres écarts éventuels restent dans les données exactes.');}fact('Étendue des différences',arr(variant.intentional_differences).length+' cas consignés.');paragraph('Le modèle d’origine, sa variante, leurs empreintes et leur lien de révision sont conservés dans les données exactes et les fichiers.','side-note');}
else {paragraph('Ces branches restent à représenter et à vérifier. Les octets conservés ne constituent pas une traduction de leur comportement.','scope');const list=arr(coverage.untranslated);for(const item of list.length?list:['Routes HTTP et rendu MVC','Persistance et transitions des tentatives','Compte, éditeur et brouillons du navigateur'])fact('Non traduit',typeof item==='string'?item:JSON.stringify(item));for(const item of arr(REPORT.limitations))paragraph(typeof item==='string'?item:JSON.stringify(item),'side-note');}
$('#side-status').textContent=s.state;switchTab('summary');}
function switchTab(id){document.querySelectorAll('.panel').forEach(p=>p.hidden=p.id!==id);document.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===id)));}
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>switchTab(b.dataset.tab)));
for(const item of LINKS){const p=document.createElement('p'),a=document.createElement('a');a.className='link';a.href=item.url;a.textContent=item.label;a.target='_blank';a.rel='noopener';p.append(a);$('#files').append(p);}if(!LINKS.length){$('#files').textContent='Aucun fichier local sûr trouvé à côté du rapport. Les données complètes restent disponibles dans le téléchargement JSON.';}
$('#download').addEventListener('click',()=>{const blob=new Blob([JSON.stringify(REPORT,null,2)],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='migration-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
let view={x:-20,y:-20,w:1280,h:790},drag=null,moved=false;
function paint(){svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);const zoom=1280/view.w;svg.classList.toggle('map-low',zoom<.7);svg.classList.toggle('map-medium',zoom>=.7&&zoom<1.35);$('#zoom-value').textContent=Math.round(zoom*100)+' %';}
function mapPoint(x,y){const m=svg.getScreenCTM();return m?new DOMPoint(x,y).matrixTransform(m.inverse()):new DOMPoint(view.x+view.w/2,view.y+view.h/2);}
function zoomBy(factor,p){const next=Math.max(320,Math.min(3000,view.w/factor)),r=next/view.w;view={x:p.x-(p.x-view.x)*r,y:p.y-(p.y-view.y)*r,w:next,h:view.h*r};paint();}
svg.addEventListener('wheel',e=>{e.preventDefault();zoomBy(Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.003),mapPoint(e.clientX,e.clientY));},{passive:false});
svg.addEventListener('pointerdown',e=>{if(e.button!==0)return;moved=false;drag={x:e.clientX,y:e.clientY,start:{...view},matrix:svg.getScreenCTM().inverse(),node:e.target.closest('.node')?.id.replace('node-','')};svg.setPointerCapture(e.pointerId);svg.classList.add('dragging');});
svg.addEventListener('pointermove',e=>{if(!drag)return;const a=new DOMPoint(drag.x,drag.y).matrixTransform(drag.matrix),b=new DOMPoint(e.clientX,e.clientY).matrixTransform(drag.matrix);if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)>4)moved=true;view.x=drag.start.x+a.x-b.x;view.y=drag.start.y+a.y-b.y;paint();});
function end(e){const chosen=drag?.node;drag=null;svg.classList.remove('dragging');if(e.type==='pointerup'&&!moved&&chosen)select(chosen);}svg.addEventListener('pointerup',end);svg.addEventListener('pointercancel',end);
$('#zoom-in').onclick=()=>zoomBy(1.25,new DOMPoint(view.x+view.w/2,view.y+view.h/2));$('#zoom-out').onclick=()=>zoomBy(.8,new DOMPoint(view.x+view.w/2,view.y+view.h/2));$('#fit').onclick=()=>{view={x:-20,y:-20,w:1280,h:790};paint()};
svg.addEventListener('keydown',e=>{const d=view.w*.08;if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();view.x+=e.key==='ArrowLeft'?-d:e.key==='ArrowRight'?d:0;view.y+=e.key==='ArrowUp'?-d:e.key==='ArrowDown'?d:0;paint();}if(e.key==='+'||e.key==='=')$('#zoom-in').click();if(e.key==='-')$('#zoom-out').click();});
$('#report-status').textContent=comparisonState==='PASS · tests finis'?'PASS · périmètre borné':comparisonState;$('#report-status').classList.toggle('pass',comparisonState==='PASS · tests finis');$('#report-status').classList.toggle('fail',comparisonState==='ÉCHEC');
paint();select('source');
'''


def _embedded(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def _links(report: dict, directory: Path) -> list[dict]:
    """Only extant text artifacts inside this report's evidence directory."""
    found = {}
    extensions = ('.json', '.txt', '.cs', '.rs', '.csproj', '.cshtml', '.js', '.css')
    def visit(value, depth=0):
        if depth > 40:
            return
        if isinstance(value, dict):
            for child in value.values(): visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value: visit(child, depth + 1)
        elif isinstance(value, str) and len(value) < 2048 and value.lower().endswith(extensions):
            try:
                p = Path(value)
                if not p.is_absolute(): p = directory / p
                p = _safe_path(p)
                if p.is_relative_to(directory) and p.is_file() and p.suffix.lower() in extensions:
                    found[str(p)] = dict(label=p.relative_to(directory).as_posix(), url=p.as_uri())
            except (OSError, ValueError):
                pass
    visit(report)
    source = report.get('source_import')
    if isinstance(source, dict):
        source = source.get('receipt', source)
    if isinstance(source, dict) and isinstance(source.get('snapshot_dir'), str):
        inventory = source.get('source_inventory')
        for row in inventory if isinstance(inventory, list) else []:
            if isinstance(row, dict) and isinstance(row.get('path'), str):
                visit(str(Path(source['snapshot_dir']) / row['path']))
    for version in ('baseline', 'variant'):
        build = report.get(version, {})
        build = build.get('build', {}) if isinstance(build, dict) else {}
        if isinstance(build, dict) and isinstance(build.get('folder'), str) and isinstance(build.get('files_sha256'), dict):
            for name in build['files_sha256']:
                if isinstance(name, str):
                    visit(str(Path(build['folder']) / name))
    for name in ('report.json', 'model.json', 'baseline-model.json', 'variant-model.json'):
        visit(name)
    return [found[k] for k in sorted(found)]


def render_report(report: dict, output_path) -> str:
    """Write once; return absolute filename. Opening the report is read-only."""
    if not isinstance(report, dict):
        raise ValueError('Le rapport doit être un objet JSON.')
    raw = _embedded(report)
    if len(raw.encode('utf-8')) > MAX_REPORT_BYTES:
        raise ValueError('Rapport trop volumineux (maximum 16 Mio).')
    target = _safe_path(Path(output_path), missing=True)
    if target.suffix.lower() != '.html':
        raise ValueError('Le rapport doit porter une extension .html.')
    target.parent.mkdir(parents=True, exist_ok=True)
    _safe_path(target.parent)
    script_hash = base64.b64encode(hashlib.sha256(SCRIPT.encode('utf-8')).digest()).decode('ascii')
    csp = html.escape("default-src 'none'; script-src 'sha256-" + script_hash + "'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'", quote=True)
    output = '''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="''' + csp + '''"><title>Migration Studio · Source, maths et Rust</title><style>''' + STYLE + '''</style></head><body>
<header><div><div class="eyebrow">MultiversIA · Atelier de migration</div><h1>Du Studio C# aux règles en Rust</h1><p class="subtitle">Une carte des sources conservées, des calculs représentés et des différences observées. Rapport figé, consultable hors connexion.</p></div><span id="report-status" class="badge">INCONCLUSIF</span></header>
<main class="workspace"><section class="map-wrap" aria-label="Carte de migration"><div class="toolbar"><button id="zoom-out" aria-label="Dézoomer">−</button><output id="zoom-value" aria-label="Niveau de zoom">100 %</output><button id="zoom-in" aria-label="Zoomer">+</button><button id="fit">Vue globale</button></div>
<svg id="map" tabindex="0" role="group" aria-label="Schéma libre : molette pour zoomer, glisser pour déplacer, Tab pour choisir une étape"><defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" fill="#6c8998"/></marker></defs><g id="edges"><path class="edge" d="M290 150H340 M590 150H640 M890 150H940"/><path class="edge" d="M470 225V315 M590 400H770V235"/><path class="edge branch" d="M170 225V625H640"/></g><g id="nodes"></g></svg><div class="hint">Molette : zoom libre · Glisser : déplacer · Tab puis Entrée : détails · Flèches : parcourir</div></section>
<aside aria-label="Détails de l’étape"><div id="detail-kicker" class="eyebrow"></div><h2 id="detail-title"></h2><span id="side-status" class="badge"></span><div class="tabs-row"><nav aria-label="Détails"><button data-tab="summary" aria-selected="true">Résumé</button><button data-tab="raw" aria-selected="false">Données exactes</button><button data-tab="files" aria-selected="false">Fichiers</button></nav></div><div id="summary" class="panel"></div><pre id="raw" class="panel" hidden></pre><div id="files" class="panel" hidden></div><p class="side-note">Les vues résument le rapport sans retirer ses données. Le JSON téléchargeable conserve toutes les entrées reçues.</p><button id="download" class="download">Télécharger le rapport JSON complet</button></aside></main>
<footer>Portée actuelle : calcul de notation d’un profil C# connu. Le site MVC complet, son état et ses brouillons ne sont pas traduits.</footer>
<script id="report-data" type="application/json">''' + raw + '''</script><script id="artifact-data" type="application/json">''' + _embedded(_links(report, target.parent)) + '''</script><script>''' + SCRIPT + '''</script></body></html>'''
    with target.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(output)
    return str(target)
