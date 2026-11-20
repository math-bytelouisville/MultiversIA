'use strict';
(() => {
  const copy = value => JSON.parse(JSON.stringify(value));
  const config = () => ({grading:'all-or-nothing',passingPercent:60,shuffleQuestions:false,shuffleOptions:false,showCorrection:true,instructions:'',conclusion:'',accentColor:'#397866',allowUnanswered:false,answerLayout:'list',contentWidth:'standard',textAlign:'left'});
  const quizConfig = value => ({...config(),...value});
  const uid = () => crypto.randomUUID();
  const blankQuestion = () => ({id:uid(),text:'',mode:'single',options:[{id:uid(),text:'',correct:true},{id:uid(),text:'',correct:false}],explanation:''});
  const draftKey = quiz => quiz.localDraftId?`mia-studio-draft:recovery:${quiz.localDraftId}`:`mia-studio-draft:${quiz.id || 'new'}:${quiz.revision || 0}`;
  const draftIdentity = quiz => JSON.stringify({id:quiz.id,revision:quiz.revision,title:quiz.title,questions:quiz.questions,config:quizConfig(quiz.config)});
  const semanticLevel = zoom => zoom < .7 ? 0 : zoom < 1.4 ? 1 : 2;
  const IMPORT_MAX_BYTES=10*1024*1024, EXPORT_FORMAT='multiversia-qcm', EXPORT_VERSION=2;
  const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const keysOnly=(value,allowed,required=allowed)=>plain(value)&&Object.keys(value).every(key=>allowed.includes(key))&&required.every(key=>Object.hasOwn(value,key));
  const boundedText=(value,max)=>typeof value==='string'&&value.length<=max;
  const validId=value=>typeof value==='string'&&value.length>=1&&value.length<=80&&!/[^A-Za-z0-9-]/.test(value);
  const validAccent=value=>typeof value==='string'&&value.length===7&&/^#[0-9a-fA-F]{6}$/.test(value);
  const validMediaId=value=>typeof value==='string'&&value.length===64&&!/[^a-f0-9]/.test(value);
  function validateImage(image){
    if(image===undefined||image===null)return null;
    if(!keysOnly(image,['id','alt','position','size','fit'],['id','alt'])||!validMediaId(image.id)||!boundedText(image.alt,300)||!image.alt.trim()||!['above','below','left','right'].includes(image.position===undefined?'above':image.position)||!['small','medium','large'].includes(image.size===undefined?'medium':image.size)||!['contain','cover'].includes(image.fit===undefined?'contain':image.fit))return 'Image invalide : référence locale, description non vide (300 caractères maximum) et disposition requises.';
    return null;
  }
  function imageIds(quiz){return [...new Set(quiz.questions.flatMap(q=>[q.image,...q.options.map(o=>o.image)]).filter(Boolean).map(image=>image.id))];}
  function validateContent(quiz,strict=false) {
    if(!plain(quiz)||!boundedText(quiz.title,120)||!Array.isArray(quiz.questions)||quiz.questions.length<1||quiz.questions.length>50||!plain(quiz.config))return 'Structure du QCM invalide (titre, questions ou configuration).';
    if(strict&&!keysOnly(quiz,['title','questions','config']))return 'Le document doit contenir uniquement le contenu du QCM, sans identifiant de publication ni historique.';
    const required=['grading','passingPercent','shuffleQuestions','shuffleOptions','showCorrection'],allowed=[...required,'instructions','conclusion','accentColor','allowUnanswered','answerLayout','contentWidth','textAlign'];
    if(!keysOnly(quiz.config,allowed,required))return 'Configuration inconnue ou incomplète.';
    const c=quizConfig(quiz.config);
    if(!['all-or-nothing','partial'].includes(c.grading)||!Number.isFinite(c.passingPercent)||c.passingPercent<0||c.passingPercent>100)return 'Choisis un barème valide et un seuil de 0 à 100 %.';
    if(['shuffleQuestions','shuffleOptions','showCorrection','allowUnanswered'].some(key=>typeof c[key]!=='boolean'))return 'Les options de configuration doivent être des booléens.';
    if(!boundedText(c.instructions,2000)||!boundedText(c.conclusion,2000)||!validAccent(c.accentColor))return 'Consignes et conclusion : 2 000 caractères maximum ; couleur au format #RRGGBB.';
    if(!['list','grid'].includes(c.answerLayout)||!['standard','wide'].includes(c.contentWidth)||!['left','center'].includes(c.textAlign))return 'Disposition du contenu invalide.';
    const questionIds=new Set();
    for(const q of quiz.questions){
      if(!keysOnly(q,['id','text','mode','options','explanation','image'],['id','text','mode','options','explanation'])||!validId(q.id)||questionIds.has(q.id)||!boundedText(q.text,500)||!boundedText(q.explanation,1000)||!['single','multiple'].includes(q.mode)||!Array.isArray(q.options)||q.options.length<2||q.options.length>8)return 'Question invalide, identifiant répété ou limite dépassée.';
      const imageError=validateImage(q.image);if(imageError)return imageError;
      questionIds.add(q.id);const optionIds=new Set();
      for(const o of q.options){if(!keysOnly(o,['id','text','correct','image'],['id','text','correct'])||!validId(o.id)||optionIds.has(o.id)||questionIds.has(o.id)||!boundedText(o.text,200)||typeof o.correct!=='boolean')return 'Réponse invalide, identifiant répété ou limite dépassée.';const error=validateImage(o.image);if(error)return error;optionIds.add(o.id);questionIds.add(o.id);}
    }
    return null;
  }
  function validateQuiz(quiz) {
    const shape=validateContent(quiz);if(shape)return shape;
    if (!quiz.title.trim() || quiz.title.length > 120) return 'Donne un titre de 1 à 120 caractères.';
    if (quiz.questions.length < 1 || quiz.questions.length > 50) return 'Il faut de 1 à 50 questions.';
    for (let i=0;i<quiz.questions.length;i++) {
      const q=quiz.questions[i],n=q.options.filter(o=>o.correct).length;
      if (!q.text.trim() || q.text.length>500) return `Question ${i+1} : écris un énoncé de 1 à 500 caractères.`;
      if (q.options.length<2 || q.options.length>8 || q.options.some(o=>(!o.text.trim()&&!o.image) || o.text.length>200)) return `Question ${i+1} : il faut 2 à 8 réponses avec du texte ou une image décrite (texte de 200 caractères au plus).`;
      if ((q.mode==='single' && n!==1) || (q.mode==='multiple' && n<1)) return `Question ${i+1} : coche ${q.mode==='single'?'exactement une':'au moins une'} bonne réponse.`;
      if (q.explanation.length>1000) return `Question ${i+1} : explication limitée à 1 000 caractères.`;
    }
    if (!Number.isFinite(quiz.config.passingPercent) || quiz.config.passingPercent<0 || quiz.config.passingPercent>100) return 'Le seuil de réussite doit aller de 0 à 100 %.';
    return null;
  }
  function restorableDraft(value){
    if(!plain(value)||!plain(value.config))return false;
    // A deliberately cleared percentage is an editable draft, not a corrupt cache.
    const candidate={...value,config:{...value.config,passingPercent:value.config.passingPercent===null?0:value.config.passingPercent}};
    return validateContent(candidate)===null;
  }
  function restorableAnswers(answers,questions){
    if(!Array.isArray(answers)||!Array.isArray(questions)||answers.length>questions.length)return false;
    const seen=new Set();return answers.every(answer=>{
      if(!keysOnly(answer,['questionId','optionIds'])||seen.has(answer.questionId)||!Array.isArray(answer.optionIds)||answer.optionIds.length<1)return false;
      const question=questions.find(q=>q.id===answer.questionId);if(!question||answer.optionIds.length>question.options.length||(question.mode==='single'&&answer.optionIds.length>1))return false;
      seen.add(answer.questionId);return new Set(answer.optionIds).size===answer.optionIds.length&&answer.optionIds.every(id=>typeof id==='string'&&question.options.some(option=>option.id===id));
    });
  }
  function creationSignature(path,body){
    const content=path==='/api/drafts'?body.content:body;
    if(!['/api/drafts','/api/quizzes'].includes(path)||validateContent(content)!==null)throw new Error('Contenu de création invalide.');
    if(path==='/api/drafts'&&(!keysOnly(body,['name','content'])||!boundedText(body.name,100)||!body.name.trim()))throw new Error('Nom du brouillon invalide.');
    if(path==='/api/quizzes'&&!keysOnly(body,['title','questions','config']))throw new Error('Contenu de publication invalide.');
    const logical={title:content.title,config:quizConfig(content.config),questions:content.questions.map(question=>({text:question.text,mode:question.mode,explanation:question.explanation,image:question.image??null,options:question.options.map(option=>({text:option.text,correct:option.correct,image:option.image??null}))}))};
    return JSON.stringify({path,name:path==='/api/drafts'?body.name:null,content:logical});
  }
  function duplicateQuestion(question){const cloned=copy(question);cloned.id=uid();cloned.options.forEach(option=>option.id=uid());return cloned;}
  function moveItem(items,from,to){if(!Number.isInteger(from)||!Number.isInteger(to)||from<0||to<0||from>=items.length||to>=items.length)throw new Error('Déplacement invalide.');const next=[...items];const [item]=next.splice(from,1);next.splice(to,0,item);return next;}
  function parsePercent(value){if(typeof value!=='string'||!/^\d+(?:[.,]\d+)?$/.test(value.trim()))return null;const n=Number(value.trim().replace(',','.'));return Number.isFinite(n)&&n>=0&&n<=100?n:null;}
  function validateAssets(quiz,assets){
    const refs=imageIds(quiz);if(!Array.isArray(assets)||assets.length!==refs.length)throw new Error('Les images embarquées doivent correspondre exactement aux références du QCM.');
    const seen=new Set();for(const asset of assets){if(!keysOnly(asset,['id','dataUrl'])||!validMediaId(asset.id)||typeof asset.dataUrl!=='string'||asset.dataUrl.length>710000||!/^data:image\/(png|jpeg|webp);base64,/.test(asset.dataUrl)||seen.has(asset.id)||!refs.includes(asset.id))throw new Error('Image embarquée inconnue, répétée ou invalide.');seen.add(asset.id);}
  }
  function exportDocument(quiz,assets=[]){const content={title:quiz.title,questions:copy(quiz.questions),config:quizConfig(quiz.config)};const error=validateContent(content,true);if(error)throw new Error(error);validateAssets(content,assets);const text=JSON.stringify({format:EXPORT_FORMAT,version:EXPORT_VERSION,quiz:content,assets},null,2);if(new TextEncoder().encode(text).length>IMPORT_MAX_BYTES)throw new Error('Le document dépasse 10 Mio.');return text;}
  function parseImportDocument(text){
    if(typeof text!=='string'||new TextEncoder().encode(text).length>IMPORT_MAX_BYTES)throw new Error('Le fichier JSON doit faire au plus 10 Mio.');
    let document;try{document=JSON.parse(text);}catch{throw new Error('Fichier JSON illisible.');}
    if(!plain(document)||document.format!==EXPORT_FORMAT||![1,2].includes(document.version)||!keysOnly(document,document.version===1?['format','version','quiz']:['format','version','quiz','assets']))throw new Error('Format ou version du document QCM non pris en charge.');
    const error=validateContent(document.quiz,true);if(error)throw new Error(error);
    if(document.version===1&&imageIds(document.quiz).length)throw new Error('Les images exigent un document de version 2 avec ses fichiers embarqués.');
    const assets=document.version===1?[]:document.assets;validateAssets(document.quiz,assets);
    return {content:{title:document.quiz.title,questions:document.quiz.questions.map(duplicateQuestion),config:quizConfig(document.quiz.config)},assets};
  }
  function importDocument(text){const bundle=parseImportDocument(text);if(bundle.assets.length)throw new Error('Utilise l’import avec fichiers embarqués pour ce document illustré.');return bundle.content;}
  async function verifyAssetHashes(assets,decode){for(const asset of assets){const {bytes}=decode(asset.dataUrl);const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),byte=>byte.toString(16).padStart(2,'0')).join('');if(hash!==asset.id)throw new Error('Le contenu d’une image ne correspond pas à son empreinte. Import annulé.');}}
  const progress = attempt => ({answered:attempt.answers.filter(a=>a.optionIds.length).length,total:attempt.questions.length});
  const core={copy,config,quizConfig,blankQuestion,draftKey,draftIdentity,semanticLevel,validateQuiz,validateContent,validateImage,restorableDraft,restorableAnswers,creationSignature,imageIds,progress,duplicateQuestion,moveItem,parsePercent,exportDocument,importDocument,parseImportDocument,verifyAssetHashes,IMPORT_MAX_BYTES,EXPORT_FORMAT,EXPORT_VERSION};
  if (typeof document==='undefined') { if(typeof module!=='undefined')module.exports=core; return; }
  globalThis.StudioCore=core;
  const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
  const S={studio:null,view:'home',draft:null,draftBaseline:null,namedDraft:null,draftName:'',preview:null,questionId:null,attempt:null,answers:[],answerGeneration:0,savedGeneration:0,savePromise:null,saveTimer:null,startRequests:new Map(),busy:false,loading:false,saveError:null,zoom:1,x:0,y:0,mapDrag:null};
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
  function setQuizAccent(value){const hex=validAccent(value)?value:'#397866';if(globalThis.QcmPreferences?.setAccent)globalThis.QcmPreferences.setAccent(hex);else $('panel').style.setProperty('--quiz-accent',hex);$('panel-content').querySelector('h2')?.classList.add('quiz-accent-heading');}
  function focusElement(id){const node=$(id);if(node){if(!node.matches('input,button,select,textarea,a'))node.tabIndex=-1;node.focus();}}
  function authorText(panel,text,kind,title){if(typeof text==='string'&&text.trim())panel.append(el('section',{class:kind},el('h3',{},title),paragraph(text)));}
  function applyLayout(settings){const c=quizConfig(settings);$('panel').dataset.qcmAnswerLayout=['list','grid'].includes(c.answerLayout)?c.answerLayout:'list';$('panel').dataset.qcmContentWidth=['standard','wide'].includes(c.contentWidth)?c.contentWidth:'standard';$('panel').dataset.qcmTextAlign=['left','center'].includes(c.textAlign)?c.textAlign:'left';}
  function mediaModule(){if(!globalThis.QcmMedia)throw new Error('Le module des images est indisponible. Recharge la page avant de modifier les images.');return globalThis.QcmMedia;}
  function imageContent(reference,node){if(!reference)return node;if(!globalThis.QcmMedia)return el('div',{},node,paragraph('Image indisponible : '+reference.alt));return globalThis.QcmMedia.renderContent(reference,node);}
  function choiceContent(option,fallback='Réponse à compléter'){
    if(option.image&&!option.text.trim())return globalThis.QcmMedia?globalThis.QcmMedia.render(option.image):paragraph('Image indisponible : '+option.image.alt);
    return imageContent(option.image,el('span',{},option.text||fallback));
  }
  function mediaControls(target,draft,prefix){
    if(!globalThis.QcmMedia)return paragraph('Module des images indisponible. Le contenu existant est conservé.');
    let changed=false,disabled=[],widget;
    widget=globalThis.QcmMedia.controls(target.image??null,(next,options={})=>{
      if(S.draft!==draft)return;target.image=next;persistDraft();changed=true;if(!S.busy&&options.render!==false)render();
    },{idPrefix:prefix,disabled:S.busy,onBusy:busy=>{
      if(busy){S.busy=true;changed=false;disabled=[...$('panel-content').querySelectorAll('button,input,select,textarea')].filter(node=>!widget.contains(node)).map(node=>[node,node.disabled]);disabled.forEach(([node])=>node.disabled=true);}
      else {S.busy=false;if(changed)render();else disabled.forEach(([node,wasDisabled])=>node.disabled=wasDisabled);}
    }});return widget;
  }
  const localMemory=new Map();
  function localRead(key) {if(localMemory.has(key))return copy(localMemory.get(key));let raw;try{raw=localStorage.getItem(key);}catch{return null;}if(raw===null)return null;try{const value=JSON.parse(raw);if(value===null)return {invalidLocalJson:true};localMemory.set(key,copy(value));return value;}catch{return {invalidLocalJson:true};}}
  function preserveMalformedLocal(key) {
    try {
      const raw=localStorage.getItem(key);if(raw===null)return true;
      try{JSON.parse(raw);return true;}catch{/* Keep the original bytes before replacing an unreadable cache. */}
      const quarantineKey='mia-studio-quarantine:'+uid();
      localStorage.setItem(quarantineKey,JSON.stringify({originalKey:key,raw,savedAt:new Date().toISOString()}));
      notice('Des données locales illisibles ont été conservées dans une copie de secours.');return true;
    }catch{notice('Stockage indisponible : le cache original est conservé. Les modifications restent en mémoire dans cet onglet.');return false;}
  }
  function localWrite(key,value) {localMemory.set(key,copy(value));if(!preserveMalformedLocal(key))return false;try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{return false;}}
  function localRemove(key) {localMemory.delete(key);if(!preserveMalformedLocal(key))return;try{localStorage.removeItem(key);}catch{/* The server remains authoritative. */}}
  function localKeys() {const keys=new Set(localMemory.keys());try{for(let i=0;i<localStorage.length;i++)keys.add(localStorage.key(i));}catch{/* In-memory drafts remain accessible. */}return [...keys];}
  async function api(path,method='GET',body) {
    let response;
    try {response=await fetch(path,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:AbortSignal.timeout(20000)});}
    catch {throw new Error(method==='GET'?'Serveur injoignable. Les données affichées peuvent être anciennes.':'Envoi non confirmé. Les données sont conservées ici ; recharge l’état du serveur avant de réessayer.');}
    let data;try{data=await response.json();}catch{throw new Error('Réponse serveur illisible. Aucun succès n’est confirmé.');}
    if(!response.ok){const error=new Error(data.message || data.error || `Erreur ${response.status}`);error.code=response.status;throw error;}
    return data;
  }
  async function createWithReceipt(path,body){
    const signature=creationSignature(path,body),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(signature))),byte=>byte.toString(16).padStart(2,'0')).join('');
    const key='mia-studio-create:'+hash,previous=localRead(key);
    let receipt=previous;
    if(receipt!==null){
      let valid=false;try{valid=keysOnly(receipt,['requestId','body'])&&typeof receipt.requestId==='string'&&receipt.requestId.length===36&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(receipt.requestId)&&creationSignature(path,receipt.body)===signature;}catch{/* Refuse an ambiguous creation receipt. */}
      if(!valid)throw new Error('Le reçu local de cette création est incohérent. Aucun nouvel envoi effectué ; vérifie la bibliothèque du serveur.');
    }else receipt={requestId:uid(),body:copy(body)};
    const durable=localWrite(key,receipt);
    if(!durable)notice('Stockage local indisponible : le reçu de création reste seulement dans cet onglet. En cas d’envoi non confirmé, réessaie ici ; après fermeture, vérifie la bibliothèque avant de recréer.');
    try {
      const saved=await api(path,'POST',{...receipt.body,requestId:receipt.requestId});
      if(!plain(saved)||!validId(saved.id)||!Number.isSafeInteger(saved.revision)||saved.revision<1||validateContent(path==='/api/drafts'?saved.content:saved)!==null)throw new Error('Réponse de création incohérente. Le reçu est conservé pour un nouvel essai.');
      localRemove(key);return saved;
    }catch(error){if(!durable&&error instanceof Error)error.message+=' Le reçu reste en mémoire dans cet onglet seulement : ne le ferme pas avant vérification du serveur.';throw error;}
  }
  async function guard(action) {notice('');try{await action();}catch(error){notice((error.code===409?'Conflit de version : ':'')+error.message);$('connection-state').textContent='Dernière action non confirmée';}}
  async function refresh() {
    const data=await api('/api/studio');S.studio=data;$('account').textContent=data.account.displayName;
    $('connection-state').textContent='Données locales vérifiées à '+new Date().toLocaleTimeString('fr-FR');drawMap();return data;
  }
  function persistDraft() {
    if(!S.draft)return true;
    if(S.namedDraft){
      const key=namedDraftKey(S.namedDraft);
      if(!draftChanged()){localRemove(key);return true;}
      const stored=localWrite(key,{content:S.draft,name:S.draftName});
      status(stored?'Modifications en attente. Enregistre le brouillon pour le retrouver après redémarrage.':'Modifications en mémoire. Enregistre le brouillon avant de quitter.');
      return stored;
    }
    if(draftIdentity(S.draft)===S.draftBaseline){localRemove(draftKey(S.draft));return true;}
    const durable=localWrite(draftKey(S.draft),{quiz:S.draft,savedAt:new Date().toISOString()});status(durable?'Brouillon local · non publié. Enregistre pour créer une nouvelle version.':'Brouillon en mémoire seulement : stockage local indisponible. Enregistre avant de fermer cet onglet.');
    return durable;
  }
  function answerCacheKey(id) {return 'mia-studio-answers:'+id;}
  function persistAnswers() {if(S.attempt)return localWrite(answerCacheKey(S.attempt.id),{revision:S.attempt.revision,answers:S.answers});return false;}
  async function navigate(view) {
    if(S.loading || S.busy)return;
    if(S.attempt && S.attempt.status==='in-progress' && S.answerGeneration!==S.savedGeneration) {
      try{await saveAnswers();}catch{notice('Réponses non confirmées : reste ici pour les enregistrer ou recharger la partie.');return;}
    }
    if(S.draft)persistDraft();S.loading=true;S.busy=true;render();
    try{if(S.namedDraft)await writeNamedDraft();await refresh();S.view=view;notice('');render();drawMap();status('Vue actualisée depuis le serveur. Les brouillons restent séparés.');}
    finally{S.loading=false;S.busy=false;render();}
  }
  const poi=[
    {id:'editor',x:205,y:250,title:'Game Editor',icon:'✎',sub:'Créer et imaginer',hint:'Un QCM à ton image'},
    {id:'quizzes',x:535,y:200,title:'Mes QCM',icon:'▦',sub:'Choisir une aventure',hint:'Questions uniques ou multiples'},
    {id:'sessions',x:740,y:430,title:'Mes parties',icon:'▷',sub:'Reprendre son chemin',hint:'Chaque réponse sauvegardée'},
    {id:'results',x:475,y:575,title:'Résultats',icon:'✓',sub:'Comprendre et progresser',hint:'Le détail de chaque tentative'},
    {id:'scores',x:180,y:520,title:'Classements',icon:'☆',sub:'Voir ses progrès',hint:'Une liste par version et barème'}
  ];
  function svg(tag,attrs,text) {const n=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs)){if(k==='fontSize'){if(!Number.isFinite(v)||v<1||v>64)throw new Error('Taille SVG invalide.');n.style.fontSize=v+'px';}else n.setAttribute(k,String(v));}if(text!==undefined)n.textContent=text;return n;}
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
      group.append(svg('circle',{r:39*enlargement,class:'poi-halo'}),svg('circle',{r:28*enlargement,class:'poi-core'}),svg('text',{'text-anchor':'middle',y:8*enlargement,class:'poi-icon',fontSize:25*enlargement},node.icon),svg('text',{'text-anchor':'middle',y:63*enlargement,class:'poi-title',fontSize:16*enlargement},node.title));
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
    const panel=$('panel-content'),openSettings=panel.dataset.view===S.view?[...panel.querySelectorAll('details[open]')].map(node=>node.querySelector('summary')?.textContent):[];panel.replaceChildren();panel.dataset.view=S.view;setQuizAccent();applyLayout();document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-current',b.dataset.view===S.view?'page':'false'));
    if(!S.studio){panel.append(...heading('ESPACE LOCAL','Préparons la carte','Aucune partie ne démarre avant ton choix.'),button('Recharger',async()=>{await refresh();render();}));return;}
    ({home:renderHome,editor:renderEditor,preview:renderPreview,quizzes:renderQuizzes,sessions:renderSessions,play:renderPlay,results:renderResults,scores:renderScores,account:renderAccount}[S.view]||renderHome)(panel);
    panel.querySelectorAll('details').forEach(node=>{if(openSettings.includes(node.querySelector('summary')?.textContent))node.open=true;});
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
    setQuizAccent(quiz.config.accentColor);applyLayout(quiz.config);authorText(panel,quiz.config.instructions,'quiz-instructions','Consignes');panel.append(paragraph(quiz.config.allowUnanswered?'Tu peux laisser des questions sans réponse : elles compteront zéro point.':'Chaque question doit recevoir une réponse avant de terminer.'));
    panel.append(button('Commencer une nouvelle partie',()=>startAttempt(quiz),'primary',quiz.archived),button('Retour aux QCM',()=>navigate('quizzes')));if(quiz.archived)panel.append(paragraph('Ce QCM est archivé. Les anciennes parties restent disponibles.'));
  }
  function gradingText(c) {return c.grading==='partial'?'Barème partiel : sur les questions multiples, chaque bonne sélection apporte sa fraction des points et chaque mauvaise en retire une fraction, sans descendre sous zéro. Une question simple vaut 0 ou 1.':'Tout-ou-rien : une question vaut 1 point si les choix correspondent exactement aux bonnes réponses, sinon 0.';}
  async function loadQuiz(id) {
    if(S.loading||S.busy)return;if(S.draft)persistDraft();S.loading=true;S.busy=true;render();
    try{const quiz=await api('/api/quizzes/'+encodeURIComponent(id));const cached=localRead(draftKey(quiz));const canRestore=restorableDraft(cached?.quiz)&&cached.quiz.id===quiz.id&&cached.quiz.revision===quiz.revision;S.namedDraft=null;S.draftBaseline=draftIdentity(quiz);S.draft=canRestore?copy(cached.quiz):copy(quiz);S.questionId=S.draft.questions[0]?.id;S.view='editor';render();drawMap();status(draftIdentity(S.draft)!==S.draftBaseline?'Brouillon local de cette version restauré, non publié.':'Version '+quiz.revision+' chargée depuis le serveur.');if(cached&&!canRestore)notice('Le brouillon local est invalide ou ne correspond pas à cette version. La version du serveur est affichée ; le cache n’a pas été supprimé.');}
    finally{S.loading=false;S.busy=false;render();}
  }
  function newQuiz() {const cached=localRead('mia-studio-draft:new:0');if(cached&&!restorableDraft(cached.quiz)){notice('L’ancien brouillon local est invalide. Il est conservé ; tu peux créer un autre brouillon nommé.');return;}S.namedDraft=null;S.draftBaseline=null;S.draft=cached?.quiz||{title:'Mon nouveau QCM',questions:[blankQuestion()],config:config()};S.questionId=S.draft.questions[0].id;render();persistDraft();}
  const namedDraftKey=d=>`mia-studio-named-draft:${d.id}:${d.revision}`;
  const draftContent=({title,questions,config:settings})=>copy({title,questions,config:quizConfig(settings)});
  function draftChanged() {
    return !!S.draft && (draftIdentity(S.draft)!==S.draftBaseline || !!S.namedDraft && S.draftName!==S.namedDraft.name);
  }
  function openNamedDraft(draft, restoreLocal=false) {
    const cache=restoreLocal?localRead(namedDraftKey(draft)):null;
    const validCache=cache&&restorableDraft(cache.content)&&(cache.name===undefined||boundedText(cache.name,100));
    S.namedDraft=draft;
    S.draftName=validCache?(cache.name??draft.name):draft.name;
    S.draft=copy(validCache?cache.content:draft.content);
    S.draft.config=quizConfig(S.draft.config);
    S.draftBaseline=draftIdentity(draft.content);
    S.questionId=S.draft.questions[0]?.id;
    S.view='editor';
    if(cache&&!validCache)notice('Le cache local de ce brouillon est invalide. Le brouillon du serveur est chargé ; le cache est conservé.');
  }
  function rememberNamedDraft({id,name,revision}) {
    S.studio.drafts=[...S.studio.drafts.filter(d=>d.id!==id),{id,name,revision}];
  }
  async function createNamedDraft(name,content) {
    const draft=await createWithReceipt('/api/drafts',{name,content});
    rememberNamedDraft(draft);
    return draft;
  }
  async function writeNamedDraft() {
    if(!draftChanged())return;
    const previous=S.namedDraft, question=S.questionId;
    const saved=await api('/api/drafts/'+encodeURIComponent(previous.id),'PUT',{revision:previous.revision,name:S.draftName,content:draftContent(S.draft)});
    localRemove(namedDraftKey(previous));
    rememberNamedDraft(saved);
    openNamedDraft(saved);
    S.questionId=question;
  }
  async function changeDraft(action) {
    if(S.busy)return;
    S.busy=true;
    render();
    try{await action();}
    finally{S.busy=false;render();}
  }
  function renderTransferControls(panel){
    const file=el('input',{id:'quiz-import-file',type:'file',accept:'.json,application/json',required:true,disabled:S.busy});
    const name=el('input',{id:'quiz-import-name',value:'QCM importé',required:true,maxLength:100,disabled:S.busy});
    const confirm=S.draft?el('input',{id:'quiz-import-confirm',type:'checkbox',required:true,disabled:S.busy}):null;
    const submit=el('button',{id:'quiz-import-submit',type:'submit',class:'secondary',disabled:S.busy},'Importer dans un nouveau brouillon');
    const form=el('form',{onSubmit:event=>{
      event.preventDefault();const selected=file.files?.[0],draftName=name.value.trim();
      guard(async()=>{
        if(!selected)throw new Error('Choisis un fichier JSON.');
        if(selected.size>IMPORT_MAX_BYTES)throw new Error('Le fichier dépasse 10 Mio.');
        if(!draftName||draftName.length>100)throw new Error('Donne un nom de 1 à 100 caractères au nouveau brouillon.');
        if(confirm&&!confirm.checked)throw new Error('Confirme l’ouverture du nouveau brouillon. Le brouillon courant sera conservé.');
        await changeDraft(async()=>{
          const bundle=parseImportDocument(await selected.text());
          if(bundle.assets.length)await verifyAssetHashes(bundle.assets,mediaModule().validateDataUrl);
          const durable=persistDraft();if(S.namedDraft)await writeNamedDraft();else if(S.draft&&!durable)throw new Error('Import suspendu : le brouillon courant est seulement en mémoire. Crée une copie nommée ou exporte-le avant de quitter cette édition.');
          for(const asset of bundle.assets){const uploaded=await mediaModule().uploadDataUrl(asset.dataUrl);if(uploaded.id!==asset.id)throw new Error('Empreinte retournée différente. Le brouillon courant est conservé.');}
          const imported=await createNamedDraft(draftName,bundle.content);openNamedDraft(imported);
          status('Import créé dans un nouveau brouillon. Aucun QCM ni aucune partie modifié.');
        });focusElement('quiz-instructions');
      });
    }},el('label',{class:'field'},'Fichier QCM JSON (10 Mio maximum, images embarquées)',file),el('label',{class:'field'},'Nom du nouveau brouillon',name),
      confirm?el('label',{class:'check'},confirm,'Ouvrir le nouveau brouillon après import ; conserver le brouillon courant.'):null,submit);
    const section=el('details',{class:'settings'},el('summary',{},'Importer / exporter un QCM'),paragraph('Document éditeur : contient les bonnes réponses et les explications. L’import ne publie rien et crée toujours un nouveau brouillon.'),form);
    if(S.draft){const download=button('Exporter le contenu éditeur (JSON + images)',()=>changeDraft(async()=>{
      const content=draftContent(S.draft),error=validateContent(content,true);if(error)throw new Error(error);
      const assets=[];for(const id of imageIds(content))assets.push(await mediaModule().exportAsset(id));
      if(assets.length)await verifyAssetHashes(assets,mediaModule().validateDataUrl);
      const text=exportDocument(content,assets),url=URL.createObjectURL(new Blob([text],{type:'application/json;charset=utf-8'}));
      const anchor=el('a',{href:url,download:(content.title.replace(/[<>:"/\\|?*\x00-\x1f]/g,'-').slice(0,80)||'qcm')+'.json'},'Télécharger');
      anchor.hidden=true;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);status('Document éditeur exporté ; les données du serveur sont inchangées.');
    }),'secondary',S.busy);download.id='quiz-export';section.append(download);}
    panel.append(section);
  }
  function renderNamedDraftList(panel) {
    const name=el('input',{required:true,maxLength:100,placeholder:'Nom du brouillon'});
    panel.append(el('form',{class:'card',onSubmit:event=>{
      event.preventDefault();
      guard(()=>changeDraft(async()=>{
        const draft=await createNamedDraft(name.value,{title:'Mon nouveau QCM',questions:[blankQuestion()],config:config()});
        openNamedDraft(draft);
        status('Brouillon créé. Rien n’a été publié.');
      }));
    }},el('label',{class:'field'},'Nom du brouillon',name),el('button',{type:'submit',class:'primary',disabled:S.busy},'Nouveau brouillon')));
    S.studio.drafts.forEach(draft=>panel.append(el('article',{class:'card'},el('h3',{},draft.name),button('Ouvrir le brouillon',()=>changeDraft(async()=>{
      openNamedDraft(await api('/api/drafts/'+encodeURIComponent(draft.id)),true);
      status(draftChanged()?'Modifications locales restaurées. Enregistre le brouillon pour les conserver.':'Brouillon chargé.');
    }),'secondary',S.busy))));
    if(localRead('mia-studio-draft:new:0')?.quiz)panel.append(button('Reprendre l’ancien brouillon local',newQuiz,'quiet',S.busy));
    renderLocalRecoveries(panel);
    renderTransferControls(panel);
  }
  function renderLocalRecoveries(panel){
    const archived=new Set(S.studio.quizzes.filter(quiz=>quiz.archived).map(quiz=>quiz.id));
    for(const key of localKeys()){
      if(typeof key!=='string'||!key.startsWith('mia-studio-draft:'))continue;
      const cached=localRead(key),content=cached?.quiz;if(!restorableDraft(content))continue;
      const independent=key.startsWith('mia-studio-draft:recovery:')&&draftKey(content)===key;
      const fromArchive=content.id&&archived.has(content.id)&&draftKey(content)===key;
      if(!independent&&!fromArchive)continue;
      panel.append(el('article',{class:'card'},el('h3',{},content.title||'Copie locale sans titre'),paragraph(fromArchive?'Ce QCM est archivé. Ses modifications locales peuvent devenir une copie indépendante.':'Copie locale indépendante, non publiée.'),button(fromArchive?'Récupérer une copie du brouillon archivé':'Reprendre cette copie locale',()=>{
        const recovered=independent?copy(content):{...draftContent(content),localDraftId:uid()};
        if(!localWrite(draftKey(recovered),{quiz:recovered,savedAt:new Date().toISOString()}))throw new Error('Récupération non ouverte : le stockage local est indisponible. Le brouillon original est conservé.');
        S.namedDraft=null;S.draftBaseline=null;S.draft=recovered;S.questionId=recovered.questions[0].id;S.view='editor';render();
        notice('Copie indépendante récupérée. Le QCM archivé et ses anciennes parties restent inchangés.');
      },'secondary',S.busy)));
    }
  }
  function renderDraftActions(panel) {
    const targets=S.studio.drafts.filter(d=>d.id!==S.namedDraft?.id);
    const copyName=el('input',{required:true,maxLength:100,placeholder:'Nom de la copie'});
    panel.append(el('details',{class:'settings'},el('summary',{},'Copier ce brouillon'),
      el('form',{onSubmit:event=>{
        event.preventDefault();
        guard(()=>changeDraft(async()=>{
          await createNamedDraft(copyName.value,draftContent(S.draft));
          status('Copie créée. Le brouillon d’origine est conservé.');
        }));
      }},el('label',{class:'field'},'Nom de la nouvelle copie',copyName),
      el('button',{type:'submit',class:'secondary',disabled:S.busy},'Créer une copie'))));
    if(targets.length){
      const target=el('select',{},...targets.map(d=>el('option',{value:d.id},d.name)));
      const confirm=el('input',{type:'checkbox',required:true});
      panel.lastChild.append(el('form',{onSubmit:event=>{
        event.preventDefault();
        const destination=targets.find(d=>d.id===target.value);
        if(!destination||!confirm.checked)return;
        guard(()=>changeDraft(async()=>{
          const saved=await api('/api/drafts/'+encodeURIComponent(destination.id),'PUT',{revision:destination.revision,name:destination.name,content:draftContent(S.draft)});
          rememberNamedDraft(saved);
          status('Contenu copié dans « '+saved.name+' ». Le brouillon d’origine est conservé.');
        }));
      }},el('label',{class:'field'},'Brouillon de destination',target),el('label',{class:'check'},confirm,'Remplacer le contenu du brouillon choisi'),
      el('button',{type:'submit',class:'secondary',disabled:S.busy},'Copier dans ce brouillon')));
    }
    if(!S.namedDraft)return;
    const expected=S.namedDraft.name;
    const confirmName=el('input',{required:true,maxLength:100});
    panel.append(el('details',{class:'settings'},el('summary',{},'Supprimer ce brouillon'),
      el('form',{onSubmit:event=>{
        event.preventDefault();
        if(confirmName.value!==expected){notice('Retape exactement le nom du brouillon.');return;}
        guard(()=>changeDraft(async()=>{
          const draft=S.namedDraft;
          await api('/api/drafts/'+encodeURIComponent(draft.id),'DELETE',{revision:draft.revision,name:confirmName.value});
          const prefix=`mia-studio-named-draft:${draft.id}:`;
          localKeys().filter(key=>key.startsWith(prefix)).forEach(localRemove);
          S.studio.drafts=S.studio.drafts.filter(d=>d.id!==draft.id);
          S.namedDraft=null;S.draft=null;
          status('Brouillon supprimé.');
        }));
      }},el('label',{class:'field'},'Retape « '+expected+' » pour confirmer',confirmName),
      el('button',{type:'submit',class:'secondary',disabled:S.busy},'Confirmer la suppression'))));
  }
  function inputField(label,value,change,{type='text',maxLength,rows,min,max,id,step}={}) {
    const input=el(rows?'textarea':'input',{...(rows?{rows}:{type}),value:value??'',disabled:S.busy,...(id?{id}:{}),...(maxLength?{maxLength}:{}),...(min!==undefined?{min,max}:{}),...(step!==undefined?{step}:{})});input.addEventListener('input',()=>{change(type==='number'?parsePercent(input.value):input.value);persistDraft();});return el('label',{class:'field'},el('span',{},label),input);
  }
  function checkbox(label,value,change) {return el('label',{class:'check'},el('input',{type:'checkbox',checked:value,disabled:S.busy,onChange:event=>{change(event.target.checked);persistDraft();}}),el('span',{},label));}
  function renderEditor(panel) {
    panel.append(...heading('GAME EDITOR','Créer son terrain de jeu','QCM est le premier type de jeu disponible. Les autres formats ne sont pas encore implémentés.'));
    if(!S.draft) {renderNamedDraftList(panel);S.studio.quizzes.filter(q=>!q.archived).forEach(q=>panel.append(quizCard(q,true)));return;}
    const d=S.draft;
    d.config=quizConfig(d.config);setQuizAccent(d.config.accentColor);applyLayout(d.config);
    if(S.namedDraft)panel.append(inputField('Nom du brouillon',S.draftName,value=>S.draftName=value,{maxLength:100}));
    panel.append(el('div',{class:'version-note'},`Édition ${d.id?'de la version '+d.revision:'d’un nouveau QCM'} · aucune partie existante ne change.`));
    panel.append(inputField('Titre du QCM',d.title,v=>d.title=v,{maxLength:120}));
    const settings=el('details',{class:'settings'},el('summary',{},'Configuration et barème'));
    const presets=el('fieldset',{class:'question-fieldset'},el('legend',{},'Préréglages facultatifs'),paragraph('Ces boutons remplacent les règles ci-dessous dans ce brouillon. Tu peux ensuite modifier chaque réglage librement.'));
    const training=button('Appliquer Entraînement',()=>{Object.assign(d.config,{grading:'partial',passingPercent:60,shuffleQuestions:false,shuffleOptions:false,showCorrection:true,allowUnanswered:true});persistDraft();render();status('Entraînement : points partiels, seuil 60 %, ordre fixe, correction visible, réponses facultatives.');focusElement('preset-training');},'secondary',S.busy);training.id='preset-training';
    const assessment=button('Appliquer Évaluation',()=>{Object.assign(d.config,{grading:'all-or-nothing',passingPercent:60,shuffleQuestions:true,shuffleOptions:true,showCorrection:false,allowUnanswered:false});persistDraft();render();status('Évaluation : tout-ou-rien, seuil 60 %, questions et réponses mélangées, correction masquée, réponses obligatoires.');focusElement('preset-assessment');},'secondary',S.busy);assessment.id='preset-assessment';
    presets.append(paragraph('Entraînement : points partiels, 60 %, ordre fixe, correction visible, réponses facultatives.'),training,paragraph('Évaluation : tout-ou-rien, 60 %, mélanges activés, correction masquée, réponses obligatoires.'),assessment);settings.append(presets);
    const grading=el('select',{disabled:S.busy,'aria-label':'Barème choisi'},el('option',{value:'all-or-nothing'},'Tout-ou-rien'),el('option',{value:'partial'},'Points partiels'));grading.value=d.config.grading;grading.onchange=()=>{d.config.grading=grading.value;persistDraft();render();};
    const optional=checkbox('Autoriser les questions sans réponse (zéro point)',d.config.allowUnanswered,v=>d.config.allowUnanswered=v);optional.querySelector('input').id='quiz-allow-unanswered';
    settings.append(el('label',{class:'field'},el('span',{},'Barème choisi'),grading),paragraph(gradingText(d.config)),inputField('Seuil de réussite (%) — décimales autorisées',d.config.passingPercent,v=>d.config.passingPercent=v,{type:'number',min:0,max:100,step:'any',id:'quiz-passing-percent'}),checkbox('Mélanger les questions',d.config.shuffleQuestions,v=>d.config.shuffleQuestions=v),checkbox('Mélanger les réponses',d.config.shuffleOptions,v=>d.config.shuffleOptions=v),checkbox('Montrer la correction après la partie',d.config.showCorrection,v=>d.config.showCorrection=v),optional);panel.append(settings);
    panel.append(el('fieldset',{class:'question-fieldset'},el('legend',{},'Accueil et apparence'),inputField('Consignes avant et pendant la partie',d.config.instructions,v=>d.config.instructions=v,{rows:3,maxLength:2000,id:'quiz-instructions'}),inputField('Message de conclusion après la partie',d.config.conclusion,v=>d.config.conclusion=v,{rows:3,maxLength:2000,id:'quiz-conclusion'}),inputField('Couleur d’accent',d.config.accentColor,v=>{d.config.accentColor=v;setQuizAccent(v);},{type:'color',id:'quiz-accent'}),paragraph('Ces textes et cette couleur accompagnent la version jouée. Le joueur garde ses réglages personnels de lisibilité.')));
    const layout=el('fieldset',{class:'question-fieldset'},el('legend',{},'Disposition du questionnaire'));
    for(const [label,key,id,choices] of [
      ['Présentation des réponses','answerLayout','quiz-answer-layout',[['list','Liste'],['grid','Grille']]],
      ['Largeur du contenu','contentWidth','quiz-content-width',[['standard','Standard'],['wide','Large']]],
      ['Alignement du texte','textAlign','quiz-text-align',[['left','À gauche'],['center','Centré']]],
    ]){const select=el('select',{id,disabled:S.busy},...choices.map(([value,text])=>el('option',{value},text)));select.value=d.config[key];select.onchange=()=>{d.config[key]=select.value;persistDraft();render();focusElement(id);};layout.append(el('label',{class:'field'},label,select));}panel.append(layout);
    const tabs=el('div',{class:'question-tabs','aria-label':'Questions à éditer'});d.questions.forEach((q,i)=>tabs.append(button(String(i+1),()=>{S.questionId=q.id;render();focusElement('question-text');},q.id===S.questionId?'selected-tab':'secondary',S.busy)));tabs.append(button('+ Question',()=>{const q=blankQuestion();d.questions.push(q);S.questionId=q.id;persistDraft();render();focusElement('question-text');},'secondary',S.busy||d.questions.length>=50));panel.append(tabs);
    const q=d.questions.find(q=>q.id===S.questionId)||d.questions[0];S.questionId=q.id;const index=d.questions.indexOf(q);
    panel.append(el('h3',{},'Question '+(index+1)),inputField('Énoncé',q.text,v=>q.text=v,{rows:3,maxLength:500,id:'question-text'}));
    panel.append(mediaControls(q,d,'question-image'));
    const mode=el('select',{disabled:S.busy,'aria-label':'Type de réponse'},el('option',{value:'single'},'Une seule bonne réponse'),el('option',{value:'multiple'},'Plusieurs bonnes réponses possibles'));mode.value=q.mode;mode.onchange=()=>{q.mode=mode.value;persistDraft();render();};panel.append(el('label',{class:'field'},el('span',{},'Type de réponse'),mode),paragraph(q.mode==='single'?'Coche exactement une bonne réponse.':'Coche toutes les bonnes réponses, au moins une.'));
    const options=el('fieldset',{class:'question-fieldset'},el('legend',{},'Réponses et bonnes réponses'));
    q.options.forEach((option,i)=>{
      const up=button('↑ Réponse '+(i+1),()=>{q.options=moveItem(q.options,i,i-1);persistDraft();render();focusElement('option-text-'+option.id);},'quiet',S.busy||i===0);up.id='option-up-'+option.id;up.setAttribute('aria-label','Monter la réponse '+(i+1));
      const down=button('↓ Réponse '+(i+1),()=>{q.options=moveItem(q.options,i,i+1);persistDraft();render();focusElement('option-text-'+option.id);},'quiet',S.busy||i===q.options.length-1);down.id='option-down-'+option.id;down.setAttribute('aria-label','Descendre la réponse '+(i+1));
      const row=el('div',{class:'edit-option'},el('span',{class:'option-number'},String.fromCharCode(65+i)),inputField('Texte de la réponse '+(i+1)+' (facultatif avec une image décrite)',option.text,v=>option.text=v,{maxLength:200,id:'option-text-'+option.id}),checkbox('Bonne réponse '+(i+1),option.correct,value=>{option.correct=value;if(value&&q.mode==='single')q.options.forEach(o=>{if(o.id!==option.id)o.correct=false;});render();focusElement('option-text-'+option.id);}),up,down,button('Retirer la réponse '+(i+1),()=>{q.options=q.options.filter(o=>o.id!==option.id);persistDraft();render();focusElement('question-text');},'quiet',S.busy||q.options.length<=2));options.append(row,mediaControls(option,d,'option-image-'+option.id));
    });panel.append(options);
    panel.append(button('+ Réponse',()=>{q.options.push({id:uid(),text:'',correct:false});persistDraft();render();},'secondary',S.busy||q.options.length>=8),inputField('Explication après correction (facultative)',q.explanation,v=>q.explanation=v,{rows:2,maxLength:1000}));
    panel.append(el('div',{class:'row'},button('↑ Monter',()=>{[d.questions[index-1],d.questions[index]]=[d.questions[index],d.questions[index-1]];persistDraft();render();},'quiet',S.busy||index===0),button('↓ Descendre',()=>{[d.questions[index+1],d.questions[index]]=[d.questions[index],d.questions[index+1]];persistDraft();render();},'quiet',S.busy||index===d.questions.length-1),button('Retirer la question',()=>{d.questions=d.questions.filter(item=>item.id!==q.id);S.questionId=d.questions[0].id;persistDraft();render();},'quiet',S.busy||d.questions.length<=1)));
    const duplicate=button('Dupliquer cette question',()=>{const cloned=duplicateQuestion(q);d.questions.splice(index+1,0,cloned);S.questionId=cloned.id;persistDraft();render();focusElement('question-text');},'secondary',S.busy||d.questions.length>=50);duplicate.id='duplicate-question';panel.append(duplicate);
    const preview=button('Aperçu joueur local',()=>{const error=validateContent(d);if(error)throw new Error(error);persistDraft();S.preview={quiz:draftContent(d),index:0,answers:Object.create(null),finished:false};S.view='preview';render();drawMap();focusElement('preview-question');},'secondary',S.busy);preview.id='quiz-preview';panel.append(preview);
    panel.append(el('div',{class:'sticky-actions'},
      S.namedDraft?button('Enregistrer le brouillon',()=>changeDraft(async()=>{await writeNamedDraft();status('Brouillon enregistré. Rien n’a été publié.');}),'primary',S.busy):null,
      button(S.namedDraft?'Publier le QCM':'Enregistrer le QCM',saveQuiz,S.namedDraft?'secondary':'primary',S.busy),
      button('Mes brouillons / QCM',async()=>{if(S.namedDraft)await changeDraft(writeNamedDraft);else persistDraft();S.namedDraft=null;S.draft=null;render();},'secondary',S.busy)));
    renderDraftActions(panel);
    renderTransferControls(panel);
    if(d.id){panel.append(button('Recharger la version serveur (brouillon conservé)',()=>loadQuiz(d.id),'quiet',S.busy),button('Archiver ce QCM · conserver les parties',archiveQuiz,'quiet',S.busy));
      for(const key of localKeys()){if(key.startsWith('mia-studio-draft:'+d.id+':')&&key!==draftKey(d)){const cached=localRead(key);if(restorableDraft(cached?.quiz)&&cached.quiz.id===d.id)panel.append(button('Reprendre le brouillon de version '+cached.quiz.revision+' (conflit possible)',()=>{persistDraft();S.draft=copy(cached.quiz);S.questionId=S.draft.questions[0]?.id;render();status('Ancien brouillon restauré. Sa version doit encore correspondre au serveur pour être enregistré.');},'quiet',S.busy));}}
      panel.append(button('Recopier ce brouillon sur la version serveur actuelle',async()=>{if(S.busy)return;persistDraft();const snapshot=copy(d);S.busy=true;render();try{const fresh=await api('/api/quizzes/'+encodeURIComponent(d.id));S.draftBaseline=draftIdentity(fresh);S.draft={...snapshot,revision:fresh.revision,archived:fresh.archived};persistDraft();notice('Brouillon recopié sur la version '+fresh.revision+'. Relis toutes les questions avant Enregistrer : ce contenu remplacera celui de cette version.');}finally{S.busy=false;render();}},'quiet',S.busy));
    }
  }
  async function saveQuiz() {
    if(S.busy)return;const error=validateQuiz(S.draft);if(error){notice(error);return;}persistDraft();const source=copy(S.draft),key=draftKey(source);S.busy=true;render();
    try {const named=!!S.namedDraft;if(named)await writeNamedDraft();const body={title:source.title,questions:source.questions,config:source.config,...(source.id?{revision:source.revision}:{})};const saved=source.id?await api('/api/quizzes/'+encodeURIComponent(source.id),'PUT',body):await createWithReceipt('/api/quizzes',body);if(!named)localRemove(key);S.namedDraft=null;S.draft=saved;S.draftBaseline=draftIdentity(saved);await refresh();status('QCM enregistré sur le serveur · version '+saved.revision+'.');}
    finally{S.busy=false;render();}
  }
  async function archiveQuiz() {const d=S.draft;if(!d?.id||S.busy)return;persistDraft();S.busy=true;render();try{await api('/api/quizzes/'+encodeURIComponent(d.id)+'/archive','POST',{revision:d.revision});S.draft=null;await refresh();status('QCM archivé. Son historique est conservé.');}finally{S.busy=false;render();}}
  function renderPreview(panel){
    const preview=S.preview;if(!preview){S.view='editor';renderEditor(panel);return;}
    const quiz=preview.quiz,c=quizConfig(quiz.config),q=quiz.questions[preview.index];setQuizAccent(c.accentColor);applyLayout(c);
    panel.append(...heading('APERÇU LOCAL — AUCUNE PARTIE CRÉÉE',quiz.title||'QCM sans titre','Cet aperçu n’enregistre aucune réponse, tentative ou note. Les choix restent seulement dans cet aperçu.'),paragraph(gradingText(c)),paragraph(`Seuil ${c.passingPercent} % · ${c.allowUnanswered?'Réponses facultatives, zéro point si omises.':'Toutes les réponses sont requises en partie réelle.'}`));
    setQuizAccent(c.accentColor);
    authorText(panel,c.instructions,'quiz-instructions','Consignes');
    if(preview.finished){authorText(panel,c.conclusion,'quiz-conclusion','Message de conclusion');panel.append(paragraph('Fin de l’aperçu. Le résultat réel sera calculé uniquement lors d’une vraie partie.'));if(!c.conclusion)panel.append(paragraph('Aucun message de conclusion personnalisé.'));panel.append(button('Revenir aux questions de l’aperçu',()=>{preview.finished=false;render();focusElement('preview-question');}));}
    else {
      const fieldset=el('fieldset',{class:'question-fieldset quiz-preview','aria-labelledby':'preview-question'},el('legend',{},`Question ${preview.index+1}/${quiz.questions.length}`),imageContent(q.image,el('h3',{id:'preview-question',tabIndex:-1},q.text||'Énoncé à compléter')));
      const selected=preview.answers[q.id]||[];
      const previewOptions=el('div',{class:'qcm-answer-options'});
      q.options.forEach((option,index)=>{const label=option.text.trim()||option.image?.alt||'Réponse '+(index+1)+' à compléter';previewOptions.append(el('label',{class:'play-option'},el('input',{type:q.mode==='single'?'radio':'checkbox',name:'preview-'+q.id,'aria-label':label,checked:selected.includes(option.id),onChange:event=>{let ids=preview.answers[q.id]||[];preview.answers[q.id]=q.mode==='single'?[option.id]:event.target.checked?[...new Set([...ids,option.id])]:ids.filter(id=>id!==option.id);}}),choiceContent(option,'Réponse '+(index+1)+' à compléter')));});fieldset.append(previewOptions);
      panel.append(fieldset,el('div',{class:'row'},button('← Question précédente',()=>{preview.index--;render();focusElement('preview-question');},'secondary',preview.index===0),button('Question suivante →',()=>{preview.index++;render();focusElement('preview-question');},'secondary',preview.index===quiz.questions.length-1)),button('Effacer le choix de l’aperçu',()=>{delete preview.answers[q.id];render();focusElement('preview-question');},'quiet'),button('Voir le message de fin',()=>{preview.finished=true;render();}));
    }
    const back=button('Revenir au brouillon',()=>{S.preview=null;S.view='editor';render();drawMap();focusElement('quiz-preview');},'primary');back.id='preview-exit';panel.append(back);
  }
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
  function clearAnswer(question){S.answers=S.answers.filter(a=>a.questionId!==question.id);S.answerGeneration++;persistAnswers();clearTimeout(S.saveTimer);S.saveTimer=setTimeout(()=>guard(saveAnswers),350);render();drawMap();focusElement('play-question');status('Choix effacé. Enregistrement en attente.');}
  function updatePlayProgress() {const count=S.answers.filter(a=>a.optionIds.length).length,p=$('play-progress');if(p)p.textContent=`${count} / ${S.attempt.questions.length} questions renseignées${S.attempt.config.allowUnanswered?' · les questions omises compteront zéro point':''}`;const finish=$('finish-attempt');if(finish)finish.disabled=S.busy||(!S.attempt.config.allowUnanswered&&count!==S.attempt.questions.length);const clear=$('clear-answer');if(clear)clear.disabled=S.busy||!S.answers.some(answer=>answer.questionId===S.questionId&&answer.optionIds.length);}
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
    setQuizAccent(a.config.accentColor);applyLayout(a.config);authorText(panel,a.config.instructions,'quiz-instructions','Consignes');
    const cache=localRead(answerCacheKey(a.id));
    if(cache&&!restorableAnswers(cache.answers,a.questions))panel.append(paragraph('Les réponses locales retrouvées sont invalides pour cette partie. Les réponses du serveur restent affichées ; le cache est conservé.','recovery'));
    if(cache&&restorableAnswers(cache.answers,a.questions)&&S.answerGeneration===S.savedGeneration&&JSON.stringify(cache.answers)!==JSON.stringify(S.answers))panel.append(el('div',{class:'recovery'},paragraph('Des réponses locales non confirmées ont été retrouvées. Le serveur reste affiché.'),button('Restaurer ces choix dans le brouillon',()=>{S.answers=copy(cache.answers);S.answerGeneration++;persistAnswers();render();drawMap();status('Choix restaurés dans le brouillon. Clique Enregistrer les réponses.');})));
    panel.append(el('p',{class:'eyebrow'},`QUESTION ${index+1} / ${a.questions.length}`),paragraph(q.mode==='multiple'?'Plusieurs réponses peuvent être justes.':'Choisis une seule réponse.'));
    const ids=S.answers.find(x=>x.questionId===q.id)?.optionIds||[];
    const choices=el('fieldset',{class:'question-fieldset','aria-labelledby':'play-question'},el('legend',{},'Choisir une réponse à la question '+(index+1)),imageContent(q.image,el('h3',{id:'play-question',class:'question-heading',tabIndex:-1},q.text))),answerOptions=el('div',{class:'qcm-answer-options'});
    q.options.forEach((o,i)=>{const label=o.text.trim()||o.image?.alt||'Réponse '+(i+1);answerOptions.append(el('label',{class:'play-option'},el('input',{type:q.mode==='single'?'radio':'checkbox',name:'answer-'+q.id,value:o.id,checked:ids.includes(o.id),disabled:S.busy,'aria-label':label,onChange:event=>chooseAnswer(q,o.id,event.target.checked)}),el('span',{class:'option-letter','aria-hidden':'true'},String.fromCharCode(65+i)),choiceContent(o)));});choices.append(answerOptions);panel.append(choices);
    const clear=button('Effacer ma réponse à cette question',()=>clearAnswer(q),'quiet',S.busy||ids.length===0);clear.id='clear-answer';panel.append(clear);
    panel.append(el('div',{class:'row'},button('← Précédente',()=>{S.questionId=a.questions[index-1].id;render();drawMap();focusElement('play-question');},'secondary',index===0||S.busy),button('Suivante →',()=>{S.questionId=a.questions[index+1].id;render();drawMap();focusElement('play-question');},'secondary',index===a.questions.length-1||S.busy)));
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
    setQuizAccent(a.config.accentColor);applyLayout(a.config);authorText(panel,a.config.conclusion,'quiz-conclusion','Message de conclusion');
    if(r.details?.length)r.details.forEach(detail=>{
      const q=a.questions.find(q=>q.id===detail.questionId),names=ids=>ids.map(id=>{const option=q?.options.find(o=>o.id===id);return option?.text.trim()||option?.image?.alt||id;}).join(', ');
      const correction=el('details',{class:'correction'},el('summary',{},`${detail.isCorrect?'✓':'○'} ${q?.text||detail.questionId} · ${Number(detail.points).toFixed(2)} / 1`),paragraph('Tes choix : '+(names(detail.selectedOptionIds)||'Aucune réponse')),paragraph('Bonnes réponses : '+names(detail.correctOptionIds)),detail.explanation?paragraph(detail.explanation):null);
      if(q?.image)correction.append(imageContent(q.image,paragraph(q.text)));
      if(q?.options.some(option=>option.image)){const mediaAnswers=el('div',{class:'qcm-answer-options'});q.options.forEach(option=>mediaAnswers.append(el('article',{class:'card'},imageContent(option.image,paragraph(option.text.trim()||option.image?.alt||'Réponse')),paragraph((detail.selectedOptionIds.includes(option.id)?'Choisie · ':'')+(detail.correctOptionIds.includes(option.id)?'Bonne réponse':'Autre réponse')))));correction.append(mediaAnswers);}
      panel.append(correction);
    });else panel.append(paragraph('La correction détaillée est masquée selon la configuration de cette partie.'));
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
  window.addEventListener('beforeunload',event=>{if(S.draft)persistDraft();const answerDirty=S.attempt&&S.answerGeneration!==S.savedGeneration;if(answerDirty)persistAnswers();if(draftChanged()||answerDirty){event.preventDefault();event.returnValue='';}});
  render();drawMap();guard(async()=>{await refresh();status('Prêt. Choisis une action ; aucune partie ne démarre automatiquement.');render();});
})();
