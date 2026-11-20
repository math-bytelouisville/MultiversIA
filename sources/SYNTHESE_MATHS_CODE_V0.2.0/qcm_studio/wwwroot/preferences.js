'use strict';
(() => {
  const KEY = 'mia-qcm-preferences:v1';
  const DEFAULTS = Object.freeze({version:1,theme:'system',text:100,spacing:'normal',contrast:'normal',motion:'system'});
  const VALUES = Object.freeze({theme:['system','light','dark'],text:[100,115,130],spacing:['normal','airy'],contrast:['normal','strong'],motion:['system','reduced']});
  function normalize(value) {
    const result={...DEFAULTS};
    if(!value || typeof value!=='object' || Array.isArray(value) || value.version!==1)return result;
    for(const [key,allowed] of Object.entries(VALUES))if(allowed.includes(value[key]))result[key]=value[key];
    return result;
  }
  function preferenceStore(storageProvider) {
    let current={...DEFAULTS},persistent=true;
    try {const raw=storageProvider().getItem(KEY);current=normalize(raw===null?null:JSON.parse(raw));}
    catch(error) {if(!(error instanceof SyntaxError))persistent=false;}
    return {
      get:()=>({...current}),isPersistent:()=>persistent,
      set(value) {
        current=normalize(value);
        try {storageProvider().setItem(KEY,JSON.stringify(current));persistent=true;}catch{persistent=false;}
        return {...current};
      },
      reset(){return this.set(DEFAULTS);}
    };
  }
  function effective(value,dark,reduced) {
    const prefs=normalize(value);
    return {theme:prefs.theme==='system'?(dark?'dark':'light'):prefs.theme,
      scale:prefs.text/100,spacing:prefs.spacing,contrast:prefs.contrast,
      motion:prefs.motion==='reduced'||reduced?'reduced':'normal'};
  }
  const core={KEY,DEFAULTS,normalize,preferenceStore,effective};
  if(typeof document==='undefined'){if(typeof module!=='undefined')module.exports=core;return;}
  const panel=document.getElementById('panel'),host=document.getElementById('qcm-preferences');
  if(!panel||!host)return;
  const store=preferenceStore(()=>window.localStorage);
  const dark=window.matchMedia('(prefers-color-scheme: dark)'),reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
  const controls=new Map();
  const message=document.createElement('p');message.id='qcm-preferences-status';message.className='qcm-preferences-status';message.setAttribute('role','status');message.setAttribute('aria-live','polite');
  function apply() {
    const prefs=store.get(),resolved=effective(prefs,dark.matches,reduced.matches);
    panel.dataset.qcmTheme=resolved.theme;panel.dataset.qcmSpacing=resolved.spacing;
    panel.dataset.qcmContrast=resolved.contrast;panel.dataset.qcmMotion=resolved.motion;
    panel.style.setProperty('--qcm-text-scale',String(resolved.scale));
    for(const [key,control] of controls)control.value=String(prefs[key]);
    message.textContent=store.isPersistent()?'Préférences propres à ce navigateur. Aucun score ni questionnaire ne change.':'Stockage local indisponible : préférences en mémoire seulement, perdues à la fermeture. Aucun score ne change.';
  }
  const summary=document.createElement('summary');summary.textContent='Confort de lecture du QCM';host.append(summary);
  const descriptions={theme:['Thème',[['system','Système'],['light','Clair'],['dark','Sombre']]],text:['Taille du texte',[[100,'100 %'],[115,'115 %'],[130,'130 %']]],spacing:['Espacement',[['normal','Normal'],['airy','Aéré']]],contrast:['Contraste',[['normal','Normal'],['strong','Renforcé']]],motion:['Mouvements du panneau QCM',[['system','Suivre le système'],['reduced','Réduits']]]};
  for(const [key,[title,choices]] of Object.entries(descriptions)) {
    const label=document.createElement('label');label.className='field';
    const caption=document.createElement('span');caption.textContent=title;
    const select=document.createElement('select');select.id='qcm-preference-'+key;
    for(const [value,text] of choices){const option=document.createElement('option');option.value=String(value);option.textContent=text;select.append(option);}
    select.addEventListener('change',()=>{store.set({...store.get(),[key]:key==='text'?Number(select.value):select.value});apply();});
    label.append(caption,select);host.append(label);controls.set(key,select);
  }
  const reset=document.createElement('button');reset.type='button';reset.id='qcm-preferences-reset';reset.className='secondary';reset.textContent='Réinitialiser mes préférences';reset.addEventListener('click',()=>{store.reset();apply();message.textContent+=' Réglages par défaut rétablis.';});
  const scope=document.createElement('p');scope.className='qcm-preferences-status';scope.textContent='Ces réglages concernent le panneau QCM. La carte conserve ses propres commandes.';
  host.append(reset,message,scope);
  dark.addEventListener('change',apply);reduced.addEventListener('change',apply);
  globalThis.QcmPreferences={apply,setAccent(hex){panel.style.setProperty('--quiz-accent',typeof hex==='string'&&hex.length===7&&/^#[0-9a-f]{6}$/i.test(hex)?hex:'#397866');}};
  globalThis.QcmPreferences.setAccent('#397866');apply();
})();
