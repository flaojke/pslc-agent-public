const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const{chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
const root=path.resolve(__dirname,'..'),qa=JSON.parse(fs.readFileSync(root+'/.local/model-label-qa.json')),out=root+'/docs/qa/model-label-refresh';fs.mkdirSync(out+'/screenshots',{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true}),adminCtx=await browser.newContext({viewport:{width:1440,height:1050}}),operatorCtx=await browser.newContext({viewport:{width:1440,height:1050}}),admin=await adminCtx.newPage(),op=await operatorCtx.newPage(),errors=[],paths=[],observed=[];
for(const p of [admin,op]){p.setDefaultTimeout(15000);p.on('pageerror',e=>errors.push(e.message));}
op.on('request',r=>paths.push(new URL(r.url()).pathname));
const badge=op.locator('.model-label');
const shot=async(name)=>op.screenshot({path:out+'/screenshots/'+name+'.png'});
async function login(p,name,password){await p.goto(qa.url);await p.getByLabel('Логин',{exact:true}).fill(name);await p.getByLabel('Пароль',{exact:true}).fill(password);await p.getByRole('button',{name:'Войти в систему'}).click();await p.getByRole('heading',{name:'Основной раздел',exact:true}).waitFor();}
async function switchModel(id){const s=await admin.request.get(qa.url+'/api/settings/providers');assert(s.ok());const state=await s.json();const r=await admin.request.post(qa.url+'/api/settings/providers/active',{headers:{Origin:qa.url},data:{provider:'openwebui',profile_id:id,revision:state.revision}});assert(r.ok());}
async function model(name){await badge.filter({hasText:name}).waitFor();}
async function getStatus(){const r=await op.request.get(qa.url+'/qa/status');assert(r.ok());return r.json();}
async function waitUntil(fn,message,ms=12000){const start=Date.now();while(Date.now()-start<ms){if(await fn())return;await new Promise(r=>setTimeout(r,80));}throw Error(message);}
async function focus(){await op.evaluate(()=>window.dispatchEvent(new Event('focus')));}
async function observe(){await op.evaluate(()=>{window.__modelLabels=[];window.__modelObserver?.disconnect();const e=document.querySelector('.model-label');window.__modelObserver=new MutationObserver(()=>window.__modelLabels.push(e.textContent));window.__modelObserver.observe(e,{childList:true,subtree:true,characterData:true});});}
try{
 await login(admin,'admin','admin');
 const created=await admin.request.post(qa.url+'/api/users',{headers:{Origin:qa.url},data:{username:'qa-operator',password:'public-model-label-password',roles:['operator'],device_ids:['*'],knowledge_spaces:[],disabled:false}});assert.equal(created.status(),201);
 await login(op,'qa-operator','public-model-label-password');await model('fixture-a');
 const safe=await(await op.request.get(qa.url+'/api/model/active')).json();assert.deepEqual(Object.keys(safe).sort(),['id','provider','status']);assert.equal(safe.provider,'external');
 await op.getByLabel('Сообщение агенту',{exact:true}).fill('QA first message creates a new conversation');await op.getByLabel('Сообщение агенту',{exact:true}).press('Control+Enter');await op.getByText('Искусственный ответ fixture-a. Действий на узлах нет.',{exact:true}).last().waitFor();await op.getByRole('button',{name:'Переименовать',exact:true}).waitFor();
 let chats=await(await op.request.get(qa.url+'/api/conversations')).json();const a=chats[0].id;
 assert((await op.request.put(qa.url+'/api/conversations/'+a,{headers:{Origin:qa.url},data:{title:'QA model case A'}})).ok());
 const rb=await op.request.post(qa.url+'/api/conversations',{headers:{Origin:qa.url},data:{title:'QA model case B'}});assert.equal(rb.status(),201);const b=(await rb.json()).id;
 await op.locator('.conversation-name').filter({hasText:'QA model case A'}).click();
 await switchModel('fixture_b');await model('fixture-b');await shot('01-admin-switch-updated-operator');
 // Fixed metadata failures must hide the old identity and must not block ordinary chat sends.
 const fail=route=>route.fulfill({status:503,contentType:'application/json',body:'{"error":"public-fixture-unavailable"}'});
 await op.route('**/api/model/active',fail);await focus();await model('статус недоступен');assert(!(await badge.innerText()).includes('fixture-b'));
 const before=(await getStatus()).model_calls;await op.getByLabel('Сообщение агенту',{exact:true}).fill('QA message while metadata is unavailable');await op.getByRole('button',{name:'Отправить →',exact:true}).click();
 await waitUntil(async()=>(await getStatus()).model_calls===before+1,'Metadata failure blocked message');
 await op.getByText('Искусственный ответ fixture-b. Действий на узлах нет.',{exact:true}).waitFor();await shot('02-unavailable-no-stale-identity');
 await op.unroute('**/api/model/active',fail);await focus();await model('fixture-b');
 // Hold one metadata transport beyond the timeout. Poll/focus/pre-send reuse it.
 let releaseHold,startedResolve,holdCount=0;const held=new Promise(r=>releaseHold=r),started=new Promise(r=>startedResolve=r);
 const hold=async route=>{holdCount++;if(holdCount!==1){await route.continue();return;}const response=await route.fetch();const value=await response.json();startedResolve();await held;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});};
 await op.route('**/api/model/active',hold);await focus();await started;await switchModel('legacy');await model('статус недоступен');
 const beforeHung=(await getStatus()).model_calls;await op.getByLabel('Сообщение агенту',{exact:true}).fill('QA non-blocking send during metadata timeout');await op.getByRole('button',{name:'Отправить →',exact:true}).click();
 await waitUntil(async()=>(await getStatus()).model_calls===beforeHung+1,'Hung metadata blocked message',1800);
 await op.getByText('Искусственный ответ fixture-a. Действий на узлах нет.',{exact:true}).last().waitFor();assert.equal(holdCount,1,'Metadata refresh not single-flight');
 await op.locator('.conversation-name').filter({hasText:'QA model case B'}).click();await op.getByLabel('Сообщение агенту',{exact:true}).fill('Draft B remains across global metadata refresh');
 await observe();releaseHold();await model('fixture-a');observed.push(...await op.evaluate(()=>window.__modelLabels));assert(!observed.some(s=>s.includes('fixture-b')),'Timed-out response restored stale model');
 assert.equal(await op.getByLabel('Сообщение агенту',{exact:true}).inputValue(),'Draft B remains across global metadata refresh');await op.unroute('**/api/model/active',hold);await shot('03-recovered-current-selection');
 // A held old component request cannot overwrite a fresh mount.
 let releaseOld,startOldResolve;const oldHeld=new Promise(r=>releaseOld=r),oldStarted=new Promise(r=>startOldResolve=r);let oldCalls=0;
 const oldRoute=async route=>{oldCalls++;if(oldCalls!==1){await route.continue();return;}const r=await route.fetch();const value=await r.json();startOldResolve();await oldHeld;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});};
 await op.route('**/api/model/active',oldRoute);await focus();await oldStarted;
 await op.getByRole('button',{name:'Настройки',exact:true}).click();await op.locator('.settings-nav').waitFor();await switchModel('fixture_b');await op.locator('.sidebar nav').getByRole('button',{name:/Основной раздел/}).click();await model('fixture-b');await observe();releaseOld();await op.waitForTimeout(350);assert(!(await op.evaluate(()=>window.__modelLabels)).some(s=>s.includes('fixture-a')),'Unmounted metadata overwritten new mount');await op.unroute('**/api/model/active',oldRoute);
 // A late send response must not clear a different conversation draft.
 await op.locator('.conversation-name').filter({hasText:'QA model case A'}).click();
 let releasePost,startPostResolve;const postHeld=new Promise(r=>releasePost=r),postStarted=new Promise(r=>startPostResolve=r);
 const postRoute=async route=>{const response=await route.fetch();startPostResolve();await postHeld;await route.fulfill({response});};
 await op.route('**/api/conversations/'+a+'/messages',postRoute);await op.getByLabel('Сообщение агенту',{exact:true}).fill('QA message bound to case A');await op.getByRole('button',{name:'Отправить →',exact:true}).click();await postStarted;
 await op.locator('.conversation-name').filter({hasText:'QA model case B'}).click();await op.getByLabel('Сообщение агенту',{exact:true}).fill('Draft B must survive old send response');releasePost();
 await waitUntil(async()=>await op.getByRole('button',{name:'Отправить →',exact:true}).count()===1,'Late send did not settle');await op.waitForTimeout(300);assert.equal(await op.getByLabel('Сообщение агенту',{exact:true}).inputValue(),'Draft B must survive old send response');await op.unroute('**/api/conversations/'+a+'/messages',postRoute);
 // A newly selected case is not the previous loaded case. Synthetic UI-only target lets
 // both composer actions be checked; backend retains zero enrolled nodes/no plan execution.
 const rc=await op.request.post(qa.url+'/api/conversations',{headers:{Origin:qa.url},data:{title:'QA model case C'}});assert.equal(rc.status(),201);const c=(await rc.json()).id;
 const uiDevice={id:'browser-only-target',name:'BROWSER-ONLY-QA',os_family:'windows',device_kind:'workstation',virtual:true,online:true,revoked:false,commands_enabled:false,installations_enabled:false,diagnostics_enabled:false,last_seen:0};
 const devicesRoute=route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify([uiDevice])});
 const casePattern=/\/api\/conversations\/[^/?]+$/;
 const scopesRoute=async route=>{if(route.request().method()!=='GET'){await route.continue();return;}const response=await route.fetch();const data=await response.json();data.target_scope={kind:'all',ids:[]};await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});};
 await op.route('**/api/devices',devicesRoute);await op.route(casePattern,scopesRoute);
 const planButton=op.getByRole('button',{name:'Подготовить план',exact:true}),sendButton=op.getByRole('button',{name:'Отправить →',exact:true});
 await waitUntil(async()=>await planButton.isEnabled(),'Plan was not available before selected-case hold');
 let releaseCase,startCaseResolve;const caseHeld=new Promise(r=>releaseCase=r),caseStarted=new Promise(r=>startCaseResolve=r);
 const caseRoute=async route=>{const response=await route.fetch();const data=await response.json();data.target_scope={kind:'all',ids:[]};startCaseResolve();await caseHeld;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});};
 const casePosts=[];const casePostListener=r=>{if(r.method()==='POST'&&/\/api\/conversations\/[^/]+\/(messages|plans)$/.test(new URL(r.url()).pathname))casePosts.push(new URL(r.url()).pathname);};op.on('request',casePostListener);
 const planReceipt=route=>route.fulfill({status:200,contentType:'application/json',body:'{}'});await op.route('**/api/conversations/'+c+'/plans',planReceipt);await op.route('**/api/conversations/'+c,caseRoute);
 await op.locator('.conversation-name').filter({hasText:'QA model case C'}).click();await caseStarted;
 await op.getByLabel('Сообщение агенту',{exact:true}).fill('QA draft while case C is loading');assert(await sendButton.isDisabled());assert(await planButton.isDisabled());
 await op.getByLabel('Сообщение агенту',{exact:true}).press('Control+Enter');
 await op.locator('.case-composer').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
 await op.waitForTimeout(250);assert.deepEqual(casePosts,[],'A pending selected-case GET allowed a POST to old case');await shot('05-selected-case-loading-blocks-actions');
 releaseCase();await op.locator('.case-heading h2').filter({hasText:'QA model case C'}).waitFor();await waitUntil(async()=>await sendButton.isEnabled(),'Loaded new case did not enable sending');
 const beforeNewCase=(await getStatus()).model_calls;await sendButton.click();await waitUntil(async()=>(await getStatus()).model_calls===beforeNewCase+1,'Loaded new case did not send');
 await op.getByText('Искусственный ответ fixture-b. Действий на узлах нет.',{exact:true}).waitFor();await waitUntil(async()=>await planButton.isEnabled(),'Loaded new case did not enable plan');
 await op.getByLabel('Сообщение агенту',{exact:true}).fill('QA browser-only new-case plan receipt');await planButton.click();await waitUntil(async()=>casePosts.length===2,'New case plan was not posted');
 assert.deepEqual(casePosts,['/api/conversations/'+c+'/messages','/api/conversations/'+c+'/plans']);
 await waitUntil(async()=>await planButton.isEnabled(),'New case plan route did not settle');op.off('request',casePostListener);
 await op.unroute('**/api/conversations/'+c,caseRoute);await op.unroute('**/api/conversations/'+c+'/plans',planReceipt);await op.unroute(casePattern,scopesRoute);await op.unroute('**/api/devices',devicesRoute);
 await op.setViewportSize({width:390,height:844});assert.equal(await op.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await shot('04-mobile-current-model');
 assert(!paths.some(x=>/^\/api\/settings\/(model|providers|local-models)/.test(x)),'Operator UI fetched admin model config');
 assert.equal(await op.evaluate(()=>localStorage.length+sessionStorage.length),0);assert.deepEqual(errors,[]);
 const status=await getStatus();assert.equal(status.enrolled_nodes,0);assert.equal(status.node_actions,0);assert.equal(status.real_provider_calls,false);assert(!fs.readFileSync(qa.database).includes(Buffer.from('PUBLIC-MODEL-REFRESH-KEY')));
 const result={scope:'two independent browser contexts, isolated accounts/SQLite, mocked model transport',adminSelectionAPI:true,operatorUpdatedFromAnotherAdmin:true,metadataFieldsOnly:['id','provider','status'],failureHidesOldModel:true,failedOrHungMetadataDoesNotBlockMessages:true,singleFlightHeldRequestCount:1,metadataRequestsIncludingRecovery:holdCount,timeoutResponseDiscarded:true,lateUnmountResponseDiscarded:true,otherConversationDraftPreserved:true,emptySelectionCreatesConversation:true,pendingCaseGetBlocksMessageAndPlan:true,newCaseMessageAndPlanRouting:true,planReceiptIsBrowserOnly:true,operatorAdminEndpointCalls:paths.filter(x=>/^\/api\/settings\/(model|providers|local-models)/.test(x)).length,noBrowserStorage:true,mobile390NoOverflow:true,modelsUsed:status.models_used,realProviderCalls:false,nodeActions:0,pageErrors:errors};
 fs.writeFileSync(out+'/browser.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
