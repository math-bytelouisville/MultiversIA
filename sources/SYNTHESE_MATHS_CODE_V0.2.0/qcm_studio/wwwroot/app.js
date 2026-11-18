'use strict';
(() => {
  const copy = value => JSON.parse(JSON.stringify(value));
  const config = () => ({grading:'all-or-nothing',passingPercent:60,shuffleQuestions:false,shuffleOptions:false,showCorrection:true});
  const uid = () => crypto.randomUUID();
  const blankQuestion = () => ({id:uid(),text:'',mode:'single',options:[{id:uid(),text:'',correct:true},{id:uid(),text:'',correct:false}],explanation:''});
  const draftKey = quiz => `mia-studio-draft:${quiz.id || 'new'}:${quiz.revision || 0}`;
  const draftIdentity = quiz => JSON.stringify({id:quiz.id,revision:quiz.revision,title:quiz.title,questions:quiz.questions,config:quiz.config});
  const semanticLevel = zoom => zoom < .7 ? 0 : zoom < 1.4 ? 1 : 2;
  function validateQuiz(quiz) {
    if (!quiz.title.trim() || quiz.title.length > 120) return 'Donne un titre de 1 à 120 caractères.';
    if (quiz.questions.length < 1 || quiz.questions.length > 50) return 'Il faut de 1 à 50 questions.';
    for (let i=0;i<quiz.questions.length;i++) {
      const q=quiz.questions[i],n=q.options.filter(o=>o.correct).length;
      if (!q.text.trim() || q.text.length>500) return `Question ${i+1} : écris un énoncé de 1 à 500 caractères.`;
      if (q.options.length<2 || q.options.length>8 || q.options.some(o=>!o.text.trim() || o.text.length>200)) return `Question ${i+1} : il faut 2 à 8 réponses non vides (200 caractères au plus).`;
      if ((q.mode==='single' && n!==1) || (q.mode==='multiple' && n<1)) return `Question ${i+1} : coche ${q.mode==='single'?'exactement une':'au moins une'} bonne réponse.`;
      if (q.explanation.length>1000) return `Question ${i+1} : explication limitée à 1 000 caractères.`;
    }
    if (!Number.isFinite(quiz.config.passingPercent) || quiz.config.passingPercent<0 || quiz.config.passingPercent>100) return 'Le seuil de réussite doit aller de 0 à 100 %.';
    return null;
  }
  const progress = attempt => ({answered:attempt.answers.filter(a=>a.optionIds.length).length,total:attempt.questions.length});
  const core={copy,config,blankQuestion,draftKey,draftIdentity,semanticLevel,validateQuiz,progress};
  if (typeof document==='undefined') { if(typeof module!=='undefined')module.exports=core; return; }
  globalThis.StudioCore=core;
  const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
  const S={studio:null,view:'home',draft:null,draftBaseline:null,questionId:null,attempt:null,answers:[],answerGeneration:0,savedGeneration:0,savePromise:null,saveTimer:null,startRequests:new Map(),busy:false,loading:false,saveError:null,zoom:1,x:0,y:0,mapDrag:null};
  function el(tag,attrs={},...children) {
    const node=document.createElement(tag);
    for(const [key,value] of Object.entries(attrs)) {
      if(key.startsWith('on'))node.addEventListener(key.slice(2).toLowerCase(),value);
      else if(key==='class')node.className=value;
      else if(key in node)node[key]=value;
      else node.setAttribute(key,String(value));
    }
    children.flat().forEach(child=>{if(child!==null && child!==undefined)node.append(child instanceof Node?child:document.createTextNode(String(child)));});
    return node;
  }
  const button=(text,action,kind='secondary',disabled=false)=>el('button',{type:'button',class:kind,disabled,onClick:()=>guard(action)},text);
  const paragraph=(text,kind='muted')=>el('p',{class:kind},text);
  function heading(kicker,title,description) {return [el('p',{class:'eyebrow'},kicker),el('h2',{},title),description?paragraph(description):null];}
  function notice(text) {$('notice').textContent=text||'';$('notice').hidden=!text;}
  function status(text) {$('status').textContent=text;}
  const localMemory=new Map();
  function localRead(key) {if(localMemory.has(key))return copy(localMemory.get(key));try{const value=JSON.parse(localStorage.getItem(key));if(value!==null)localMemory.set(key,copy(value));return value;}catch{return null;}}
  function localWrite(key,value) {localMemory.set(key,copy(value));try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{return false;}}
  function localRemove(key) {localMemory.delete(key);try{localStorage.removeItem(key);}catch{/* The server remains authoritative. */}}
  function localKeys() {const keys=new Set(localMemory.keys());try{for(let i=0;i<localStorage.length;i++)keys.add(localStorage.key(i));}catch{/* In-memory drafts remain accessible. */}return [...keys];}
  async function api(path,method='GET',body) {
    let response;
    try {response=await fetch(path,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:AbortSignal.timeout(20000)});}
    catch {throw new Error(method==='GET'?'Serveur injoignable. Les données affichées peuvent être anciennes.':'Envoi non confirmé. Les données sont conservées ici ; recharge l’état du serveur avant de réessayer.');}
    let data;try{data=await response.json();}catch{throw new Error('Réponse serveur illisible. Aucun succès n’est confirmé.');}
    if(!response.ok){const error=new Error(data.message || data.error || `Erreur ${response.status}`);error.code=response.status;throw error;}
    return data;
  }
  async function guard(action) {notice('');try{await action();}catch(error){notice((error.code===409?'Conflit de version : ':'')+error.message);$('connection-state').textContent='Dernière action non confirmée';}}
  async function refresh() {
    const data=await api('/api/studio');S.studio=data;$('account').textContent=data.account.displayName;
    $('connection-state').textContent='Données locales vérifiées à '+new Date().toLocaleTimeString('fr-FR');drawMap();return data;
  }
  function persistDraft() {
    if(!S.draft)return;
    if(draftIdentity(S.draft)===S.draftBaseline){localRemove(draftKey(S.draft));return;}
    const durable=localWrite(draftKey(S.draft),{quiz:S.draft,savedAt:new Date().toISOString()});status(durable?'Brouillon local · non publié. Enregistre pour créer une nouvelle version.':'Brouillon en mémoire seulement : stockage local indisponible. Enregistre avant de fermer cet onglet.');
  }
  function answerCacheKey(id) {return 'mia-studio-answers:'+id;}
  function persistAnswers() {if(S.attempt)return localWrite(answerCacheKey(S.attempt.id),{revision:S.attempt.revision,answers:S.answers});return false;}
  async function navigate(view) {
    if(S.loading || S.busy)return;
    if(S.attempt && S.attempt.status==='in-progress' && S.answerGeneration!==S.savedGeneration) {
      try{await saveAnswers();}catch{notice('Réponses non confirmées : reste ici pour les enregistrer ou recharger la partie.');return;}
    }
    if(S.draft)persistDraft();S.loading=true;S.busy=true;render();
    try{await refresh();S.view=view;notice('');render();drawMap();status('Vue actualisée depuis le serveur. Les brouillons restent séparés.');}
    finally{S.loading=false;S.busy=false;render();}
  }
  const poi=[
    {id:'editor',x:205,y:250,title:'Game Editor',icon:'✎',sub:'Créer et imaginer',hint:'Un QCM à ton image'},
    {id:'quizzes',x:535,y:200,title:'Mes QCM',icon:'▦',sub:'Choisir une aventure',hint:'Questions uniques ou multiples'},
    {id:'sessions',x:740,y:430,title:'Mes parties',icon:'▷',sub:'Reprendre son chemin',hint:'Chaque réponse sauvegardée'},
    {id:'results',x:475,y:575,title:'Résultats',icon:'✓',sub:'Comprendre et progresser',hint:'Le détail de chaque tentative'},
    {id:'scores',x:180,y:520,title:'Classements',icon:'☆',sub:'Voir ses progrès',hint:'Une liste par version et barème'}
  ];
  function svg(tag,attrs,text) {const n=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;}
  function mapNodes() {
    const nodes=poi.map(n=>({...n}));
    if(S.view==='play' && S.attempt)S.attempt.questions.forEach((q,i)=>{const angle=2*Math.PI*i/S.attempt.questions.length,radius=Math.max(230,S.attempt.questions.length*27);nodes.push({id:'question:'+q.id,x:500+radius*Math.cos(angle),y:400+radius*Math.sin(angle),title:'Question '+(i+1),sub:S.answers.some(a=>a.questionId===q.id&&a.optionIds.length)?'Réponse choisie':'À découvrir',hint:q.text,icon:String(i+1),question:true});});
    return S.view==='play'&&S.attempt?nodes.filter(n=>n.question):nodes;
  }
  const maximumMapZoom=2.6;
  const mapControlsButton=$('sigma-controls-toggle'),mapControls=mapControlsButton.parentElement;
  let followedActor=null,followFrame=null;
  function setMapControlsExpanded(open) {
    const action=open?'Masquer':'Afficher';
    const label=action+' les commandes de la carte';
    mapControlsButton.setAttribute('aria-expanded',String(open));
    mapControlsButton.setAttribute('aria-label',label);
    mapControlsButton.title=label;
  }
  function closeMapControls() {
    if(mapControlsButton.getAttribute('aria-expanded')!=='true')return;
    if(mapControls.contains(document.activeElement))mapControlsButton.focus();
    setMapControlsExpanded(false);
  }
  function positionMap() {
    $('map-world').setAttribute('transform',`translate(${S.x} ${S.y}) scale(${S.zoom})`);
    mapControls.classList.toggle('sigma-camera-moved',Math.abs(S.x)>.01 || Math.abs(S.y)>.01 || Math.abs(S.zoom-1)>.001);
  }
  function stopFollowing() {
    if(!followedActor)return;
    followedActor.setAttribute('aria-pressed','false');
    followedActor=null;
    if(followFrame!==null){cancelAnimationFrame(followFrame);followFrame=null;}
    closeMapControls();
    mapControls.classList.remove('sigma-following');
  }
  function centerOnActor() {
    const position=followedActor.transform.baseVal.getItem(0).matrix;
    const x=500-position.e*S.zoom,y=400-position.f*S.zoom;
    if(S.x===x && S.y===y)return;
    S.x=x;S.y=y;positionMap();
  }
  function followActorFrame() {
    followFrame=null;
    if(followedActor.style.display==='none'){stopFollowing();return;}
    if(!S.mapDrag)centerOnActor();
    followFrame=requestAnimationFrame(followActorFrame);
  }
  function followActor(actor) {
    if(followedActor===actor)return;
    if(followedActor)followedActor.setAttribute('aria-pressed','false');
    else mapControls.classList.add('sigma-following');
    followedActor=actor;
    actor.setAttribute('aria-pressed','true');
    const matrix=$('world-map').getScreenCTM();
    const targetHeight=actor.querySelector('.sigma-actor-target').height.baseVal.value;
    if(matrix && matrix.a>0)S.zoom=Math.max(S.zoom,Math.min(maximumMapZoom,42/(targetHeight*matrix.a)));
    centerOnActor();
    drawMap();
    if(followFrame===null)followFrame=requestAnimationFrame(followActorFrame);
  }
  function drawMap() {
    const mapArea=$('world-map').closest('.map-area'),cityVisible=S.view==='home';
    if(mapArea.classList.contains('sigma-map-active')!==cityVisible){
      mapArea.classList.toggle('sigma-map-active',cityVisible);
      if(!cityVisible){closeMapControls();stopFollowing();}
      if(S.view!=='play'){S.x=0;S.y=0;S.zoom=1;}
    }
    const matrix=$('world-map').getScreenCTM(),effective=S.zoom*(matrix?Math.abs(matrix.a):1),level=semanticLevel(effective);positionMap();
    $('zoom-value').textContent=Math.round(S.zoom*100)+' %';$('zoom-level').textContent=['Vue d’ensemble','Points d’intérêt','Détails du parcours'][level];
    if(cityVisible)return;
    const nodes=mapNodes(),nodeLayer=$('map-nodes');
    const focusedId=nodeLayer.contains(document.activeElement)?document.activeElement.dataset.mapNode:undefined;
    $('map-edges').replaceChildren();nodeLayer.replaceChildren();
    nodes.forEach((node,i)=>{
      if(i){const a=nodes[i-1];$('map-edges').append(svg('path',{d:`M${a.x},${a.y} Q${(a.x+node.x)/2+35},${(a.y+node.y)/2-35} ${node.x},${node.y}`,class:'trail'}));}
      const selected=node.id===S.view || node.id==='question:'+S.questionId;
      const group=svg('g',{transform:`translate(${node.x} ${node.y})`,class:'poi'+(selected?' selected':''),tabindex:'0',role:'button','data-map-node':node.id,'aria-disabled':String(S.loading||S.busy),'aria-label':node.title+' : '+node.sub});
      const enlargement=nodes.length<=12?Math.min(1.8,Math.max(1,.7/effective)):1;
      group.append(svg('circle',{r:39*enlargement,class:'poi-halo'}),svg('circle',{r:28*enlargement,class:'poi-core'}),svg('text',{'text-anchor':'middle',y:8*enlargement,class:'poi-icon',style:`font-size:${25*enlargement}px`},node.icon),svg('text',{'text-anchor':'middle',y:63*enlargement,class:'poi-title',style:`font-size:${16*enlargement}px`},node.title));
      if(level>=1)group.append(svg('text',{'text-anchor':'middle',y:83,class:'poi-sub'},node.sub));
      if(level>=2)group.append(svg('text',{'text-anchor':'middle',y:102,class:'poi-hint'},node.hint.length>35?node.hint.slice(0,33)+'…':node.hint));
      const activate=()=>guard(async()=>{if(S.loading||S.busy)return;if(node.question){S.questionId=node.id.slice(9);render();drawMap();}else await navigate(node.id);});
      group.addEventListener('click',event=>{event.stopPropagation();if(!S.mapMoved)activate();});
      group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();activate();}});nodeLayer.append(group);
      if(node.id===focusedId)group.focus({preventScroll:true});
    });
  }
  function mapPoint(event) {return new DOMPoint(event.clientX,event.clientY).matrixTransform($('world-map').getScreenCTM().inverse());}
  function zoom(factor,point={x:500,y:400}) {
    const next=Math.max(.12,Math.min(maximumMapZoom,S.zoom*factor));
    if(next===S.zoom)return;
    if(followedActor){
      S.zoom=next;
      centerOnActor();
    }else{
      const ratio=next/S.zoom;
      S.x=point.x-(point.x-S.x)*ratio;
      S.y=point.y-(point.y-S.y)*ratio;
      S.zoom=next;
    }
    drawMap();
  }
  function fitMap() {
    if(S.view==='home'){stopFollowing();S.zoom=1;S.x=0;S.y=0;drawMap();return;}
    const nodes=mapNodes(),xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y),left=Math.min(...xs)-130,right=Math.max(...xs)+130,top=Math.min(...ys)-130,bottom=Math.max(...ys)+130;S.zoom=Math.max(.12,Math.min(1,920/(right-left),620/(bottom-top)));S.x=500-(left+right)/2*S.zoom;S.y=430-(top+bottom)/2*S.zoom;drawMap();
  }
  $('world-map').addEventListener('wheel',event=>{
    if(event.deltaY===0)return;
    event.preventDefault();
    const anchorAtPointer=!followedActor && (S.view!=='home' || $('sigma-zoom-pointer').checked);
    zoom(event.deltaY<0?1.12:1/1.12,anchorAtPointer?mapPoint(event):undefined);
  },{passive:false});
  $('world-map').addEventListener('pointerdown',event=>{if(event.button!==0)return;closeMapControls();const p=mapPoint(event);S.mapDrag={x:p.x,y:p.y,ox:S.x,oy:S.y};S.mapMoved=false;});
  $('world-map').addEventListener('pointermove',event=>{if(!S.mapDrag)return;const p=mapPoint(event),d=S.mapDrag;if(!S.mapMoved && Math.abs(p.x-d.x)+Math.abs(p.y-d.y)>4){stopFollowing();S.mapMoved=true;$('world-map').setPointerCapture(event.pointerId);}if(!S.mapMoved)return;S.x=d.ox+p.x-d.x;S.y=d.oy+p.y-d.y;drawMap();});
  window.addEventListener('pointerup',()=>{if(S.mapDrag){S.mapDrag=null;setTimeout(()=>{S.mapMoved=false;},0);}});
  $('world-map').addEventListener('pointercancel',()=>{S.mapDrag=null;});
  $('world-map').addEventListener('click',event=>{
    if(S.view!=='home' || event.button!==0 || S.mapMoved)return;
    const actor=event.target.closest('[data-sigma-track]');
    if(actor)followActor(actor);
    else stopFollowing();
  });
  window.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    if(followedActor || mapControlsButton.getAttribute('aria-expanded')==='true'){
      event.preventDefault();closeMapControls();stopFollowing();
    }
  });
  $('world-map').addEventListener('keydown',event=>{
    const actor=event.target.closest('[data-sigma-track]');
    if(S.view==='home' && actor && (event.key==='Enter'||event.key===' ')){event.preventDefault();if(!event.repeat)followActor(actor);}
    else if(event.key==='+'||event.key==='='){event.preventDefault();zoom(1.2);}
    else if(event.key==='-'){event.preventDefault();zoom(1/1.2);}
    else if(event.key==='Home'){event.preventDefault();fitMap();}
    else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();stopFollowing();S.x+=event.key==='ArrowLeft'?35:event.key==='ArrowRight'?-35:0;S.y+=event.key==='ArrowUp'?35:event.key==='ArrowDown'?-35:0;drawMap();}
  });
  $('zoom-in').onclick=()=>zoom(1.2);$('zoom-out').onclick=()=>zoom(1/1.2);
  $('map-home').onclick=()=>{if(S.view==='home')closeMapControls();fitMap();};
  mapControlsButton.onclick=()=>{
    if(mapControlsButton.getAttribute('aria-expanded')==='true')closeMapControls();
    else setMapControlsExpanded(true);
  };
  $('sigma-stop-follow').onclick=stopFollowing;
  document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>guard(()=>navigate(b.dataset.view)));$('account').onclick=()=>guard(()=>navigate('account'));
  document.querySelector('.brand').onclick=event=>{event.preventDefault();guard(()=>navigate('home'));};
  function render() {
    const panel=$('panel-content');panel.replaceChildren();document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-current',b.dataset.view===S.view?'page':'false'));
    if(!S.studio){panel.append(...heading('ESPACE LOCAL','Préparons la carte','Aucune partie ne démarre avant ton choix.'),button('Recharger',async()=>{await refresh();render();}));return;}
    ({home:renderHome,editor:renderEditor,quizzes:renderQuizzes,sessions:renderSessions,play:renderPlay,results:renderResults,scores:renderScores,account:renderAccount}[S.view]||renderHome)(panel);
    $('map-nodes').querySelectorAll('.poi').forEach(node=>node.setAttribute('aria-disabled',String(S.loading||S.busy)));
  }
  function renderHome(panel) {
    panel.append(...heading('BIENVENUE DANS TON STUDIO','À toi de tracer la suite.','Crée un QCM, fais une partie, puis explore tes résultats.'));
    panel.append(el('div',{class:'stats'},el('div',{},el('strong',{},S.studio.quizzes.filter(q=>!q.archived).length),el('span',{},'QCM disponibles')),el('div',{},el('strong',{},S.studio.attempts.filter(a=>a.status==='in-progress').length),el('span',{},'parties en cours'))));
    panel.append(button('Ouvrir Game Editor',()=>navigate('editor'),'primary'),button('Choisir un QCM',()=>navigate('quizzes')));
    panel.append(paragraph('Le Studio conserve tes parties sur le serveur local. Le zoom change la vue, jamais tes données.','note'));
  }
  function quizCard(quiz,editor=false) {
    const card=el('article',{class:'card'},el('h3',{},quiz.title),paragraph(`${quiz.questionCount} question(s) · version ${quiz.revision} · ${quiz.config.grading==='partial'?'barème partiel':'tout-ou-rien'}${quiz.archived?' · archivé':''}`));
    card.append(button(editor?'Modifier ce QCM':'Ouvrir / jouer',()=>editor?loadQuiz(quiz.id):showQuiz(quiz.id),'secondary',quiz.archived&&editor));return card;
  }
  function renderQuizzes(panel) {panel.append(...heading('BIBLIOTHÈQUE','Mes QCM','Une nouvelle partie utilise une copie figée de la version choisie.'));S.studio.quizzes.forEach(q=>panel.append(quizCard(q)));if(!S.studio.quizzes.length)panel.append(paragraph('Aucun QCM pour le moment.'),button('Créer mon premier QCM',()=>navigate('editor'),'primary'));}
  async function showQuiz(id) {
    if(S.loading||S.busy)return;S.loading=true;let quiz;try{quiz=await api('/api/quizzes/'+encodeURIComponent(id));}finally{S.loading=false;}const panel=$('panel-content');S.view='quizzes';drawMap();
    panel.replaceChildren(...heading('PRÊT À JOUER ?',quiz.title,`${quiz.questions.length} questions · version ${quiz.revision}`),paragraph(gradingText(quiz.config)),paragraph(`Réussite à partir de ${quiz.config.passingPercent} %. ${quiz.config.showCorrection?'Correction détaillée après la partie.':'Correction détaillée masquée.'}`));
    panel.append(button('Commencer une nouvelle partie',()=>startAttempt(quiz),'primary',quiz.archived),button('Retour aux QCM',()=>navigate('quizzes')));if(quiz.archived)panel.append(paragraph('Ce QCM est archivé. Les anciennes parties restent disponibles.'));
  }
  function gradingText(c) {return c.grading==='partial'?'Barème partiel : sur les questions multiples, chaque bonne sélection apporte sa fraction des points et chaque mauvaise en retire une fraction, sans descendre sous zéro. Une question simple vaut 0 ou 1.':'Tout-ou-rien : une question vaut 1 point si les choix correspondent exactement aux bonnes réponses, sinon 0.';}
  async function loadQuiz(id) {
    if(S.loading||S.busy)return;if(S.draft)persistDraft();S.loading=true;S.busy=true;render();
    try{const quiz=await api('/api/quizzes/'+encodeURIComponent(id));const cached=localRead(draftKey(quiz));S.draftBaseline=draftIdentity(quiz);S.draft=cached&&cached.quiz&&cached.quiz.id===quiz.id&&cached.quiz.revision===quiz.revision?cached.quiz:copy(quiz);S.questionId=S.draft.questions[0]?.id;S.view='editor';render();drawMap();status(draftIdentity(S.draft)!==S.draftBaseline?'Brouillon local de cette version restauré, non publié.':'Version '+quiz.revision+' chargée depuis le serveur.');}
    finally{S.loading=false;S.busy=false;render();}
  }
  function newQuiz() {const cached=localRead('mia-studio-draft:new:0');S.draftBaseline=null;S.draft=cached?.quiz||{title:'Mon nouveau QCM',questions:[blankQuestion()],config:config()};S.questionId=S.draft.questions[0].id;render();persistDraft();}
  function inputField(label,value,change,{type='text',maxLength,rows,min,max}={}) {
    const input=el(rows?'textarea':'input',{...(rows?{rows}:{type}),value,disabled:S.busy,...(maxLength?{maxLength}:{}),...(min!==undefined?{min,max}:{})});input.addEventListener('input',()=>{change(type==='number'?Number(input.value):input.value);persistDraft();});return el('label',{class:'field'},el('span',{},label),input);
  }
  function checkbox(label,value,change) {return el('label',{class:'check'},el('input',{type:'checkbox',checked:value,disabled:S.busy,onChange:event=>{change(event.target.checked);persistDraft();}}),el('span',{},label));}
  function renderEditor(panel) {
    panel.append(...heading('GAME EDITOR','Créer son terrain de jeu','QCM est le premier type de jeu disponible. Les autres formats ne sont pas encore implémentés.'));
    if(!S.draft) {panel.append(el('div',{class:'game-choice'},el('span',{class:'game-icon'},'▦'),el('div',{},el('h3',{},'QCM'),paragraph('Réponses uniques ou multiples')),button('Nouveau QCM',newQuiz,'primary')));S.studio.quizzes.filter(q=>!q.archived).forEach(q=>panel.append(quizCard(q,true)));return;}
    const d=S.draft;
    panel.append(el('div',{class:'version-note'},`Édition ${d.id?'de la version '+d.revision:'d’un nouveau QCM'} · aucune partie existante ne change.`));
    panel.append(inputField('Titre du QCM',d.title,v=>d.title=v,{maxLength:120}));
    const settings=el('details',{class:'settings'},el('summary',{},'Configuration et barème'));
    const grading=el('select',{disabled:S.busy,'aria-label':'Barème choisi'},el('option',{value:'all-or-nothing'},'Tout-ou-rien'),el('option',{value:'partial'},'Points partiels'));grading.value=d.config.grading;grading.onchange=()=>{d.config.grading=grading.value;persistDraft();render();};
    settings.append(el('label',{class:'field'},el('span',{},'Barème choisi'),grading),paragraph(gradingText(d.config)),inputField('Seuil de réussite (%)',d.config.passingPercent,v=>d.config.passingPercent=v,{type:'number',min:0,max:100}),checkbox('Mélanger les questions',d.config.shuffleQuestions,v=>d.config.shuffleQuestions=v),checkbox('Mélanger les réponses',d.config.shuffleOptions,v=>d.config.shuffleOptions=v),checkbox('Montrer la correction après la partie',d.config.showCorrection,v=>d.config.showCorrection=v));panel.append(settings);
    const tabs=el('div',{class:'question-tabs','aria-label':'Questions à éditer'});d.questions.forEach((q,i)=>tabs.append(button(String(i+1),()=>{S.questionId=q.id;render();},q.id===S.questionId?'selected-tab':'secondary',S.busy)));tabs.append(button('+ Question',()=>{const q=blankQuestion();d.questions.push(q);S.questionId=q.id;persistDraft();render();},'secondary',S.busy||d.questions.length>=50));panel.append(tabs);
    const q=d.questions.find(q=>q.id===S.questionId)||d.questions[0];S.questionId=q.id;const index=d.questions.indexOf(q);
    panel.append(el('h3',{},'Question '+(index+1)),inputField('Énoncé',q.text,v=>q.text=v,{rows:3,maxLength:500}));
    const mode=el('select',{disabled:S.busy,'aria-label':'Type de réponse'},el('option',{value:'single'},'Une seule bonne réponse'),el('option',{value:'multiple'},'Plusieurs bonnes réponses possibles'));mode.value=q.mode;mode.onchange=()=>{q.mode=mode.value;persistDraft();render();};panel.append(el('label',{class:'field'},el('span',{},'Type de réponse'),mode),paragraph(q.mode==='single'?'Coche exactement une bonne réponse.':'Coche toutes les bonnes réponses, au moins une.'));
    q.options.forEach((option,i)=>{const row=el('div',{class:'edit-option'},el('span',{class:'option-number'},String.fromCharCode(65+i)),inputField('Texte de la réponse '+(i+1),option.text,v=>option.text=v,{maxLength:200}),checkbox('Bonne',option.correct,value=>{option.correct=value;if(value&&q.mode==='single')q.options.forEach(o=>{if(o.id!==option.id)o.correct=false;});render();}),button('Retirer',()=>{q.options=q.options.filter(o=>o.id!==option.id);persistDraft();render();},'quiet',S.busy||q.options.length<=2));panel.append(row);});
    panel.append(button('+ Réponse',()=>{q.options.push({id:uid(),text:'',correct:false});persistDraft();render();},'secondary',S.busy||q.options.length>=8),inputField('Explication après correction (facultative)',q.explanation,v=>q.explanation=v,{rows:2,maxLength:1000}));
    panel.append(el('div',{class:'row'},button('↑ Monter',()=>{[d.questions[index-1],d.questions[index]]=[d.questions[index],d.questions[index-1]];persistDraft();render();},'quiet',S.busy||index===0),button('↓ Descendre',()=>{[d.questions[index+1],d.questions[index]]=[d.questions[index],d.questions[index+1]];persistDraft();render();},'quiet',S.busy||index===d.questions.length-1),button('Retirer la question',()=>{d.questions=d.questions.filter(item=>item.id!==q.id);S.questionId=d.questions[0].id;persistDraft();render();},'quiet',S.busy||d.questions.length<=1)));
    panel.append(el('div',{class:'sticky-actions'},button(S.busy?'Enregistrement…':'Enregistrer le QCM',saveQuiz,'primary',S.busy),button('Mes brouillons / QCM',()=>{persistDraft();S.draft=null;render();},'secondary',S.busy)));
    if(d.id){panel.append(button('Recharger la version serveur (brouillon conservé)',()=>loadQuiz(d.id),'quiet',S.busy),button('Archiver ce QCM · conserver les parties',archiveQuiz,'quiet',S.busy));
      for(const key of localKeys()){if(key.startsWith('mia-studio-draft:'+d.id+':')&&key!==draftKey(d)){const cached=localRead(key);if(cached?.quiz)panel.append(button('Reprendre le brouillon de version '+cached.quiz.revision+' (conflit possible)',()=>{persistDraft();S.draft=copy(cached.quiz);S.questionId=S.draft.questions[0]?.id;render();status('Ancien brouillon restauré. Sa version doit encore correspondre au serveur pour être enregistré.');},'quiet',S.busy));}}
      panel.append(button('Recopier ce brouillon sur la version serveur actuelle',async()=>{if(S.busy)return;persistDraft();const snapshot=copy(d);S.busy=true;render();try{const fresh=await api('/api/quizzes/'+encodeURIComponent(d.id));S.draftBaseline=draftIdentity(fresh);S.draft={...snapshot,revision:fresh.revision,archived:fresh.archived};persistDraft();notice('Brouillon recopié sur la version '+fresh.revision+'. Relis toutes les questions avant Enregistrer : ce contenu remplacera celui de cette version.');}finally{S.busy=false;render();}},'quiet',S.busy));
    }
  }
  async function saveQuiz() {
    if(S.busy)return;const error=validateQuiz(S.draft);if(error){notice(error);return;}persistDraft();const source=copy(S.draft),key=draftKey(source);S.busy=true;render();
    try {const body={title:source.title,questions:source.questions,config:source.config,...(source.id?{revision:source.revision}:{})};const saved=await api(source.id?'/api/quizzes/'+encodeURIComponent(source.id):'/api/quizzes',source.id?'PUT':'POST',body);localRemove(key);S.draft=saved;S.draftBaseline=draftIdentity(saved);await refresh();status('QCM enregistré sur le serveur · version '+saved.revision+'.');}
    finally{S.busy=false;render();}
  }
  async function archiveQuiz() {const d=S.draft;if(!d?.id||S.busy)return;persistDraft();S.busy=true;render();try{await api('/api/quizzes/'+encodeURIComponent(d.id)+'/archive','POST',{revision:d.revision});S.draft=null;await refresh();status('QCM archivé. Son historique est conservé.');}finally{S.busy=false;render();}}
  async function startAttempt(quiz) {
    if(S.busy)return;S.busy=true;status('Création d’une partie pour la version '+quiz.revision+'…');
    const key='mia-studio-start:'+quiz.id+':'+quiz.revision;let requestId=S.startRequests.get(key)||localRead(key);if(typeof requestId!=='string')requestId=uid();S.startRequests.set(key,requestId);localWrite(key,requestId);
    try {const attempt=await api('/api/attempts','POST',{quizId:quiz.id,quizRevision:quiz.revision,requestId});S.startRequests.delete(key);localRemove(key);setAttempt(attempt);await refresh();status('Partie créée. Tes réponses seront enregistrées au fil du jeu.');}
    finally{S.busy=false;render();drawMap();}
  }
  function setAttempt(attempt) {S.attempt=attempt;S.answers=copy(attempt.answers);S.answerGeneration=0;S.savedGeneration=0;S.saveError=null;S.questionId=attempt.questions[0]?.id;S.view=attempt.status==='completed'?'results':'play';fitMap();}
  async function openAttempt(id,recover=false) {
    if(S.loading||S.busy)return;S.loading=true;S.busy=true;render();clearTimeout(S.saveTimer);let uncertain=false;
    try{
      try{await saveAnswers();}catch(error){if(!recover)throw error;uncertain=true;persistAnswers();}
      const attempt=await api('/api/attempts/'+encodeURIComponent(id));setAttempt(attempt);render();drawMap();
      status(uncertain?'État serveur relu ; les choix non confirmés restent conservés dans cet onglet.':'Tentative chargée · version du QCM '+attempt.quizRevision+'. Aucune nouvelle partie créée.');
    }finally{S.loading=false;S.busy=false;render();}
  }
  function renderSessions(panel) {panel.append(...heading('REPRENDRE SON CHEMIN','Mes parties en cours','Reprendre conserve la même tentative et la même version de questions.'));const rows=S.studio.attempts.filter(a=>a.status==='in-progress');rows.forEach(a=>panel.append(el('article',{class:'card'},el('h3',{},a.quizTitle),paragraph(`Version ${a.quizRevision} · ${a.answeredCount}/${a.questionCount} réponses`),button('Reprendre cette partie',()=>openAttempt(a.id),'primary'))));if(!rows.length)panel.append(paragraph('Aucune partie en cours.'),button('Choisir un QCM',()=>navigate('quizzes')));}
  function chooseAnswer(question,optionId,checked) {
    let ids=S.answers.find(a=>a.questionId===question.id)?.optionIds||[];
    ids=question.mode==='single'?[optionId]:checked?[...new Set([...ids,optionId])]:ids.filter(id=>id!==optionId);
    S.answers=S.answers.filter(a=>a.questionId!==question.id);if(ids.length)S.answers.push({questionId:question.id,optionIds:ids});
    S.answerGeneration++;persistAnswers();status('Réponses en attente d’enregistrement…');clearTimeout(S.saveTimer);S.saveTimer=setTimeout(()=>guard(saveAnswers),350);drawMap();updatePlayProgress();
  }
  function updatePlayProgress() {const count=S.answers.filter(a=>a.optionIds.length).length,p=$('play-progress');if(p)p.textContent=`${count} / ${S.attempt.questions.length} questions renseignées`;const finish=$('finish-attempt');if(finish)finish.disabled=S.busy||count!==S.attempt.questions.length;}
  function saveAnswers() {
    if(S.savePromise)return S.savePromise;
    if(!S.attempt||S.attempt.status!=='in-progress'||S.answerGeneration===S.savedGeneration)return Promise.resolve();
    S.savePromise=(async()=>{
      try {
        while(S.answerGeneration!==S.savedGeneration){const generation=S.answerGeneration,answers=copy(S.answers);status('Enregistrement des réponses…');const saved=await api('/api/attempts/'+encodeURIComponent(S.attempt.id)+'/answers','PUT',{revision:S.attempt.revision,answers});S.attempt=saved;S.savedGeneration=generation;persistAnswers();}
        localRemove(answerCacheKey(S.attempt.id));S.saveError=null;status('Réponses enregistrées sur le serveur.');
      }catch(error){S.saveError=error.message;const durable=persistAnswers();status(durable?'Réponses non confirmées · brouillon conservé dans ce navigateur.':'Réponses non confirmées · copie en mémoire dans cet onglet, à enregistrer avant fermeture.');throw error;}
      finally{S.savePromise=null;}
    })();return S.savePromise;
  }
  function renderPlay(panel) {
    const a=S.attempt;if(!a||a.status!=='in-progress'){renderSessions(panel);return;}const q=a.questions.find(q=>q.id===S.questionId)||a.questions[0],index=a.questions.indexOf(q);S.questionId=q.id;
    panel.append(...heading('PARTIE EN COURS',a.quizTitle,`Version ${a.quizRevision} · ${a.config.grading==='partial'?'barème partiel':'tout-ou-rien'}`),el('p',{id:'play-progress',class:'progress'},''));
    const cache=localRead(answerCacheKey(a.id));
    if(cache&&S.answerGeneration===S.savedGeneration&&JSON.stringify(cache.answers)!==JSON.stringify(S.answers))panel.append(el('div',{class:'recovery'},paragraph('Des réponses locales non confirmées ont été retrouvées. Le serveur reste affiché.'),button('Restaurer ces choix dans le brouillon',()=>{S.answers=copy(cache.answers);S.answerGeneration++;persistAnswers();render();drawMap();status('Choix restaurés dans le brouillon. Clique Enregistrer les réponses.');})));
    panel.append(el('p',{class:'eyebrow'},`QUESTION ${index+1} / ${a.questions.length}`),el('h3',{class:'question-heading'},q.text),paragraph(q.mode==='multiple'?'Plusieurs réponses peuvent être justes.':'Choisis une seule réponse.'));
    const ids=S.answers.find(x=>x.questionId===q.id)?.optionIds||[];
    q.options.forEach((o,i)=>panel.append(el('label',{class:'play-option'},el('input',{type:q.mode==='single'?'radio':'checkbox',name:'answer-'+q.id,value:o.id,checked:ids.includes(o.id),disabled:S.busy,'aria-label':o.text,onChange:event=>chooseAnswer(q,o.id,event.target.checked)}),el('span',{class:'option-letter','aria-hidden':'true'},String.fromCharCode(65+i)),el('span',{},o.text))));
    panel.append(el('div',{class:'row'},button('← Précédente',()=>{S.questionId=a.questions[index-1].id;render();drawMap();},'secondary',index===0||S.busy),button('Suivante →',()=>{S.questionId=a.questions[index+1].id;render();drawMap();},'secondary',index===a.questions.length-1||S.busy)));
    const finish=button('Terminer et corriger',completeAttempt,'primary',S.busy);finish.id='finish-attempt';panel.append(button('Enregistrer les réponses',saveAnswers,'quiet',S.busy),finish,button('Recharger la partie depuis le serveur',()=>openAttempt(a.id,true),'quiet',S.busy));
    panel.append(paragraph('Le résultat sera calculé par le serveur selon le questionnaire et le barème figés au début de cette partie.','note'));updatePlayProgress();
  }
  async function completeAttempt() {
    if(S.busy)return;clearTimeout(S.saveTimer);S.busy=true;render();
    try{await saveAnswers();const completed=await api('/api/attempts/'+encodeURIComponent(S.attempt.id)+'/complete','POST',{revision:S.attempt.revision});setAttempt(completed);await refresh();status('Partie terminée et résultat enregistré.');}finally{S.busy=false;render();drawMap();}
  }
  function renderResults(panel) {
    const a=S.attempt;
    if(!a||a.status!=='completed'){panel.append(...heading('PRENDRE DU RECUL','Mes résultats','Chaque résultat appartient à la version jouée, même si le QCM a changé depuis.'));const completed=S.studio.attempts.filter(a=>a.status==='completed');completed.forEach(a=>panel.append(el('article',{class:'card'},el('h3',{},a.quizTitle),paragraph(`Version ${a.quizRevision} · ${new Date(a.completedAt).toLocaleString('fr-FR')}`),button('Voir ce résultat',()=>openAttempt(a.id)))));if(!completed.length)panel.append(paragraph('Aucune partie terminée pour le moment.'));return;}
    const r=a.result;panel.append(...heading('RÉSULTAT ENREGISTRÉ',a.quizTitle,`Version ${a.quizRevision} · ${a.config.grading==='partial'?'barème partiel':'tout-ou-rien'}`),el('div',{class:'score-hero'},el('strong',{},Number(r.score).toFixed(2)+' / '+r.maxScore),el('span',{},Number(r.percent).toFixed(2)+' %'),el('p',{},r.passed?'Objectif atteint !':'Un nouvel essai pour progresser')));
    if(r.details?.length)r.details.forEach(detail=>{const q=a.questions.find(q=>q.id===detail.questionId);const names=ids=>ids.map(id=>q?.options.find(o=>o.id===id)?.text||id).join(', ');panel.append(el('details',{class:'correction'},el('summary',{},`${detail.isCorrect?'✓':'○'} ${q?.text||detail.questionId} · ${Number(detail.points).toFixed(2)} / 1`),paragraph('Tes choix : '+names(detail.selectedOptionIds)),paragraph('Bonnes réponses : '+names(detail.correctOptionIds)),detail.explanation?paragraph(detail.explanation):null));});else panel.append(paragraph('La correction détaillée est masquée selon la configuration de cette partie.'));
    panel.append(button('Tous mes résultats',()=>{S.attempt=null;render();}),button('Voir les classements',()=>navigate('scores'),'primary'));
  }
  let scoresToken=0;
  function renderScores(panel) {
    const token=++scoresToken;panel.append(...heading('MES REPÈRES','Classements','Chaque version et chaque barème ont leur classement. Les scores de questionnaires différents ne sont pas mélangés.'),paragraph('Lecture des résultats enregistrés…'));
    api('/api/scores').then(data=>{if(S.view!=='scores'||token!==scoresToken)return;panel.lastChild.remove();if(!data.groups.length)panel.append(paragraph('Aucun score enregistré.'));data.groups.forEach(group=>{const section=el('section',{class:'score-group'},el('h3',{},group.quizTitle),paragraph(`Version ${group.quizRevision} · ${group.grading==='partial'?'partiel':'tout-ou-rien'}`));group.entries.forEach((entry,i)=>section.append(el('div',{class:'score-row'},el('strong',{},String(i+1).padStart(2,'0')),el('div',{},el('b',{},entry.playerName),el('small',{},new Date(entry.completedAt).toLocaleString('fr-FR'))),el('span',{},Number(entry.percent).toFixed(2)+' %'),button('Voir',()=>openAttempt(entry.attemptId),'quiet'))));panel.append(section);});}).catch(error=>{if(token===scoresToken&&S.view==='scores')notice(error.message);});
  }
  function renderAccount(panel) {
    panel.append(...heading('TON ESPACE','Un seul compte local','Ce nom accompagne tes nouvelles parties. Les anciennes gardent leur nom enregistré au départ.'));
    const input=el('input',{value:S.studio.account.displayName,maxLength:60,disabled:S.busy});panel.append(el('label',{class:'field'},el('span',{},'Nom affiché'),input),button('Enregistrer le nom',async()=>{if(S.busy)return;if(!input.value.trim()){notice('Le nom ne peut pas être vide.');return;}const displayName=input.value;S.busy=true;render();try{await api('/api/account','PUT',{displayName});await refresh();status('Nom local enregistré.');}finally{S.busy=false;render();}},'primary',S.busy),paragraph('Il ne s’agit pas d’une connexion cloud. Aucun mot de passe n’est demandé.','note'));
  }
  window.addEventListener('beforeunload',event=>{if(S.draft)persistDraft();const draftDirty=S.draft&&draftIdentity(S.draft)!==S.draftBaseline,answerDirty=S.attempt&&S.answerGeneration!==S.savedGeneration;if(answerDirty)persistAnswers();if(draftDirty||answerDirty){event.preventDefault();event.returnValue='';}});
  render();drawMap();guard(async()=>{await refresh();status('Prêt. Choisis une action ; aucune partie ne démarre automatiquement.');render();});
})();
