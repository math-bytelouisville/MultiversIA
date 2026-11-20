'use strict';
(() => {
  const MAX_BYTES=512*1024,MAX_INPUT_BYTES=12*1024*1024,MAX_EDGE=1600;
  const positions=['above','below','left','right'],sizes=['small','medium','large'],fits=['contain','cover'];
  const validId=id=>typeof id==='string'&&id.length===64&&/^[0-9a-f]{64}$/.test(id);
  // Container dimensions are checked before calling the browser decoder. This is
  // deliberately not a complete image decoder or a hard browser-memory guarantee.
  function inspectRaster(bytes,mime,{maxDimension=32768,maxPixels=40000000}={}) {
    if(!(bytes instanceof Uint8Array)||bytes.length<4)throw new Error('En-tête image invalide.');
    const data=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
    const text=(at,count)=>String.fromCharCode(...bytes.subarray(at,at+count));
    function bounded(width,height){if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>maxDimension||height>maxDimension||width*height>maxPixels)throw new Error('Image trop grande : dimensions au-delà de la limite autorisée.');return {width,height};}
    if(mime==='image/png') {
      if(bytes.length<33||![137,80,78,71,13,10,26,10].every((v,i)=>bytes[i]===v)||data.getUint32(8)!==13||text(12,4)!=='IHDR')throw new Error('En-tête PNG invalide.');
      const dimensions=bounded(data.getUint32(16),data.getUint32(20));
      let offset=8,headers=0,hasData=false,ended=false;
      while(offset+12<=bytes.length){
        const length=data.getUint32(offset),type=text(offset+4,4),end=offset+12+length;
        if(end>bytes.length)throw new Error('Bloc PNG tronqué.');
        if(['acTL','fcTL','fdAT'].includes(type))throw new Error('Les images animées ne sont pas prises en charge.');
        if(type==='IHDR'&&++headers!==1)throw new Error('En-têtes PNG multiples refusés.');
        if(type==='IDAT')hasData=true;
        if(type==='IEND'){if(length!==0||end!==bytes.length)throw new Error('Fin PNG invalide.');ended=true;break;}
        offset=end;
      }
      if(!hasData||!ended)throw new Error('Structure PNG incomplète.');return dimensions;
    }
    if(mime==='image/jpeg') {
      if(bytes[0]!==255||bytes[1]!==216)throw new Error('En-tête JPEG invalide.');
      let offset=2,dimensions=null;
      while(offset<bytes.length){
        if(bytes[offset++]!==255)throw new Error('Structure JPEG invalide.');
        while(offset<bytes.length&&bytes[offset]===255)offset++;
        if(offset>=bytes.length)break;const marker=bytes[offset++];
        if(marker===0||marker===216||marker===217)throw new Error('Structure JPEG incomplète.');
        if(marker===1||marker>=208&&marker<=215)continue;
        if(offset+2>bytes.length)throw new Error('Bloc JPEG tronqué.');
        const length=data.getUint16(offset);if(length<2||offset+length>bytes.length)throw new Error('Bloc JPEG tronqué.');
        if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)){
          if(dimensions||length<8||![192,193,194].includes(marker)||bytes[offset+2]!==8)throw new Error('Variante JPEG non prise en charge.');
          dimensions=bounded(data.getUint16(offset+5),data.getUint16(offset+3));
        }
        if(marker===218){if(!dimensions)throw new Error('Dimensions JPEG absentes.');return dimensions;}
        offset+=length;
      }
      throw new Error('Structure JPEG incomplète.');
    }
    if(mime==='image/webp') {
      if(bytes.length<20||text(0,4)!=='RIFF'||text(8,4)!=='WEBP'||data.getUint32(4,true)+8!==bytes.length)throw new Error('En-tête WebP invalide.');
      let offset=12,canvas=null,frame=null;
      while(offset+8<=bytes.length){
        const type=text(offset,4),length=data.getUint32(offset+4,true),start=offset+8,end=start+length;
        if(end>bytes.length)throw new Error('Bloc WebP tronqué.');
        if(type==='ANIM'||type==='ANMF')throw new Error('Les images animées ne sont pas prises en charge.');
        if(type==='VP8X'){
          if(canvas||length!==10)throw new Error('En-tête WebP étendu invalide.');
          if(bytes[start]&2)throw new Error('Les images animées ne sont pas prises en charge.');
          canvas=bounded(1+bytes[start+4]+(bytes[start+5]<<8)+(bytes[start+6]<<16),1+bytes[start+7]+(bytes[start+8]<<8)+(bytes[start+9]<<16));
        }
        if(type==='VP8 '){
          if(frame||length<10||(bytes[start]&1)||text(start+3,3)!=='\x9d\x01\x2a')throw new Error('En-tête WebP avec perte invalide.');
          frame=bounded(data.getUint16(start+6,true)&16383,data.getUint16(start+8,true)&16383);
        }
        if(type==='VP8L'){
          if(frame||length<5||bytes[start]!==47)throw new Error('En-tête WebP sans perte invalide.');
          const packed=data.getUint32(start+1,true);if(packed>>>29)throw new Error('Version WebP non prise en charge.');
          frame=bounded(1+(packed&16383),1+((packed>>>14)&16383));
        }
        offset=end+(length&1);
      }
      if(offset!==bytes.length||!frame||canvas&&(canvas.width!==frame.width||canvas.height!==frame.height))throw new Error('Dimensions WebP incohérentes.');
      return canvas??frame;
    }
    throw new Error('Format image non pris en charge.');
  }
  function validateReference(ref) {
    if(ref===null||ref===undefined)return null;
    if(typeof ref!=='object'||Array.isArray(ref)||!validId(ref.id))return 'Référence image invalide.';
    if(Object.keys(ref).some(key=>!['id','alt','position','size','fit'].includes(key)))return 'Champ image inconnu.';
    if(typeof ref.alt!=='string'||!ref.alt.trim()||ref.alt.length>300)return 'Décris l’image en 1 à 300 caractères.';
    if(!positions.includes(ref.position===undefined?'above':ref.position)||!sizes.includes(ref.size===undefined?'medium':ref.size)||!fits.includes(ref.fit===undefined?'contain':ref.fit))return 'Disposition image invalide.';
    return null;
  }
  function decodeDataUrl(dataUrl) {
    if(typeof dataUrl!=='string'||dataUrl.length>MAX_BYTES*4/3+100)throw new Error('Image limitée à 512 Kio après préparation.');
    const match=/^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]*={0,2})$/.exec(dataUrl);
    if(!match||match[0].length!==dataUrl.length||match[2].length%4!==0)throw new Error('Image PNG, JPEG ou WebP en base64 requise.');
    let binary;try{binary=atob(match[2]);}catch{throw new Error('Données image invalides.');}
    if(!binary.length||binary.length>MAX_BYTES)throw new Error('Image vide ou supérieure à 512 Kio.');
    const bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));
    const png=bytes.length>=24&&[137,80,78,71,13,10,26,10].every((v,i)=>bytes[i]===v);
    const jpeg=bytes.length>=4&&bytes[0]===255&&bytes[1]===216&&bytes[2]===255;
    const webp=bytes.length>=16&&binary.slice(0,4)==='RIFF'&&binary.slice(8,12)==='WEBP';
    if(!(match[1]==='image/png'&&png||match[1]==='image/jpeg'&&jpeg||match[1]==='image/webp'&&webp))throw new Error('La signature du fichier ne correspond pas au format image.');
    inspectRaster(bytes,match[1],{maxDimension:8192,maxPixels:16000000});
    return {mime:match[1],bytes};
  }
  function resizeGeometry(width,height,max=MAX_EDGE) {
    if(!Number.isFinite(width)||!Number.isFinite(height)||width<1||height<1||width*height>40000000)throw new Error('Image trop grande ou dimensions invalides (40 millions de pixels maximum).');
    const ratio=Math.min(1,max/Math.max(width,height));
    return {width:Math.max(1,Math.round(width*ratio)),height:Math.max(1,Math.round(height*ratio))};
  }
  async function digest(bytes) {return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');}
  async function readBounded(response,limit) {
    if(!response.body)throw new Error('Réponse image vide.');
    const reader=response.body.getReader(),chunks=[];let total=0;
    try {while(true){const {done,value}=await reader.read();if(done)break;total+=value.length;if(total>limit){await reader.cancel();throw new Error('Réponse image trop volumineuse.');}chunks.push(value);}}
    finally{reader.releaseLock();}
    const bytes=new Uint8Array(total);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}return bytes;
  }
  async function uploadDataUrl(dataUrl,request=fetch) {
    const decoded=decodeDataUrl(dataUrl);
    const response=await request('/api/media',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataUrl}),signal:AbortSignal.timeout(20000),redirect:'error',mode:'same-origin',credentials:'same-origin'});
    const bytes=await readBounded(response,16384);let result;
    try {result=JSON.parse(new TextDecoder().decode(bytes));}catch{throw new Error('Réponse du stockage image illisible.');}
    if(!result||typeof result!=='object'||Array.isArray(result))throw new Error('Réponse du stockage image invalide. Aucun remplacement confirmé.');
    if(!response.ok)throw new Error(result.message||result.error||'Enregistrement image refusé.');
    if(!validId(result.id)||result.id!==await digest(decoded.bytes))throw new Error('Empreinte de l’image enregistrée incohérente.');
    return {id:result.id,url:'/api/media/'+result.id};
  }
  async function exportAsset(id,request=fetch) {
    if(!validId(id))throw new Error('Identifiant image invalide.');
    const response=await request('/api/media/'+id,{cache:'no-store',signal:AbortSignal.timeout(20000),redirect:'error',mode:'same-origin',credentials:'same-origin'});
    if(!response.ok)throw new Error('Image indisponible pour l’export.');
    const mime=(response.headers.get('content-type')||'').split(';')[0].trim();
    if(!['image/png','image/jpeg','image/webp'].includes(mime))throw new Error('Format exporté refusé.');
    const bytes=await readBounded(response,MAX_BYTES);
    if(!bytes.length||await digest(bytes)!==id)throw new Error('Empreinte de l’image exportée incohérente.');
    let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    return {id,dataUrl:`data:${mime};base64,${btoa(binary)}`};
  }
  const core={MAX_BYTES,MAX_EDGE,validateReference,decodeDataUrl,validateDataUrl:decodeDataUrl,inspectRaster,resizeGeometry,uploadDataUrl,exportAsset};
  if(typeof document==='undefined'){if(typeof module!=='undefined')module.exports=core;return;}
  function node(tag,className,text){const value=document.createElement(tag);if(className)value.className=className;if(text!==undefined)value.textContent=text;return value;}
  function render(ref,{className=''}={}) {
    if(!ref)return null;
    const error=validateReference(ref);if(error)return node('p','qcm-media-error','Image non affichée : '+error);
    const figure=node('figure','qcm-media '+className);figure.dataset.imageSize=ref.size??'medium';figure.dataset.imageFit=ref.fit??'contain';
    const img=node('img');img.src='/api/media/'+ref.id;img.alt=ref.alt;img.loading='lazy';img.decoding='async';img.referrerPolicy='no-referrer';
    img.addEventListener('error',()=>{img.hidden=true;if(!figure.querySelector('.qcm-media-error'))figure.append(node('figcaption','qcm-media-error','Image indisponible : '+img.alt));});
    figure.append(img);return figure;
  }
  function renderContent(ref,contentNode,{className=''}={}) {
    const wrapper=node('div','qcm-image-layout '+className);wrapper.dataset.imagePosition=ref?.position??'above';
    const content=node('div','qcm-image-content');content.append(contentNode);
    const picture=render(ref);if(picture)wrapper.append(picture);else wrapper.dataset.imagePosition='none';
    wrapper.append(content);return wrapper;
  }
  async function prepareFile(file) {
    if(!file||!['image/png','image/jpeg','image/webp'].includes(file.type))throw new Error('Choisis un fichier PNG, JPEG ou WebP.');
    if(file.size>MAX_INPUT_BYTES||file.size===0)throw new Error('Le fichier source doit faire entre 1 octet et 12 Mio.');
    let inputBytes;
    try{inputBytes=new Uint8Array(await file.arrayBuffer());}catch{throw new Error('Impossible de décoder cette image : lecture locale impossible.');}
    if(inputBytes.length>MAX_INPUT_BYTES||inputBytes.length!==file.size)throw new Error('Taille du fichier image incohérente.');
    inspectRaster(inputBytes,file.type);
    let bitmap;try{bitmap=await createImageBitmap(file);}catch{throw new Error('Impossible de décoder cette image.');}
    try {
      let size=resizeGeometry(bitmap.width,bitmap.height);
      const canvas=document.createElement('canvas');const context=canvas.getContext('2d');if(!context)throw new Error('Préparation image indisponible dans ce navigateur.');
      for(let step=0;step<7;step++) {
        canvas.width=size.width;canvas.height=size.height;context.clearRect(0,0,size.width,size.height);context.drawImage(bitmap,0,0,size.width,size.height);
        const dataUrl=canvas.toDataURL(file.type==='image/jpeg'?'image/jpeg':'image/webp',Math.max(.5,.86-step*.06));
        try {decodeDataUrl(dataUrl);return dataUrl;}catch(error){if(step===6)throw new Error('Cette image reste trop lourde (limite 512 Kio). Choisis une image plus simple ou plus petite.');}
        size={width:Math.max(1,Math.round(size.width*.8)),height:Math.max(1,Math.round(size.height*.8))};
      }
      throw new Error('Préparation image interrompue.');
    }finally{bitmap.close();}
  }
  function controls(ref,onChange,{idPrefix='qcm-image',disabled=false,onBusy=()=>{}}={}) {
    const section=node('section','qcm-media-controls');section.setAttribute('aria-label','Image illustrative');
    const title=node('h4',null,ref?'Image illustrative : remplacer ou ajuster':'Ajouter une image illustrative');section.append(title);
    const message=node('p','qcm-media-status');message.id=idPrefix+'-status';message.setAttribute('role','status');message.setAttribute('aria-live','polite');
    section.append(node('p','muted','Fichier de ton ordinateur : PNG, JPEG ou WebP non animé, jusqu’à 12 Mio et 40 millions de pixels (32 768 px par côté au maximum). Préparation locale : côté maximal 1 600 px, compression et réduction si nécessaire pour rester sous 512 Kio. L’image est envoyée uniquement au serveur local du QCM.'));
    function field(labelText,input){const label=node('label','field');label.append(node('span',null,labelText),input);section.append(label);}
    const alt=node('input');alt.type='text';alt.id=idPrefix+'-alt';alt.maxLength=300;alt.value=ref?.alt??'';alt.required=true;alt.disabled=disabled;field('Description de l’image (obligatoire, 300 caractères maximum)',alt);
    function validAlt(){const valid=alt.value.trim().length>0&&alt.value.length<=300;alt.setCustomValidity(valid?'':'Décris l’image en 1 à 300 caractères.');return valid;}
    alt.addEventListener('input',()=>alt.setCustomValidity(''));
    alt.addEventListener('change',()=>{
      if(!ref)return;
      if(!validAlt()){alt.value=ref.alt;alt.setCustomValidity('');message.textContent='Description obligatoire : la description précédente est conservée et réaffichée.';return;}
      ref={...ref,alt:alt.value.trim()};alt.value=ref.alt;
      section.querySelectorAll('img').forEach(img=>img.alt=ref.alt);
      section.querySelectorAll('.qcm-media-error').forEach(error=>error.textContent='Image indisponible : '+ref.alt);
      // Persist without destroying the neighboring file input during its focus/click sequence.
      onChange(ref,{render:false});
    });
    if(ref){section.append(render(ref));for(const [key,values,title] of [['position',positions,'Position'],['size',sizes,'Taille'],['fit',fits,'Cadrage']]){
      const labels={above:'Au-dessus',below:'En dessous',left:'À gauche',right:'À droite',small:'Petite',medium:'Moyenne',large:'Grande',contain:'Image entière',cover:'Remplir le cadre (peut recadrer)'};
      const select=node('select');select.id=idPrefix+'-'+key;select.disabled=disabled;
      for(const value of values){const option=node('option',null,labels[value]);option.value=value;select.append(option);}select.value=ref[key]??({position:'above',size:'medium',fit:'contain'}[key]);
      select.addEventListener('change',()=>onChange({...ref,[key]:select.value}));field(title,select);
    }}
    const file=node('input');file.type='file';file.id=idPrefix+'-file';file.accept='image/png,image/jpeg,image/webp';file.disabled=disabled;field(ref?'Remplacer depuis mon ordinateur':'Choisir depuis mon ordinateur',file);
    file.addEventListener('change',async()=>{
      const selected=file.files?.[0];if(!selected)return;
      if(!validAlt()){message.textContent='Décris d’abord l’image, puis choisis ton fichier.';alt.reportValidity();file.value='';return;}
      const description=alt.value.trim();const inputs=[...section.querySelectorAll('input,select,button')];onBusy(true);inputs.forEach(control=>control.disabled=true);message.textContent='Préparation locale de l’image…';
      try {const dataUrl=await prepareFile(selected);message.textContent='Enregistrement sur le serveur local…';const result=await uploadDataUrl(dataUrl);onChange({id:result.id,alt:description,position:ref?.position??'above',size:ref?.size??'medium',fit:ref?.fit??'contain'});message.textContent='Image enregistrée. Publie le QCM pour l’utiliser dans une nouvelle partie.';}
      catch(error){message.textContent=(error instanceof Error?error.message:'Échec image.')+' L’image précédente est conservée.';}
      finally{file.value='';inputs.forEach(control=>control.disabled=disabled);onBusy(false);}
    });
    if(ref){const remove=node('button','secondary','Retirer cette image du contenu');remove.type='button';remove.id=idPrefix+'-remove';remove.disabled=disabled;remove.addEventListener('click',()=>onChange(null));section.append(remove);}
    section.append(message);return section;
  }
  globalThis.QcmMedia={...core,render,renderContent,controls};
})();
