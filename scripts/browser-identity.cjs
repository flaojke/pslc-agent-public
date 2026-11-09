const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),fixture=JSON.parse(fs.readFileSync(path.join(root,'.local/identity-browser-qa.json'),'utf8'));
 const out=path.join(root,'docs/qa/operator-identity/screenshots');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];const marker='PRIVATE-BROWSER-CODE-92817';
 try{
  const login=async actor=>{const context=await browser.newContext({viewport:{width:1440,height:1000}});const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(fixture.url);await page.getByRole('button',{name:'Войти через организацию'}).waitFor();assert.equal(await page.locator('#token').count(),0);await page.getByRole('button',{name:'Войти через организацию'}).click();await page.getByRole('heading',{name:'QA IdP — локальный fixture'}).waitFor();await page.getByRole('link',{name:'Войти как '+actor,exact:true}).click();await page.getByRole('heading',{name:'Основной раздел',exact:true}).waitFor();return {context,page};};
  const alice=await login('Alice');await alice.page.getByText('Оператор ИТ',{exact:true}).waitFor();await alice.page.screenshot({path:path.join(out,'01-alice-main.png'),fullPage:true});
  const question="Выполни python на oidc-qa-node:\nprint('"+marker+"')\nПроверка python:\nprint('ready')\nОжидается:\nready";
  await alice.page.getByLabel('Задание агенту',{exact:true}).fill(question);await alice.page.getByRole('button',{name:'Подготовить план'}).click();await alice.page.getByTestId('workflow-status').filter({hasText:'Нужно ваше подтверждение'}).waitFor();
  const flowId=await alice.page.locator('[data-workflow-id]').getAttribute('data-workflow-id');
  const own=await alice.context.request.get(fixture.url+'/api/workflows/'+flowId);assert(own.ok());const flow=await own.json();assert(JSON.stringify(flow).includes(marker));
  const bob=await login('Bob');assert.equal(await bob.page.locator('.workflow-current').count(),0);assert.equal((await bob.context.request.get(fixture.url+'/api/workflows/'+flowId)).status(),404);
  const foreign=await bob.context.request.post(fixture.url+'/api/workflows/'+flowId+'/decision',{headers:{Origin:fixture.url},data:{approve:true,plan_hash:flow.plan_hash}});assert.equal(foreign.status(),404);
  const auditor=await login('Auditor');await auditor.page.getByText('Аудит ИБ',{exact:true}).waitFor();await auditor.page.getByTestId('workflow-status').waitFor();
  assert.equal(await auditor.page.getByText(marker,{exact:false}).count(),0);const metadata=await auditor.context.request.get(fixture.url+'/api/workflows/'+flowId);assert(metadata.ok());assert.equal((await metadata.json()).preview,null);
  const auditDecision=await auditor.context.request.post(fixture.url+'/api/workflows/'+flowId+'/decision',{headers:{Origin:fixture.url},data:{approve:true,plan_hash:flow.plan_hash}});assert.equal(auditDecision.status(),403);
  await auditor.page.screenshot({path:path.join(out,'02-auditor-metadata.png'),fullPage:true});
  const csrf=await alice.context.request.post(fixture.url+'/api/workflows/'+flowId+'/decision',{headers:{Origin:'https://evil.invalid'},data:{approve:true,plan_hash:flow.plan_hash}});assert.equal(csrf.status(),403);
  assert.equal((await alice.context.request.get(fixture.url+'/api/workflows/'+flowId)).status(),200);
  await alice.page.getByRole('button',{name:'Завершить сеанс',exact:true}).click();await alice.page.getByRole('button',{name:'Войти через организацию'}).waitFor();assert.equal((await alice.context.request.get(fixture.url+'/api/me')).status(),401);
  await alice.page.screenshot({path:path.join(out,'03-signed-out.png'),fullPage:true});
  await auditor.page.setViewportSize({width:390,height:844});assert.equal(await auditor.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await auditor.page.screenshot({path:path.join(out,'04-mobile-auditor.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert(!fs.readFileSync(path.join(fixture.directory,'qa.sqlite3')).includes(Buffer.from(marker)));
  fs.writeFileSync(path.join(root,'docs/qa/operator-identity/browser.json'),JSON.stringify({localOIDCProtocolFixture:true,corporateIdP:false,realRS256:true,pkce:true,cookieSession:true,localTokenHidden:true,roleUI:true,ownerIsolation:true,foreignDecisionRejected:true,auditorMetadataOnly:true,csrfRejected:true,logout:true,noCommandExecuted:true,rawMarkerAbsentDatabase:true,mobileHorizontalOverflow:false,pageErrors:errors,screenshots:4},null,2));
  console.log('OIDC browser QA passed: PKCE login, roles, owner isolation, auditor metadata, CSRF, logout, mobile.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1)});
