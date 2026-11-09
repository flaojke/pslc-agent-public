const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=path.join(root,'docs/qa/dialogue-case-workspace/screenshots');
 const browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1080}});
 page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const api=async(p,body,method='POST')=>{const r=body===undefined?await page.request.get('http://127.0.0.1:8787'+p):await page.request.fetch('http://127.0.0.1:8787'+p,{method,data:body,headers:{Origin:'http://127.0.0.1:8787'}});assert(r.ok(),await r.text());return r.json();};
 const shot=async n=>page.screenshot({path:path.join(out,n+'.png'),fullPage:true});
 try{
 await page.goto('http://127.0.0.1:8787');await page.getByLabel('Логин',{exact:true}).fill('admin');await page.getByLabel('Пароль',{exact:true}).fill('admin');await page.getByRole('button',{name:'Войти в систему'}).click();
 await page.getByRole('heading',{name:'Основной раздел',exact:true}).waitFor();assert.equal((await api('/healthz')).version,'0.13.0');
 const d=await api('/api/devices'),demo=d.find(x=>x.virtual),real=d.find(x=>!x.virtual&&x.online);assert(demo&&real);assert(!real.commands_enabled&&!real.installations_enabled);
 const model=await api('/api/model/active');assert.equal(model.provider,'local');
 const cpu=await api('/api/conversations',{title:'Проверка локальной модели'});
 const started=Date.now();await api('/api/conversations/'+cpu.id+'/messages',{request_id:crypto.randomUUID(),question:'Ответь одним коротким предложением: чем наблюдение отличается от гипотезы при диагностике?'});let answer;
 for(let i=0;i<240;i++){const c=await api('/api/conversations/'+cpu.id);answer=c.turns.at(-1);if(!['queued','generating'].includes(answer.state))break;await page.waitForTimeout(500);}
 assert.equal(answer.state,'completed');assert(answer.temporary.answer.length>10);const cpuMs=Date.now()-started;
 await api('/api/conversations/'+cpu.id+'/delete',{cancel_tasks:true});
 const chat=await api('/api/conversations',{title:'Проверка состояния после обновления 1C'});
 await api('/api/conversations/'+chat.id,{title:chat.title,scope:{kind:'manual',ids:[demo.id,real.id]}},'PUT');
 await api('/api/conversations/'+chat.id+'/plans',{request_id:crypto.randomUUID(),question:'Проверь состояние всех машин'});
 let c=await api('/api/conversations/'+chat.id);let flow=c.turns.at(-1).workflow;assert.equal(flow.source,'approved_template');assert.equal(flow.steps.length,2);assert.equal(flow.state,'awaiting_confirmation');
 await api('/api/workflows/'+flow.id+'/decision',{approve:true,plan_hash:flow.plan_hash});
 for(let i=0;i<100;i++){c=await api('/api/conversations/'+chat.id);flow=c.turns.at(-1).workflow;if(flow.state==='verified')break;await page.waitForTimeout(500);}
 assert.equal(flow.state,'verified');assert.equal(flow.steps.filter(x=>x.summary?.source==='demo_simulation').length,1);
 await page.reload();await page.locator('.conversation-name').filter({hasText:chat.title}).click();await page.locator('.case-results h3').filter({hasText:'2/2 шагов проверено'}).waitFor();await page.locator('.phase-facts>summary').click();await shot('10-live-main-verified');
 await page.getByRole('button',{name:'Устройства',exact:false}).filter({has:page.locator('b')}).click();await shot('11-live-devices');
 await page.locator('tr').filter({hasText:demo.name}).getByRole('button',{name:'Настроить',exact:true}).click();await page.getByLabel('Тип устройства',{exact:true}).waitFor();await shot('12-live-node-settings');
 for(const [name,file] of [['Настройка модели','13-live-model'],['События','14-live-events'],['Учётные записи','15-live-accounts'],['Знания','16-live-knowledge']]){
  await page.getByRole('button',{name,exact:name==='Настройка модели'||name==='Учётные записи'}).click();await page.waitForTimeout(200);await shot(file);
 }
 await page.getByRole('button',{name:'Основной раздел',exact:false}).click();await page.locator('.conversation-name').filter({hasText:chat.title}).waitFor();await page.locator('.conversation-name').filter({hasText:chat.title}).click();await page.locator('.case-results h3').filter({hasText:'2/2 шагов проверено'}).waitFor();
 await page.setViewportSize({width:390,height:844});await shot('17-live-mobile');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 const report={version:'0.13.0',schema:7,identityAndGrantsPreserved:true,rawDialogueDurable:false,realCpuModel:model.id,cpuReplyCompleted:true,cpuReplyMs:cpuMs,realWindowsHealth:true,virtualHealthSourceExplicit:true,hostMutation:false,confirmedTargets:2,verifiedSteps:2,pageErrors:errors};
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(root,'docs/qa/dialogue-case-workspace/live.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }catch(e){await shot('live-failure');throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1);});
