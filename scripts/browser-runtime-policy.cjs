const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),qa=JSON.parse(fs.readFileSync(path.join(root,'.local/runtime-policy-qa.json'),'utf8'));
 const out=path.join(root,'docs/qa/runtime-policy');fs.mkdirSync(path.join(out,'screenshots'),{recursive:true});
 const b=await chromium.launch({channel:'msedge',headless:true});const p=await b.newPage({viewport:{width:1440,height:1080}}),errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 try{
  await p.goto(qa.url);await p.getByLabel('Логин',{exact:true}).fill('admin');await p.getByLabel('Пароль',{exact:true}).fill('admin');
  await p.getByRole('button',{name:'Войти в систему'}).click();await p.getByRole('heading',{name:'Основной раздел',exact:true}).waitFor();
  const badge=p.locator('.model-label');await badge.filter({hasText:'Windows блокирует запуск'}).waitFor();
  assert(!(await badge.innerText()).includes('требуется загрузка'));
  const active=await(await p.request.get(qa.url+'/api/model/active')).json();assert.equal(active.status,'local_runtime_policy_blocked');assert.equal(active.provider,'local');assert.equal(active.id,'qwen3.5-4b-q4');assert.deepEqual(Object.keys(active).sort(),['id','provider','status']);
  await p.screenshot({path:path.join(out,'screenshots/01-main-policy-block.png'),fullPage:true});
  await p.getByRole('button',{name:'Настройки',exact:true}).click();await p.locator('.settings-nav').getByRole('button',{name:'Модели',exact:true}).click();
  await p.getByText('Windows запретила запуск CPU runtime',{exact:false}).first().waitFor();
  const card=p.locator('.local-model-card').filter({has:p.getByRole('heading',{name:'Qwen3.5-4B Q4_K_M',exact:true})});
  await card.getByText('Файл проверен',{exact:true}).waitFor();assert.equal(await card.getByRole('button',{name:/Скачать|Повторить загрузку/}).count(),0);
  const response=p.waitForResponse(r=>r.url()===qa.url+'/api/settings/local-models/check'&&r.request().method()==='POST');
  await p.getByRole('button',{name:'Проверить локальную модель',exact:true}).click();const check=await response;assert.equal(check.status(),502);assert.equal((await check.json()).error,'local_runtime_policy_blocked');
  assert.equal(await p.getByText('Windows запретила запуск CPU runtime',{exact:false}).count(),1);
  await p.screenshot({path:path.join(out,'screenshots/02-model-policy-block.png'),fullPage:true});
  await p.getByRole('button',{name:'Использовать Qwen3.5-2B Q4_K_M',exact:true}).click();
  await p.getByText('Выбрана локальная модель для новых запросов. Проверьте запуск и JSON-контракт перед использованием.',{exact:true}).waitFor();
  await p.locator('.sidebar nav').getByRole('button',{name:/Основной раздел/}).click();await badge.filter({hasText:'qwen3.5-2b-q4'}).waitFor();await badge.filter({hasText:'Windows блокирует запуск'}).waitFor();
  await p.setViewportSize({width:390,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await p.screenshot({path:path.join(out,'screenshots/03-mobile-policy-block.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.equal(await p.evaluate(()=>localStorage.length+sessionStorage.length),0);
  const hub=await(await p.request.get(qa.url+'/api/settings/providers')).json();assert.equal(hub.active.provider,'local');assert.equal(hub.local.runtime.error,'local_runtime_policy_blocked');
  const result={scope:'isolated accounts/SQLite; public tiny weights and fixed authorization failure; no actual native launch',mainBadgePolicyBlock:true,missingWeightsNotSuggested:true,intactWeightsStayVerified:true,modelProbeHttp502:true,fixedCodeOnly:true,selectedModelIdentityPreserved:true,selectionDoesNotClaimReady:true,noFallback:true,mobile390NoOverflow:true,pageErrors:errors,realProviderCalls:false,nodeActions:0,securitySettingsChanged:false};
  fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await b.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
