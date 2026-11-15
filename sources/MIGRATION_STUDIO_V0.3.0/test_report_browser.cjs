'use strict';
const fs=require('fs'),path=require('path'),assert=require('assert'),{pathToFileURL}=require('url');
const {chromium}=require('C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1112-611-0_26.1112.611.0_x64__3k8sg7r9htsxt/dependencies/node/node_modules/playwright');
async function main(){
 const fixture=path.resolve(process.argv[2]);
 if(!fs.existsSync(path.join(fixture,'index.html')))throw Error('Explicit generated browser fixture required');
 const output=path.join(fixture,'browser-'+Date.now());fs.mkdirSync(output);
 const checks=[],errors=[],requests=[];
 const ctx=await chromium.launchPersistentContext(path.join(output,'profile'),{channel:'msedge',headless:true,viewport:{width:1440,height:960}});
 const page=await ctx.newPage();page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>requests.push(r.url()));
 const record=(name,pass)=>{checks.push({name,pass:!!pass});assert(pass,name)};
 try{
  await page.goto(pathToFileURL(path.join(fixture,'index.html')).href);
  await page.locator('#node-source').waitFor();
  record('offline startup without external requests',requests.every(u=>u.startsWith('file:')));
  record('hostile JSON stays text',await page.evaluate(()=>globalThis.PWNED===undefined));
  record('scope visible',await page.locator('footer').innerText().then(s=>s.includes('ne sont pas traduits')));
  await page.locator('#node-math').click();record('pointer selects mathematical mapping',await page.locator('#detail-title').innerText()==='Règles exactes');
  record('human readable formula in summary',await page.locator('#summary').innerText().then(s=>s.includes('max(0, bonnes choisies')&&s.includes('aucune division par zéro')));
  await page.locator('[data-tab=raw]').click();record('curated source spans retained in exact data',await page.locator('#raw').innerText().then(s=>s.includes('167')&&s.includes('174')));
  await page.locator('#node-variant').focus();await page.keyboard.press('Enter');record('variant shows rational weight and actually changed question',await page.locator('#summary').innerText().then(s=>s.includes('1/2')&&s.includes('0.00 → 0.25')&&s.includes('question-differente')&&!s.includes('fixture-variant')));
  await page.locator('#node-uncovered').focus();await page.keyboard.press('Enter');record('keyboard selects uncovered branch',await page.locator('#detail-title').innerText()==='Le site complet');
  await page.locator('[data-tab=raw]').click();record('exact data preserve hostile string',await page.locator('#raw').innerText().then(s=>s.includes('globalThis.PWNED=true')));
  const point=await page.evaluate(()=>{const r=document.querySelector('#map').getBoundingClientRect();return{x:Math.round(r.x+r.width*.68),y:Math.round(r.y+r.height*.41)}});
  const at=async()=>page.evaluate(p=>{const q=new DOMPoint(p.x,p.y).matrixTransform(document.querySelector('#map').getScreenCTM().inverse());return{x:q.x,y:q.y}},point);
  const before=await at();await page.mouse.move(point.x,point.y);await page.mouse.wheel(0,-100);await page.waitForTimeout(60);const after=await at();
  fs.writeFileSync(path.join(output,'zoom-probe.json'),JSON.stringify({point,before,after,view:await page.locator('#map').getAttribute('viewBox')},null,2));
  record('zoom centered on pointer despite SVG letterboxing',Math.abs(before.x-after.x)<.02&&Math.abs(before.y-after.y)<.02);
  const view=await page.locator('#map').getAttribute('viewBox');await page.mouse.move(point.x,point.y);await page.mouse.down();await page.mouse.move(point.x+65,point.y+30,{steps:5});await page.mouse.up();
  record('pan changes map view',await page.locator('#map').getAttribute('viewBox')!==view);
  const panned=await page.locator('#map').getAttribute('viewBox');await page.locator('#node-rust').focus();await page.keyboard.press('Enter');record('selection preserves camera',await page.locator('#map').getAttribute('viewBox')===panned);
  await page.locator('#fit').click();await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});record('mobile no horizontal page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  await page.goto(pathToFileURL(path.join(fixture,'empty.html')).href);record('absent status stays inconclusive',await page.locator('#report-status').innerText()==='INCONCLUSIF');
  await page.goto(pathToFileURL(path.join(fixture,'missing-csharp.html')).href);record('Python Rust PASS cannot hide missing C#',await page.locator('#report-status').innerText()==='INCONCLUSIF');
  await page.goto(pathToFileURL(path.join(fixture,'failed-csharp.html')).href);record('C# FAIL overrides other PASS',await page.locator('#report-status').innerText()==='ÉCHEC');
  await page.locator('#node-compare').focus();await page.keyboard.press('Enter');record('C# observation shown separately',await page.locator('#summary').innerText().then(s=>s.includes('C# observé ↔ modèle')&&s.includes('fractions internes ne sont pas observées')));
  record('no browser script errors',errors.length===0);
 }finally{
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({checks,errors,requests,scope:'Headless Edge, generated fixture and empty report; no real migration run, no visible GUI'},null,2));await ctx.close();console.log(output);
 }
}
main().catch(e=>{console.error(e);process.exitCode=1});
